from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock

from bridge.notebooklm.auth import AuthManager, extract_page_metadata
from bridge.notebooklm.cdp import BrowserSessionCapture
from bridge.notebooklm.client import NotebookLMClient
from bridge.notebooklm.errors import AuthenticationError, ProtocolError
from bridge.notebooklm.transport import RPCTransport


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, str]] = []
        self.query_params: Any = None

    def call(self, rpc_id: str, params: Any, path: str = "/", timeout: float = 60.0, **kwargs: Any) -> Any:
        self.calls.append((rpc_id, params, path))
        if rpc_id == NotebookLMClient.RPC_LIST_NOTEBOOKS:
            return [[
                ["OA A", [], "notebook-a"],
                ["OA B", [[['source-b'], "材料B", [], [None, 2]]], "notebook-b"],
            ]]
        if rpc_id == NotebookLMClient.RPC_GET_NOTEBOOK:
            return [["OA A", [[['source-a'], "材料A", [], [None, 2]]], "notebook-a"]]
        if rpc_id == NotebookLMClient.RPC_CREATE_NOTEBOOK:
            return [params[0], None, "new-notebook-id"]
        if rpc_id == NotebookLMClient.RPC_GET_CONVERSATIONS:
            return []
        return []

    def streamed_query(self, params: Any, timeout: float = 180.0) -> str:
        self.query_params = params
        type_info = [None, None, None, [
            [["passage"], [None, None, None, None, [[0, 2, [[[0, 2, "证据文字"]]]]], [[["source-a"]]]]],
        ], 1]
        inner = [["分析答案", None, ["conversation-id"], None, type_info]]
        chunk = [["wrb.fr", None, json.dumps(inner, ensure_ascii=False)]]
        return ")]}'\n" + json.dumps(chunk, ensure_ascii=False)


class RefreshingAuth:
    def __init__(self) -> None:
        self.refresh_count = 0

    def metadata(self) -> dict[str, Any]:
        return {"csrf_token": "csrf-old", "session_id": "sid-old", "build_label": "build-old"}

    def cookie_header(self, host: str | None = None) -> str:
        return "SID=secret"


class RetryTransport(RPCTransport):
    def __init__(self) -> None:
        self.auth = RefreshingAuth()
        self._lock = __import__("threading").RLock()
        self._reqid = 100000
        self.parse_count = 0
        self.refresh_count = 0

    def refresh_tokens(self) -> dict[str, Any]:
        self.refresh_count += 1
        return {"csrf_token": "csrf-new", "session_id": "sid-new", "build_label": "build-new"}

    def _parse_rpc(self, raw: str, rpc_id: str) -> Any:  # type: ignore[override]
        self.parse_count += 1
        if self.parse_count == 1:
            raise AuthenticationError("NotebookLM 登录已过期，请重新连接 Google 账号")
        return [["Notebook", [], "id-1"]]


