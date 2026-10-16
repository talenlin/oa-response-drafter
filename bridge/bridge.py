"""Local bridge for the Patent OA Workbench MVP.

The bridge deliberately owns only local work: it serves the workbench during
development, exposes its health state, and queues confirmed issue-card jobs.
It does not collect Google cookies or send files to third parties.
"""

from __future__ import annotations

import json
import importlib.util
import base64
import difflib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from notebooklm import AuthenticationError, NotebookLMError, NotebookLMGateway, ProtocolError
except ModuleNotFoundError:  # Supports both `bridge.py` execution and package imports in tests.
    from bridge.notebooklm import AuthenticationError, NotebookLMError, NotebookLMGateway, ProtocolError


APP_NAME = "OA阅答"
APP_VERSION = "1.0.1"
FROZEN_APP = bool(getattr(sys, "frozen", False))
PACKAGED_APP = FROZEN_APP or os.environ.get("OA_YUEDA_PACKAGED") == "1"
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) if FROZEN_APP else Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"
DEFAULT_OUTPUT_ROOT = (
    Path(os.environ.get("OA_YUEDA_DATA_ROOT") or Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME)
    if PACKAGED_APP
    else ROOT / "output"
)
SETTINGS_PATH = DEFAULT_OUTPUT_ROOT / "workbench-settings.json"
AUDIT_EVENTS_PATH = DEFAULT_OUTPUT_ROOT / "notebook-audit-events.jsonl"
AUDIT_SETTINGS_PATH = DEFAULT_OUTPUT_ROOT / "notebook-audit-settings.json"
AUDIT_SYNC_LOCK = threading.Lock()
AUDIT_SYNC: dict[str, Any] = {"state": "idle", "notebookCount": None, "error": None, "syncedAt": None}
AUDIT_PLAN_LIMITS = {
    "free": {"label": "免费 / Standard", "notebooks": 100, "dailyChats": 50, "sourcesPerNotebook": 50},
    "pro": {"label": "Pro", "notebooks": 500, "dailyChats": 500, "sourcesPerNotebook": 300},
}


def configured_archive_root() -> Path:
    try:
        configured = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("archiveRoot")
    except (OSError, json.JSONDecodeError, AttributeError):
        configured = None
    if not isinstance(configured, str) or not configured.strip():
        return DEFAULT_OUTPUT_ROOT
    try:
        candidate = Path(configured).expanduser().resolve()
    except OSError:
        return DEFAULT_OUTPUT_ROOT
    return candidate if candidate != candidate.parent else DEFAULT_OUTPUT_ROOT


def configure_archive_root(path_value: str) -> Path:
    """Persist and activate a user-selected root for newly created local records."""
    candidate = Path(path_value.strip()).expanduser().resolve()
    if not path_value.strip() or candidate == candidate.parent:
        raise ValueError("Please choose a specific archive folder")
    candidate.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps({"archiveRoot": str(candidate)}, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_archive_root(candidate)
    return candidate


def apply_archive_root(archive_root: Path) -> None:
    global ARCHIVE_OUTPUT_ROOT, OCR_JOB_ROOT, NOTEBOOK_JOB_ROOT, ISSUE_ANALYSIS_JOB_ROOT, FAVORITES_ROOT, EXPORT_ROOT, ROUND_ARCHIVE_ROOT
    ARCHIVE_OUTPUT_ROOT = archive_root
    OCR_JOB_ROOT = archive_root / "ocr-jobs"
    NOTEBOOK_JOB_ROOT = archive_root / "notebook-jobs"
    ISSUE_ANALYSIS_JOB_ROOT = archive_root / "issue-analysis-jobs"
    FAVORITES_ROOT = archive_root / "favorites"
    EXPORT_ROOT = archive_root / "exports"
    ROUND_ARCHIVE_ROOT = archive_root / "round-archives"


ARCHIVE_OUTPUT_ROOT = configured_archive_root()
NOTEBOOK_GATEWAY = NotebookLMGateway(DEFAULT_OUTPUT_ROOT)
JOBS: deque[dict[str, Any]] = deque()
AUTH_CACHE: dict[str, Any] | None = None
AUTH_CACHE_AT = 0.0
AUTH_CACHE_TTL_SECONDS = 45.0
AUTH_STATUS_PATH = DEFAULT_OUTPUT_ROOT / "notebook-auth-status.json"
AUTH_REFRESH_LOCK = threading.Lock()
AUTH_REFRESH_IN_FLIGHT = False
AUTH_LOGIN_WATCH_LOCK = threading.Lock()
AUTH_LOGIN_WATCH_IN_FLIGHT = False
OCR_JOBS: dict[str, dict[str, Any]] = {}
OCR_JOB_ROOT = ARCHIVE_OUTPUT_ROOT / "ocr-jobs"
OCR_VENV_ROOT = (DEFAULT_OUTPUT_ROOT if PACKAGED_APP else ROOT) / ".venv-ocr"
OCR_PYTHON = OCR_VENV_ROOT / "Scripts" / "python.exe"
RUNTIME_PYTHON = ROOT / "runtime" / "python" / "python.exe"
OCR_SETUP: dict[str, Any] = {"state": "idle", "stage": None, "progress": 0, "message": None, "error": None}
OCR_SETUP_LOCK = threading.Lock()
NOTEBOOK_JOBS: dict[str, dict[str, Any]] = {}
NOTEBOOK_JOB_ROOT = ARCHIVE_OUTPUT_ROOT / "notebook-jobs"
NOTEBOOK_JOBS_LOCK = threading.Lock()
ISSUE_ANALYSIS_JOBS: dict[str, dict[str, Any]] = {}
ISSUE_ANALYSIS_JOB_ROOT = ARCHIVE_OUTPUT_ROOT / "issue-analysis-jobs"
FAVORITES_ROOT = ARCHIVE_OUTPUT_ROOT / "favorites"
EXPORT_ROOT = ARCHIVE_OUTPUT_ROOT / "exports"
ROUND_ARCHIVE_ROOT = ARCHIVE_OUTPUT_ROOT / "round-archives"


def archive_data_roots(folder_name: str) -> list[Path]:
    """Include the default archive as a read-only legacy source after relocation."""
    roots = [ARCHIVE_OUTPUT_ROOT / folder_name]
    legacy_root = DEFAULT_OUTPUT_ROOT / folder_name
    if legacy_root not in roots:
        roots.append(legacy_root)
    return roots

ISSUE_LIST_PROMPT = (
    "请只返回一个合法 JSON 对象，不要 Markdown，不要代码围栏。格式必须为："
    '{"issues":[{"id":"I-01","title":"新颖性问题","ground":"新颖性",'
    '"legal_basis":"中国：专利法第二十二条第二款；美国：35 U.S.C. 102（仅写 OA 实际适用者）",'
    '"claim_or_requirement":"涉及的全部权利要求或审查事项，例如：权利要求1、4、5",'
    '"prior_art":["D1"],"summary":"该类别下审查员理由的合并摘要",'
    '"evidence":[{"source":"来源名称","location":"页码或段落","excerpt":"不超过80字"}],'
    '"analysis_focus":"后续答复应核对的具体技术场景或差异"}]}. '
    "按审查理由/法律依据归类，绝不能按单个权利要求拆分。每个类别只生成一项，并把该类别涵盖的全部权利要求、"
    "对比文件和审查理由合并在同一项中。仅列 OA 实际出现的类别："
    "（1）新颖性：中国专利法第二十二条第二款 / 美国 35 U.S.C. 102；"
    "（2）创造性：中国专利法第二十二条第三款 / 美国 35 U.S.C. 103；"
    "（3）清楚性或说明书支持问题：中国专利法第二十六条第三款、第四款 / 美国 35 U.S.C. 112；"
    "（4）附图问题：美国 37 CFR 1.81、37 CFR 1.83（或 OA 实际引用的对应规则）；"
    "（5）单一性：中国专利法第三十一条第一款 / 美国 35 U.S.C. 121（限制要求；以 OA 实际引用为准）；"
    "（6）专利客体：中国专利法第二条第二款、第二十五条或第五条第一款 / 美国 35 U.S.C. 101；"
    "（7）防止重复授权：中国专利法第九条 / 美国重复授权（法定或显而易见型；以 OA 实际引用为准）；"
    "（8）修改超范围：中国专利法第三十三条 / 美国 35 U.S.C. 132 New Matter。"
    "不要把中国和美国法条混在同一项；根据本 OA 的法域填写 legal_basis。不要起草完整答复。"
)


def restore_ocr_jobs() -> None:
    """Retain completed local material sets across a bridge restart."""
    for root in archive_data_roots("ocr-jobs"):
        if not root.exists():
            continue
        for folder in root.glob("ocr-*"):
            manifest = folder / "manifest.json"
            result = folder / "result.json"
            if not manifest.exists() or not result.exists():
                continue
            try:
                state = json.loads(result.read_text(encoding="utf-8")).get("state", "complete")
            except json.JSONDecodeError:
                continue
            OCR_JOBS[folder.name] = {
                "id": folder.name,
                "state": state,
                "result": result,
                "manifest": manifest,
                "error": None,
            }


def save_notebook_job(job: dict[str, Any]) -> None:
    NOTEBOOK_JOB_ROOT.mkdir(parents=True, exist_ok=True)
    (NOTEBOOK_JOB_ROOT / f"{job['id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def save_issue_analysis_job(job: dict[str, Any]) -> None:
    ISSUE_ANALYSIS_JOB_ROOT.mkdir(parents=True, exist_ok=True)
    (ISSUE_ANALYSIS_JOB_ROOT / f"{job['id']}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def known_round_archive_roots() -> list[Path]:
    """Keep the original archive visible after a user chooses a new location."""
    roots = [ROUND_ARCHIVE_ROOT]
    legacy_root = DEFAULT_OUTPUT_ROOT / "round-archives"
    if legacy_root not in roots:
        roots.append(legacy_root)
    return roots


def case_path(case_id: str, archive_root: Path | None = None) -> Path:
    return (archive_root or ROUND_ARCHIVE_ROOT) / f"{case_id}.json"


def normalize_case_record(record: dict[str, Any]) -> dict[str, Any]:
    """Read legacy one-round archives as editable cases without losing them."""
    if isinstance(record.get("rounds"), list):
        return record
    round_number = int(record.get("roundNumber") or 1)
    return {
        "id": record.get("id"),
        "kind": "case",
        "createdAt": record.get("archivedAt") or now(),
        "updatedAt": record.get("archivedAt") or now(),
        "applicationNumber": record.get("applicationNumber"),
        "activeRoundNumber": round_number,
        "rounds": [{
            "number": round_number,
            "createdAt": record.get("archivedAt") or now(),
            "updatedAt": record.get("archivedAt") or now(),
            "ocrJobId": record.get("ocrJobId"),
            "notebookId": record.get("notebookId"),
            "issueAnalysisIds": record.get("issueAnalysisIds", []),
        }],
    }


def read_case(case_id: str) -> dict[str, Any] | None:
    for root in known_round_archive_roots():
        path = case_path(case_id, root)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        normalized = normalize_case_record(record)
        normalized["_archiveRoot"] = str(root)
        return normalized
    return None


def save_case(record: dict[str, Any]) -> None:
    source_root = record.get("_archiveRoot")
    archive_root = Path(source_root) if isinstance(source_root, str) and source_root else ROUND_ARCHIVE_ROOT
    archive_root.mkdir(parents=True, exist_ok=True)
    record["updatedAt"] = now()
    payload = {key: value for key, value in record.items() if key != "_archiveRoot"}
    case_path(str(record["id"]), archive_root).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def case_round(record: dict[str, Any], number: int) -> dict[str, Any] | None:
    return next((entry for entry in record.get("rounds", []) if int(entry.get("number") or 0) == number), None)


def case_summary(record: dict[str, Any]) -> dict[str, Any]:
    rounds = record.get("rounds", [])
    active_number = int(record.get("activeRoundNumber") or len(rounds) or 1)
    active = case_round(record, active_number) or (rounds[-1] if rounds else {})
    material_rounds = [entry for entry in rounds if entry.get("ocrJobId")]
    editable_number = max((int(entry.get("number") or 0) for entry in material_rounds), default=active_number)
    answer_count = sum(len(entry.get("issueAnalysisIds") or []) for entry in rounds)
    return {
        "id": record.get("id"),
        "applicationNumber": record.get("applicationNumber"),
        "createdAt": record.get("createdAt"),
        "updatedAt": record.get("updatedAt"),
        "activeRoundNumber": active_number,
        "editableRoundNumber": editable_number,
        "roundCount": len(rounds),
        "answerCount": answer_count,
        "notebookId": active.get("notebookId"),
        "ocrJobId": active.get("ocrJobId"),
    }


def material_snapshot_for_round(round_record: dict[str, Any]) -> dict[str, Any]:
    """Return display-only local material metadata for a preserved OA round."""
    ocr_job_id = str(round_record.get("ocrJobId") or "")
    ocr_job = OCR_JOBS.get(ocr_job_id)
    if ocr_job is None:
        return {
            "roundNumber": int(round_record.get("number") or 0),
            "materials": [],
            "available": False,
        }
    try:
        manifest = json.loads(ocr_job["manifest"].read_text(encoding="utf-8"))
        result = read_ocr_job(ocr_job)
    except (OSError, json.JSONDecodeError, KeyError):
        return {
            "roundNumber": int(round_record.get("number") or 0),
            "materials": [],
            "available": False,
        }
    result_by_id = {str(item.get("id")): item for item in result.get("files", [])}
    materials = []
    for item in manifest.get("files", []):
        item_result = result_by_id.get(str(item.get("id")), {})
        materials.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "kind": item.get("kind", "priorArt"),
            "state": item_result.get("state", result.get("state")),
            "method": item_result.get("method"),
            "pages": item_result.get("pages"),
        })
    return {
        "roundNumber": int(round_record.get("number") or 0),
        "materials": materials,
        "available": True,
    }


def prior_material_snapshots(record: dict[str, Any], current_round_number: int) -> list[dict[str, Any]]:
    return [
        material_snapshot_for_round(entry)
        for entry in record.get("rounds", [])
        if int(entry.get("number") or 0) < current_round_number and entry.get("ocrJobId")
    ]


def archived_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for root in known_round_archive_roots():
        if not root.exists():
            continue
        for path in root.glob("round-*.json"):
            if path.stem in seen_ids:
                continue
            record = read_case(path.stem)
            if record is not None:
                cases.append(case_summary(record))
                seen_ids.add(path.stem)
    return sorted(cases, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)


def update_case_round(case_id: str, number: int, **values: Any) -> None:
    record = read_case(case_id)
    if record is None:
        return
    round_record = case_round(record, number)
    if round_record is None:
        return
    round_record.update({key: value for key, value in values.items() if value is not None})
    round_record["updatedAt"] = now()
    if values.get("applicationNumber") and not record.get("applicationNumber"):
        record["applicationNumber"] = values["applicationNumber"]
    save_case(record)


def display_notebook_text(value: Any) -> str:
    """Unwrap the CLI JSON envelope while preserving ordinary Notebook text."""
    result = value
    for _ in range(3):
        if not isinstance(result, str):
            break
        try:
            decoded = json.loads(result)
        except json.JSONDecodeError:
            break
        result = decoded.get("answer", decoded) if isinstance(decoded, dict) else decoded
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)


