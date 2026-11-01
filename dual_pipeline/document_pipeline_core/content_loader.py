"""Universal multi-format document content loader with SHA-256 digests."""
from __future__ import annotations

import email
import hashlib
import logging
import os
from email.message import Message
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from dual_pipeline.l0.ingestion_timing import (
    SPAN_CONVERSION,
    SPAN_DEPENDENCY_CHECKS,
    SPAN_SOURCE_DISCOVERY,
    timed_child_span,
)

logger = logging.getLogger(__name__)


def _decode_bytes_safely(data: bytes) -> str:
    """Decode raw bytes trying utf-8-sig, utf-16, falling back to latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except Exception:
            return data.decode("utf-8", errors="replace")


from dual_pipeline.document_pipeline_core.eml_loader import (
    extract_eml_body_and_attachments as _extract_eml_body_and_attachments,
    extract_eml_markdown as _extract_eml_markdown,
)


def _load_docx(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    """Extract all DOCX body text with coverage proof; never fall back silently."""
    with timed_child_span(SPAN_DEPENDENCY_CHECKS, {"dependency": "docx_coverage", "file": source_p.name}):
        from dual_pipeline.document_pipeline_core.docx_coverage import extract_docx_with_coverage
    logger.info("Loading DOCX document (%s)", source_p.name)
    with timed_child_span(SPAN_CONVERSION, {"format": "docx", "file": source_p.name}):
        text, coverage = extract_docx_with_coverage(raw_bytes)
        if metadata is not None:
            from dual_pipeline.document_pipeline_core.format_source_units import build_docx_source_units
            source_hash = hashlib.sha256(raw_bytes).hexdigest()
            units = build_docx_source_units(coverage, source_hash)
            metadata["_source_coverage"] = coverage
            metadata["source_coverage"] = {key: value for key, value in coverage.items() if key != "cells"}
            metadata["source_units"] = [u.to_dict() for u in units]
        return text


def _load_pdf(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    """Extract markdown from PDF pages with visual escalation and coverage tracking."""
    logger.info("Loading PDF document (%s)", source_p.name)
    with timed_child_span(SPAN_DEPENDENCY_CHECKS, {"dependency": "pdf_source", "file": source_p.name}):
        from dual_pipeline.pdf_source import inspect_pdf_pages
    with timed_child_span(SPAN_CONVERSION, {"format": "pdf", "file": source_p.name}):
        report = inspect_pdf_pages(raw_bytes, title=source_p.stem.replace("_", " ").title())
        if not any(p.text.strip() for p in report.pages):
            raise ValueError(
                f"PDF source '{source_p.name}' contains no extractable text (scanned or blank). "
                "Governed OCR route is required."
            )
        if metadata is not None:
            from dual_pipeline.document_pipeline_core.format_source_units import (
                build_pdf_source_units,
                generate_document_structural_index,
            )
            source_hash = hashlib.sha256(raw_bytes).hexdigest()
            units = build_pdf_source_units(report, source_hash)
            metadata["_source_coverage"] = {
                "total_records": report.total_pages,
                "processed_records": report.total_pages,
                "pages_count": report.total_pages,
                "text_native_pages": report.text_native_pages,
                "escalated_pages": report.escalated_pages,
                "hitl_pages": report.hitl_pages,
                "complete": True,
            }
            metadata["source_units"] = [u.to_dict() for u in units]
            metadata["document_structural_index"] = generate_document_structural_index(units)
        return report.extracted_markdown


def _load_pptx(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    """Extract markdown from PPTX presentation with visual escalation and coverage tracking."""
    logger.info("Loading PPTX document (%s)", source_p.name)
    with timed_child_span(SPAN_DEPENDENCY_CHECKS, {"dependency": "pptx_source", "file": source_p.name}):
        from dual_pipeline.pptx_source import extract_pptx_presentation
    with timed_child_span(SPAN_CONVERSION, {"format": "pptx", "file": source_p.name}):
        pres = extract_pptx_presentation(raw_bytes)
        if metadata is not None:
            from dual_pipeline.document_pipeline_core.format_source_units import (
                build_pptx_source_units,
                generate_document_structural_index,
            )
            source_hash = hashlib.sha256(raw_bytes).hexdigest()
            units = build_pptx_source_units(pres, source_hash)
            metadata["_source_coverage"] = {
                "total_records": pres.slide_count,
                "processed_records": pres.slide_count,
                "slides_count": pres.slide_count,
                "text_native_slides": pres.text_native_slides,
                "escalated_slides": pres.escalated_slides,
                "hitl_slides": pres.hitl_slides,
                "complete": True,
            }
            metadata["source_units"] = [u.to_dict() for u in units]
            metadata["document_structural_index"] = generate_document_structural_index(units)
        return pres.extracted_markdown


def _load_xlsx(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    """Extract XLSX workbook supporting multi-sheet coverage, cell locators, and formula provenance."""
    from dual_pipeline.document_pipeline_core.xlsx_projection import project_xlsx_workbook

    with timed_child_span(SPAN_CONVERSION, {"format": "xlsx", "file": source_p.name}):
        try:
            projection = project_xlsx_workbook(raw_bytes, filename=source_p.name, metadata=metadata)
            return projection.rendered_markdown
        except Exception as e:
            raise ValueError(f"Failed to parse valid XLSX workbook for '{source_p.name}': {e}") from e



def _load_csv(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    import csv
    import io
    from dual_pipeline.document_pipeline_core.format_source_units import build_csv_tsv_source_units

    text = _decode_bytes_safely(raw_bytes)
    ext = source_p.suffix.lower()
    delimiter = "\t" if ext == ".tsv" else ("," if "\t" not in text[:1000] else "\t")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return ""

    header = rows[0]
    data_rows = rows[1:]
    lines = ["| Row | " + " | ".join(header) + " |", "| --- | " + " | ".join(["---"] * len(header)) + " |"]
    for idx, r in enumerate(data_rows, start=1):
        lines.append(f"| R{idx} | " + " | ".join(r) + " |")

    if metadata is not None:
        source_hash = hashlib.sha256(raw_bytes).hexdigest()
        fmt = "tsv" if ext == ".tsv" else "csv"
        units = build_csv_tsv_source_units(data_rows, delimiter, header, source_hash, fmt)
        metadata["_source_coverage"] = {
            "total_records": len(data_rows),
            "processed_records": len(data_rows),
            "delimiter": delimiter,
            "header": list(header),
            "columns": list(header),
            "complete": True,
        }
        metadata["source_units"] = [u.to_dict() for u in units]
    return "\n".join(lines)


def _load_html(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    from dual_pipeline.document_pipeline_core.format_source_units import build_html_source_units
    from dual_pipeline.html_source import extract_html_markdown

    text = extract_html_markdown(raw_bytes)
    if metadata is not None:
        source_hash = hashlib.sha256(raw_bytes).hexdigest()
        units = build_html_source_units(raw_bytes, source_hash)
        metadata["_source_coverage"] = {
            "total_blocks": len(units),
            "processed_records": len(units),
            "dom_blocks": len(units),
            "links_count": sum(len(u.metadata.get("links", [])) for u in units),
            "complete": True,
        }
        metadata["source_units"] = [u.to_dict() for u in units]
    return text


def _load_eml(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    from dual_pipeline.document_pipeline_core.format_source_units import build_eml_source_units

    text = _extract_eml_markdown(raw_bytes)
    if metadata is not None:
        source_hash = hashlib.sha256(raw_bytes).hexdigest()
        units = build_eml_source_units(raw_bytes, source_hash)
        metadata["_source_coverage"] = {
            "total_units": len(units),
            "processed_records": len(units),
            "header_units": len([u for u in units if u.unit_type == "email_header"]),
            "body_units": len([u for u in units if u.unit_type == "email_body"]),
            "attachment_units": len([u for u in units if u.unit_type == "email_attachment"]),
            "attachments": [u.metadata.get("filename") for u in units if u.unit_type == "email_attachment"],
            "complete": True,
        }
        metadata["source_units"] = [u.to_dict() for u in units]
    return text


_IMAGE_OCR_CACHE: dict[str, dict[str, Any]] = {}


def clear_image_ocr_cache() -> None:
    """Clear in-memory OCR cache (useful for test isolation)."""
    _IMAGE_OCR_CACHE.clear()


def _validate_image_bytes(raw_bytes: bytes, filename: str) -> None:
    """Preflight image payload and fail fast on corrupt or empty images."""
    from dual_pipeline.exceptions import SourceIntegrityError
    if not raw_bytes:
        raise SourceIntegrityError(f"Image payload for '{filename}' is empty (0 bytes).")
    import io
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise SourceIntegrityError(f"Corrupt or invalid image file '{filename}': {exc}") from exc


def _extract_image_bytes(source_p: Path, raw_bytes: bytes) -> dict[str, Any]:
    from dual_pipeline.attachment_core.image_extractor import extract_image_visual
    import tempfile

    _validate_image_bytes(raw_bytes, source_p.name)

    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest in _IMAGE_OCR_CACHE:
        return _IMAGE_OCR_CACHE[digest]

    if source_p.exists() and source_p.is_file():
        res = extract_image_visual(source_p)
    else:
        ext = source_p.suffix.lower() or ".png"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = Path(tmp.name)
        try:
            res = extract_image_visual(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    _IMAGE_OCR_CACHE[digest] = res
    return res


def _populate_visual_metadata(
    metadata: dict[str, Any] | None, res: dict[str, Any], text: str, stem: str
) -> None:
    if metadata is None:
        return
    comps = res.get("components") or []
    comp_count = max(1, len(comps))
    metadata["_source_coverage"] = {
        "total_records": comp_count,
        "processed_records": comp_count,
        "complete": True,
        "ocr_text_length": len(text),
        "visual_components": len(comps),
    }
    metadata["visual_extraction"] = {
        "title": res.get("title") or stem,
        "components": comps,
        "connections": res.get("connections") or [],
        "summary": res.get("summary", ""),
    }


def _load_image_document(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None) -> str:
    res = _extract_image_bytes(source_p, raw_bytes)
    if not res or res.get("status") == "FAILED" or res.get("extraction_status") == "FAILED":
        reason = res.get("error_reason", "Extraction failure") if res else "No response"
        raise ValueError(f"Failed to extract visual content from image '{source_p.name}': {reason}")
    text = (res.get("text") or res.get("summary") or f"Visual document: {source_p.stem}").strip()
    _populate_visual_metadata(metadata, res, text, source_p.stem)
    if metadata is not None and text:
        from dual_pipeline.document_pipeline_core.format_source_units import build_text_source_units
        units = build_text_source_units(text, hashlib.sha256(raw_bytes).hexdigest(), source_p.suffix.lstrip(".").lower() or "png")
        metadata["source_units"] = [u.to_dict() for u in units]
    return text


def _is_visual_image_enabled(metadata: dict[str, Any] | None) -> bool:
    if not metadata:
        return False
    for k in ("enable_visual_ocr", "allow_visual_image", "is_visual_document", "visual_document"):
        if metadata.get(k):
            return True
    return metadata.get("intake_mode") == "visual"


def _handle_image_dispatch(
    ext: str, source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None
) -> Tuple[str, str]:
    if _is_visual_image_enabled(metadata):
        fmt = ext.lstrip(".").lower()
        return _load_image_document(source_p, raw_bytes, metadata), ("jpg" if fmt == "jpeg" else fmt)
    raise ValueError(
        f"Binary image format '{ext}' cannot be ingested as text document. "
        "Architecture diagrams and visual assets must be ingested via architecture diagram "
        "intake packages (urn:nee:dual-pipeline:architecture-diagram-intake:1.0.0) or MultimodalSlideExtractor."
    )


def _load_text_document(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None, fmt: str) -> str:
    from dual_pipeline.document_pipeline_core.format_source_units import (
        build_text_source_units,
        generate_document_structural_index,
    )
    from dual_pipeline.document_pipeline_core.unit_decomposition import extract_slide_and_section_units
    text = _decode_bytes_safely(raw_bytes)
    if metadata is not None:
        source_hash = hashlib.sha256(raw_bytes).hexdigest()
        units = build_text_source_units(text, source_hash, fmt)
        slide_units = extract_slide_and_section_units(text, source_hash, fmt)
        metadata["_source_coverage"] = {
            "total_records": len(units),
            "processed_records": len(units),
            "lines_count": len(units),
            "units_count": len(units),
            "slides_count": len([u for u in slide_units if u.unit_type == "slide"]),
            "headings_count": len([u for u in units if u.unit_type == "heading"]),
            "code_blocks_count": len([u for u in units if u.unit_type == "code_block"]),
            "tables_count": len([u for u in units if u.unit_type == "table_block"]),
            "list_items_count": len([u for u in units if u.unit_type == "list_item"]),
            "has_front_matter": any(u.unit_type == "front_matter" for u in units),
            "complete": True,
        }
        metadata["source_units"] = [u.to_dict() for u in units]
        if slide_units:
            metadata["slide_units"] = [u.to_dict() for u in slide_units]
            metadata["document_structural_index"] = generate_document_structural_index(slide_units)
    return text



def _load_collateral_vtt(
    source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None
) -> str:
    """Extract clean text cues from a collateral .vtt or .srt document attachment."""
    from dual_pipeline.document_pipeline_core.format_source_units import build_text_source_units
    import re
    text = _decode_bytes_safely(raw_bytes)
    clean_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("WEBVTT") or "-->" in s or s.isdigit():
            continue
        clean_s = re.sub(r"</?v(?:\s+[^>]+)?>", "", s)
        clean_s = re.sub(r"<?</?v\b", "", clean_s).strip()
        if clean_s:
            clean_lines.append(clean_s)
    clean_text = "\n".join(clean_lines)

    if metadata is not None:
        source_hash = hashlib.sha256(raw_bytes).hexdigest()
        units = build_text_source_units(clean_text, source_hash, "vtt_collateral")
        metadata["_source_coverage"] = {
            "total_records": len(units),
            "processed_records": len(units),
            "lines_count": len(clean_lines),
            "units_count": len(units),
            "complete": True,
            "is_collateral_transcript": True,
        }
        metadata["source_units"] = [u.to_dict() for u in units]
        metadata["is_collateral_attachment"] = True

    return clean_text


def _load_json(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None) -> str:
    from dual_pipeline.document_pipeline_core.structured_data_loader import extract_json_with_locators
    return extract_json_with_locators(source_p, raw_bytes, metadata)


def _load_diagram(source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None, ext: str) -> str:
    from dual_pipeline.document_pipeline_core.diagram_loader import extract_diagram_with_locators
    return extract_diagram_with_locators(source_p, raw_bytes, metadata, ext=ext)


_DOC_EXT_HANDLERS = {
    ".docx": lambda p, b, m: (_load_docx(p, b, m), "docx"),
    ".pdf": lambda p, b, m: (_load_pdf(p, b, m), "pdf"),
    ".pptx": lambda p, b, m: (_load_pptx(p, b, m), "pptx"),
    ".html": lambda p, b, m: (_load_html(p, b, m), "html"),
    ".htm": lambda p, b, m: (_load_html(p, b, m), "html"),
    ".eml": lambda p, b, m: (_load_eml(p, b, m), "eml"),
    ".md": lambda p, b, m: (_load_text_document(p, b, m, "md"), "md"),
    ".markdown": lambda p, b, m: (_load_text_document(p, b, m, "md"), "md"),
    ".json": lambda p, b, m: (_load_json(p, b, m), "json"),
    ".xlsx": lambda p, b, m: (_load_xlsx(p, b, m), "xlsx"),
    ".xlsm": lambda p, b, m: (_load_xlsx(p, b, m), "xlsx"),
    ".csv": lambda p, b, m: (_load_csv(p, b, m), "csv"),
    ".tsv": lambda p, b, m: (_load_csv(p, b, m), "tsv"),
    ".txt": lambda p, b, m: (_load_text_document(p, b, m, "txt"), "txt"),
    ".text": lambda p, b, m: (_load_text_document(p, b, m, "txt"), "txt"),
    ".svg": lambda p, b, m: (_load_diagram(p, b, m, ".svg"), "svg"),
    ".drawio": lambda p, b, m: (_load_diagram(p, b, m, ".drawio"), "drawio"),
    ".vsdx": lambda p, b, m: (_load_diagram(p, b, m, ".vsdx"), "vsdx"),
}


def _dispatch_file_loader(
    ext: str, source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None = None
) -> Tuple[str, str]:
    """Dispatch file content extraction based on file extension."""
    from dual_pipeline.source_classification.capability_registry import get_image_extensions
    if ext in get_image_extensions():
        return _handle_image_dispatch(ext, source_p, raw_bytes, metadata)

    is_collateral = bool(metadata.get("is_collateral") or metadata.get("allow_collateral_transcript")) if metadata else False

    from dual_pipeline.document_pipeline_core.format_source_units import preflight_attachment
    pf = preflight_attachment(raw_bytes, source_p.name, is_collateral=is_collateral)
    if not pf.is_valid:
        raise ValueError(f"Attachment preflight failed for '{source_p.name}': {pf.error_code} - {pf.error_message}")

    if is_collateral and ext in {".vtt", ".srt"}:
        return _load_collateral_vtt(source_p, raw_bytes, metadata), ext.lstrip(".")

    handler = _DOC_EXT_HANDLERS.get(ext)
    if handler:
        return handler(source_p, raw_bytes, metadata)
    raise ValueError(f"Unknown document extension '{ext}'. Only explicit formats are allowed. To inspect supported formats, run: nee formats")


def _normalize_format_id(ext: str) -> str:
    clean = ext.lstrip(".").lower()
    if clean in {"markdown", "md"}:
        return "md"
    if clean in {"xlsm", "xlsx"}:
        return "xlsx"
    if clean in {"htm", "html"}:
        return "html"
    if clean in {"jpeg", "jpg"}:
        return "jpg"
    if clean in {"text", "txt"}:
        return "txt"
    return clean


def _restore_cached_metadata(
    metadata: dict[str, Any] | None, record: Any, was_cached: bool, cache_key: str
) -> None:
    if metadata is None or record is None:
        return
    metadata["conversion_artifact"] = {
        "cache_key": cache_key,
        "artifact_path": record.artifact_path,
        "was_cached": was_cached,
    }
    if not was_cached:
        if record.coverage_proof and "source_coverage" not in metadata:
            metadata["source_coverage"] = {k: v for k, v in record.coverage_proof.items() if k != "cells"}
        return
    if record.coverage_proof:
        metadata["_source_coverage"] = record.coverage_proof
        metadata["source_coverage"] = {k: v for k, v in record.coverage_proof.items() if k != "cells"}
        if record.coverage_proof.get("is_collateral_transcript"):
            metadata["is_collateral_attachment"] = True
    if record.source_units:
        metadata["source_units"] = record.source_units
        from dual_pipeline.document_pipeline_core.format_source_units import generate_document_structural_index
        metadata["document_structural_index"] = generate_document_structural_index(record.source_units)
    if getattr(record, "slide_units", None):
        metadata["slide_units"] = record.slide_units
        from dual_pipeline.document_pipeline_core.format_source_units import generate_document_structural_index
        metadata["document_structural_index"] = generate_document_structural_index(record.slide_units)
    if getattr(record, "visual_extraction", None):
        metadata["visual_extraction"] = record.visual_extraction


def _execute_cached_loader(
    ext: str, source_p: Path, raw_bytes: bytes, metadata: dict[str, Any] | None
) -> Tuple[str, Optional[dict[str, Any]], Optional[list[dict[str, Any]]], str, Optional[list[dict[str, Any]]], Optional[dict[str, Any]]]:
    eff_meta: dict[str, Any] = metadata if metadata is not None else {}
    res = _dispatch_file_loader(ext, source_p, raw_bytes, eff_meta)
    txt = res[0] if isinstance(res, tuple) else res
    fmt = res[1] if isinstance(res, tuple) and len(res) > 1 else _normalize_format_id(ext)
    proof = eff_meta.get("_source_coverage")
    units = eff_meta.get("source_units")
    slide_units = eff_meta.get("slide_units")
    visual_ext = eff_meta.get("visual_extraction")
    return txt, proof, units, fmt, slide_units, visual_ext


def _load_document_file(
    source_p: Path,
    *,
    metadata: dict[str, Any] | None = None,
    cache_dir: Optional[Union[str, Path]] = None,
    force_recompute: bool = False,
) -> Tuple[str, str, str]:
    MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
    if source_p.stat().st_size == 0:
        from dual_pipeline.exceptions import SourceIntegrityError
        raise SourceIntegrityError(f"Input source file is empty (0 bytes): {source_p.name}")
    if source_p.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError(f"Document {source_p.name} exceeds {MAX_DOCUMENT_BYTES} bytes limit.")
    with open(source_p, "rb") as f:
        raw_bytes = f.read()
    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    ext = source_p.suffix.lower()
    norm_fmt = _normalize_format_id(ext)
    from dual_pipeline.document_pipeline_core.conversion_cache import (
        resolve_conversion_with_cache,
        resolve_default_conversion_cache_dir,
    )
    eff_force_recompute = force_recompute or bool(
        metadata and (metadata.get("no_cache") or metadata.get("force_recompute"))
    )
    eff_cache_dir = (
        None
        if (metadata is None and cache_dir is None)
        else resolve_default_conversion_cache_dir(metadata=metadata, explicit_cache_dir=cache_dir)
    )
    is_collateral = bool(metadata.get("is_collateral") or metadata.get("allow_collateral_transcript")) if metadata else False
    if eff_cache_dir and not (ext in {".vtt", ".srt"} and not is_collateral):
        text, cache_key, was_cached, record = resolve_conversion_with_cache(
            source_p=source_p,
            raw_bytes=raw_bytes,
            format_name=norm_fmt,
            loader_fn=lambda: _execute_cached_loader(ext, source_p, raw_bytes, metadata),
            cache_dir=eff_cache_dir,
            force_recompute=eff_force_recompute,
            metadata=metadata,
        )
        _restore_cached_metadata(metadata, record, was_cached, cache_key)
        ret_fmt = record.format if (record and record.format) else norm_fmt
        return text, source_hash, ret_fmt

    text, fmt = _dispatch_file_loader(ext, source_p, raw_bytes, metadata)
    if metadata is not None:
        metadata.setdefault("conversion_artifact", {
            "cache_key": "",
            "artifact_path": None,
            "was_cached": False,
        })
    return text, source_hash, fmt


def load_document_content(
    input_source: Union[str, Path],
    *,
    metadata: dict[str, Any] | None = None,
    cache_dir: Optional[Union[str, Path]] = None,
    force_recompute: bool = False,
) -> Tuple[str, str, str]:
    """Load document content from path or string paste and return (markdown, sha256, format)."""
    with timed_child_span(SPAN_SOURCE_DISCOVERY, {"input_source": str(input_source)[:200]}):
        if isinstance(input_source, Path) or (
            isinstance(input_source, str) and os.path.isfile(str(input_source))
        ):
            source_p = Path(input_source).resolve()
            return _load_document_file(
                source_p,
                metadata=metadata,
                cache_dir=cache_dir,
                force_recompute=force_recompute,
            )

        raw_text = str(input_source)
        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        return raw_text, raw_hash, "text_paste"


def extract_document_text_snippet(
    input_source: Union[str, Path],
    max_chars: int = 5000,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Extract a fast text snippet from any supported document format for metadata inference."""
    try:
        text, _, _ = load_document_content(input_source, metadata=metadata)
        return text[:max_chars]
    except Exception as exc:
        logger.debug("Failed to extract document text snippet from %s: %s", input_source, exc)
        return ""