class NotebookLMNativeTests(unittest.TestCase):
    def test_streamed_query_retries_a_transient_network_timeout(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"response"
        transport = RPCTransport(RefreshingAuth())  # type: ignore[arg-type]
        transient = urllib.error.URLError(TimeoutError("timed out"))
        with mock.patch(
            "bridge.notebooklm.transport.urllib.request.urlopen",
            side_effect=[transient, response],
        ) as opener, mock.patch("bridge.notebooklm.transport.time.sleep"):
            self.assertEqual(transport.streamed_query([[], "test", None, [2], "id"]), "response")
        self.assertEqual(opener.call_count, 2)

    def test_streamed_query_retries_when_reading_the_response_times_out(self) -> None:
        timed_out_response = mock.MagicMock()
        timed_out_response.__enter__.return_value.read.side_effect = TimeoutError("read timed out")
        successful_response = mock.MagicMock()
        successful_response.__enter__.return_value.read.return_value = b"complete response"
        transport = RPCTransport(RefreshingAuth())  # type: ignore[arg-type]
        with mock.patch(
            "bridge.notebooklm.transport.urllib.request.urlopen",
            side_effect=[timed_out_response, successful_response],
        ) as opener, mock.patch("bridge.notebooklm.transport.time.sleep"):
            self.assertEqual(
                transport.streamed_query([[], "test", None, [2], "id"]),
                "complete response",
            )
        self.assertEqual(opener.call_count, 2)

    def test_upload_retries_a_transient_timeout_without_changing_source_id(self) -> None:
        start_response = mock.MagicMock()
        start_response.__enter__.return_value.headers = {"x-goog-upload-url": "https://upload.example/session"}
        finalize_response = mock.MagicMock()
        transient = urllib.error.URLError(TimeoutError("timed out"))
        transport = RPCTransport(RefreshingAuth())  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "D1.pdf"
            source.write_bytes(b"PDF")
            with mock.patch(
                "bridge.notebooklm.transport.urllib.request.urlopen",
                side_effect=[transient, start_response, finalize_response],
            ) as opener, mock.patch("bridge.notebooklm.transport.time.sleep"):
                transport.upload_file("notebook-id", "source-id", source)
        self.assertEqual(opener.call_count, 3)
        requests = [call.args[0] for call in opener.call_args_list]
        self.assertIn('"SOURCE_ID":"source-id"', requests[0].data.decode("utf-8"))
        self.assertIn('"SOURCE_ID":"source-id"', requests[1].data.decode("utf-8"))

    def test_streamed_query_uses_the_dedicated_free_form_endpoint(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"response"
        transport = RPCTransport(RefreshingAuth())  # type: ignore[arg-type]
        with mock.patch("bridge.notebooklm.transport.urllib.request.urlopen", return_value=response) as opener:
            transport.streamed_query([[], "test", None, [2, None, [1]], "conversation-id"])
        request = opener.call_args.args[0]
        self.assertIn("/GenerateFreeFormStreamed?", request.full_url)

    def test_login_capture_accepts_current_and_legacy_notebook_hosts(self) -> None:
        self.assertTrue(BrowserSessionCapture.is_notebook_url("https://notebook.google.com/notebook/abc"))
        self.assertTrue(BrowserSessionCapture.is_notebook_url("https://notebooklm.google.com/notebook/abc"))
        self.assertFalse(BrowserSessionCapture.is_notebook_url("https://accounts.google.com/signin"))

    def test_cookie_header_selects_the_cookie_for_the_active_notebook_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            auth = AuthManager(Path(temporary))
            auth.save_session([
                {"name": name, "value": name.lower(), "domain": ".google.com", "path": "/"}
                for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")
            ] + [
                {"name": "OSID", "value": "new-host", "domain": "notebook.google.com", "path": "/"},
                {"name": "OSID", "value": "legacy-host", "domain": "notebooklm.google.com", "path": "/"},
            ], {})
            header = auth.cookie_header("notebook.google.com")
            self.assertIn("OSID=new-host", header)
            self.assertNotIn("legacy-host", header)

    def test_rpc_parser_unwraps_chunked_response(self) -> None:
        payload = [["wrb.fr", "wXbhsf", json.dumps([["Notebook", [], "id-1"]])]]
        raw = ")]}'\n123\n" + json.dumps(payload)
        self.assertEqual(RPCTransport._parse_rpc(raw, "wXbhsf"), [["Notebook", [], "id-1"]])

    def test_rpc_error_16_is_mapped_to_expired_login(self) -> None:
        payload = [["wrb.fr", "wXbhsf", None, None, None, [16], "generic"]]
        raw = ")]}'\n" + json.dumps(payload)
        with self.assertRaisesRegex(AuthenticationError, "登录已过期"):
            RPCTransport._parse_rpc(raw, "wXbhsf")

    def test_other_rpc_errors_remain_protocol_errors(self) -> None:
        payload = [["wrb.fr", "wXbhsf", None, None, None, [3], "generic"]]
        raw = ")]}'\n" + json.dumps(payload)
        with self.assertRaisesRegex(ProtocolError, "代码 3"):
            RPCTransport._parse_rpc(raw, "wXbhsf")

    def test_rpc_error_16_refreshes_tokens_once_before_succeeding(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"response"
        transport = RetryTransport()
        with mock.patch("bridge.notebooklm.transport.urllib.request.urlopen", return_value=response):
            result = transport.call("wXbhsf", [None, 1, None, [2]])
        self.assertEqual(result, [["Notebook", [], "id-1"]])
        self.assertEqual(transport.refresh_count, 1)
        self.assertEqual(transport.parse_count, 2)

    def test_client_lists_and_creates_notebooks(self) -> None:
        transport = FakeTransport()
        client = NotebookLMClient(transport)  # type: ignore[arg-type]
        self.assertEqual(len(client.list_notebooks()), 2)
        self.assertEqual(client.create_notebook("新案件")["id"], "new-notebook-id")

    def test_query_uses_all_sources_and_preserves_citations(self) -> None:
        transport = FakeTransport()
        client = NotebookLMClient(transport)  # type: ignore[arg-type]
        result = client.query("notebook-a", "问题")
        self.assertEqual(result["answer"], "分析答案")
        self.assertEqual(result["conversation_id"], "conversation-id")
        self.assertEqual(result["references"][0]["source_id"], "source-a")
        self.assertEqual(transport.query_params[0], [[['source-a']]])

    def test_metadata_extraction_keeps_previous_values(self) -> None:
        html = '<script>{"SNlM0e":"csrf-new","FdrFJe":"sid-new","cfb2h":"build-new"}</script>'
        metadata = extract_page_metadata(html, {"email": "user@example.com"})
        self.assertEqual(metadata["csrf_token"], "csrf-new")
        self.assertEqual(metadata["session_id"], "sid-new")
        self.assertEqual(metadata["email"], "user@example.com")

    def test_auth_status_never_returns_cookie_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            manager = AuthManager(Path(folder))
            cookies = [
                {"name": name, "value": f"secret-{name}", "domain": ".google.com", "path": "/"}
                for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")
            ]
            manager.save_session(cookies, {"email": "user@example.com"})
            rendered = json.dumps(manager.status())
            self.assertNotIn("secret-", rendered)
            self.assertEqual(manager.status()["state"], "authenticated")

    def test_server_rejected_session_stays_expired_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = AuthManager(root)
            cookies = [
                {"name": name, "value": f"secret-{name}", "domain": ".google.com", "path": "/"}
                for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")
            ]
            manager.save_session(cookies, {"email": "user@example.com"})
            manager.invalidate_session("NotebookLM 登录已过期，请重新连接 Google 账号")
            restarted = AuthManager(root)
            self.assertEqual(restarted.status()["state"], "not_authenticated")
            self.assertIn("登录已过期", restarted.status()["message"])

    def test_successful_login_clears_expired_marker(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            manager = AuthManager(Path(folder))
            cookies = [
                {"name": name, "value": f"secret-{name}", "domain": ".google.com", "path": "/"}
                for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")
            ]
            manager.save_session(cookies, {"email": "user@example.com"})
            manager.invalidate_session("expired")
            manager.save_session(cookies, manager.metadata())
            self.assertEqual(manager.status()["state"], "authenticated")


if __name__ == "__main__":
    unittest.main()