def export_issue_analysis_docx(job: dict[str, Any]) -> Path:
    """Create a portable local record without sending its content anywhere."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    path = EXPORT_ROOT / f"{job['id']}-analysis.docx"
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.2

    title = document.add_heading("专利 OA 分项分析", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.runs[0].font.color.rgb = RGBColor(25, 77, 70)
    document.add_paragraph(f"审查类别：{job.get('issue', {}).get('title', '审查问题')}")
    document.add_paragraph(f"生成时间：{job.get('completedAt') or job.get('createdAt') or now()}")
    document.add_paragraph("本文件仅供专业人员复核，不构成法律意见。")
    extraction = display_notebook_text(job.get("examinerIssueExtraction") or "").strip()
    if extraction:
        document.add_heading("第一次输出：审查意见问题逐项提取", level=1)
        document.add_paragraph("本节仅列明审查意见、依据和待核验位置；后续策略分析不重复该内容。")
        for line in extraction.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("### "):
                document.add_heading(stripped[4:], level=2)
            elif stripped.startswith("## "):
                document.add_heading(stripped[3:], level=1)
            elif stripped.startswith("# "):
                document.add_heading(stripped[2:], level=1)
            else:
                document.add_paragraph(stripped)
    document.add_heading("第二次输出：答复策略分析", level=1)
    for line in display_notebook_text(job.get("analysis") or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            document.add_heading(stripped[4:], level=2)
        elif stripped.startswith("## "):
            document.add_heading(stripped[3:], level=1)
        elif stripped.startswith("# "):
            document.add_heading(stripped[2:], level=1)
        else:
            document.add_paragraph(stripped)
    follow_ups = job.get("followUps") or []
    if follow_ups:
        document.add_heading("追问记录", level=1)
        for index, entry in enumerate(follow_ups, start=1):
            document.add_heading(f"追问 {index}", level=2)
            document.add_paragraph(f"问题：{entry.get('question', '')}")
            document.add_paragraph(f"回答：{display_notebook_text(entry.get('answer') or '')}")
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("本地工作台导出 · 请结合原始材料复核")
    document.save(path)
    return path


def restore_notebook_jobs() -> None:
    for root in archive_data_roots("notebook-jobs"):
        if not root.exists():
            continue
        for path in root.glob("notebook-*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(job, dict) and isinstance(job.get("id"), str):
                if not job.get("applicationNumber"):
                    ocr_job = OCR_JOBS.get(str(job.get("ocrJobId") or ""))
                    manifest = ocr_job.get("manifest") if ocr_job else None
                    if isinstance(manifest, Path) and manifest.exists():
                        try:
                            sources = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
                            application_number = patent_application_number(sources, read_ocr_job(ocr_job)) if isinstance(sources, list) else None
                        except (OSError, json.JSONDecodeError):
                            application_number = None
                        if application_number:
                            job["applicationNumber"] = application_number
                            save_notebook_job(job)
                NOTEBOOK_JOBS[job["id"]] = job


def restore_issue_analysis_jobs() -> None:
    for root in archive_data_roots("issue-analysis-jobs"):
        if not root.exists():
            continue
        for path in root.glob("issue-*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(job, dict) and isinstance(job.get("id"), str):
                ISSUE_ANALYSIS_JOBS[job["id"]] = job


def latest_notebook_job() -> dict[str, Any] | None:
    if not NOTEBOOK_JOBS:
        return None
    return max(NOTEBOOK_JOBS.values(), key=lambda job: str(job.get("completedAt") or job.get("createdAt") or ""))


def existing_notebook_job_for_ocr(ocr_job_id: str) -> dict[str, Any] | None:
    """Return the current result/task for one material batch.

    A material batch represents one authorised NotebookLM submission.  Reusing an
    existing task makes a retry safe if the browser is slow or the user clicks
    twice, and prevents duplicate Notebooks/sources from being created.
    """
    candidates = [job for job in NOTEBOOK_JOBS.values() if job.get("ocrJobId") == ocr_job_id]
    completed = [job for job in candidates if job.get("status") == "complete"]
    if completed:
        return max(completed, key=lambda job: str(job.get("completedAt") or job.get("createdAt") or ""))
    active_states = {"queued", "creating_notebook", "uploading_sources", "configuring_analysis", "generating_issue_list"}
    active = [job for job in candidates if job.get("status") in active_states]
    if active:
        return max(active, key=lambda job: str(job.get("createdAt") or ""))
    return None


def failed_notebook_job_for_ocr(ocr_job_id: str) -> dict[str, Any] | None:
    """Return the newest resumable failed submission for one material batch."""
    candidates = [
        job for job in NOTEBOOK_JOBS.values()
        if job.get("ocrJobId") == ocr_job_id
        and job.get("status") == "error"
        and job.get("notebookId")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda job: str(job.get("completedAt") or job.get("createdAt") or ""))


def hidden_subprocess_options() -> dict[str, int]:
    """Keep console-based helpers invisible when OA阅答 runs as a GUI app."""
    if os.name != "nt":
        return {}
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0))}


def local_ocr_status() -> dict[str, Any]:
    """Report only locally available document/OCR tooling; never upload files."""
    setup = dict(OCR_SETUP)
    paddle_available = False
    # The UI polls this endpoint while installation is running. Starting a new
    # Python interpreter on every poll both delays progress reporting and, in a
    # Windows GUI build, flashes a console window every 1.2 seconds.
    if setup.get("state") == "ready":
        paddle_available = OCR_PYTHON.exists()
    elif setup.get("state") not in {"queued", "running"} and OCR_PYTHON.exists():
        try:
            paddle_available = subprocess.run(
                [str(OCR_PYTHON), "-c", "import paddle, paddleocr"],
                capture_output=True,
                timeout=15,
                check=False,
                **hidden_subprocess_options(),
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            paddle_available = False
    engines = {
        "PaddleOCR": paddle_available,
        "Docling": importlib.util.find_spec("docling") is not None,
        "MinerU": importlib.util.find_spec("magic_pdf") is not None,
    }
    active = [name for name, available in engines.items() if available]
    text_extractor = (shutil.which("pdftotext.exe") or shutil.which("pdftotext")) is not None
    setup_result = OCR_JOB_ROOT / "ocr-setup.json"
    if setup["state"] == "idle" and paddle_available and setup_result.exists():
        try:
            persisted = json.loads(setup_result.read_text(encoding="utf-8"))
            if persisted.get("state") == "complete" and persisted.get("modelState") == "ready":
                setup = {
                    "state": "ready",
                    "stage": "complete",
                    "progress": 100,
                    "message": "OCR 引擎和中文模型已就绪",
                    "error": None,
                    "completedAt": "previous local setup",
                }
        except json.JSONDecodeError:
            pass
    return {
        "state": "available" if active else "not_configured",
        "engines": engines,
        "activeEngines": active,
        "nativeTextExtractor": text_extractor,
        "recommendedEngine": "PaddleOCR",
        "setup": setup,
        "installHint": None,
    }


def run_ocr_setup() -> None:
    """Explicit one-time model preparation, isolated from material processing."""
    OCR_SETUP.update({
        "state": "running",
        "stage": "preparing_environment",
        "progress": 2,
        "message": f"正在准备本机 OCR 环境：{OCR_VENV_ROOT}",
        "error": None,
        "startedAt": now(),
    })
    output = OCR_JOB_ROOT / "ocr-setup.json"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not OCR_PYTHON.exists():
            OCR_SETUP.update({
                "stage": "creating_environment",
                "progress": 5,
                "message": "正在创建 OA阅答 专用 OCR 环境…",
            })
            bootstrap_python = RUNTIME_PYTHON if RUNTIME_PYTHON.is_file() else Path(sys.executable)
            created = subprocess.run(
                [str(bootstrap_python), "-m", "venv", str(OCR_VENV_ROOT)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60 * 5,
                check=False,
                **hidden_subprocess_options(),
            )
            if created.returncode:
                raise RuntimeError((created.stderr or created.stdout or "Unable to create the local OCR environment")[-1200:])
        paddle_ready = subprocess.run(
            [str(OCR_PYTHON), "-c", "import paddle, paddleocr"],
            cwd=ROOT,
            capture_output=True,
            timeout=30,
            check=False,
            **hidden_subprocess_options(),
        ).returncode == 0
        if not paddle_ready:
            OCR_SETUP.update({
                "stage": "installing_paddlepaddle",
                "progress": 20,
                "message": "正在安装 PaddlePaddle CPU 引擎；首次安装可能需要数分钟…",
            })
            paddle_installed = subprocess.run(
                [
                    str(OCR_PYTHON), "-m", "pip", "install", "--disable-pip-version-check",
                    "paddlepaddle", "-i", "https://www.paddlepaddle.org.cn/packages/stable/cpu/",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60 * 30,
                check=False,
                **hidden_subprocess_options(),
            )
            if paddle_installed.returncode:
                raise RuntimeError((paddle_installed.stderr or paddle_installed.stdout or "Unable to install PaddlePaddle")[-1200:])
            OCR_SETUP.update({
                "stage": "installing_paddleocr",
                "progress": 50,
                "message": "PaddlePaddle 已安装，正在安装 PaddleOCR…",
            })
            ocr_installed = subprocess.run(
                [
                    str(OCR_PYTHON), "-m", "pip", "install", "--disable-pip-version-check",
                    "paddleocr",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60 * 30,
                check=False,
                **hidden_subprocess_options(),
            )
            if ocr_installed.returncode:
                raise RuntimeError((ocr_installed.stderr or ocr_installed.stdout or "Unable to install PaddleOCR")[-1200:])
        OCR_SETUP.update({
            "stage": "warming_up_model",
            "progress": 75,
            "message": "OCR 引擎已安装，正在下载并初始化中文识别模型…",
        })
        completed = subprocess.run(
            [str(OCR_PYTHON), str(ROOT / "bridge" / "ocr_worker.py"), "--warmup", "--output", str(output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60 * 15,
            check=False,
            **hidden_subprocess_options(),
        )
        if completed.returncode:
            OCR_SETUP.update({
                "state": "error",
                "stage": "failed",
                "message": "OCR 中文模型初始化失败",
                "error": (completed.stderr or completed.stdout or "OCR setup failed")[-800:],
            })
        else:
            OCR_SETUP.update({
                "state": "ready",
                "stage": "complete",
                "progress": 100,
                "message": "OCR 引擎和中文模型已就绪",
                "error": None,
                "completedAt": now(),
            })
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        OCR_SETUP.update({
            "state": "error",
            "stage": "failed",
            "message": "OCR 安装或初始化失败",
            "error": str(error),
        })


def read_ocr_job(job: dict[str, Any]) -> dict[str, Any]:
    result_path: Path = job["result"]
    if result_path.exists():
        try:
            return {"id": job["id"], **json.loads(result_path.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            pass
    return {"id": job["id"], "state": job["state"], "files": [], "error": job["error"]}


def application_number_for_ocr_job(job: dict[str, Any], result: dict[str, Any] | None = None) -> str | None:
    manifest: Path | None = job.get("manifest")
    if not isinstance(manifest, Path) or not manifest.exists():
        return None
    try:
        sources = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(sources, list):
        return None
    return patent_application_number(sources, result or read_ocr_job(job))


def latest_completed_ocr_job() -> dict[str, Any] | None:
    """Expose the latest local result so a refreshed page can resume safely."""
    candidates = [job for job in OCR_JOBS.values() if job["result"].exists() and job["manifest"].exists()]
    candidates.sort(key=lambda job: job["result"].stat().st_mtime, reverse=True)
    for job in candidates:
        payload = read_ocr_job(job)
        if payload.get("state") != "complete":
            continue
        try:
            manifest = json.loads(job["manifest"].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        return {"job": payload, "materials": manifest.get("files", []), "applicationNumber": application_number_for_ocr_job(job, payload)}
    return None


def run_ocr_job(job: dict[str, Any], manifest: Path) -> None:
    job["state"] = "running"
    result_path: Path = job["result"]
    try:
        completed = subprocess.run(
            [str(OCR_PYTHON), str(ROOT / "bridge" / "ocr_worker.py"), "--manifest", str(manifest), "--output", str(result_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60 * 30,
            check=False,
            **hidden_subprocess_options(),
        )
        if completed.returncode:
            job["state"] = "error"
            job["error"] = (completed.stderr or completed.stdout or "PaddleOCR task failed")[-1000:]
        else:
            job["state"] = "complete"
    except (OSError, subprocess.TimeoutExpired) as error:
        job["state"] = "error"
        job["error"] = str(error)


def local_citation_source_catalog(job: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Recover source filenames for older jobs by matching cited text to local OCR text."""
    try:
        analysis = json.loads(str(job.get("analysis") or "{}"))
        references = analysis.get("references", [])
        ocr_job = OCR_JOBS.get(str(job.get("ocrJobId") or ""))
        if not isinstance(references, list) or not ocr_job:
            return {}
        manifest = json.loads(ocr_job["manifest"].read_text(encoding="utf-8")).get("files", [])
        result = read_ocr_job(ocr_job).get("files", [])
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(manifest, list) or not isinstance(result, list):
        return {}

    def normalize(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", value).lower()

    texts: list[tuple[str, str]] = []
    result_by_id = {str(item.get("id")): item for item in result if isinstance(item, dict)}
    for material in manifest:
        if not isinstance(material, dict):
            continue
        result_item = result_by_id.get(str(material.get("id")), {})
        text_path = result_item.get("localTextPath") if isinstance(result_item, dict) else None
        if not isinstance(text_path, str) or not Path(text_path).is_file():
            continue
        try:
            texts.append((str(material.get("name") or "材料文件"), normalize(Path(text_path).read_text(encoding="utf-8", errors="replace"))))
        except OSError:
            continue

    citations: dict[str, list[str]] = {}
    for reference in references:
        if not isinstance(reference, dict):
            continue
        source_id = reference.get("source_id")
        cited_text = reference.get("cited_text")
        if isinstance(source_id, str) and isinstance(cited_text, str):
            citations.setdefault(source_id, []).append(normalize(cited_text))

    catalog: dict[str, dict[str, str]] = {}
    for source_id, excerpts in citations.items():
        best_name = ""
        best_score = 0
        for name, source_text in texts:
            score = 0
            for excerpt in excerpts:
                # OCR output can differ in spacing and punctuation.  The longest
                # common passage gives a resilient filename mapping without any
                # network call or transmission of the local material.
                excerpt_slice = excerpt[:6000]
                score += difflib.SequenceMatcher(None, excerpt_slice, source_text, autojunk=False).find_longest_match().size
            if score > best_score:
                best_name, best_score = name, score
        if best_score >= 18:
            catalog[source_id] = {"title": best_name}
    return catalog


def enrich_local_citation_source_catalog(job: dict[str, Any]) -> bool:
    """Persist filename mappings without calling NotebookLM, including for old analyses."""
    if job.get("citationCatalogLocalResolved"):
        return False
    recovered = local_citation_source_catalog(job)
    catalog = job.setdefault("sourceCatalog", {})
    if not isinstance(catalog, dict):
        catalog = job["sourceCatalog"] = {}
    catalog.update({key: value for key, value in recovered.items() if key not in catalog})
    job["citationCatalogLocalResolved"] = True
    return True


def patent_application_number(manifest_sources: list[dict[str, Any]], ocr_result: dict[str, Any]) -> str | None:
    """Extract the filing number only from parsed OA/application body text, never filenames."""
    preferred = sorted(manifest_sources, key=lambda item: 0 if item.get("kind") == "officeAction" else 1)
    result_by_id = {str(item.get("id")): item for item in ocr_result.get("files", [])}
    for item in preferred:
        if item.get("kind") not in {"officeAction", "application"}:
            continue
        text_path = result_by_id.get(str(item.get("id")), {}).get("localTextPath")
        if not isinstance(text_path, str) or not Path(text_path).is_file():
            continue
        text = Path(text_path).read_text(encoding="utf-8", errors="replace")[:200_000]
        match = re.search(
            r"(?:申请号|Application\s*(?:No\.?|Number))\s*[：:]?\s*((?:20[\d\s.]{10,18})|(?:1\d\s*/\s*\d{6}))",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            normalized = re.sub(r"\D", "", match.group(1))
            if len(normalized) >= 8:
                return normalized
    return None


def notebook_history_for_application(application_number: str) -> dict[str, Any] | None:
    candidates = [
        job for job in NOTEBOOK_JOBS.values()
        if job.get("status") == "complete"
        and (job.get("applicationNumber") == application_number or str(job.get("title", "")).startswith(f"{application_number}-"))
    ]
    if not candidates:
        return None
    job = max(candidates, key=lambda item: str(item.get("completedAt") or item.get("createdAt") or ""))
    analyses = [
        entry for entry in ISSUE_ANALYSIS_JOBS.values()
        if entry.get("notebookId") == job.get("notebookId") and entry.get("status") == "complete"
    ]
    latest_analysis = max(analyses, key=lambda item: str(item.get("completedAt") or item.get("createdAt") or ""), default=None)
    return {
        "job": {
            key: job.get(key)
            for key in ("id", "status", "title", "notebookId", "applicationNumber", "createdAt", "completedAt")
        },
        "latestIssueAnalysisId": latest_analysis.get("id") if latest_analysis else None,
    }


def issue_analysis_history_for_notebook(notebook_id: str) -> list[dict[str, Any]]:
    """Return compact, local-only answer history for one Notebook."""
    analyses = [
        entry for entry in ISSUE_ANALYSIS_JOBS.values()
        if entry.get("notebookId") == notebook_id and entry.get("status") == "complete" and entry.get("analysis")
    ]
    analyses.sort(key=lambda item: str(item.get("completedAt") or item.get("createdAt") or ""), reverse=True)
    return [
        {
            "id": entry.get("id"),
            "title": f"{entry.get('issue', {}).get('title') or '审查问题'}：答复要点",
            "completedAt": entry.get("completedAt") or entry.get("createdAt"),
            "favorite": bool(entry.get("favorite")),
        }
        for entry in analyses
    ]


def audit_plan() -> str:
    try:
        plan = json.loads(AUDIT_SETTINGS_PATH.read_text(encoding="utf-8")).get("plan")
    except (OSError, json.JSONDecodeError, AttributeError):
        plan = "free"
    return str(plan) if plan in AUDIT_PLAN_LIMITS else "free"


def save_audit_plan(plan: str) -> None:
    if plan not in AUDIT_PLAN_LIMITS:
        raise ValueError("Unsupported NotebookLM plan")
    AUDIT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_SETTINGS_PATH.write_text(json.dumps({"plan": plan}, ensure_ascii=False), encoding="utf-8")


def record_notebook_chat(command: list[str]) -> None:
    """Keep a local, transparent counter of queries initiated by this application."""
    if len(command) < 3 or command[1:3] != ["notebook", "query"]:
        return
    try:
        AUDIT_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        event = {"at": now(), "type": "notebook_query", "notebookId": command[3] if len(command) > 3 else None}
        with AUDIT_EVENTS_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        # Usage display must never interrupt a paid or free NotebookLM request.
        return


def local_today_notebook_chat_count() -> int:
    today = datetime.now().astimezone().date()
    count = 0
    try:
        for line in AUDIT_EVENTS_PATH.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") != "notebook_query":
                continue
            if datetime.fromisoformat(str(event.get("at"))).astimezone().date() == today:
                count += 1
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return count


def refresh_notebook_audit_in_background() -> None:
    """Synchronise the account notebook count without delaying the audit page."""
    with AUDIT_SYNC_LOCK:
        if AUDIT_SYNC.get("state") == "syncing":
            return
        AUDIT_SYNC.update({"state": "syncing", "error": None})

    def sync() -> None:
        try:
            # Audit is an interactive button, so fail with a useful message
            # instead of leaving the page spinning through the general 60-second
            # operation timeout and automatic retry cycle.
            count = len(NOTEBOOK_GATEWAY.list_notebooks(timeout=15.0, retry_network=False))
            with AUDIT_SYNC_LOCK:
                AUDIT_SYNC.update({"state": "ready", "notebookCount": count, "error": None, "syncedAt": now()})
        except (OSError, NotebookLMError, RuntimeError, ValueError) as error:
            message = str(error)[:700]
            if "登录" in message or "Authentication" in message:
                message = "NotebookLM 登录已过期；请回到工作台重新连接 Google 账号后再同步。"
            with AUDIT_SYNC_LOCK:
                AUDIT_SYNC.update({"state": "error", "error": message, "syncedAt": now()})

    threading.Thread(target=sync, daemon=True).start()


def audit_summary() -> dict[str, Any]:
    plan = audit_plan()
    limits = AUDIT_PLAN_LIMITS[plan]
    known_notebooks = {str(job.get("notebookId")) for job in NOTEBOOK_JOBS.values() if job.get("notebookId")}
    latest = latest_notebook_job()
    active_sources = len(latest.get("sourceCatalog") or {}) if latest else 0
    with AUDIT_SYNC_LOCK:
        sync = dict(AUDIT_SYNC)
    notebook_count = sync.get("notebookCount") if isinstance(sync.get("notebookCount"), int) else len(known_notebooks)
    return {
        "plan": plan,
        "planLabel": limits["label"],
        "limits": limits,
        "usage": {
            "notebooks": notebook_count,
            "dailyChats": local_today_notebook_chat_count(),
            "sourcesPerNotebook": active_sources,
        },
        "notebookCountScope": "account" if isinstance(sync.get("notebookCount"), int) else "local_known",
        "activeNotebook": {"id": latest.get("notebookId"), "title": latest.get("title")} if latest else None,
        "sync": sync,
    }


def run_native_notebook_query(
    notebook_id: str, prompt: str, *, timeout: float = 180.0, conversation_id: str | None = None
) -> str:
    """Return the same JSON text shape previously persisted from the CLI."""
    record_notebook_chat(["native", "notebook", "query", notebook_id])
    try:
        result = NOTEBOOK_GATEWAY.query(
            notebook_id, prompt, conversation_id=conversation_id, timeout=timeout
        )
    except AuthenticationError as error:
        mark_notebook_authentication_expired(str(error))
        raise
    return json.dumps(result, ensure_ascii=False)


def is_transient_notebook_query_error(error: Exception) -> bool:
    """Return whether retrying a whole NotebookLM answer is safe and useful."""
    if isinstance(error, (OSError, TimeoutError)):
        return True
    if not isinstance(error, ProtocolError):
        return False
    message = str(error)
    return any(marker in message for marker in (
        "连接暂时中断",
        "连接超时",
        "timed out",
        "temporarily unavailable",
        "connection reset",
    ))


def run_issue_query_with_recovery(
    job: dict[str, Any],
    prompt: str,
    *,
    stage: str,
    timeout: float,
) -> str:
    """Resume a selected-issue request after a brief notebook connection loss.

    The low-level transport already retries individual HTTP requests.  This extra
    boundary covers a disconnect after a streamed answer was started: retrying the
    selected question is preferable to losing the whole local analysis job.
    """
    attempts = 3
    stage_label = "审查意见提取" if stage == "extracting_examiner_issues" else "答复策略分析"
    for attempt in range(1, attempts + 1):
        job["status"] = stage
        job["recovery"] = {
            "state": "requesting",
            "stage": stage,
            "stageLabel": stage_label,
            "attempt": attempt,
            "maxAttempts": attempts,
            "message": f"正在请求 NotebookLM（第 {attempt}/{attempts} 次）",
        }
        save_issue_analysis_job(job)
        try:
            answer = run_native_notebook_query(job["notebookId"], prompt, timeout=timeout)
            job.pop("recovery", None)
            save_issue_analysis_job(job)
            return answer
        except AuthenticationError:
            raise
        except (OSError, ProtocolError) as error:
            if not is_transient_notebook_query_error(error) or attempt >= attempts:
                raise
            delay_seconds = attempt * 4
            job["recovery"] = {
                "state": "recovering",
                "stage": stage,
                "stageLabel": stage_label,
                "attempt": attempt,
                "maxAttempts": attempts,
                "nextAttemptInSeconds": delay_seconds,
                "message": f"NotebookLM 连接短暂中断，正在恢复后重试（第 {attempt + 1}/{attempts} 次）",
            }
            save_issue_analysis_job(job)
            time.sleep(delay_seconds)
    raise RuntimeError("NotebookLM query recovery unexpectedly ended")


def unique_archive_directory(job_directory: Path) -> Path:
    base = job_directory / "notebook-upload"
    if not base.exists():
        return base
    index = 2
    while (candidate := job_directory / f"notebook-upload-{index}").exists():
        index += 1
    return candidate


def archive_notebook_sources(
    job_directory: Path, manifest_sources: list[dict[str, str]], ocr_result: dict[str, Any]
) -> tuple[list[dict[str, str]], Path]:
    """Archive one source per material: original text file or OCR Markdown."""
    by_id = {str(item.get("id")): item for item in ocr_result.get("files", [])}
    archive = unique_archive_directory(job_directory)
    archive.mkdir(parents=True, exist_ok=False)
    sources: list[dict[str, str]] = []
    material_labels = {
        "application": "申请文件",
        "officeAction": "本轮审查意见",
        "priorArt": "补充对比文件",
        "response": "历史意见陈述书",
    }
    for index, item in enumerate(manifest_sources, start=1):
        name = str(item["name"])
        kind = str(item.get("kind") or "")
        response_purpose = str(item.get("responsePurpose") or "")
        source_role = "response_optimization" if kind == "response" and response_purpose == "optimize" else kind
        material_label = material_labels.get(str(item.get("kind") or ""), "补充材料")
        result = by_id.get(str(item["id"]), {})
        if source_role == "response_optimization":
            material_label = "本轮已有意见陈述书（优化基线）"
            item = {**item, "kind": source_role}
        text_path = result.get("localTextPath")
        scanned = result.get("method") == "paddle_ocr" or result.get("confidence") is not None
        converted_path = result.get("convertedPath") if result.get("method") == "legacy_doc_to_docx" else None
        # Born-digital PDF/DOCX files already have reliable text: send the
        # original directly. Scanned files instead get one OCR Markdown source.
        if isinstance(converted_path, str) and Path(converted_path).is_file():
            converted = Path(converted_path)
            original = archive / f"{index:02d}-转换DOCX-{converted.name}"
            shutil.copy2(converted, original)
            sources.append({"path": str(original), "name": f"[{material_label}｜DOC 转换 DOCX] {converted.name}", "role": str(item.get("kind") or "converted_docx")})
        elif not scanned:
            original = archive / f"{index:02d}-原件-{Path(name).name}"
            shutil.copy2(item["path"], original)
            sources.append({"path": str(original), "name": f"[{material_label}｜原件] {name}", "role": str(item.get("kind") or "original")})
        elif isinstance(text_path, str) and Path(text_path).is_file():
            text = Path(text_path).read_text(encoding="utf-8", errors="replace")
            companion = archive / f"{index:02d}-材料文本-{Path(name).stem}.md"
            if Path(text_path).suffix.lower() == ".md":
                companion.write_text(text, encoding="utf-8")
            else:
                companion.write_text(
                    f"# {Path(name).stem}\n\n> 本机 PaddleOCR 识别文本；请以同名原件的页码、附图和版式为准。\n\n{text.strip()}\n",
                    encoding="utf-8",
                )
            sources.append({
                "path": str(companion),
                "name": f"[{material_label}｜材料文本] {Path(name).stem}.md",
                "role": str(item.get("kind") or "markdown_text"),
            })
    return sources, archive


def reconcile_uploaded_source(notebook_id: str, title: str) -> dict[str, Any] | None:
    """Return a source that reached NotebookLM even if the local request was interrupted.

    A resumable upload can finish remotely while the response is lost locally.  Looking
    up the source before retrying prevents a duplicate registration in that case.
    """
    for poll in range(4):
        try:
            remote_sources = NOTEBOOK_GATEWAY.list_sources(notebook_id)
        except AuthenticationError:
            raise
        except (OSError, NotebookLMError, RuntimeError):
            return None
        match = next(
            (
                item for item in remote_sources
                if str(item.get("title") or "").strip() == title.strip()
            ),
            None,
        )
        if match is None or match.get("status") == 3:
            return None
        if match.get("status") == 2 and match.get("id"):
            return match
        if poll < 3:
            time.sleep(2.0)
    return None


def add_notebook_source_with_retry(
    job: dict[str, Any],
    notebook_id: str,
    source: dict[str, str],
    *,
    attempts: int = 3,
) -> dict[str, Any]:
    """Upload one source with a checkpoint-safe retry boundary."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        job["currentSource"] = source["name"]
        job["currentSourceAttempt"] = attempt
        job["currentSourceMaxAttempts"] = attempts
        save_notebook_job(job)
        try:
            added = NOTEBOOK_GATEWAY.add_source(
                notebook_id, Path(source["path"]), source["name"]
            )
            job.pop("uploadRetryMessage", None)
            return added
        except AuthenticationError:
            raise
        except (OSError, NotebookLMError, RuntimeError) as error:
            last_error = error
            reconciled = reconcile_uploaded_source(notebook_id, source["name"])
            if reconciled is not None:
                job.pop("uploadRetryMessage", None)
                return reconciled
            if attempt >= attempts:
                break
            job["uploadRetryMessage"] = (
                f"第 {attempt} 次上传未完成，正在仅重试当前材料；"
                "此前已完成的材料不会重复上传。"
            )
            save_notebook_job(job)
            time.sleep(min(2.0 * attempt, 5.0))
    detail = str(last_error or "未知错误")
    raise RuntimeError(
        f"{source['name']} 上传仍未完成（已自动重试 {attempts} 次）；"
        f"此前材料进度已保留。最后错误：{detail}"
    ) from last_error


def run_notebook_submission(job: dict[str, Any], source_files: list[dict[str, str]], profile: str) -> None:
    try:
        notebook_id = str(job.get("existingNotebookId") or job.get("notebookId") or "")
        if notebook_id:
            job["status"] = "using_existing_notebook"
            job["notebookId"] = notebook_id
            job["reusedNotebook"] = True
            save_notebook_job(job)
        else:
            job["status"] = "creating_notebook"
            save_notebook_job(job)
            created = NOTEBOOK_GATEWAY.create_notebook(job["title"])
            notebook_id = str(created["id"])
            job["notebookId"] = notebook_id
        job["status"] = "uploading_sources"
        if not isinstance(job.get("sourceCatalog"), dict):
            job["sourceCatalog"] = {}
        completed_titles = {
            str(details.get("title") or "")
            for details in job["sourceCatalog"].values()
            if isinstance(details, dict)
        }
        job["uploadedSources"] = sum(1 for source in source_files if source["name"] in completed_titles)
        save_notebook_job(job)
        for source in source_files:
            if source["name"] in completed_titles:
                continue
            added = add_notebook_source_with_retry(job, notebook_id, source)
            source_id = str(added["id"])
            job["sourceCatalog"][source_id] = {
                "title": source["name"],
                "path": source["path"],
                "role": source.get("role"),
            }
            job["uploadedSources"] += 1
            completed_titles.add(source["name"])
            save_notebook_job(job)
        job.pop("currentSource", None)
        job.pop("currentSourceAttempt", None)
        job.pop("currentSourceMaxAttempts", None)
        job.pop("uploadRetryMessage", None)
        try:
            listed_sources = {
                str(item["id"]): {"title": str(item.get("title") or "材料文件")}
                for item in NOTEBOOK_GATEWAY.list_sources(notebook_id)
                if item.get("id")
            }
            if listed_sources:
                for source_id, details in listed_sources.items():
                    job["sourceCatalog"].setdefault(source_id, {}).update(details)
                save_notebook_job(job)
        except NotebookLMError:
            # Citation display remains usable even if a protocol revision blocks listing.
            pass
        prompt = (
            "你是专利审查意见答复的研究助手。仅依据本 Notebook 中的材料工作。"
            "先从审查意见通知书中逐项提取审查问题，按权利要求、法条/理由、引用对比文件和审查员论证编号。"
            "不要起草完整答复；每一项都标明需要进一步比对的申请文件与对比文件位置。"
            "来源标题会标注材料角色：本轮审查意见是本次答复的控制性依据；补充对比文件用于核对引用；历史意见陈述书用于了解此前已作出的答复和修改；申请文件用于核对技术方案。"
            "带有“原件”的来源为可直接读取文字的 PDF/DOCX；带有“材料文本”的来源为扫描件的本机 OCR Markdown 转写。"
        )
        if job.get("responseOptimization"):
            prompt += (
                "\n\n本次材料包含标注为‘本轮已有意见陈述书（优化基线）’的文件。它不是历史背景，而是本轮待优化的答复草稿。"
                "后续策略分析必须以该草稿为起点：保留其已经成立的论证，识别空缺、薄弱、重复或表述失准之处；"
                "任何修改建议均应说明对应草稿的位置和理由，不能假装该草稿不存在或从零开始撰写。"
            )
        job["status"] = "configuring_analysis"
        save_notebook_job(job)
        NOTEBOOK_GATEWAY.configure_chat(notebook_id, prompt)
        job["status"] = "generating_issue_list"
        save_notebook_job(job)
        answer = run_native_notebook_query(notebook_id, ISSUE_LIST_PROMPT, timeout=300.0)
        job["status"] = "complete"
        job["initialAnalysis"] = answer
        job["completedAt"] = now()
        save_notebook_job(job)
        if job.get("caseId") and job.get("roundNumber"):
            update_case_round(
                str(job["caseId"]), int(job["roundNumber"]),
                ocrJobId=job.get("ocrJobId"), notebookId=job.get("notebookId"),
                applicationNumber=job.get("applicationNumber"),
            )
    except AuthenticationError as error:
        mark_notebook_authentication_expired(str(error))
        job["status"] = "error"
        job["error"] = str(error)[:1400]
        job["completedAt"] = now()
        save_notebook_job(job)
    except (OSError, NotebookLMError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        job["status"] = "error"
        job["error"] = str(error)[:1400]
        job["completedAt"] = now()
        save_notebook_job(job)


def run_notebook_recovery(job: dict[str, Any], profile: str) -> None:
    """Re-query a user-authorised notebook after a local bridge restart."""
    try:
        job["status"] = "generating_issue_list"
        save_notebook_job(job)
        job["initialAnalysis"] = run_native_notebook_query(
            job["notebookId"], ISSUE_LIST_PROMPT, timeout=300.0
        )
        job["status"] = "complete"
        job["completedAt"] = now()
    except (OSError, NotebookLMError, RuntimeError) as error:
        job["status"] = "error"
        job["error"] = str(error)[:1400]
        job["completedAt"] = now()
    save_notebook_job(job)


def run_issue_analysis(job: dict[str, Any], profile: str) -> None:
    """Ask the existing Notebook one bounded, user-selected OA issue at a time."""
    try:
        if not job.get("sourceCatalog"):
            try:
                job["sourceCatalog"] = {
                    str(item["id"]): {"title": str(item.get("title") or "材料文件")}
                    for item in NOTEBOOK_GATEWAY.list_sources(job["notebookId"])
                    if item.get("id")
                }
                save_issue_analysis_job(job)
            except NotebookLMError:
                # This enrichment is cosmetic and must not block the legal analysis.
                pass
        issue = job["issue"]
        extraction_prompt = (
            "你是专利 OA 审查意见提取助手。仅依据当前 Notebook 中已有材料，"
            "只对下列审查类别输出‘审查意见问题逐项提取（依据原件）’。"
            "逐项列出涉及的权利要求/事项、法条或理由、引用对比文件、审查员论证摘要，以及需要核对的本申请与对比文件位置。"
            "不要评价、不要给答复建议、不要估计通过率；这是独立的第一份输出。\n\n"
            f"审查类别：{issue.get('title', '')}\n"
            f"法律依据：{issue.get('ground', '')}；{issue.get('legalBasis', '')}\n"
            f"涉及权利要求/事项：{issue.get('claims', '')}\n"
            f"涉及对比文件：{', '.join(issue.get('priorArt', []))}\n"
            f"审查理由摘要：{issue.get('summary', '')}"
        )
        job["examinerIssueExtraction"] = run_issue_query_with_recovery(
            job,
            extraction_prompt,
            stage="extracting_examiner_issues",
            timeout=300.0,
        )
        prompt = (
            "你是专利 OA 答复策略研究助手。仅基于当前 Notebook 中已有材料，"
            "只分析下面这一项审查问题，不要扩展到其他法条类别，也不要生成完整的正式意见陈述书。\n\n"
            f"审查问题：{issue.get('title', '')}\n"
            f"法律依据：{issue.get('ground', '')}；{issue.get('legalBasis', '')}\n"
            f"涉及权利要求/事项：{issue.get('claims', '')}\n"
            f"涉及对比文件：{', '.join(issue.get('priorArt', []))}\n"
            f"审查理由摘要：{issue.get('summary', '')}\n"
            f"已识别的核对重点：{issue.get('analysisFocus', '')}\n\n"
            "前一步已经单独输出‘审查意见问题逐项提取’，本次不要机械复述或逐项重列其内容；"
            "但必须充分利用其中已识别的审查逻辑、权利要求组和引文，形成一份有实质深度的答复策略备忘录。"
            "不要为了简短而省略论证。除引文外，建议输出约 2,800 至 4,500 个中文字符；材料不足时应明确说明不足，不能编造。"
            "篇幅应优先用于第 2、3 部分：第 2 部分原则上不少于 900 个中文字符，第 3 部分原则上不少于 1,200 个中文字符。\n\n"
            "请按下列五个部分作答，并用清晰的小标题和分点展开：\n"
            "1. 审查员立场与真正争点：综合说明审查员的推理链、其依赖的技术事实和隐含前提；区分哪些是文字比对、哪些是技术启示或常规选择判断。\n"
            "2. 具体技术场景下的显著差异：这是重点部分，不要只罗列术语。结合本申请的系统结构、部件关系、工艺/信号/控制流程、运行条件、作用机制和技术效果，至少提出四项可用于答辩的具体差异。对每项差异分别说明：（a）本申请在实际运行场景中的具体配置或过程；（b）对比文件公开到何种程度、缺少何种连接关系/条件/控制逻辑/效果链条；（c）该缺失为何不能由审查员所称的常规知识、简单替换或任意组合自然补足；（d）该差异产生的技术效果及其与审查结论的关联。必要时比较各对比文件之间是否存在组合动机或技术方向冲突。\n"
            "3. 可行答复路径与必要时的修改方向：这是重点部分。针对审查员每条核心论证分别给出可操作的反驳路径，而非只给结论；说明适合强调的权利要求限定、说明书表述、实施例事实和技术效果。请按‘主张—依据—回应的审查逻辑—预期作用—风险’展开。若建议修改，写明可考虑并入的限定内容、可能的原始记载依据、预期解决的审查缺口、与现有权利要求的从属/独立关系、以及潜在缩窄、超范围、清楚性或支持问题风险；可以给出修改要素组合，不要起草正式全文。\n"
            "4. 需要人工核验的证据位置：逐项列出需要确认的本申请、OA 与对比文件位置，尽可能注明文件名、权利要求、页码、段落、附图或实施例；并说明核验结果会如何影响上述答辩。\n"
            "5. 粗略通过可能性：给出高/中/低或区间判断，并从现有证据强度、差异与效果的可证明性、修改代价及审查理由严密程度解释；同时列出提高该判断的下一步取证或撰写动作。\n\n"
            "结论仅供专业人员复核，不构成法律意见。"
        )
        if job.get("responseOptimization"):
            prompt += (
                "\n\n【已有意见陈述书优化模式】\n"
                "本轮已有意见陈述书是待优化的基线，不要从零起草另一份泛泛的答复。请先读取并尊重其已有的论证结构、"
                "权利要求修改和证据安排；仅对当前所选审查类别提出必要、可执行的优化。\n"
                "在前述五部分之后，必须追加‘6. 对已有意见陈述书的定点优化清单’，逐项列出：\n"
                "（a）草稿位置：文件名、页码/标题/段落序号；若无法可靠编号，写出该段开头不超过 30 字的定位短语；\n"
                "（b）问题类型：权利要求是否仍需修改、答辩点缺失、论证不充分、证据未连接、表述易被误解或重复；\n"
                "（c）为什么需要调整：对应哪一条审查逻辑、技术事实或现有答辩缺口；\n"
                "（d）具体动作：保留、删除、插入到何处，或替换建议；\n"
                "（e）如建议替换，先以不超过 45 字摘录原段关键句，再给出可直接使用的‘建议替换文本’或‘建议新增段落’。"
                "替换文本应紧扣本案材料、避免虚构证据，并明确其中仍需人工核验的事实。\n"
                "对于权利要求，分别说明：不建议改动 / 建议增加的限定要素 / 需核查原始记载后才可采用的限定；"
                "不要在没有原始记载依据时推荐新增技术特征。"
            )
        job["analysis"] = run_issue_query_with_recovery(
            job,
            prompt,
            stage="generating_analysis",
            timeout=480.0,
        )
        job["status"] = "complete"
        job["completedAt"] = now()
        if job.get("caseId") and job.get("roundNumber"):
            record = read_case(str(job["caseId"]))
            round_record = case_round(record, int(job["roundNumber"])) if record else None
            if round_record is not None:
                ids = list(round_record.get("issueAnalysisIds") or [])
                if job["id"] not in ids:
                    ids.append(job["id"])
                update_case_round(str(job["caseId"]), int(job["roundNumber"]), issueAnalysisIds=ids)
    except (OSError, NotebookLMError, RuntimeError) as error:
        job["status"] = "error"
        job["error"] = str(error)[:1400]
        job["completedAt"] = now()
    save_issue_analysis_job(job)


def run_issue_followup(job: dict[str, Any], question: str, profile: str) -> None:
    """Ask one contextual follow-up in the existing Notebook; no materials are uploaded."""
    try:
        prompt = (
            "你是专利 OA 答复策略研究助手。仅基于当前 Notebook 已有材料，"
            "回答下面追问；不要上传、虚构或引用 Notebook 外材料。请简洁说明依据、限制和需要人工核验之处。\n\n"
            f"当前审查类别：{job.get('issue', {}).get('title', '')}\n"
            f"追问：{question}"
        )
        job["status"] = "generating_followup"
        save_issue_analysis_job(job)
        conversation_id = None
        existing_followups = job.get("followUps") or []
        if existing_followups:
            try:
                previous = json.loads(str(existing_followups[-1].get("answer") or "{}"))
                conversation_id = previous.get("conversation_id")
            except (json.JSONDecodeError, AttributeError):
                conversation_id = None
        answer = run_native_notebook_query(
            job["notebookId"], prompt, timeout=300.0, conversation_id=conversation_id
        )
        job.setdefault("followUps", []).append({"question": question, "answer": answer, "createdAt": now()})
        job["status"] = "complete"
        job["completedAt"] = now()
    except (OSError, NotebookLMError, RuntimeError) as error:
        job["status"] = "complete"
        job["followUpError"] = str(error)[:1400]
    save_issue_analysis_job(job)


def notebook_cli_setup_status() -> dict[str, Any]:
    return {
        "state": "embedded",
        "nativeEmbedded": True,
        # Backward-compatible UI field; no external CLI is required anymore.
        "cliAvailable": True,
        "uvAvailable": False,
        "bundledUv": False,
    }
def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_notebook_status_from_cli() -> dict[str, Any]:
    """Compatibility name for the internal bridge's live session check."""
    return NOTEBOOK_GATEWAY.status(live=True)


def restore_cached_notebook_status() -> dict[str, Any] | None:
    try:
        saved = json.loads(AUTH_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(saved, dict) or saved.get("state") not in {"authenticated", "not_authenticated"}:
        return None
    return {key: saved.get(key) for key in ("state", "cliAvailable", "provider", "account", "profile")}


def store_cached_notebook_status(status: dict[str, Any]) -> None:
    if status.get("state") not in {"authenticated", "not_authenticated"}:
        return
    AUTH_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: status.get(key) for key in ("state", "cliAvailable", "provider", "account", "profile")}
    payload["checkedAt"] = now()
    AUTH_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def mark_notebook_authentication_expired(message: str) -> None:
    """Invalidate the optimistic UI cache after Google rejects a saved session."""
    global AUTH_CACHE, AUTH_CACHE_AT
    NOTEBOOK_GATEWAY.auth.invalidate_session(message)
    status = NOTEBOOK_GATEWAY.status(live=False)
    status.update({
        "state": "not_authenticated",
        "live": False,
        "stage": "expired",
        "message": message,
    })
    AUTH_CACHE = status
    AUTH_CACHE_AT = time.monotonic()
    store_cached_notebook_status(status)


def refresh_notebook_status_in_background() -> None:
    global AUTH_CACHE, AUTH_CACHE_AT, AUTH_REFRESH_IN_FLIGHT
    with AUTH_REFRESH_LOCK:
        if AUTH_REFRESH_IN_FLIGHT:
            return
        AUTH_REFRESH_IN_FLIGHT = True

    def refresh() -> None:
        global AUTH_CACHE, AUTH_CACHE_AT, AUTH_REFRESH_IN_FLIGHT
        try:
            status = read_notebook_status_from_cli()
            AUTH_CACHE = status
            AUTH_CACHE_AT = time.monotonic()
            store_cached_notebook_status(status)
        finally:
            with AUTH_REFRESH_LOCK:
                AUTH_REFRESH_IN_FLIGHT = False

    threading.Thread(target=refresh, daemon=True).start()


def watch_notebook_login_completion_in_background() -> None:
    """Promote a completed browser login without depending on an open web page.

    The login browser and the workbench are separate processes.  A page poll can
    be lost when the dialog is closed or a single HTTP request fails, so the
    bridge itself owns the transition from browser login to live verification.
    """
    global AUTH_LOGIN_WATCH_IN_FLIGHT
    with AUTH_LOGIN_WATCH_LOCK:
        if AUTH_LOGIN_WATCH_IN_FLIGHT:
            return
        AUTH_LOGIN_WATCH_IN_FLIGHT = True

    def watch() -> None:
        global AUTH_CACHE, AUTH_CACHE_AT, AUTH_LOGIN_WATCH_IN_FLIGHT
        deadline = time.monotonic() + 305.0
        try:
            while time.monotonic() < deadline:
                current = NOTEBOOK_GATEWAY.status(live=False)
                if current.get("state") == "login_in_progress":
                    time.sleep(0.5)
                    continue
                status = NOTEBOOK_GATEWAY.status(live=True) if current.get("state") == "authenticated" else current
                AUTH_CACHE = status
                AUTH_CACHE_AT = time.monotonic()
                store_cached_notebook_status(status)
                if status.get("state") == "unavailable":
                    # The browser session is already saved; keep verifying after
                    # a short network hiccup instead of falling back to the page's
                    # much slower global refresh interval.
                    time.sleep(1.5)
                    continue
                return
        finally:
            with AUTH_LOGIN_WATCH_LOCK:
                AUTH_LOGIN_WATCH_IN_FLIGHT = False

    threading.Thread(target=watch, daemon=True).start()


def local_notebook_status(*, refresh: bool = False) -> dict[str, Any]:
    """Return cached status without calling saved credentials a live session.

    A cookie file is only a login candidate.  Until NotebookLM accepts a live
    request, the UI must show ``checking`` rather than ``authenticated``.
    """
    global AUTH_CACHE, AUTH_CACHE_AT
    if refresh:
        status = NOTEBOOK_GATEWAY.status(live=True)
        AUTH_CACHE = status
        AUTH_CACHE_AT = time.monotonic()
        store_cached_notebook_status(status)
        status["setup"] = notebook_cli_setup_status()
        return status
    if AUTH_CACHE is None:
        AUTH_CACHE = NOTEBOOK_GATEWAY.status(live=False)
        AUTH_CACHE_AT = time.monotonic()
        if AUTH_CACHE.get("state") == "authenticated":
            refresh_notebook_status_in_background()
    if time.monotonic() - AUTH_CACHE_AT >= AUTH_CACHE_TTL_SECONDS:
        refresh_notebook_status_in_background()
    status = dict(AUTH_CACHE)
    current = NOTEBOOK_GATEWAY.status(live=False)
    if current.get("state") == "login_in_progress":
        status.update(current)
    elif status.get("state") == "login_in_progress":
        # The browser worker has moved on, but AUTH_CACHE still describes the
        # previous polling tick.  Promote the worker's current state immediately
        # instead of waiting for the normal cache TTL.  A completed login still
        # needs one live NotebookLM request before the UI may call it connected.
        status.update(current)
        if current.get("state") == "authenticated":
            refresh_notebook_status_in_background()
            status["state"] = "checking"
            status["live"] = False
            status["checking"] = True
            status["message"] = "浏览器登录已完成，正在向 NotebookLM 验证会话"
    has_verified_live_session = status.get("state") == "authenticated" and status.get("live") is True
    status["revalidating"] = AUTH_REFRESH_IN_FLIGHT and has_verified_live_session
    status["checking"] = AUTH_REFRESH_IN_FLIGHT and not has_verified_live_session
    if status.get("state") == "checking":
        status["checking"] = True
    if (
        AUTH_REFRESH_IN_FLIGHT
        and not has_verified_live_session
        and current.get("state") != "login_in_progress"
        and status.get("stage") != "expired"
    ):
        status["state"] = "checking"
        status["live"] = False
        status["checking"] = True
        status["message"] = "正在向 NotebookLM 验证登录状态"
    if status.get("state") == "authenticated" and status.get("live") is not True:
        status["state"] = "checking"
        status["checking"] = True
        status["message"] = "正在向 NotebookLM 验证已保存的登录状态"
    status["loginLaunching"] = current.get("state") == "login_in_progress"
    status["cliAvailable"] = True
    status["setup"] = notebook_cli_setup_status()
    return status


class WorkbenchHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the windowed desktop build independent from a console stream."""
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/settings":
            self.respond_json(
                HTTPStatus.OK,
                {
                    "archiveRoot": str(ARCHIVE_OUTPUT_ROOT),
                    "defaultArchiveRoot": str(DEFAULT_OUTPUT_ROOT),
                },
            )
            return
        if self.path.startswith("/api/cases/"):
            segments = [part for part in urlparse(self.path).path.split("/") if part]
            # /api/cases/{caseId}/rounds/{number}
            if len(segments) == 5 and segments[3] == "rounds":
                try:
                    round_number = int(segments[4])
                except ValueError:
                    self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Round number must be numeric"})
                    return
                record = read_case(segments[2])
                round_record = case_round(record, round_number) if record else None
                if record is None or round_record is None:
                    self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Case round not found"})
                    return
                ocr_job_id = str(round_record.get("ocrJobId") or "")
                ocr_job = OCR_JOBS.get(ocr_job_id)
                ocr = None
                if ocr_job is not None:
                    result = read_ocr_job(ocr_job)
                    try:
                        manifest = json.loads(ocr_job["manifest"].read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        manifest = {"files": []}
                    ocr = {
                        "job": result,
                        "materials": manifest.get("files", []),
                        "applicationNumber": application_number_for_ocr_job(ocr_job, result) or record.get("applicationNumber"),
                    }
                notebook = next((entry for entry in NOTEBOOK_JOBS.values() if entry.get("notebookId") == round_record.get("notebookId")), None)
                self.respond_json(
                    HTTPStatus.OK,
                    {
                        "case": record,
                        "round": round_record,
                        "ocr": ocr,
                        "notebook": notebook,
                        "historyMaterials": prior_material_snapshots(record, round_number),
                    },
                )
                return
        if self.path == "/api/cases":
            self.respond_json(HTTPStatus.OK, {"cases": archived_cases()})
            return
        if self.path.startswith("/api/notebook/history"):
            query = parse_qs(urlparse(self.path).query)
            application_number = str(query.get("applicationNumber", [""])[0])
            if not application_number:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Application number is required"})
                return
            history = notebook_history_for_application(application_number)
            if history is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "No local history for this application"})
            else:
                self.respond_json(HTTPStatus.OK, history)
            return
        if self.path == "/api/health":
            self.respond_json(
                HTTPStatus.OK,
                {
                    "status": "ready",
                    "bridgeVersion": APP_VERSION,
                    "notebookProvider": "oa-yueda-native",
                    "queuedJobs": len(JOBS),
                    "time": now(),
                },
            )
            return
        if self.path == "/api/jobs":
            self.respond_json(HTTPStatus.OK, {"jobs": list(JOBS)})
            return
        if self.path.startswith("/api/audit"):
            if "refresh=1" in self.path:
                refresh_notebook_audit_in_background()
            self.respond_json(HTTPStatus.OK, audit_summary())
            return
        if self.path.startswith("/api/notebook/status"):
            if "login=1" in self.path or "verify=1" in self.path:
                # During an interactive login, the old credential cache must not
                # delay recognition until its normal ten-minute refresh window.
                # The check remains background-only, so this HTTP response stays
                # immediate while the UI continues showing progress.
                refresh_notebook_status_in_background()
            self.respond_json(HTTPStatus.OK, local_notebook_status(refresh="refresh=1" in self.path))
            return
        if self.path == "/api/ocr/status":
            self.respond_json(HTTPStatus.OK, local_ocr_status())
            return
        if self.path == "/api/ocr/latest":
            latest = latest_completed_ocr_job()
            if latest is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "No completed local OCR job"})
            else:
                self.respond_json(HTTPStatus.OK, latest)
            return
        if self.path.startswith("/api/ocr/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            job = OCR_JOBS.get(job_id)
            if job is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "OCR job not found"})
            else:
                payload = read_ocr_job(job)
                if payload.get("state") == "complete":
                    payload["applicationNumber"] = application_number_for_ocr_job(job, payload)
                self.respond_json(HTTPStatus.OK, payload)
            return
        if self.path.startswith("/api/notebook/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            job = NOTEBOOK_JOBS.get(job_id)
            if job is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "NotebookLM job not found"})
            else:
                self.respond_json(HTTPStatus.OK, job)
            return
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/api/notebook/issue-jobs":
            notebook_id = (parse_qs(parsed_url.query).get("notebookId") or [""])[0]
            if not notebook_id:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "notebookId is required"})
            else:
                self.respond_json(HTTPStatus.OK, {"jobs": issue_analysis_history_for_notebook(notebook_id)})
            return
        if self.path.startswith("/api/notebook/issue-jobs/") and self.path.endswith("/export.docx"):
            job_id = self.path.split("/")[4]
            job = ISSUE_ANALYSIS_JOBS.get(job_id)
            if job is None or job.get("status") != "complete":
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Completed issue analysis job not found"})
            else:
                self.respond_file(export_issue_analysis_docx(job), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            return
        if self.path.startswith("/api/notebook/issue-jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            job = ISSUE_ANALYSIS_JOBS.get(job_id)
            if job is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Issue analysis job not found"})
            else:
                if enrich_local_citation_source_catalog(job):
                    save_issue_analysis_job(job)
                self.respond_json(HTTPStatus.OK, job)
            return
        if self.path == "/api/notebook/latest":
            job = latest_notebook_job()
            if job is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "No local NotebookLM job"})
            else:
                self.respond_json(HTTPStatus.OK, job)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/audit/plan":
            self.set_audit_plan()
            return
        if self.path == "/api/settings/archive-root":
            self.set_archive_root()
            return
        if self.path == "/api/notebook/login":
            self.start_notebook_login()
            return
        if self.path == "/api/notebook/setup":
            self.start_notebook_cli_install()
            return
        if self.path == "/api/rounds/archive":
            self.archive_current_round()
            return
        if self.path.startswith("/api/cases/") and self.path.endswith("/rounds"):
            self.create_case_round()
            return
        if self.path == "/api/ocr/jobs":
            self.start_ocr_job()
            return
        if self.path == "/api/ocr/setup":
            self.start_ocr_setup()
            return
        if self.path == "/api/notebook/submit":
            self.start_notebook_submission()
            return
        if self.path == "/api/notebook/recover":
            self.recover_notebook_submission()
            return
        if self.path == "/api/notebook/issue-analysis":
            self.start_issue_analysis()
            return
        if self.path.startswith("/api/notebook/issue-jobs/") and self.path.endswith("/favorite"):
            self.favorite_issue_analysis()
            return
        if self.path.startswith("/api/notebook/issue-jobs/") and self.path.endswith("/followup"):
            self.start_issue_followup()
            return
        if self.path != "/api/jobs":
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return

        required = (
            "caseId",
            "roundId",
            "issueId",
            "googleAccount",
            "confirmedFiles",
            "consentAcknowledged",
        )
        missing = [key for key in required if not payload.get(key)]
        if missing:
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Confirmation is incomplete", "missing": missing},
            )
            return

        job = {
            "id": f"job-{len(JOBS) + 1:03d}",
            "status": "waiting_for_notebook_provider",
            "createdAt": now(),
            **{key: payload[key] for key in required},
        }
        JOBS.append(job)
        self.respond_json(HTTPStatus.ACCEPTED, {"job": job})

    def set_audit_plan(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            save_audit_plan(str(payload.get("plan") or ""))
        except (ValueError, json.JSONDecodeError) as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": str(error) or "Invalid NotebookLM plan"})
            return
        self.respond_json(HTTPStatus.OK, audit_summary())

    def set_archive_root(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            root = configure_archive_root(str(payload.get("archiveRoot") or ""))
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": str(error) or "Unable to set archive folder"})
            return
        self.respond_json(
            HTTPStatus.OK,
            {
                "archiveRoot": str(root),
                "defaultArchiveRoot": str(DEFAULT_OUTPUT_ROOT),
                "message": "Future cases will use this archive folder. Existing archives were not moved.",
            },
        )

    def start_notebook_login(self) -> None:
        """Open OA阅答's own isolated browser profile and return immediately."""
        global AUTH_CACHE, AUTH_CACHE_AT
        status = NOTEBOOK_GATEWAY.begin_login()
        AUTH_CACHE = status
        AUTH_CACHE_AT = time.monotonic()
        watch_notebook_login_completion_in_background()
        self.respond_json(HTTPStatus.ACCEPTED, status)

    def start_notebook_cli_install(self) -> None:
        """Compatibility endpoint: the bridge is now compiled into OA阅答."""
        self.respond_json(
            HTTPStatus.OK,
            {"setup": notebook_cli_setup_status(), "message": "NotebookLM 原生桥接器已内置，无需安装。"},
        )

    def archive_current_round(self) -> None:
        """Save the current editable case before the browser starts another case."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            round_number = int(payload.get("roundNumber"))
            if round_number < 1:
                raise ValueError("roundNumber must be positive")
        except (ValueError, TypeError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "A valid round number is required"})
            return
        existing_case_id = str(payload.get("caseId") or "")
        if existing_case_id:
            existing = read_case(existing_case_id)
            if existing is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Current case not found"})
                return
            if case_round(existing, round_number) is None:
                self.respond_json(HTTPStatus.CONFLICT, {"error": "Current case round not found"})
                return
            update_case_round(
                existing_case_id, round_number,
                applicationNumber=payload.get("applicationNumber"),
                ocrJobId=payload.get("ocrJobId"), notebookId=payload.get("notebookId"),
                issueAnalysisIds=payload.get("issueAnalysisIds", []),
            )
            self.respond_json(HTTPStatus.OK, read_case(existing_case_id) or existing)
            return
        ROUND_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        case_id = f"round-{uuid.uuid4().hex[:12]}"
        archived_at = now()
        archive = {
            "id": case_id,
            "kind": "case",
            "createdAt": archived_at,
            "updatedAt": archived_at,
            "applicationNumber": payload.get("applicationNumber"),
            "activeRoundNumber": round_number,
            "rounds": [{
                "number": round_number,
                "createdAt": archived_at,
                "updatedAt": archived_at,
                "ocrJobId": payload.get("ocrJobId"),
                "notebookId": payload.get("notebookId"),
                "issueAnalysisIds": payload.get("issueAnalysisIds", []),
            }],
        }
        save_case(archive)
        self.respond_json(HTTPStatus.CREATED, archive)

    def create_case_round(self) -> None:
        """Add an editable later OA round without sealing the existing case."""
        segments = [part for part in urlparse(self.path).path.split("/") if part]
        if len(segments) != 4 or segments[3] != "rounds":
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Unknown case operation"})
            return
        record = read_case(segments[2])
        if record is None:
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Case not found"})
            return
        existing_rounds = record.get("rounds", [])
        latest = max(existing_rounds, key=lambda entry: int(entry.get("number") or 0), default=None)
        if latest and not latest.get("ocrJobId") and not latest.get("issueAnalysisIds"):
            self.respond_json(HTTPStatus.OK, {"case": record, "round": latest, "reused": True})
            return
        number = max((int(entry.get("number") or 0) for entry in existing_rounds), default=0) + 1
        previous = case_round(record, number - 1) or {}
        round_record = {
            "number": number,
            "createdAt": now(),
            "updatedAt": now(),
            "ocrJobId": None,
            "notebookId": previous.get("notebookId"),
            "issueAnalysisIds": [],
            "previousRoundNumber": number - 1,
        }
        record.setdefault("rounds", []).append(round_record)
        record["activeRoundNumber"] = number
        save_case(record)
        self.respond_json(HTTPStatus.CREATED, {"case": record, "round": round_record})

    def start_ocr_job(self) -> None:
        status = local_ocr_status()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            files = payload["files"]
            if not isinstance(files, list) or not files:
                raise ValueError("files is required")
        except (ValueError, KeyError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid OCR job payload"})
            return
        requires_paddle = any(Path(str(file.get("name") or "")).suffix.lower() == ".pdf" for file in files if isinstance(file, dict))
        if requires_paddle and not status["engines"]["PaddleOCR"]:
            self.respond_json(HTTPStatus.CONFLICT, {"error": "PaddleOCR is not installed", "ocr": status})
            return

        job_id = f"ocr-{uuid.uuid4().hex[:12]}"
        job_dir = OCR_JOB_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        manifest_files = []
        try:
            for index, file in enumerate(files):
                raw = base64.b64decode(file["contentBase64"], validate=True)
                if not raw:
                    raise ValueError("An OCR input is empty")
                if len(raw) > 60 * 1024 * 1024:
                    raise ValueError("Single OCR input exceeds 60 MB")
                source_name = Path(str(file["name"])).name
                source_path = job_dir / f"{index:02d}-{source_name}"
                source_path.write_bytes(raw)
                manifest_files.append({
                    "id": str(file["id"]),
                    "name": source_name,
                    "kind": str(file.get("kind", "priorArt")),
                    "responsePurpose": str(file.get("responsePurpose") or ""),
                    "path": str(source_path),
                })
        except (KeyError, ValueError, base64.binascii.Error) as error:
            shutil.rmtree(job_dir, ignore_errors=True)
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        manifest = job_dir / "manifest.json"
        result = job_dir / "result.json"
        manifest.write_text(json.dumps({"files": manifest_files}, ensure_ascii=False), encoding="utf-8")
        job = {"id": job_id, "state": "queued", "result": result, "manifest": manifest, "error": None}
        OCR_JOBS[job_id] = job
        threading.Thread(target=run_ocr_job, args=(job, manifest), daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, read_ocr_job(job))

    def start_ocr_setup(self) -> None:
        with OCR_SETUP_LOCK:
            already_running = OCR_SETUP.get("state") in {"queued", "running"}
            if not already_running:
                OCR_SETUP.update({
                    "state": "queued",
                    "stage": "queued",
                    "progress": 0,
                    "message": "OCR 安装任务已开始，正在准备本机环境…",
                    "error": None,
                })
        if already_running:
            self.respond_json(HTTPStatus.ACCEPTED, local_ocr_status())
            return
        threading.Thread(target=run_ocr_setup, daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, local_ocr_status())

    def start_notebook_submission(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            ocr_job = OCR_JOBS[payload["ocrJobId"]]
            if payload.get("consentAcknowledged") is not True:
                raise ValueError("Explicit consent is required")
        except (KeyError, ValueError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "OCR job and explicit consent are required"})
            return
        result = read_ocr_job(ocr_job)
        if result.get("state") != "complete" or any(item.get("state") != "complete" for item in result.get("files", [])):
            self.respond_json(HTTPStatus.CONFLICT, {"error": "OCR must complete successfully before submission"})
            return
        with NOTEBOOK_JOBS_LOCK:
            existing = existing_notebook_job_for_ocr(payload["ocrJobId"])
        if existing:
            self.respond_json(HTTPStatus.OK, {**existing, "reused": True})
            return
        # A saved cookie file proves only a previous login.  Submission is an
        # external write, so validate it live before creating/resuming sources.
        notebook_status = local_notebook_status(refresh=True)
        if notebook_status.get("state") != "authenticated":
            self.respond_json(HTTPStatus.CONFLICT, {"error": "NotebookLM is not authenticated on this computer"})
            return
        manifest_path = ocr_job.get("manifest")
        if not isinstance(manifest_path, Path) or not manifest_path.exists():
            self.respond_json(HTTPStatus.CONFLICT, {"error": "Original local material set is unavailable"})
            return
        manifest_sources = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
        sources, archive = archive_notebook_sources(manifest_path.parent, manifest_sources, result)
        response_optimization = any(source.get("role") == "response_optimization" for source in sources)
        application_number = patent_application_number(manifest_sources, result)
        case_id = str(payload.get("caseId") or "")
        round_number = int(payload.get("roundNumber") or 0)
        existing_notebook_id = str(payload.get("existingNotebookId") or "")
        if case_id:
            record = read_case(case_id)
            if record is None or case_round(record, round_number) is None:
                self.respond_json(HTTPStatus.CONFLICT, {"error": "Selected case round is unavailable"})
                return
            application_number = application_number or record.get("applicationNumber")
            if not existing_notebook_id:
                previous = case_round(record, round_number - 1) if round_number > 1 else None
                existing_notebook_id = str((previous or {}).get("notebookId") or "")
        profile = notebook_status.get("profile") or "default"
        resumed = False
        with NOTEBOOK_JOBS_LOCK:
            # Recheck after the authentication/archive work: two browser requests
            # may have arrived at nearly the same time.
            existing = existing_notebook_job_for_ocr(payload["ocrJobId"])
            if existing:
                self.respond_json(HTTPStatus.OK, {**existing, "reused": True})
                return
            failed = failed_notebook_job_for_ocr(payload["ocrJobId"])
            if failed:
                job = failed
                job.update({
                    "status": "queued",
                    "sourceCount": len(sources),
                    "archivePath": str(archive),
                    "sourceFiles": sources,
                    "error": None,
                })
                job.pop("completedAt", None)
                resumed = True
            else:
                job_id = f"notebook-{uuid.uuid4().hex[:12]}"
                job = {
                "id": job_id,
                "status": "queued",
                "createdAt": now(),
                "title": f"{application_number}-OA策略分析" if application_number else "OA策略分析",
                "applicationNumber": application_number,
                "ocrJobId": payload["ocrJobId"],
                "caseId": case_id or None,
                "roundNumber": round_number or None,
                "existingNotebookId": existing_notebook_id or None,
                "responseOptimization": response_optimization,
                "uploadedSources": 0,
                "sourceCount": len(sources),
                "sourceFiles": sources,
                "archivePath": str(archive),
                "notebookId": None,
                "initialAnalysis": None,
                "error": None,
                }
                NOTEBOOK_JOBS[job_id] = job
        save_notebook_job(job)
        threading.Thread(target=run_notebook_submission, args=(job, sources, profile), daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, {**job, "resumed": resumed})

    def recover_notebook_submission(self) -> None:
        """Restore a completed issue list for a notebook already authorised by the user."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            notebook_id = str(payload["notebookId"])
            ocr_job_id = str(payload.get("ocrJobId") or "")
            if not notebook_id:
                raise ValueError("notebookId is required")
        except (ValueError, KeyError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Notebook ID is required"})
            return
        status = local_notebook_status()
        if status.get("state") != "authenticated":
            self.respond_json(HTTPStatus.CONFLICT, {"error": "NotebookLM is not authenticated on this computer"})
            return
        job_id = f"notebook-{uuid.uuid4().hex[:12]}"
        job = {
            "id": job_id,
            "status": "queued",
            "title": "恢复的 OA 审查问题清单",
            "ocrJobId": ocr_job_id,
            "notebookId": notebook_id,
            "initialAnalysis": None,
            "error": None,
            "createdAt": now(),
        }
        NOTEBOOK_JOBS[job_id] = job
        save_notebook_job(job)
        profile = status.get("profile") or "default"
        threading.Thread(target=run_notebook_recovery, args=(job, profile), daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, job)

    def start_issue_analysis(self) -> None:
        """Queue a bounded follow-up analysis; it never uploads materials."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            notebook_id = str(payload["notebookId"])
            issue = payload["issue"]
            if not notebook_id or not isinstance(issue, dict) or payload.get("consentAcknowledged") is not True:
                raise ValueError("Notebook, issue, and explicit confirmation are required")
        except (ValueError, KeyError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Notebook, selected issue, and explicit confirmation are required"})
            return
        status = local_notebook_status()
        if status.get("state") != "authenticated":
            self.respond_json(HTTPStatus.CONFLICT, {"error": "NotebookLM is not authenticated on this computer"})
            return
        job_id = f"issue-{uuid.uuid4().hex[:12]}"
        notebook_job = next((entry for entry in NOTEBOOK_JOBS.values() if entry.get("notebookId") == notebook_id), None)
        job = {
            "id": job_id,
            "status": "queued",
            "createdAt": now(),
            "notebookId": notebook_id,
            "ocrJobId": payload.get("ocrJobId"),
            "caseId": (notebook_job or {}).get("caseId"),
            "roundNumber": (notebook_job or {}).get("roundNumber"),
            "sourceCatalog": (notebook_job or {}).get("sourceCatalog", {}),
            "responseOptimization": bool((notebook_job or {}).get("responseOptimization")),
            "mode": "response_optimization" if (notebook_job or {}).get("responseOptimization") else "strategy",
            "issue": issue,
            "analysis": None,
            "followUps": [],
            "favorite": False,
            "error": None,
        }
        ISSUE_ANALYSIS_JOBS[job_id] = job
        save_issue_analysis_job(job)
        profile = status.get("profile") or "default"
        threading.Thread(target=run_issue_analysis, args=(job, profile), daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, job)

    def favorite_issue_analysis(self) -> None:
        job_id = self.path.split("/")[4]
        job = ISSUE_ANALYSIS_JOBS.get(job_id)
        if job is None or job.get("status") != "complete":
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Completed issue analysis job not found"})
            return
        job["favorite"] = True
        job["favoritedAt"] = now()
        FAVORITES_ROOT.mkdir(parents=True, exist_ok=True)
        (FAVORITES_ROOT / f"{job_id}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        save_issue_analysis_job(job)
        self.respond_json(HTTPStatus.OK, job)

    def start_issue_followup(self) -> None:
        job_id = self.path.split("/")[4]
        job = ISSUE_ANALYSIS_JOBS.get(job_id)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = str(payload.get("question") or "").strip()
            if not question:
                raise ValueError("Question is required")
        except (ValueError, json.JSONDecodeError):
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Follow-up question is required"})
            return
        if job is None or job.get("status") != "complete":
            self.respond_json(HTTPStatus.CONFLICT, {"error": "This analysis is not ready for a follow-up"})
            return
        status = local_notebook_status()
        if status.get("state") != "authenticated":
            self.respond_json(HTTPStatus.CONFLICT, {"error": "NotebookLM is not authenticated on this computer"})
            return
        job["status"] = "generating_followup"
        job.pop("followUpError", None)
        save_issue_analysis_job(job)
        profile = status.get("profile") or "default"
        threading.Thread(target=run_issue_followup, args=(job, question, profile), daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, job)

    def respond_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(body)

    def respond_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def guess_type(self, path: str) -> str:
        return mimetypes.guess_type(path)[0] or "application/octet-stream"


if __name__ == "__main__":
    restore_ocr_jobs()
    restore_notebook_jobs()
    restore_issue_analysis_jobs()
    address = ("127.0.0.1", 8787)
    print(f"{APP_NAME} v{APP_VERSION} is running at http://{address[0]}:{address[1]}")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer(address, WorkbenchHandler).serve_forever()
