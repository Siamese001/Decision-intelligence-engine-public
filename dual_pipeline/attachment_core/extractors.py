"""
Multi-modal attachment content extractors for images, diagrams, slides, and documents.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Union

from dual_pipeline.source_classification.capability_registry import get_image_extensions
from .types import AttachmentRecord, AttachmentType, ExtractedAttachmentContent

logger = logging.getLogger(__name__)

_RASTER_SUFFIXES = set(get_image_extensions())
_PDF_SUFFIXES = {".pdf", ".pptx"}
_THUMBNAIL_MAX_WIDTH = 900  # px — wide enough for Output 1/2 embeds, light enough to keep prompts lean


def _render_thumbnail_file(src: Path, suffix: str, thumb_path: Path, max_width: int, filename: str) -> bool:
    """Helper to render a thumbnail for raster, PDF, or PPTX."""
    if suffix in _RASTER_SUFFIXES:
        try:
            from PIL import Image as _PILImage  # type: ignore[import]
            img = _PILImage.open(src).convert("RGB")
            if img.width > max_width:
                img = img.resize((max_width, int(img.height * (max_width / img.width))), _PILImage.LANCZOS)
            img.save(thumb_path, format="PNG", optimize=True)
            return True
        except ImportError:
            import shutil as _shutil
            _shutil.copy2(src, thumb_path)
            return True
    if suffix == ".pdf":
        try:
            import fitz  # type: ignore[import]
            doc = fitz.open(str(src))
            page = doc[0]
            mat = fitz.Matrix(max_width / max(page.rect.width, 1), max_width / max(page.rect.width, 1))
            pix = page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(thumb_path))
            doc.close()
            return True
        except Exception as exc:
            logger.debug("PDF thumbnail failed for %s: %s", filename, exc)
            return False
    if suffix == ".pptx":
        try:
            from PIL import Image as _PILImage  # type: ignore[import]
            from PIL import ImageDraw as _PILDraw
            from pptx import Presentation as _Prs  # type: ignore[import]
            prs = _Prs(str(src))
            slide = prs.slides[0]
            title_text = slide.shapes.title.text if slide.shapes.title else filename
            img = _PILImage.new("RGB", (max_width, int(max_width * 9 / 16)), color=(30, 40, 60))
            _PILDraw.Draw(img).text((40, 40), title_text[:80], fill=(255, 255, 255))
            img.save(thumb_path, format="PNG")
            return True
        except Exception as exc:
            logger.debug("PPTX thumbnail failed for %s: %s", filename, exc)
            return False
    return False


def render_slide_thumbnails(
    records: List[AttachmentRecord],
    output_dir: Path,
    max_width: int = _THUMBNAIL_MAX_WIDTH,
) -> Dict[str, str]:
    """
    Renders a PNG thumbnail for each COLLATERAL_REVIEWED attachment and writes it
    alongside the original in referenced_artifacts/.

    Rendering strategy (zero-system-dependency):
    - Raster images (.png/.jpg/.webp etc.): copy as-is (already embeddable).
    - PDFs: render page 0 via PyMuPDF (fitz) -> PNG.
    - .pptx: extract first slide thumbnail via python-pptx's slide.shapes thumbnailing,
      falling back to a text-only placeholder if unavailable.
    - Unknown types: skip silently.

    Returns:
        Dict mapping attachment_id -> relative path string of the thumbnail PNG
        (relative to output_dir), e.g. "referenced_artifacts/LENS_v1.2.png".
        Only entries that successfully produced a thumbnail are included.
    """
    result: Dict[str, str] = {}
    ref_dir = Path(output_dir) / "referenced_artifacts"
    ref_dir.mkdir(parents=True, exist_ok=True)

    for rec in records:
        if rec.attribution_mode != "COLLATERAL_REVIEWED":
            continue
        src = Path(rec.staged_path)
        thumb_name = src.stem + "_thumb.png"
        thumb_path = ref_dir / thumb_name

        try:
            if _render_thumbnail_file(src, src.suffix.lower(), thumb_path, max_width, rec.filename):
                result[rec.attachment_id] = f"referenced_artifacts/{thumb_name}"
                logger.info("Thumbnail rendered: %s -> %s", rec.filename, thumb_name)
        except Exception as exc:
            logger.warning("Thumbnail rendering failed for %s: %s", rec.filename, exc)

    return result


from .image_extractor import (
    _extract_image_visual,
)


def _extract_document(staged_path: Path) -> Dict[str, Any]:
    """Extracts text from Word, PDF, markdown, or plain text document."""
    from dual_pipeline.document_pipeline_core.content_loader import load_document_content
    try:
        metadata = {}
        text, _, _ = load_document_content(staged_path, metadata=metadata)
        return {"title": staged_path.stem.replace("_", " ").title(), "text": text}
    except Exception as exc:
        logger.warning(f"Document extraction error: {exc}")
    return {"title": staged_path.stem, "text": f"Document: {staged_path.name}"}

def _extract_presentation(staged_path: Path) -> Dict[str, Any]:
    """Extracts slides, notes, and tables from a presentation deck (.pptx)."""
    if staged_path.suffix.lower() == ".pptx":
        from dual_pipeline.document_pipeline_core.content_loader import load_document_content
        try:
            metadata = {}
            text, _, _ = load_document_content(staged_path, metadata=metadata)
            return {
                "title": staged_path.stem.replace("_", " ").title(),
                "text": text,
                "slide_count": text.count("### Slide"),
                "summary": f"Presentation '{staged_path.name}'",
            }
        except Exception as exc:
            logger.warning(f"PPTX extraction error: {exc}")
    return {"title": staged_path.stem, "text": f"Presentation file: {staged_path.name}", "slide_count": 1}


def _extract_spreadsheet(staged_path: Path) -> Dict[str, Any]:
    """Extracts tabular data from CSV or spreadsheet."""
    suffix = staged_path.suffix.lower()
    if suffix in (".csv", ".xlsx", ".xlsm", ".xls"):
        from dual_pipeline.document_pipeline_core.content_loader import load_document_content
        try:
            metadata: dict[str, Any] = {}
            text, _, _ = load_document_content(staged_path, metadata=metadata)
            cov = metadata.get("_source_coverage", {})
            return {
                "title": staged_path.stem.replace("_", " ").title(),
                "text": text,
                "tables": [{"row_count": cov.get("total_records", 0)}],
            }
        except Exception as exc:
            logger.warning(f"Spreadsheet extraction error: {exc}")
    return {"title": staged_path.stem, "text": f"Spreadsheet: {staged_path.name}"}


def _build_image_content(record: AttachmentRecord, mock_mode: bool) -> ExtractedAttachmentContent:
    p = record.staged_path
    res = _extract_image_visual(p, mock_mode=mock_mode)
    is_failed = res.get("status") == "FAILED" or res.get("extraction_status") == "FAILED"
    return ExtractedAttachmentContent(
        attachment_id=record.attachment_id,
        filename=record.filename,
        attachment_type=record.attachment_type,
        sha256=record.sha256,
        title=res.get("title") or p.stem.replace("_", " ").title(),
        extracted_text=res.get("text", ""),
        summary=res.get("summary", f"Visual collateral '{record.filename}'"),
        components=res.get("components", []),
        connections=res.get("connections", []),
        tables=res.get("tables", []),
        claims=res.get("claims", []),
        milestones=res.get("milestones", []),
        has_diagrams=bool(res.get("components") or res.get("connections")),
        extraction_status="FAILED" if is_failed else res.get("extraction_status", "SUCCESS"),
        error_reason=res.get("error_reason") or (res.get("summary") if is_failed else None),
        error_type=res.get("error_type"),
        is_failed=is_failed,
        is_demo_or_sample=res.get("is_demo_or_sample", False),
        raw_payload=res,
    )


def _build_presentation_content(record: AttachmentRecord) -> ExtractedAttachmentContent:
    p = record.staged_path
    res = _extract_presentation(p)
    return ExtractedAttachmentContent(
        attachment_id=record.attachment_id,
        filename=record.filename,
        attachment_type=record.attachment_type,
        sha256=record.sha256,
        title=res.get("title", p.stem.replace("_", " ").title()),
        extracted_text=res.get("text", ""),
        summary=res.get("summary", f"Presentation deck '{record.filename}'"),
        has_diagrams=True,
    )


def _build_spreadsheet_content(record: AttachmentRecord) -> ExtractedAttachmentContent:
    p = record.staged_path
    res = _extract_spreadsheet(p)
    return ExtractedAttachmentContent(
        attachment_id=record.attachment_id,
        filename=record.filename,
        attachment_type=record.attachment_type,
        sha256=record.sha256,
        title=res.get("title", p.stem.replace("_", " ").title()),
        extracted_text=res.get("text", ""),
        tables=res.get("tables", []),
    )


def _build_document_content(record: AttachmentRecord) -> ExtractedAttachmentContent:
    p = record.staged_path
    res = _extract_document(p)
    text_content = res.get("text", "")
    is_diagram = bool(
        re.search(r"([┌┬┐├┼┤└┴┘│─┼]{3,}|[╔╦╗╠╬╣╚╩╝║═]{3,})", text_content)
        or re.search(r"(\+[-=]{3,}\+)", text_content)
        or re.search(r"(--+>|==+>)", text_content)
        or (re.search(r"\bZONE \d+:\b", text_content, re.IGNORECASE) and re.search(r"[│─║═|▲▼◄►]", text_content))
        or re.search(r"[▲▼◄►]", text_content)
    )
    t = AttachmentType.DIAGRAM if is_diagram and record.attachment_type == AttachmentType.DOCUMENT else record.attachment_type
    return ExtractedAttachmentContent(
        attachment_id=record.attachment_id,
        filename=record.filename,
        attachment_type=t,
        sha256=record.sha256,
        title=res.get("title", p.stem.replace("_", " ").title()),
        extracted_text=text_content,
        summary=f"Meeting reference {'architecture diagram / ' if is_diagram else ''}document '{record.filename}'",
        has_diagrams=is_diagram,
    )


def extract_attachment_content(
    record: AttachmentRecord,
    mock_mode: bool = False,
) -> ExtractedAttachmentContent:
    """Extracts structured content payload from a staged attachment record."""
    t = record.attachment_type
    if t in (AttachmentType.IMAGE, AttachmentType.DIAGRAM):
        return _build_image_content(record, mock_mode)
    if t == AttachmentType.PRESENTATION:
        return _build_presentation_content(record)
    if t == AttachmentType.SPREADSHEET:
        return _build_spreadsheet_content(record)
    return _build_document_content(record)


def extract_docx_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_document(Path(path))


def extract_pdf_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_document(Path(path))


def extract_text_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_document(Path(path))


def extract_html_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_document(Path(path))


def extract_eml_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_document(Path(path))


def extract_pptx_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_presentation(Path(path))


def extract_csv_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_spreadsheet(Path(path))


def extract_xlsx_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_spreadsheet(Path(path))


def extract_diagram_attachment(path: Union[str, Path]) -> Dict[str, Any]:
    return _extract_document(Path(path))
