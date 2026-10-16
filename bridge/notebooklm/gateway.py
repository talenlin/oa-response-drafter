"""Deep interface consumed by OA workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .auth import AuthManager
from .client import NotebookLMClient
from .errors import AuthenticationError, NotebookLMError
from .transport import RPCTransport


class NotebookLMGateway:
    """One cohesive in-process bridge; private protocol details stay hidden."""

    def __init__(self, data_root: Path, profile: str = "default") -> None:
        self.auth = AuthManager(data_root, profile)
        self.client = NotebookLMClient(RPCTransport(self.auth))

    def status(self, *, live: bool = False) -> dict[str, Any]:
        status = self.auth.status()
        if live and status.get("state") == "authenticated":
            try:
                self.client.list_notebooks(timeout=8.0, retry_network=False)
                status["live"] = True
                status["stage"] = "verified"
                status["message"] = "NotebookLM 已在线验证"
            except AuthenticationError as error:
                self.auth.invalidate_session(str(error))
                status.update({"state": "not_authenticated", "live": False, "stage": "expired", "message": "NotebookLM 登录已失效，请重新登录"})
            except (NotebookLMError, OSError, TimeoutError) as error:
                status.update({"state": "unavailable", "live": False, "stage": "unavailable", "message": f"暂时无法向 NotebookLM 验证登录：{str(error)[:300]}"})
        return status

    def begin_login(self) -> dict[str, Any]:
        return self.auth.begin_login()

    def list_notebooks(self, *, timeout: float = 60.0, retry_network: bool = True) -> list[dict[str, Any]]:
        return self.client.list_notebooks(timeout=timeout, retry_network=retry_network)

    def create_notebook(self, title: str) -> dict[str, Any]:
        return self.client.create_notebook(title)

    def add_source(self, notebook_id: str, path: Path, title: str) -> dict[str, Any]:
        return self.client.add_file(notebook_id, path, title=title, wait=True)

    def list_sources(self, notebook_id: str) -> list[dict[str, Any]]:
        return self.client.list_sources(notebook_id)

    def configure_chat(self, notebook_id: str, prompt: str) -> None:
        self.client.configure_chat(notebook_id, prompt, response_length="longer")

    def query(self, notebook_id: str, prompt: str, *, conversation_id: str | None = None, timeout: float = 180.0) -> dict[str, Any]:
        return self.client.query(notebook_id, prompt, conversation_id=conversation_id, timeout=timeout)
