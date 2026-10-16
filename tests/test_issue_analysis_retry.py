from __future__ import annotations

import unittest
from unittest import mock

from bridge import bridge
from bridge.notebooklm.errors import ProtocolError


class IssueAnalysisRetryTests(unittest.TestCase):
    def _job(self) -> dict[str, object]:
        return {
            "id": "issue-retry-test",
            "notebookId": "notebook-id",
            "sourceCatalog": {"source-1": {"title": "OA.pdf"}},
            "issue": {
                "title": "实质性问题（新颖性／创造性）",
                "ground": "新颖性；创造性",
                "legalBasis": "中国：专利法第二十二条",
                "claims": "权利要求1",
                "priorArt": ["D1"],
                "summary": "审查员认为不具备新颖性。",
                "analysisFocus": "核对技术场景差异。",
            },
            "analysis": None,
            "followUps": [],
            "favorite": False,
        }

    def test_transient_disconnect_during_first_output_retries_and_keeps_analysis_running(self) -> None:
        job = self._job()
        responses = [
            ProtocolError("NotebookLM 连接暂时中断，已自动重试仍未恢复；请稍后重试，本地任务进度会保留。"),
            '{"answer":"审查意见逐项提取"}',
            '{"answer":"答复策略分析"}',
        ]

        with (
            mock.patch.object(bridge, "run_native_notebook_query", side_effect=responses) as query,
            mock.patch.object(bridge, "save_issue_analysis_job"),
            mock.patch.object(bridge.time, "sleep"),
        ):
            bridge.run_issue_analysis(job, "default")

        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["analysis"], '{"answer":"答复策略分析"}')
        self.assertEqual(query.call_count, 3)

    def test_transient_disconnect_during_strategy_output_retries_without_restarting_extraction(self) -> None:
        job = self._job()
        responses = [
            '{"answer":"审查意见逐项提取"}',
            ProtocolError("NotebookLM 连接暂时中断，已自动重试仍未恢复；请稍后重试，本地任务进度会保留。"),
            '{"answer":"答复策略分析"}',
        ]

        with (
            mock.patch.object(bridge, "run_native_notebook_query", side_effect=responses) as query,
            mock.patch.object(bridge, "save_issue_analysis_job"),
            mock.patch.object(bridge.time, "sleep"),
        ):
            bridge.run_issue_analysis(job, "default")

        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["examinerIssueExtraction"], '{"answer":"审查意见逐项提取"}')
        self.assertEqual(job["analysis"], '{"answer":"答复策略分析"}')
        self.assertEqual(query.call_count, 3)


if __name__ == "__main__":
    unittest.main()
