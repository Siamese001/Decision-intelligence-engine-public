"""
dual_pipeline/document_pipeline_core/attachment_preflight.py

Deterministic Preflight Validation for Document Attachments.
Enforces magic header validation, OOXML zip container checks, legacy format blocking,
audio/cue isolation, and binary-in-text fail-closed detection before extraction.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Optional

from dual_pipeline.document_pipeline_core.format_source_units import (
    FailureDisposition,
    PreflightResult,
)

from dual_pipeline.source_classification.capability_registry import (
    get_audio_extensions,
    get_blocked_extensions,
    get_cue_extensions,
)

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PE_EXE_MAGIC = b"MZ"
_ELF_MAGIC = b"\x7fELF"
_MACH_O_MAGICS = (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe")

_AUDIO_EXTENSIONS = get_audio_extensions()
_TRANSCRIPT_CUES = get_cue_extensions()
_LEGACY_BLOCKED = get_blocked_extensions()


def _is_zip_valid(raw_bytes: bytes, required_marker: str) -> bool:
    if not raw_bytes.startswith(_ZIP_MAGIC):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            return any(name.startswith(required_marker) for name in z.namelist())
    except Exception:
        return False


def _check_text_payload(raw_bytes: bytes) -> Optional[str]:
    if b"\x00" in raw_bytes[:4096]:
        return "BINARY_NUL_BYTES"
    if raw_bytes.startswith((_PE_EXE_MAGIC, _ELF_MAGIC) + _MACH_O_MAGICS):
        return "BINARY_EXECUTABLE_SIGNATURE"
    try:
        raw_bytes.decode("utf-8")
        return None
    except UnicodeDecodeError:
        pass
    try:
        raw_bytes.decode("latin-1")
        return None
    except Exception:
        return "NON_DECODABLE_TEXT"


def _preflight_legacy(ext: str, raw_bytes: bytes) -> Optional[PreflightResult]:
    is_ole = raw_bytes.startswith(_OLE_MAGIC)
    if ext == ".ppt" or (is_ole and ext in {".ppt", ".pptm"}):
        cmd = "nee formats"
        return PreflightResult(
            is_valid=False, format_id="PPT_LEGACY", disposition=FailureDisposition.BLOCKED,
            error_code="LEGACY_PPT_BLOCKED", is_legacy=True,
            error_message="Legacy .ppt format is blocked. Convert to modern .pptx. To inspect supported formats, run: nee formats",
            remediation_command=cmd,
        )
    if ext == ".xls" or (is_ole and ext in {".xls", ".xla"}):
        cmd = "nee formats"
        return PreflightResult(
            is_valid=False, format_id="XLS_LEGACY", disposition=FailureDisposition.BLOCKED,
            error_code="LEGACY_XLS_BLOCKED", is_legacy=True,
            error_message="Legacy .xls format is blocked. Convert to modern .xlsx or .csv. To inspect supported formats, run: nee formats",
            remediation_command=cmd,
        )
    if ext == ".msg" or (is_ole and ext == ".msg"):
        cmd = "nee formats"
        return PreflightResult(
            is_valid=False, format_id="MSG_LEGACY", disposition=FailureDisposition.BLOCKED,
            error_code="LEGACY_MSG_BLOCKED", is_legacy=True,
            error_message="Legacy Outlook .msg format is blocked. Export or convert to standard .eml. To inspect supported formats, run: nee formats",
            remediation_command=cmd,
        )
    if is_ole:
        cmd = "nee formats"
        return PreflightResult(
            is_valid=False, format_id="LEGACY_BINARY_OLE", disposition=FailureDisposition.BLOCKED,
            error_code="LEGACY_BINARY_OLE_BLOCKED", is_legacy=True,
            error_message=f"Legacy binary OLE container '{ext}' is blocked. Convert to modern OpenXML formats. To inspect supported formats, run: nee formats",
            remediation_command=cmd,
        )
    return None



def _preflight_excluded(ext: str, filename: str = "") -> Optional[PreflightResult]:
    target_name = filename or f"<file{ext}>"
    if ext in _AUDIO_EXTENSIONS:
        cmd = f"nee ingest-meeting --input {target_name}"
        return PreflightResult(
            is_valid=False, format_id="AUDIO", disposition=FailureDisposition.BLOCKED,
            error_code="AUDIO_TRANSCRIPT_ROUTE_EXCLUDED", is_excluded_from_attachment=True,
            error_message=f"Audio format '{ext}' must be ingested via acoustic transcription route. Run: {cmd}",
            remediation_command=cmd,
        )
    if ext in _TRANSCRIPT_CUES:
        cmd = f"nee ingest-meeting --input {target_name}"
        return PreflightResult(
            is_valid=False, format_id="VTT_SRT", disposition=FailureDisposition.BLOCKED,
            error_code="AUDIO_TRANSCRIPT_ROUTE_EXCLUDED", is_excluded_from_attachment=True,
            error_message=f"Transcript cue format '{ext}' is excluded from document route. Run: {cmd}",
            remediation_command=cmd,
        )
    return None


def _preflight_ooxml(ext: str, raw_bytes: bytes) -> PreflightResult:
    marker_map = {".docx": ("word/", "DOCX"), ".pptx": ("ppt/", "PPTX"), ".vsdx": ("visio/", "VSDX")}
    marker, code = marker_map.get(ext, ("xl/", "XLSX"))
    fmt_id = "XLSX_WORKBOOK" if ext in {".xlsx", ".xlsm"} else code
    if not _is_zip_valid(raw_bytes, marker):
        return PreflightResult(
            is_valid=False, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED,
            error_code=f"MALFORMED_{code}_CONTAINER",
            error_message=f"Malformed or corrupt {fmt_id} container: missing OOXML zip structure.",
        )
    return PreflightResult(is_valid=True, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED)


def _preflight_json(raw_bytes: bytes) -> PreflightResult:
    import json
    err = _check_text_payload(raw_bytes)
    if err:
        return PreflightResult(
            is_valid=False, format_id="JSON", disposition=FailureDisposition.FAIL_CLOSED,
            error_code="BINARY_PAYLOAD_IN_JSON_ROUTE", error_message=f"JSON rejected binary payload: {err}.",
        )
    try:
        data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    except Exception as exc:
        return PreflightResult(
            is_valid=False, format_id="JSON", disposition=FailureDisposition.FAIL_CLOSED,
            error_code="MALFORMED_JSON_SYNTAX", error_message=f"Malformed JSON syntax: {exc}",
        )
    from dual_pipeline.document_pipeline_core.structured_data_loader import _check_nesting_depth
    try:
        _check_nesting_depth(data)
    except Exception as exc:
        return PreflightResult(
            is_valid=False, format_id="JSON", disposition=FailureDisposition.FAIL_CLOSED,
            error_code="JSON_DEPTH_EXCEEDED", error_message=str(exc),
        )
    return PreflightResult(is_valid=True, format_id="JSON", disposition=FailureDisposition.FAIL_CLOSED)


def _preflight_diagram_xml(ext: str, raw_bytes: bytes) -> PreflightResult:
    import xml.etree.ElementTree as ET
    fmt_id = "SVG" if ext == ".svg" else "DRAWIO"
    from dual_pipeline.document_pipeline_core.diagram_loader import _assert_safe_xml_bytes, _check_element_depth
    try:
        _assert_safe_xml_bytes(raw_bytes, fmt_id)
    except Exception as exc:
        return PreflightResult(
            is_valid=False, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED,
            error_code=f"MALICIOUS_{fmt_id}_XXE_PROHIBITED", error_message=str(exc),
        )
    try:
        root = ET.fromstring(raw_bytes)
        _check_element_depth(root)
    except Exception as exc:
        return PreflightResult(
            is_valid=False, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED,
            error_code=f"MALFORMED_{fmt_id}_XML", error_message=f"Malformed {fmt_id} XML structure: {exc}",
        )
    return PreflightResult(is_valid=True, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED)


def _preflight_pdf(raw_bytes: bytes) -> PreflightResult:
    if not raw_bytes.startswith(_PDF_MAGIC):
        return PreflightResult(
            is_valid=False, format_id="PDF", disposition=FailureDisposition.FAIL_CLOSED,
            error_code="MALFORMED_PDF_HEADER", error_message="Malformed PDF: missing '%PDF-' header signature.",
        )
    return PreflightResult(is_valid=True, format_id="PDF", disposition=FailureDisposition.FAIL_CLOSED)


def _preflight_text_family(raw_bytes: bytes, fmt_id: str) -> PreflightResult:
    err = _check_text_payload(raw_bytes)
    if err and not (fmt_id == "EML" and err == "NON_DECODABLE_TEXT"):
        prefix = "HTML" if fmt_id == "HTML_HTM" else fmt_id
        code = "BINARY_PAYLOAD_IN_TEXT_ROUTE" if fmt_id == "TXT_MD" else f"MALFORMED_{prefix}_{err}"
        return PreflightResult(
            is_valid=False, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED,
            error_code=code, error_message=f"Text-family {fmt_id} rejected binary payload: {err}.",
        )
    return PreflightResult(is_valid=True, format_id=fmt_id, disposition=FailureDisposition.FAIL_CLOSED)


def _preflight_by_ext(ext: str, raw_bytes: bytes) -> Optional[PreflightResult]:
    if ext in {".docx", ".xlsx", ".xlsm", ".pptx", ".vsdx"}:
        return _preflight_ooxml(ext, raw_bytes)
    if ext == ".pdf":
        return _preflight_pdf(raw_bytes)
    if ext in {".csv", ".tsv"}:
        return _preflight_text_family(raw_bytes, "CSV_TSV")
    if ext in {".html", ".htm"}:
        return _preflight_text_family(raw_bytes, "HTML_HTM")
    if ext == ".eml":
        return _preflight_text_family(raw_bytes, "EML")
    if ext in {".txt", ".text", ".md", ".markdown"}:
        return _preflight_text_family(raw_bytes, "TXT_MD")
    if ext == ".json":
        return _preflight_json(raw_bytes)
    if ext in {".svg", ".drawio"}:
        return _preflight_diagram_xml(ext, raw_bytes)
    return None


def preflight_attachment(raw_bytes: bytes, filename: str = "", is_collateral: bool = False) -> PreflightResult:
    """Deterministic preflight validator for document attachments."""
    if not raw_bytes:
        return PreflightResult(
            is_valid=False, format_id="EMPTY", disposition=FailureDisposition.FAIL_CLOSED,
            error_code="EMPTY_PAYLOAD", error_message="Attachment payload is empty (0 bytes).",
        )
    ext = Path(filename).suffix.lower() if filename else ""
    if is_collateral and ext in _TRANSCRIPT_CUES:
        err = _check_text_payload(raw_bytes)
        if err:
            return PreflightResult(
                is_valid=False, format_id="VTT_SRT", disposition=FailureDisposition.FAIL_CLOSED,
                error_code="MALFORMED_VTT_SRT_PAYLOAD",
                error_message=f"Collateral transcript cue '{filename}' rejected non-text payload: {err}.",
            )
        return PreflightResult(is_valid=True, format_id="VTT_SRT", disposition=FailureDisposition.FAIL_CLOSED)

    early = _preflight_legacy(ext, raw_bytes) or _preflight_excluded(ext, filename)
    if early:
        return early
    res = _preflight_by_ext(ext, raw_bytes)
    if res:
        return res
    return PreflightResult(
        is_valid=False, format_id="UNSUPPORTED", disposition=FailureDisposition.BLOCKED,
        error_code="UNSUPPORTED_BINARY_FORMAT",
        error_message=f"Format '{ext}' is not supported by the document ingestion pipeline. To inspect supported formats, run: nee formats",
        remediation_command="nee formats",
    )
