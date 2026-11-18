from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedUnit:
    unit_type: str
    page_or_slide: int | None
    text: str
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


def extract_file(path: Path, source_type: str) -> list[ExtractedUnit]:
    if source_type == "pdf":
        return _extract_pdf(path)
    if source_type == "docx":
        return _extract_docx(path)
    if source_type == "pptx":
        return _extract_pptx(path)
    if source_type in {"text", "markdown"}:
        return [ExtractedUnit("document", None, path.read_text(encoding="utf-8", errors="replace"))]
    if source_type == "image":
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(
            path.suffix.lower(), "application/octet-stream"
        )
        return [ExtractedUnit("image", None, "", path.read_bytes(), mime)]
    raise ValueError(f"Unsupported source type: {source_type}")


def _extract_pdf(path: Path) -> list[ExtractedUnit]:
    import fitz

    units = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(dpi=144, alpha=False)
            units.append(
                ExtractedUnit("page", page_number, page.get_text("text"), pixmap.tobytes("png"), "image/png")
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