def _read_source_payload(source: Union[str, Path, bytes], name: str) -> tuple[Path, bytes]:
    return (Path(name), source) if isinstance(source, bytes) else (Path(source), Path(source).read_bytes())


def _extract_docx(source: Union[str, Path, bytes]) -> str:
    p, b = _read_source_payload(source, "document.docx")
    return _load_docx(p, b)

def _extract_pdf(source: Union[str, Path, bytes]) -> str:
    p, b = _read_source_payload(source, "document.pdf")
    return _load_pdf(p, b)

def _extract_xlsx(source: Union[str, Path, bytes]) -> str:
    p, b = _read_source_payload(source, "workbook.xlsx")
    return _load_xlsx(p, b)

def _extract_csv(source: Union[str, Path, bytes]) -> str:
    p, b = _read_source_payload(source, "data.csv")
    return _load_csv(p, b)

def _extract_eml(source: Union[str, Path, bytes]) -> str:
    _, b = _read_source_payload(source, "message.eml")
    return _extract_eml_markdown(b)

def _extract_pptx(source: Union[str, Path, bytes]) -> str:
    _, b = _read_source_payload(source, "presentation.pptx")
    from dual_pipeline.pptx_source import extract_pptx_markdown
    return extract_pptx_markdown(b)

def _extract_html(source: Union[str, Path, bytes]) -> str:
    _, b = _read_source_payload(source, "page.html")
    from dual_pipeline.html_source import extract_html_markdown
    return extract_html_markdown(b)
