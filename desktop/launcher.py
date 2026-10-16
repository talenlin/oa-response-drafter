"""Windows desktop launcher for OA阅答.

The packaged app stays entirely local: it starts the bundled bridge and opens
the workbench in the user's default browser. Google sign-in remains a direct,
user-initiated action in OA阅答's isolated local browser profile.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from http.client import HTTPConnection

from bridge import bridge


# A full distributable can place the LibreOffice conversion runtime beside the
# bundled bridge.  The OCR worker inherits this location; development keeps the
# installed LibreOffice / Word fallback behaviour.
os.environ.setdefault("OA_YUEDA_RUNTIME_DIR", str(bridge.ROOT / "runtime"))


HOST = "127.0.0.1"
PORT = 8787
URL = f"http://{HOST}:{PORT}/"


def bridge_is_already_running() -> bool:
    try:
        connection = HTTPConnection(HOST, PORT, timeout=0.6)
        connection.request("GET", "/api/health")
        return connection.getresponse().status == 200
    except OSError:
        return False


def open_workbench() -> None:
    time.sleep(0.35)
    webbrowser.open(URL, new=1)


def main() -> None:
    if bridge_is_already_running():
        webbrowser.open(URL, new=1)
        return
    bridge.restore_ocr_jobs()
    bridge.restore_notebook_jobs()
    bridge.restore_issue_analysis_jobs()
    server = bridge.ThreadingHTTPServer((HOST, PORT), bridge.WorkbenchHandler)
    threading.Thread(target=open_workbench, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
