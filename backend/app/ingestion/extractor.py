from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


@dataclass(frozen=True)
class ExtractedUnit:
    unit_type: str
    page_or_slide: int | None
    text: str
    image_bytes: bytes | None = None
    image_mime_type: str | None = None
    warning: str | None = None


def extract_file(path: Path, source_type: str) -> list[ExtractedUnit]:
    if source_type == "pdf":
        return _extract_pdf(path)
    if source_type == "docx":
        return _extract_docx(path)
    if source_type == "pptx":
        return _extract_pptx(path)
    if source_type in {"text", "markdown", "webpage"}:
        return [ExtractedUnit("document", None, path.read_text(encoding="utf-8", errors="replace"))]
    if source_type == "image":
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(
            path.suffix.lower(), "application/octet-stream"
        )
        image_bytes = path.read_bytes()
        text = _ocr_image(image_bytes)
        return [
            ExtractedUnit(
                "image",
                None,
                text or "",
                image_bytes,
                mime,
                None if text is not None else "Local OCR is unavailable for this image",
            )
        ]
    raise ValueError(f"Unsupported source type: {source_type}")


def _extract_pdf(path: Path) -> list[ExtractedUnit]:
    import fitz

    units = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(dpi=144, alpha=False)
            image_bytes = pixmap.tobytes("png")
            text = page.get_text("text").strip()
            ocr_text = None
            warning = None
            if not text:
                ocr_text = _ocr_image(image_bytes)
                if ocr_text is None:
                    warning = f"Local OCR is unavailable for PDF page {page_number}"
            units.append(
                ExtractedUnit(
                    "page",
                    page_number,
                    text or ocr_text or "",
                    image_bytes,
                    "image/png",
                    warning,
                )
            )
    return units


def _extract_docx(path: Path) -> list[ExtractedUnit]:
    from docx import Document

    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    return [ExtractedUnit("document", None, text)]


def _extract_pptx(path: Path) -> list[ExtractedUnit]:
    from pptx import Presentation

    presentation = Presentation(path)
    units = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        text = "\n".join(
            shape.text for shape in slide.shapes if hasattr(shape, "text") and shape.text.strip()
        )
        units.append(ExtractedUnit("slide", slide_number, text))
    return units


def _ocr_image(image_bytes: bytes) -> str | None:
    """Use an already-installed local Tesseract binary without adding a model dependency."""
    configured = os.getenv("TESSERACT_CMD", "").strip()
    executable = configured if configured and Path(configured).is_file() else shutil.which("tesseract")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "--psm", "6"],
            input=image_bytes,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()

