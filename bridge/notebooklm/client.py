"""Domain-neutral NotebookLM operations over the private web protocol."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .errors import ProtocolError, SourceUploadError
from .transport import RPCTransport, _walk


class NotebookLMClient:
    RPC_LIST_NOTEBOOKS = os.environ.get("OA_NLM_RPC_LIST_NOTEBOOKS", "wXbhsf")
    RPC_GET_NOTEBOOK = os.environ.get("OA_NLM_RPC_GET_NOTEBOOK", "rLM1Ne")
    RPC_CREATE_NOTEBOOK = os.environ.get("OA_NLM_RPC_CREATE_NOTEBOOK", "CCqFvf")
    RPC_CONFIGURE_CHAT = os.environ.get("OA_NLM_RPC_CONFIGURE_CHAT", "s0tc2d")
    RPC_ADD_FILE = os.environ.get("OA_NLM_RPC_ADD_FILE", "o4cbdc")
    RPC_GET_CONVERSATIONS = os.environ.get("OA_NLM_RPC_GET_CONVERSATIONS", "khqZz")

    def __init__(self, transport: RPCTransport) -> None:
        self.transport = transport

    def list_notebooks(self, timeout: float = 60.0, retry_network: bool = True) -> list[dict[str, Any]]:
        result = self.transport.call(
            self.RPC_LIST_NOTEBOOKS,
            [None, 1, None, [2]],
            timeout=timeout,
            retry_network=retry_network,
        )
        data = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
        notebooks: list[dict[str, Any]] = []
        if not isinstance(data, list):
            return notebooks
        for item in data:
            if not isinstance(item, list) or len(item) < 3 or not isinstance(item[2], str):
                continue
            sources = item[1] if isinstance(item[1], list) else []
            notebooks.append({"id": item[2], "title": item[0] if isinstance(item[0], str) else "Untitled", "source_count": len(sources)})
        return notebooks

    def get_notebook(self, notebook_id: str) -> Any:
        return self.transport.call(
            self.RPC_GET_NOTEBOOK,
            [notebook_id, None, [2], None, 0],
            path=f"/notebook/{notebook_id}",
        )

    def list_sources(self, notebook_id: str) -> list[dict[str, Any]]:
        result = self.get_notebook(notebook_id)
        notebook = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
        if not isinstance(notebook, list) or len(notebook) < 2 or not isinstance(notebook[1], list):
            return []
        sources: list[dict[str, Any]] = []
        for item in notebook[1]:
            if not isinstance(item, list) or not item or not isinstance(item[0], list) or not item[0]:
                continue
            source_id = item[0][0]
            if isinstance(source_id, str):
                status = item[3][1] if len(item) > 3 and isinstance(item[3], list) and len(item[3]) > 1 else 2
                sources.append({"id": source_id, "title": item[1] if len(item) > 1 else "Untitled", "status": status})
        return sources

    def create_notebook(self, title: str) -> dict[str, Any]:
        params = [title, None, None, [2], [1, None, None, None, None, None, None, None, None, None, [1]]]
        result = self.transport.call(self.RPC_CREATE_NOTEBOOK, params)
        if isinstance(result, list) and len(result) >= 3 and isinstance(result[2], str):
            return {"id": result[2], "title": title}
        raise ProtocolError("NotebookLM 已响应，但未返回新笔记本编号")

    def configure_chat(self, notebook_id: str, prompt: str, response_length: str = "longer") -> None:
        length_code = {"default": 1, "longer": 4, "shorter": 5}.get(response_length, 4)
        params = [notebook_id, [[None, None, None, None, None, None, None, [[2, prompt], [length_code]]]]]
        self.transport.call(self.RPC_CONFIGURE_CHAT, params, path=f"/notebook/{notebook_id}")

    def add_file(self, notebook_id: str, file_path: Path, title: str | None = None, wait: bool = True) -> dict[str, Any]:
        file_path = file_path.resolve()
        if not file_path.is_file() or file_path.stat().st_size == 0:
            raise SourceUploadError(f"无法读取待上传材料：{file_path.name}")
        params = [
            [[title or file_path.name]], notebook_id, [2],
            [1, None, None, None, None, None, None, None, None, None, [1]],
        ]
        result = self.transport.call(self.RPC_ADD_FILE, params, path=f"/notebook/{notebook_id}")
        source_id = self._first_string(result)
        if not source_id:
            raise SourceUploadError(f"NotebookLM 未返回 {file_path.name} 的来源编号")
        self.transport.upload_file(notebook_id, source_id, file_path)
        if wait:
            self.wait_for_source(notebook_id, source_id, timeout=600.0)
        return {"id": source_id, "title": title or file_path.name}

    @staticmethod
    def _first_string(value: Any) -> str | None:
        for item in _walk(value):
            if isinstance(item, str) and item:
                return item
        return None

    def wait_for_source(self, notebook_id: str, source_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = next((item for item in self.list_sources(notebook_id) if item.get("id") == source_id), None)
            if match and match.get("status") == 2:
                return
            if match and match.get("status") == 3:
                raise SourceUploadError(f"NotebookLM 无法解析来源：{match.get('title') or source_id}")
            time.sleep(3.0)
        raise SourceUploadError("NotebookLM 解析材料超时；材料可能仍在后台处理")

    def get_conversation_id(self, notebook_id: str) -> str | None:
        try:
            result = self.transport.call(
                self.RPC_GET_CONVERSATIONS,
                [[], None, notebook_id, 20],
                path=f"/notebook/{notebook_id}",
            )
        except ProtocolError:
            return None
        for item in _walk(result):
            if isinstance(item, str) and len(item) >= 20:
                return item
        return None

    def query(self, notebook_id: str, question: str, conversation_id: str | None = None, timeout: float = 180.0) -> dict[str, Any]:
        sources = self.list_sources(notebook_id)
        source_ids = [str(item["id"]) for item in sources if item.get("id")]
        conversation_id = conversation_id or self.get_conversation_id(notebook_id) or str(uuid.uuid4())
        params = [[[source_id]] for source_id in source_ids]
        raw = self.transport.streamed_query([params, question, None, [2, None, [1]], conversation_id], timeout=timeout)
        answer, citation_data, server_id = self._parse_query(raw)
        return {
            "answer": answer,
            "conversation_id": server_id or conversation_id,
            "sources_used": citation_data.get("sources_used", []),
            "citations": citation_data.get("citations", {}),
            "references": citation_data.get("references", []),
            "turn_number": 1,
            "is_follow_up": False,
        }

    @classmethod
    def _parse_query(cls, raw: str) -> tuple[str, dict[str, Any], str | None]:
        if raw.startswith(")]}'"):
            raw = raw[4:]
        longest_answer = ""
        longest_thinking = ""
        citations: dict[str, Any] = {}
        conversation_id: str | None = None
        errors: list[Any] = []
        lines = raw.strip().splitlines()
        for index, line in enumerate(lines):
            line = line.strip()
            if not line or line.isdigit():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in _walk(chunk):
                if not isinstance(item, list) or len(item) < 3 or item[0] != "wrb.fr":
                    continue
                if item[2] is None:
                    if len(item) > 5:
                        errors.append(item[5])
                    continue
                if not isinstance(item[2], str):
                    continue
                try:
                    inner = json.loads(item[2])
                except json.JSONDecodeError:
                    continue
                if not isinstance(inner, list) or not inner or not isinstance(inner[0], list):
                    continue
                first = inner[0]
                text = first[0] if first and isinstance(first[0], str) else ""
                if len(first) > 2 and isinstance(first[2], list) and first[2] and isinstance(first[2][0], str):
                    conversation_id = first[2][0]
                type_info = first[4] if len(first) > 4 and isinstance(first[4], list) else []
                is_answer = bool(type_info and isinstance(type_info[-1], int) and type_info[-1] == 1)
                if is_answer and len(text) > len(longest_answer):
                    longest_answer = text
                    citations = cls._citation_data(type_info)
                elif not is_answer and len(text) > len(longest_thinking):
                    longest_thinking = text
        answer = longest_answer or longest_thinking
        if not answer:
            raise ProtocolError(f"NotebookLM 未返回可用答案{f'（错误 {errors[0]}）' if errors else ''}")
        return answer, citations, conversation_id

    @classmethod
    def _citation_data(cls, type_info: list[Any]) -> dict[str, Any]:
        if len(type_info) < 4 or not isinstance(type_info[3], list):
            return {}
        mapping: dict[int, str] = {}
        references: list[dict[str, Any]] = []
        sources: list[str] = []
        for number, passage in enumerate(type_info[3], start=1):
            try:
                detail = passage[1]
                source_id = detail[5][0][0][0]
            except (IndexError, TypeError):
                continue
            if not isinstance(source_id, str):
                continue
            mapping[number] = source_id
            if source_id not in sources:
                sources.append(source_id)
            reference: dict[str, Any] = {"source_id": source_id, "citation_number": number}
            text = cls._cited_text(detail)
            if text:
                reference["cited_text"] = text
            references.append(reference)
        return {"sources_used": sources, "citations": mapping, "references": references} if mapping else {}

    @staticmethod
    def _cited_text(detail: list[Any]) -> str | None:
        if len(detail) <= 4 or not isinstance(detail[4], list):
            return None
        values: list[str] = []
        for element in detail[4]:
            if not isinstance(element, list) or not element:
                continue
            segments = [element] if isinstance(element[0], (int, float)) else element
            for segment in segments:
                if not isinstance(segment, list) or len(segment) < 3 or not isinstance(segment[2], list):
                    continue
                for nested_group in segment[2]:
                    if not isinstance(nested_group, list):
                        continue
                    for inner in nested_group:
                        if isinstance(inner, list) and len(inner) >= 3:
                            value = inner[2]
                            if isinstance(value, str) and value.strip():
                                values.append(value.strip())
                            elif isinstance(value, list):
                                values.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
        return " ".join(values) or None
