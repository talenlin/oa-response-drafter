"""Minimal Chrome DevTools Protocol client for local sign-in capture.

Only the two commands required for authentication are implemented.  Keeping
this small avoids shipping Playwright, Selenium or a second browser runtime.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sqlite3
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import AuthenticationError


class _WebSocket:
    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise AuthenticationError("浏览器调试地址无效")
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port or 80}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.socket.recv(4096)
        if not response.startswith(b"HTTP/1.1 101"):
            raise AuthenticationError("无法连接本机浏览器登录窗口")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if expected.lower() not in response.lower():
            raise AuthenticationError("浏览器登录通道校验失败")

    def send_json(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.socket.sendall(bytes(header) + masked)

    def receive_json(self, timeout: float = 10.0) -> dict[str, Any]:
        self.socket.settimeout(timeout)
        while True:
            first = self._receive_exact(2)
            opcode = first[0] & 0x0F
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._receive_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._receive_exact(8))[0]
            if first[1] & 0x80:
                mask = self._receive_exact(4)
            else:
                mask = None
            payload = self._receive_exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                raise AuthenticationError("浏览器登录窗口已关闭")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def _receive_exact(self, size: int) -> bytes:
        value = b""
        while len(value) < size:
            chunk = self.socket.recv(size - len(value))
            if not chunk:
                raise AuthenticationError("浏览器登录连接已中断")
            value += chunk
        return value

    def _send_control(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.socket.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked)

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


class BrowserSessionCapture:
    REQUIRED_COOKIE_NAMES = frozenset({"SID", "HSID", "SSID", "APISID", "SAPISID"})

    def __init__(self, executable: Path, profile_root: Path) -> None:
        self.executable = executable
        # Chrome can silently ignore a relative user-data-dir on Windows.
        self.profile_root = profile_root.resolve()

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @staticmethod
    def is_notebook_url(url: str) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        return host in {
            "notebook.google.com",
            "notebooklm.google.com",
            "notebook.cloud.google.com",
            "notebooklm.cloud.google.com",
        }

    def interactive_login_command(self) -> list[str]:
        """Launch Google sign-in without automation or a debugging channel."""
        return [
            str(self.executable),
            f"--user-data-dir={self.profile_root}",
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            "https://notebook.google.com/",
        ]

    def _capture_command(self) -> list[str]:
        """Open the already-signed-in profile for short-lived local capture."""
        return [
            str(self.executable),
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.profile_root}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://notebook.google.com/",
        ]

    @staticmethod
    def _creation_flags() -> int:
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _required_login_cookies_present(self) -> bool:
        """Observe only cookie names/domains; encrypted cookie values stay unread."""
        candidates = [
            self.profile_root / "Default" / "Network" / "Cookies",
            self.profile_root / "Default" / "Cookies",
        ]
        for database in candidates:
            if not database.is_file():
                continue
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(
                    f"file:{database.as_posix()}?mode=ro",
                    uri=True,
                    timeout=0.2,
                )
                placeholders = ",".join("?" for _ in self.REQUIRED_COOKIE_NAMES)
                rows = connection.execute(
                    f"SELECT DISTINCT name FROM cookies "
                    f"WHERE name IN ({placeholders}) AND host_key LIKE '%google%'",
                    tuple(self.REQUIRED_COOKIE_NAMES),
                )
                names = {str(row[0]) for row in rows}
                if self.REQUIRED_COOKIE_NAMES.issubset(names):
                    return True
            except (OSError, sqlite3.Error):
                # Chrome can briefly lock the database while committing login.
                pass
            finally:
                if connection is not None:
                    connection.close()
        return False

    def _wait_for_interactive_login(
        self,
        process: subprocess.Popen[Any],
        deadline: float,
    ) -> None:
        launched_at = time.monotonic()
        while time.monotonic() < deadline:
            if self._required_login_cookies_present():
                return
            if process.poll() is not None and time.monotonic() - launched_at >= 2.0:
                raise AuthenticationError("Google 登录窗口已关闭，但登录尚未完成，请重新打开后登录")
            time.sleep(0.5)
        raise AuthenticationError("等待 Google 登录超时，请在本机浏览器完成登录后重试")

    @staticmethod
    def _close_interactive_browser(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        requested_graceful_close = False
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

                def close_owned_window(window: int, unused: int) -> bool:
                    del unused
                    owner = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
                    if owner.value == process.pid and user32.IsWindowVisible(window):
                        user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE
                    return True

                user32.EnumWindows(enum_callback(close_owned_window), 0)
                requested_graceful_close = True
            except (AttributeError, OSError):
                requested_graceful_close = False
        if not requested_graceful_close:
            process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)

    def capture(
        self,
        timeout: float,
        on_stage: Any | None = None,
    ) -> tuple[list[dict[str, Any]], str, str]:
        deadline = time.monotonic() + timeout
        self.profile_root.mkdir(parents=True, exist_ok=True)
        process: subprocess.Popen[Any] | None = None
        port = self._existing_debug_port()
        if port is None:
            # A stale file otherwise makes a fresh browser look connected even though
            # its old debugging endpoint no longer exists.
            try:
                (self.profile_root / "DevToolsActivePort").unlink()
            except FileNotFoundError:
                pass
            if on_stage is not None:
                on_stage("browser_login", "已打开安全登录窗口；请在其中完成 Google 登录。")
            login_process = subprocess.Popen(
                self.interactive_login_command(),
                creationflags=self._creation_flags(),
                close_fds=True,
            )
            self._wait_for_interactive_login(login_process, deadline)
            if on_stage is not None:
                on_stage("closing_login_browser", "已检测到登录，正在关闭登录窗口并安全保存会话…")
            self._close_interactive_browser(login_process)
            time.sleep(0.75)
            try:
                (self.profile_root / "DevToolsActivePort").unlink()
            except FileNotFoundError:
                pass
            if on_stage is not None:
                on_stage("capturing", "登录已完成，正在从本机隔离资料中读取 NotebookLM 会话…")
            process = subprocess.Popen(
                self._capture_command(),
                creationflags=self._creation_flags(),
                close_fds=True,
            )
            capture_deadline = min(deadline, time.monotonic() + 20.0)
            try:
                port = self._wait_for_active_port(process, capture_deadline)
            except AuthenticationError as error:
                raise AuthenticationError(
                    "登录已完成，但安全读取窗口未能启动；请关闭刚才的登录浏览器后重试"
                ) from error

        last_error: Exception | None = None
        while time.monotonic() < deadline:
            websocket: _WebSocket | None = None
            try:
                websocket_url = self._wait_for_target(port, deadline)
                websocket = _WebSocket(websocket_url)
                sequence = 0

                def call(method: str, params: dict[str, Any] | None = None) -> Any:
                    nonlocal sequence
                    sequence += 1
                    request_id = sequence
                    websocket.send_json({"id": request_id, "method": method, "params": params or {}})
                    while True:
                        reply = websocket.receive_json(timeout=min(10.0, max(1.0, deadline - time.monotonic())))
                        if reply.get("id") == request_id:
                            if reply.get("error"):
                                raise AuthenticationError(str(reply["error"].get("message") or "浏览器登录读取失败"))
                            return reply.get("result")

                call("Network.enable")
                call("Runtime.enable")
                while time.monotonic() < deadline:
                    result = call("Network.getAllCookies") or {}
                    cookies = result.get("cookies") if isinstance(result, dict) else []
                    names = {item.get("name") for item in cookies if isinstance(item, dict)}
                    page = call("Runtime.evaluate", {
                        "expression": "({url: location.href, html: document.documentElement ? document.documentElement.outerHTML : ''})",
                        "returnByValue": True,
                    }) or {}
                    page_value = (((page.get("result") or {}).get("value")) if isinstance(page, dict) else {}) or {}
                    page_html = str(page_value.get("html") or "") if isinstance(page_value, dict) else ""
                    page_url = str(page_value.get("url") or "") if isinstance(page_value, dict) else ""
                    if (
                        self.REQUIRED_COOKIE_NAMES.issubset(names)
                        and self.is_notebook_url(page_url)
                    ):
                        return list(cookies), page_html, (urlparse(page_url).hostname or "notebooklm.google.com")
                    time.sleep(0.75)
            except (OSError, AuthenticationError, json.JSONDecodeError) as error:
                # Google may replace the page target while moving from the account
                # screen to NotebookLM. Reattach instead of declaring login failed.
                last_error = error
                time.sleep(0.25)
            finally:
                if websocket is not None:
                    websocket.close()

        if process is not None and process.poll() is not None:
            process.wait(timeout=1)
        if last_error is not None and not isinstance(last_error, AuthenticationError):
            raise AuthenticationError(f"登录页连接曾中断，自动重连仍未成功：{last_error}") from last_error
        raise AuthenticationError("等待 Google 登录超时；请保持登录窗口打开后重试")

    def _existing_debug_port(self) -> int | None:
        active_port = self.profile_root / "DevToolsActivePort"
        try:
            port = int(active_port.read_text(encoding="utf-8").splitlines()[0])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5):
                return port
        except (OSError, ValueError, IndexError, urllib.error.URLError):
            return None

    def _wait_for_active_port(self, process: subprocess.Popen[Any], deadline: float) -> int:
        """Read the port chosen by Chrome instead of racing a preselected free port."""
        active_port = self.profile_root / "DevToolsActivePort"
        launched_at = time.time()
        while time.monotonic() < deadline:
            if process.poll() is not None:
                # Chrome may hand the profile to a just-started sibling process
                # and let the bootstrap process exit; the active-port file is authoritative.
                time.sleep(0.1)
            try:
                if active_port.stat().st_mtime < launched_at - 1:
                    raise OSError("stale DevToolsActivePort")
                first_line = active_port.read_text(encoding="utf-8").splitlines()[0]
                port = int(first_line)
                if 0 < port < 65536:
                    return port
            except (OSError, ValueError, IndexError):
                pass
            time.sleep(0.1)
        raise AuthenticationError("浏览器调试通道没有就绪，请重新打开 Google 登录")

    @classmethod
    def _wait_for_target(cls, port: int, deadline: float) -> str:
        endpoint = f"http://127.0.0.1:{port}/json/list"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(endpoint, timeout=1.0) as response:
                    targets = json.loads(response.read().decode("utf-8"))
                targets = sorted(
                    targets,
                    key=lambda target: 0 if cls.is_notebook_url(str(target.get("url") or "")) else 1,
                )
                for target in targets:
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return str(target["webSocketDebuggerUrl"])
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        raise AuthenticationError("浏览器未能启动，请确认 Chrome 或 Edge 可正常打开")
