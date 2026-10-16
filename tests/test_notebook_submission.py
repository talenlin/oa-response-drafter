from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from bridge import bridge
from bridge.notebooklm.errors import SourceUploadError


class NotebookSubmissionTests(unittest.TestCase):
    def test_four_file_submission_retries_only_the_interrupted_fourth_upload(self) -> None:
        sources = [
            {"path": str(Path("material") / f"source-{index}.pdf"), "name": f"source-{index}.pdf", "role": "priorArt"}
            for index in range(1, 5)
        ]
        job = {
            "title": "test-OA策略分析",
            "sourceCount": 4,
            "sourceCatalog": {},
            "uploadedSources": 0,
        }
        added = [
            {"id": "source-id-1"},
            {"id": "source-id-2"},
            {"id": "source-id-3"},
            SourceUploadError("上传 source-4.pdf 失败：连接超时"),
            {"id": "source-id-4"},
        ]

        with (
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "create_notebook", return_value={"id": "notebook-id"}),
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "add_source", side_effect=added) as add_source,
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "list_sources", return_value=[]),
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "configure_chat"),
            mock.patch.object(bridge, "run_native_notebook_query", return_value={"answer": "[]"}),
            mock.patch.object(bridge, "save_notebook_job"),
            mock.patch.object(bridge.time, "sleep"),
        ):
            bridge.run_notebook_submission(job, sources, "default")

        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["uploadedSources"], 4)
        self.assertEqual(len(job["sourceCatalog"]), 4)
        self.assertEqual(add_source.call_count, 5)
        attempted_names = [call.args[2] for call in add_source.call_args_list]
        self.assertEqual(attempted_names, [
            "source-1.pdf", "source-2.pdf", "source-3.pdf", "source-4.pdf", "source-4.pdf",
        ])

    def test_interrupted_upload_is_reconciled_before_creating_a_duplicate_source(self) -> None:
        sources = [
            {"path": str(Path("material") / "source-4.pdf"), "name": "source-4.pdf", "role": "priorArt"},
        ]
        job = {
            "title": "test-OA策略分析",
            "sourceCount": 1,
            "sourceCatalog": {},
            "uploadedSources": 0,
        }

        with (
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "create_notebook", return_value={"id": "notebook-id"}),
            mock.patch.object(
                bridge.NOTEBOOK_GATEWAY,
                "add_source",
                side_effect=SourceUploadError("上传 source-4.pdf 失败：连接超时"),
            ) as add_source,
            mock.patch.object(
                bridge.NOTEBOOK_GATEWAY,
                "list_sources",
                return_value=[{"id": "source-id-4", "title": "source-4.pdf", "status": 2}],
            ),
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "configure_chat"),
            mock.patch.object(bridge, "run_native_notebook_query", return_value={"answer": "[]"}),
            mock.patch.object(bridge, "save_notebook_job"),
            mock.patch.object(bridge.time, "sleep"),
        ):
            bridge.run_notebook_submission(job, sources, "default")

        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["uploadedSources"], 1)
        self.assertIn("source-id-4", job["sourceCatalog"])
        self.assertEqual(add_source.call_count, 1)

    def test_exhausted_current_file_retries_preserve_the_previous_checkpoint(self) -> None:
        sources = [
            {"path": str(Path("material") / f"source-{index}.pdf"), "name": f"source-{index}.pdf", "role": "priorArt"}
            for index in range(1, 5)
        ]
        job = {
            "title": "test-OA策略分析",
            "sourceCount": 4,
            "sourceCatalog": {},
            "uploadedSources": 0,
        }
        failures = [
            {"id": "source-id-1"},
            {"id": "source-id-2"},
            {"id": "source-id-3"},
            SourceUploadError("上传 source-4.pdf 失败（HTTP 400）"),
            SourceUploadError("上传 source-4.pdf 失败（HTTP 400）"),
            SourceUploadError("上传 source-4.pdf 失败（HTTP 400）"),
        ]

        with (
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "create_notebook", return_value={"id": "notebook-id"}),
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "add_source", side_effect=failures) as add_source,
            mock.patch.object(bridge.NOTEBOOK_GATEWAY, "list_sources", return_value=[]),
            mock.patch.object(bridge, "save_notebook_job"),
            mock.patch.object(bridge.time, "sleep"),
        ):
            bridge.run_notebook_submission(job, sources, "default")

        self.assertEqual(job["status"], "error")
        self.assertEqual(job["uploadedSources"], 3)
        self.assertEqual(len(job["sourceCatalog"]), 3)
        self.assertEqual(add_source.call_count, 6)
        self.assertIn("已自动重试 3 次", job["error"])


if __name__ == "__main__":
    unittest.main()
