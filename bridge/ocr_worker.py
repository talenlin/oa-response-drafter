"""Run one local PaddleOCR batch and persist only local job results."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from statistics import fmean
from typing import Any
from xml.etree import ElementTree


WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def payload_from_prediction(prediction: Any) -> dict[str, Any]:
    value = getattr(prediction, "json", prediction)
    value = value() if callable(value) else value
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        return {}
    return value.get("res", value)


def text_from_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{WORD_NAMESPACE}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{WORD_NAMESPACE}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def libreoffice_executable() -> str | None:
    """Locate a bundled or installed LibreOffice headless converter."""
    bundled_root = os.environ.get("OA_YUEDA_RUNTIME_DIR", "")
    candidates = [
        str(Path(bundled_root) / "libreoffice" / "program" / "soffice.exe") if bundled_root else None,
        shutil.which("soffice.exe"),
        shutil.which("soffice"),
    ]
    program_files = [
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ]
    candidates.extend(str(path) for path in program_files if path.is_file())
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def convert_with_word_com(source: Path, target: Path) -> Path:
    """Convert with a locally installed desktop Word when available.

    This is intentionally a desktop-only fallback, not server automation.  It
    keeps the document on the user's computer and generally preserves figures
    and tables much better than text-only legacy DOC parsers.
    """
    script = r'''
param([string]$InputPath, [string]$OutputPath)
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try { $word.AutomationSecurity = 3 } catch { }
    $document = $word.Documents.Open($InputPath, $false, $true, $false)
    $document.SaveAs([ref]$OutputPath, [ref]16)
}
finally {
    if ($document -ne $null) { $document.Close($false) }
    if ($word -ne $null) { $word.Quit() }
}
'''
    script_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as script_file:
            script_file.write(script)
            script_path = Path(script_file.name)
        completed = subprocess.run(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(script_path), str(source), str(target),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Microsoft Word local conversion could not start: {exc}") from exc
    finally:
        if script_path is not None:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass
    if completed.returncode or not target.is_file():
        detail = (completed.stderr or completed.stdout or "Microsoft Word is unavailable").strip()
        raise RuntimeError(detail[-700:])
    return target


def convert_with_office_oxide(source: Path, target: Path) -> Path:
    """Convert a legacy DOC with the bundled pure-Rust OfficeOxide binding.

    OfficeOxide reconstructs a DOCX from its format-neutral representation. It
    is the lightweight fallback for users who do not have desktop Word.  Word
    remains the first choice when available because it can preserve the exact
    rendering of files created by that user's Word installation.
    """
    try:
        from office_oxide import Document
    except ImportError as exc:
        raise RuntimeError("OfficeOxide document compatibility component is not installed.") from exc
    try:
        document = Document.open(str(source))
        document.save_as(str(target))
    except Exception as exc:  # The binding exposes Rust errors as Python exceptions.
        raise RuntimeError(f"OfficeOxide could not convert this DOC: {exc}") from exc
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("OfficeOxide finished without producing a usable DOCX file.")
    return target


def convert_legacy_doc(source: Path, workspace: Path) -> Path:
    """Convert legacy .doc to DOCX while preserving tables and embedded figures.

    Conversion is intentionally local.  Prefer a user's desktop Word for
    maximum layout fidelity, then use the small bundled OfficeOxide component.
    A legacy complete edition may still carry LibreOffice as a final fallback.
    """
    output_dir = workspace / "converted"
    output_dir.mkdir(parents=True, exist_ok=True)
    converted = output_dir / f"{source.stem}.docx"
    failures: list[str] = []
    try:
        return convert_with_word_com(source, converted)
    except RuntimeError as error:
        failures.append(f"Word: {error}")
    try:
        return convert_with_office_oxide(source, converted)
    except RuntimeError as error:
        failures.append(f"OfficeOxide: {error}")
    executable = libreoffice_executable()
    if executable is not None:
        try:
            completed = subprocess.run(
                [executable, "--headless", "--convert-to", "docx:Office Open XML Text", "--outdir", str(output_dir), str(source)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            if not completed.returncode and converted.is_file():
                return converted
            failures.append((completed.stderr or completed.stdout or "LibreOffice conversion failed").strip())
        except (OSError, subprocess.TimeoutExpired) as error:
            failures.append(f"LibreOffice: {error}")
    raise RuntimeError(
        "Unable to convert legacy .doc locally. Word, OfficeOxide, and any installed LibreOffice "
        "converter could not complete conversion. Markdown is intentionally not used because it can "
        "lose tables and figures. Details: " + " | ".join(failures)[-1200:]
    )


def render_pdf_pages(source: Path, workspace: Path) -> list[Path]:
    # On Windows, a .cmd shim can shadow the real Poppler executable and fail
    # inside a venv. Prefer the executable explicitly.
    executable = shutil.which("pdftoppm.exe") or shutil.which("pdftoppm")
    if executable is None:
        raise RuntimeError("未检测到 pdftoppm，无法将 PDF 转为 OCR 页图。")
    prefix = workspace / "page"
    result = subprocess.run(
        [executable, "-r", "170", "-jpeg", "-jpegopt", "quality=85", str(source), str(prefix)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "PDF 转页失败。")
    pages = sorted(workspace.glob("page-*.jpg"))
    if not pages:
        raise RuntimeError("PDF 未生成可 OCR 的页面。")
    return pages


def extract_native_pdf_text(source: Path) -> str | None:
    """Return a usable PDF text layer, keeping scanned PDFs on the OCR path."""
    executable = shutil.which("pdftotext.exe") or shutil.which("pdftotext")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "-layout", str(source), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    text = completed.stdout.strip() if completed.returncode == 0 else ""
    # A few isolated page numbers or metadata fragments are not a reliable text
    # layer.  Those files should still be passed to PaddleOCR.
    return text if len(text) >= 120 else None


def markdown_from_text(source_name: str, text: str, extraction: str) -> str:
    """Create a portable, auditable Markdown companion for NotebookLM."""
    title = Path(source_name).stem.replace("#", "") or "材料转写"
    return f"# {title}\n\n> 本机{extraction}文本；请以同名原件的页码、附图和版式为准。\n\n{text.strip()}\n"


def extracted_path(output: Path, item_id: str) -> Path:
    directory = output.parent / "extracted"
    directory.mkdir(exist_ok=True)
    return directory / f"{item_id}.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup", action="store_true", help="download/initialise the local OCR model only")
    args = parser.parse_args()

    if args.warmup:
        result: dict[str, Any] = {"state": "running", "modelState": "loading", "files": [], "error": None}
        write_result(args.output, result)
        try:
            from paddleocr import PaddleOCR

            PaddleOCR(
                lang="ch",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                engine="paddle",
                enable_mkldnn=False,
            )
            result.update({"state": "complete", "modelState": "ready"})
        except Exception as error:
            result.update({"state": "error", "modelState": "error", "error": str(error)[:500]})
        write_result(args.output, result)
        return 0 if result["state"] == "complete" else 1

    if args.manifest is None:
        parser.error("--manifest is required unless --warmup is used")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"state": "running", "files": [], "error": None}
    for item in manifest["files"]:
        result["files"].append({"id": item["id"], "name": item["name"], "state": "queued"})
    write_result(args.output, result)

    # Do not initialise PaddleOCR until it is genuinely needed. Most born-digital
    # patent PDFs already have a text layer and should complete in seconds.
    result["modelState"] = "not_needed"
    ocr: Any | None = None
    for item, status in zip(manifest["files"], result["files"], strict=True):
        status["state"] = "running"
        write_result(args.output, result)
        source = Path(item["path"])
        try:
            extension = source.suffix.lower()
            if extension in {".txt", ".md"}:
                text = source.read_text(encoding="utf-8", errors="replace")
                status.update({"state": "complete", "pages": 1, "textLength": len(text.strip()), "confidence": None, "localTextPath": str(source)})
                write_result(args.output, result)
                continue
            if extension == ".doc":
                status["state"] = "converting"
                write_result(args.output, result)
                converted = convert_legacy_doc(source, args.output.parent)
                text = text_from_docx(converted)
                local_text = extracted_path(args.output, item["id"])
                local_text.write_text(markdown_from_text(item["name"], text, "DOC 转换后的 DOCX 文本提取"), encoding="utf-8")
                status.update({
                    "state": "complete", "pages": 1, "textLength": len(text), "confidence": None,
                    "method": "legacy_doc_to_docx", "convertedPath": str(converted), "localTextPath": str(local_text),
                })
                write_result(args.output, result)
                continue
            if extension == ".docx":
                text = text_from_docx(source)
                local_text = extracted_path(args.output, item["id"])
                local_text.write_text(markdown_from_text(item["name"], text, "文档提取"), encoding="utf-8")
                status.update({"state": "complete", "pages": 1, "textLength": len(text), "confidence": None, "method": "document_text", "localTextPath": str(local_text)})
                write_result(args.output, result)
                continue
            if extension == ".pdf":
                text = extract_native_pdf_text(source)
                if text is not None:
                    local_text = extracted_path(args.output, item["id"])
                    local_text.write_text(markdown_from_text(item["name"], text, "PDF 原生提取"), encoding="utf-8")
                    status.update({"state": "complete", "pages": None, "textLength": len(text), "confidence": None, "method": "native_pdf_text", "localTextPath": str(local_text)})
                    write_result(args.output, result)
                    continue
            if ocr is None:
                result["modelState"] = "loading"
                write_result(args.output, result)
                from paddleocr import PaddleOCR

                ocr = PaddleOCR(
                    lang="ch",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    engine="paddle",
                    enable_mkldnn=False,
                )
                result["modelState"] = "ready"
                write_result(args.output, result)
            page_dir = args.output.parent / f"pages-{item['id']}"
            page_dir.mkdir(exist_ok=True)
            pages = render_pdf_pages(source, page_dir) if extension == ".pdf" else [source]
            all_text: list[str] = []
            all_scores: list[float] = []
            status["pageCurrent"] = 0
            status["pageTotal"] = len(pages)
            write_result(args.output, result)
            for page_index, page in enumerate(pages, start=1):
                for prediction in ocr.predict(str(page)):
                    payload = payload_from_prediction(prediction)
                    all_text.extend(text for text in payload.get("rec_texts", []) if text)
                    all_scores.extend(float(score) for score in payload.get("rec_scores", []) if score is not None)
                status["pageCurrent"] = page_index
                write_result(args.output, result)
            extracted = "\n".join(all_text)
            local_text = extracted_path(args.output, item["id"])
            local_text.write_text(markdown_from_text(item["name"], extracted, "PaddleOCR 识别"), encoding="utf-8")
            status.update(
                {
                    "state": "complete" if extracted.strip() else "review",
                    "pages": len(pages),
                    "textLength": len(extracted),
                    "confidence": round(fmean(all_scores), 3) if all_scores else None,
                    "method": "paddle_ocr",
                    "localTextPath": str(local_text),
                }
            )
        except Exception as error:  # local task must report instead of silently discarding a file
            status.update({"state": "error", "error": str(error)[:500]})
        write_result(args.output, result)
    result["state"] = "complete"
    write_result(args.output, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
