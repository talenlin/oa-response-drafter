"""Source-based packaged entry point used by the Windows launcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OA_YUEDA_PACKAGED", "1")
APP_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("OA_YUEDA_RUNTIME_DIR", str(APP_ROOT / "runtime"))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from bridge import bridge  # noqa: E402


def main() -> None:
    bridge.restore_ocr_jobs()
    bridge.restore_notebook_jobs()
    bridge.restore_issue_analysis_jobs()
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 8787), bridge.WorkbenchHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
