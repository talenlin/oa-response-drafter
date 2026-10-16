"""Local, user-owned NotebookLM session management.

No Google cookie is exposed to the browser UI or application logs.  Existing
``notebooklm-mcp-cli`` credentials are imported once as a migration aid; all
subsequent reads use OA阅答's own profile directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .errors import AuthenticationError


REQUIRED_COOKIE_NAMES = frozenset({"SID", "HSID", "SSID", "APISID", "SAPISID"})


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class AuthManager:
    """Own one local NotebookLM browser/session profile."""

    def __init__(self, data_root: Path, profile: str = "default") -> None:
        self.profile = profile
        self.root = data_root / "notebooklm-native"
        self.profile_root = self.root / "profiles" / profile
        self.cookies_path = self.profile_root / "cookies.json"
        self.metadata_path = self.profile_root / "metadata.json"
        self.browser_root = self.root / "chrome-profiles" / profile
        self._lock = threading.RLock()
        self._login_process: subprocess.Popen[Any] | None = None
        self._login_state: dict[str, Any] = {"state": "idle", "stage": None, "message": None}
        self._migrate_legacy_profile()

    def _migrate_legacy_profile(self) -> None:
        """Copy only the explicit cookie export and token metadata, never secrets to logs."""
        if self.cookies_path.exists() and self.metadata_path.exists():
            return
        legacy = Path.home() / ".notebooklm-mcp-cli" / "profiles" / self.profile
        try:
            if (legacy / "cookies.json").is_file() and not self.cookies_path.exists():
                self.profile_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy / "cookies.json", self.cookies_path)
            if (legacy / "metadata.json").is_file() and not self.metadata_path.exists():
                self.profile_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy / "metadata.json", self.metadata_path)
        except OSError:
            # Migration is optional. A fresh browser login remains available.
            return

    def cookies(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AuthenticationError("尚未在 OA阅答 中连接 NotebookLM") from error
        if not isinstance(value, list):
            raise AuthenticationError("本机 NotebookLM 登录资料格式无效，请重新登录")
        cookies = [item for item in value if isinstance(item, dict) and item.get("name") and item.get("value")]
        names = {str(item["name"]) for item in cookies}
        if not REQUIRED_COOKIE_NAMES.issubset(names):
            raise AuthenticationError("本机 Google 登录资料不完整，请重新登录")
        return cookies

    def metadata(self) -> dict[str, Any]:
        try:
            value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    def save_session(self, cookies: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
        filtered: list[dict[str, Any]] = []
        for item in cookies:
            if not isinstance(item, dict) or not item.get("name") or not item.get("value"):
                continue
            domain = str(item.get("domain") or "")
            if "google.com" not in domain and "notebooklm.google" not in domain:
                continue
            filtered.append({key: item.get(key) for key in (
                "name", "value", "domain", "path", "expires", "httpOnly", "secure", "session", "sameSite"
            ) if key in item})
        names = {str(item.get("name")) for item in filtered}
        if not REQUIRED_COOKIE_NAMES.issubset(names):
            raise AuthenticationError("浏览器尚未完成 Google 登录")
        metadata = dict(metadata)
        metadata.pop("invalidated_at", None)
        metadata.pop("invalid_reason", None)
        _atomic_json(self.cookies_path, filtered)
        _atomic_json(self.metadata_path, metadata)

    def invalidate_session(self, message: str) -> None:
        """Remember a server-confirmed auth rejection across application restarts."""
        metadata = self.metadata()
        metadata["invalidated_at"] = time.time()
        metadata["invalid_reason"] = message
        _atomic_json(self.metadata_path, metadata)

    def cookie_header(self, host: str | None = None) -> str:
        """Return the cookies a browser would actually send to ``host``.

        Google currently stores separate OSID cookies for the legacy and new
        Notebook hosts. Sending the legacy value to the new host makes an
        otherwise valid browser session look logged out.
        """
        host = (host or "notebooklm.google.com").lower()
        ranked: dict[str, tuple[int, str]] = {}
        for cookie in self.cookies():
            name = str(cookie["name"])
            domain = str(cookie.get("domain") or "").lower()
            normalized = domain.lstrip(".")
            if normalized and host != normalized and not host.endswith("." + normalized):
                continue
            rank = 2 if normalized == host else 1
            if name not in ranked or rank >= ranked[name][0]:
                ranked[name] = (rank, str(cookie["value"]))
        return "; ".join(f"{name}={value}" for name, (_, value) in ranked.items())

    def status(self) -> dict[str, Any]:
        with self._lock:
            login = dict(self._login_state)
        try:
            self.cookies()
            metadata = self.metadata()
            if metadata.get("invalidated_at"):
                state = "not_authenticated"
                message = str(metadata.get("invalid_reason") or "NotebookLM 登录已过期，请重新连接 Google 账号")
            else:
                state = "authenticated"
                message = "已读取本机保存的 NotebookLM 登录状态"
        except AuthenticationError as error:
            metadata = {}
            state = "not_authenticated"
            message = str(error)
        if login.get("state") in {"launching", "waiting_for_user", "capturing"}:
            state = "login_in_progress"
            message = str(login.get("message") or message)
        elif login.get("state") == "error":
            state = "not_authenticated"
            message = str(login.get("message") or message)
        return {
            "state": state,
            "provider": "oa-yueda-native",
            "cliAvailable": False,
            "profile": self.profile,
            "account": metadata.get("email"),
            "stage": login.get("stage"),
            "message": message,
        }

    @staticmethod
    def _chrome_executable() -> Path | None:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        return next((path for path in candidates if path.is_file()), None)

    def begin_login(self) -> dict[str, Any]:
        """Open the isolated local browser immediately and capture its session asynchronously."""
        with self._lock:
            if self._login_state.get("state") in {"launching", "waiting_for_user", "capturing"}:
                return self.status()
            self._login_state = {
                "state": "launching",
                "stage": "opening_browser",
                "message": "正在打开本机浏览器…",
            }
        threading.Thread(target=self._login_worker, daemon=True).start()
        return self.status()

    def _login_worker(self) -> None:
        try:
            from .cdp import BrowserSessionCapture

            chrome = self._chrome_executable()
            if chrome is None:
                raise AuthenticationError("未找到 Chrome 或 Edge，无法打开 Google 登录")
            self.browser_root.mkdir(parents=True, exist_ok=True)
            capture = BrowserSessionCapture(chrome, self.browser_root)
            with self._lock:
                self._login_state.update({
                    "state": "waiting_for_user",
                    "stage": "browser_login",
                    "message": "已打开电脑上安装的 Chrome 或 Edge；请在其中完成 Google 登录。",
                })

            def update_stage(stage: str, message: str) -> None:
                with self._lock:
                    self._login_state.update({
                        "state": "capturing" if stage != "browser_login" else "waiting_for_user",
                        "stage": stage,
                        "message": message,
                    })

            cookies, page_html, base_host = capture.capture(timeout=300.0, on_stage=update_stage)
            with self._lock:
                self._login_state.update({
                    "state": "capturing",
                    "stage": "saving_session",
                    "message": "已检测到登录，正在保存本机会话…",
                })
            previous = self.metadata()
            metadata = extract_page_metadata(page_html, previous)
            metadata["base_host"] = base_host
            self.save_session(cookies, metadata)
            with self._lock:
                self._login_state = {
                    "state": "complete",
                    "stage": "connected",
                    "message": "NotebookLM 已连接，后续启动将复用本机登录状态。",
                }
        except Exception as error:  # Browser errors must be visible in the non-technical UI.
            with self._lock:
                self._login_state = {"state": "error", "stage": "failed", "message": str(error)[:500]}


def extract_page_metadata(page_html: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract volatile request tokens from NotebookLM's signed-in homepage."""
    previous = dict(previous or {})
    patterns = {
        "csrf_token": [r'"SNlM0e"\s*:\s*"([^"]+)"', r'"SNlM0e","([^"]+)"'],
        "session_id": [r'"FdrFJe"\s*:\s*"([^"]+)"', r'"FdrFJe","([^"]+)"'],
        "build_label": [r'"cfb2h"\s*:\s*"([^"]+)"', r'[?&]bl=([^&"\\]+)'],
    }
    for key, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, page_html)
            if match:
                previous[key] = match.group(1).replace("\\u003d", "=")
                break
    previous["last_validated"] = time.time()
    return previous
