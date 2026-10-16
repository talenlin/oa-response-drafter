"""NotebookLM private-web transport.

This module is intentionally ignorant of patents, OA rounds and UI state.
Protocol changes should be repairable here without touching OA workflows.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .auth import AuthManager, extract_page_metadata
from .errors import AuthenticationError, ProtocolError, SourceUploadError


DEFAULT_BASE_URL = "https://notebooklm.google.com"
ALLOWED_BASE_HOSTS = {
    "notebooklm.google.com",
    "notebook.google.com",
    "notebooklm.cloud.google.com",
    "notebook.cloud.google.com",
}
BATCH_ENDPOINT = "/_/LabsTailwindUi/data/batchexecute"
QUERY_ENDPOINT = (
    "/_/LabsTailwindUi/data/"
    "google.internal.labs.tailwind.orchestration.v1."
    "LabsTailwindOrchestrationService/GenerateFreeFormStreamed"
)
UPLOAD_ENDPOINT = "/upload/_/"
FALLBACK_BUILD_LABEL = "boq_labs-tailwind-frontend_20260108.06_p0"


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _walk(value: Any):
    yield value
    if isinstance(value, list):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)


class RPCTransport:
    def __init__(self, auth: AuthManager) -> None:
        self.auth = auth
        self._lock = threading.RLock()
        self._reqid = random.randint(100000, 999999)

    def _base_url(self) -> str:
        override = os.environ.get("NOTEBOOKLM_BASE_URL", "").rstrip("/")
        if override:
            parsed = urllib.parse.urlparse(override)
            if parsed.scheme == "https" and parsed.hostname in ALLOWED_BASE_HOSTS:
                return override
        host = str(self.auth.metadata().get("base_host") or "")
        return f"https://{host}" if host in ALLOWED_BASE_HOSTS else DEFAULT_BASE_URL

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        base_url = self._base_url()
        base_host = urllib.parse.urlparse(base_url).hostname or "notebooklm.google.com"
        headers = {
            "Accept": "*/*",
            "Cookie": self.auth.cookie_header(base_host),
            "Origin": base_url,
            "Referer": base_url + "/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132 Safari/537.36",
            "x-goog-authuser": "0",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _open_with_retry(
        request: urllib.request.Request,
        timeout: float,
        attempts: int = 3,
    ):
        """Open a repeatable NotebookLM request with bounded backoff."""
        for attempt in range(attempts):
            try:
                return urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                if error.code not in {408, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt + 1 >= attempts:
                    raise
            time.sleep(min(1.5 * (attempt + 1), 4.5))
        raise RuntimeError("unreachable")

    def refresh_tokens(self) -> dict[str, Any]:
        request = urllib.request.Request(self._base_url() + "/", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                html = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号") from error
            raise ProtocolError(f"NotebookLM 登录校验失败（HTTP {error.code}）") from error
        metadata = extract_page_metadata(html, self.auth.metadata())
        if not metadata.get("csrf_token"):
            raise AuthenticationError("未能从 NotebookLM 读取登录令牌，请重新登录")
        self.auth.save_session(self.auth.cookies(), metadata)
        return metadata

    def _metadata(self, force_refresh: bool = False) -> dict[str, Any]:
        metadata = self.auth.metadata()
        if force_refresh or not metadata.get("csrf_token"):
            return self.refresh_tokens()
        return metadata

    def call(
        self,
        rpc_id: str,
        params: Any,
        path: str = "/",
        timeout: float = 60.0,
        *,
        retry_network: bool = True,
    ) -> Any:
        """Call one batchexecute RPC and unwrap its ``wrb.fr`` payload."""
        with self._lock:
            self._reqid += 100000
            reqid = self._reqid
        refresh_metadata = False
        for attempt in range(2):
            metadata = self._metadata(force_refresh=refresh_metadata)
            request_envelope = [[[rpc_id, compact(params), None, "generic"]]]
            body_values = {"f.req": compact(request_envelope), "at": str(metadata.get("csrf_token") or "")}
            body = urllib.parse.urlencode(body_values).encode("utf-8")
            query = {
                "rpcids": rpc_id,
                "source-path": path,
                "bl": os.environ.get("NOTEBOOKLM_BL") or metadata.get("build_label") or FALLBACK_BUILD_LABEL,
                "hl": os.environ.get("NOTEBOOKLM_HL", "zh-CN"),
                "_reqid": str(reqid),
                "rt": "c",
            }
            if metadata.get("session_id"):
                query["f.sid"] = str(metadata["session_id"])
            url = self._base_url() + BATCH_ENDPOINT + "?" + urllib.parse.urlencode(query)
            request = urllib.request.Request(
                url,
                data=body,
                headers=self._headers("application/x-www-form-urlencoded;charset=UTF-8"),
                method="POST",
            )
            try:
                with self._open_with_retry(request, timeout=timeout, attempts=3 if retry_network else 1) as response:
                    raw = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                if error.code in {400, 401, 403} and attempt == 0:
                    refresh_metadata = True
                    continue
                if error.code in {401, 403}:
                    raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号") from error
                raise ProtocolError(f"NotebookLM 请求失败（HTTP {error.code}）") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if retry_network and attempt == 0:
                    time.sleep(1.5)
                    continue
                raise ProtocolError(
                    "NotebookLM 连接暂时中断，已自动重试仍未恢复；"
                    "请稍后重试，本地任务进度会保留。"
                ) from error
            try:
                return self._parse_rpc(raw, rpc_id)
            except AuthenticationError:
                if attempt == 0:
                    refresh_metadata = True
                    continue
                raise
        raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号")

    @staticmethod
    def _json_chunks(raw: str) -> list[Any]:
        if raw.startswith(")]}'"):
            raw = raw[4:]
        lines = raw.strip().splitlines()
        values: list[Any] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line:
                index += 1
                continue
            if line.isdigit() and index + 1 < len(lines):
                index += 1
                line = lines[index].strip()
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            index += 1
        return values

    @classmethod
    def _parse_rpc(cls, raw: str, rpc_id: str) -> Any:
        for chunk in cls._json_chunks(raw):
            for item in _walk(chunk):
                if not isinstance(item, list) or len(item) < 3 or item[0] != "wrb.fr":
                    continue
                if item[1] not in {rpc_id, None}:
                    continue
                if item[2] is None:
                    code = None
                    if len(item) > 5 and isinstance(item[5], list) and item[5]:
                        code = item[5][0]
                    if code == 16:
                        raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号")
                    raise ProtocolError(f"NotebookLM 拒绝了请求（代码 {code if code is not None else 'unknown'}）")
                if isinstance(item[2], str):
                    try:
                        return json.loads(item[2])
                    except json.JSONDecodeError:
                        return item[2]
                return item[2]
        raise ProtocolError("NotebookLM 返回了无法识别的响应；网页协议可能已更新")

    def streamed_query(self, params: Any, timeout: float = 180.0) -> str:
        """Call NotebookLM's chunked free-form query endpoint."""
        with self._lock:
            self._reqid += 100000
            reqid = self._reqid
        refresh_metadata = False
        for attempt in range(2):
            metadata = self._metadata(force_refresh=refresh_metadata)
            f_req = [None, compact(params)]
            body_values = {"f.req": compact(f_req)}
            if metadata.get("csrf_token"):
                body_values["at"] = str(metadata["csrf_token"])
            query = {
                "bl": os.environ.get("NOTEBOOKLM_BL") or metadata.get("build_label") or FALLBACK_BUILD_LABEL,
                "hl": os.environ.get("NOTEBOOKLM_HL", "zh-CN"),
                "_reqid": str(reqid),
                "rt": "c",
            }
            if metadata.get("session_id"):
                query["f.sid"] = str(metadata["session_id"])
            # Match the browser request exactly. The endpoint is stricter than
            # batchexecute and expects the trailing form separator as well as
            # an explicit content type.
            body = (urllib.parse.urlencode(body_values) + "&").encode("utf-8")
            request = urllib.request.Request(
                self._base_url() + QUERY_ENDPOINT + "?" + urllib.parse.urlencode(query),
                data=body,
                headers=self._headers("application/x-www-form-urlencoded;charset=UTF-8"),
                method="POST",
            )
            try:
                with self._open_with_retry(request, timeout=timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                if error.code in {400, 401, 403} and attempt == 0:
                    refresh_metadata = True
                    continue
                if error.code in {401, 403}:
                    raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号") from error
                raise ProtocolError(f"NotebookLM 分析请求失败（HTTP {error.code}）") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                raise ProtocolError(
                    "NotebookLM 连接暂时中断，已自动重试仍未恢复；"
                    "请稍后重试，本地任务进度会保留。"
                ) from error
        raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号")

    def upload_file(self, notebook_id: str, source_id: str, file_path: Path) -> None:
        """Complete Google's resumable source upload after the registration RPC."""
        body = compact({"PROJECT_ID": notebook_id, "SOURCE_NAME": file_path.name, "SOURCE_ID": source_id}).encode("utf-8")
        headers = self._headers("application/x-www-form-urlencoded;charset=UTF-8")
        headers.update({
            "x-goog-upload-command": "start",
            "x-goog-upload-header-content-length": str(file_path.stat().st_size),
            "x-goog-upload-protocol": "resumable",
        })
        request = urllib.request.Request(self._base_url() + UPLOAD_ENDPOINT + "?authuser=0", data=body, headers=headers, method="POST")
        try:
            with self._open_with_retry(request, timeout=60.0) as response:
                upload_url = response.headers.get("x-goog-upload-url")
            if not upload_url:
                raise SourceUploadError(f"未能为 {file_path.name} 创建上传通道")
            upload_headers = self._headers("application/x-www-form-urlencoded;charset=utf-8")
            upload_headers.update({"x-goog-upload-command": "upload, finalize", "x-goog-upload-offset": "0"})
            upload_request = urllib.request.Request(
                upload_url, data=file_path.read_bytes(), headers=upload_headers, method="POST"
            )
            with self._open_with_retry(upload_request, timeout=300.0):
                return
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号") from error
            raise SourceUploadError(f"上传 {file_path.name} 失败（HTTP {error.code}）") from error
        except OSError as error:
            raise SourceUploadError(f"上传 {file_path.name} 失败：{error}") from error
