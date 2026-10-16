import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from bridge import bridge


class _HandlerProbe:
    def __init__(self) -> None:
        self.response = None

    def respond_json(self, status, payload) -> None:
        self.response = (status, payload)


class OcrSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_setup = dict(bridge.OCR_SETUP)

    def tearDown(self) -> None:
        bridge.OCR_SETUP.clear()
        bridge.OCR_SETUP.update(self.original_setup)

    def test_setup_endpoint_installs_missing_ocr_instead_of_returning_a_shell_command(self) -> None:
        status = {
            "state": "not_configured",
            "engines": {"PaddleOCR": False},
            "setup": {"state": "idle"},
        }
        probe = _HandlerProbe()
        bridge.OCR_SETUP.clear()
        bridge.OCR_SETUP.update({"state": "idle", "error": None})

        with (
            patch.object(bridge, "local_ocr_status", return_value=status),
            patch.object(bridge.threading, "Thread") as thread,
        ):
            bridge.WorkbenchHandler.start_ocr_setup(probe)

        self.assertEqual(probe.response[0], HTTPStatus.ACCEPTED)
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    def test_running_setup_status_does_not_spawn_repeated_python_probes(self) -> None:
        bridge.OCR_SETUP.clear()
        bridge.OCR_SETUP.update({
            "state": "running",
            "stage": "installing_paddlepaddle",
            "progress": 20,
            "message": "Installing PaddlePaddle",
            "error": None,
        })

        with (
            patch.object(bridge, "OCR_PYTHON", Path(__file__)),
            patch.object(bridge.subprocess, "run") as run,
        ):
            status = bridge.local_ocr_status()

        run.assert_not_called()
        self.assertEqual(status["setup"]["state"], "running")

    def test_windows_subprocesses_request_hidden_windows(self) -> None:
        with (
            patch.object(bridge.os, "name", "nt"),
            patch.object(bridge.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            options = bridge.hidden_subprocess_options()

        self.assertEqual(options, {"creationflags": 0x08000000})


if __name__ == "__main__":
    unittest.main()
