"""dual_pipeline/source_classification/capability_registry.py

Single Source of Truth (SSOT) for format capabilities, extraction adapters,
and support statuses across the NEE ingestion surface.
Grounded in the Output 3 format matrix and governed delivery contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class SupportStatus(str, Enum):
    """Governed support status for an ingestion format or container."""
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class FormatCapability:
    """Declared capabilities, adapters, and readiness status for a container format."""
    format_id: str
    name: str
    extensions: List[str]
    mime_types: List[str]
    primary_adapter: str
    attachment_adapter: str
    status: SupportStatus
    capabilities: List[str]
    notes: str = ""
    unit_type: str = ""
    unit_types: List[str] = field(default_factory=list)
    locator_grammar: str = ""
    visual_escalation_rule: str = "NEVER"
    preflight_rule: str = ""
    failure_disposition: str = "FAIL_CLOSED"
    is_attachment_supported: bool = True
    is_collateral_supported: bool = False
    converter_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format_id": self.format_id,
            "name": self.name,
            "extensions": list(self.extensions),
            "mime_types": list(self.mime_types),
            "primary_adapter": self.primary_adapter,
            "attachment_adapter": self.attachment_adapter,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
            "notes": self.notes,
            "unit_type": self.unit_type,
            "unit_types": list(self.unit_types),
            "locator_grammar": self.locator_grammar,
            "visual_escalation_rule": self.visual_escalation_rule,
            "preflight_rule": self.preflight_rule,
            "failure_disposition": self.failure_disposition,
            "is_attachment_supported": self.is_attachment_supported,
            "is_collateral_supported": self.is_collateral_supported,
            "converter_version": self.converter_version,
        }


# Canonical Format Registry
_REGISTRY: Dict[str, FormatCapability] = {
    "AUDIO": FormatCapability(
        format_id="AUDIO", name="Audio Speech Recording",
        extensions=[".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".m4v", ".mp4"],
        mime_types=["audio/wav", "audio/mpeg", "audio/mp4", "audio/flac", "audio/ogg"],
        primary_adapter="dual_pipeline.audio_ingestion", attachment_adapter="none",
        status=SupportStatus.SUPPORTED, capabilities=["acoustic_stt", "diarization", "speaker_mapping"],
        notes="Physical audio capture and MLX Whisper transcription running 100% locally.",
        unit_type="pcm_frame", unit_types=["pcm_frame"], locator_grammar="timestamp_ms",
        visual_escalation_rule="NEVER", preflight_rule="riff_wave_or_audio_container",
        failure_disposition="BLOCKED", is_attachment_supported=False,
    ),
    "DOCX": FormatCapability(
        format_id="DOCX", name="Word Document", extensions=[".docx"],
        mime_types=["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._extract_docx",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_docx_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["paragraphs", "tables", "metadata", "cell_coverage"],
        notes="Supports both document body/tables and meeting transcript text.",
        unit_type="paragraph_table", unit_types=["paragraph", "table_cell"],
        locator_grammar="p{paragraph_index}, tbl{table_index}!R{row}C{col}",
        visual_escalation_rule="NEVER", preflight_rule="ooxml_zip_container",
        failure_disposition="FAIL_CLOSED", is_attachment_supported=True, converter_version="docx-coverage-v1.1.0",
    ),
    "VTT_SRT": FormatCapability(
        format_id="VTT_SRT", name="Transcript Cues (VTT/SRT)", extensions=[".vtt", ".srt"],
        mime_types=["text/vtt", "application/x-subrip"],
        primary_adapter="dual_pipeline.transcript_pipeline_core.format_parsers",
        attachment_adapter="dual_pipeline.document_pipeline_core.content_loader._load_collateral_vtt",
        status=SupportStatus.SUPPORTED, capabilities=["cues", "timestamps", "speaker_tags", "collateral_transcript"],
        notes="Preserves cue boundaries and timing precision. Supports collateral text extraction.",
        unit_type="cue", unit_types=["cue"], locator_grammar="cue{index}@timestamp",
        visual_escalation_rule="NEVER", preflight_rule="vtt_srt_header",
        failure_disposition="BLOCKED", is_attachment_supported=False, is_collateral_supported=True,
    ),
    "TXT_MD": FormatCapability(
        format_id="TXT_MD", name="Plain Text and Markdown",
        extensions=[".txt", ".md", ".text", ".markdown"], mime_types=["text/plain", "text/markdown"],
        primary_adapter="dual_pipeline.canonical_pipeline.content_loader",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_text_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["text", "headings", "lists", "code_blocks"],
        notes="Direct intake with UTF-8 normalization.",
        unit_type="line_block", unit_types=["line_block", "front_matter", "heading", "list_item", "table_block", "code_block"],
        locator_grammar="L{line_number}", visual_escalation_rule="NEVER",
        preflight_rule="clean_text_no_nul", failure_disposition="FAIL_CLOSED",
        is_attachment_supported=True, converter_version="text-passthrough-v1.0.0",
    ),
    "JSON": FormatCapability(
        format_id="JSON", name="JavaScript Object Notation", extensions=[".json"],
        mime_types=["application/json"],
        primary_adapter="dual_pipeline.document_pipeline_core.structured_data_loader.load_json_data",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_text_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["text", "key_value", "structured_records", "field_paths"],
        notes="Structured JSON intake with JSONPath field-path locators.",
        unit_type="field_path", unit_types=["field_path", "key_value", "json_record"],
        locator_grammar="$.path.to.key or $[index]", visual_escalation_rule="NEVER",
        preflight_rule="clean_text_no_nul", failure_disposition="FAIL_CLOSED", is_attachment_supported=True,
        converter_version="json-structured-v1.0.0",
    ),
    "PDF": FormatCapability(
        format_id="PDF", name="Portable Document Format", extensions=[".pdf"],
        mime_types=["application/pdf"],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._extract_pdf",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_pdf_attachment",
        status=SupportStatus.PARTIAL, capabilities=["text", "page_breaks", "basic_tables"],
        notes="Native text extraction supported; mixed-mode raster OCR selective escalation.",
        unit_type="page", unit_types=["page"], locator_grammar="p{page_num}",
        visual_escalation_rule="ON_LOW_TEXT_OR_SCANNED", preflight_rule="pdf_magic_bytes",
        failure_disposition="FAIL_CLOSED", is_attachment_supported=True, converter_version="pypdf-pages-v1.1.0",
    ),
    "PPTX": FormatCapability(
        format_id="PPTX", name="PowerPoint Presentation", extensions=[".pptx"],
        mime_types=["application/vnd.openxmlformats-officedocument.presentationml.presentation"],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._extract_pptx",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_pptx_attachment",
        status=SupportStatus.PARTIAL, capabilities=["slides", "bullet_points", "tables", "speaker_notes"],
        notes="Text/notes/tables active; diagram visual extraction selective escalation.",
        unit_type="slide_shape", unit_types=["slide", "shape"], locator_grammar="slide{slide_num}#shape{shape_id}",
        visual_escalation_rule="ON_NON_TEXT_SHAPES", preflight_rule="ooxml_zip_container",
        failure_disposition="FAIL_CLOSED", is_attachment_supported=True, converter_version="pptx-slides-v1.1.0",
    ),
    "PPT_LEGACY": FormatCapability(
        format_id="PPT_LEGACY", name="Legacy PowerPoint Presentation", extensions=[".ppt"],
        mime_types=["application/vnd.ms-powerpoint"], primary_adapter="blocked_pending_conversion",
        attachment_adapter="blocked_pending_conversion", status=SupportStatus.BLOCKED, capabilities=[],
        notes="Legacy .ppt format is explicitly blocked; governed conversion required.",
        unit_type="none", unit_types=[], locator_grammar="none", visual_escalation_rule="BLOCKED",
        preflight_rule="legacy_binary_ole", failure_disposition="BLOCKED", is_attachment_supported=False,
    ),
    "PNG_JPEG": FormatCapability(
        format_id="PNG_JPEG", name="Raster Images & Diagrams",
        extensions=[".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"],
        mime_types=["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"],
        primary_adapter="dual_pipeline.attachment_core.image_extractor.extract_image_visual",
        attachment_adapter="dual_pipeline.attachment_core.image_extractor.extract_image_visual",
        status=SupportStatus.SUPPORTED, capabilities=["ocr", "vision", "diagram_topology"],
        notes="Fails closed to typed FAILED outcome when vision provider is exhausted.",
        unit_type="visual_component", unit_types=["visual_component"], locator_grammar="component{id}",
        visual_escalation_rule="ALWAYS", preflight_rule="image_magic_bytes",
        failure_disposition="FAIL_CLOSED", is_attachment_supported=True,
    ),
    "HTML_HTM": FormatCapability(
        format_id="HTML_HTM", name="HyperText Markup Language", extensions=[".html", ".htm"],
        mime_types=["text/html"],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._extract_html",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_html_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["text", "structural_blocks", "links"],
        notes="DOM block projection with visible text, headings, and link metadata.",
        unit_type="dom_block", unit_types=["dom_block"], locator_grammar="block{block_index}",
        visual_escalation_rule="NEVER", preflight_rule="html_markup_structure",
        failure_disposition="FAIL_CLOSED", is_attachment_supported=True, converter_version="html-parser-v1.0.0",
    ),
    "EML": FormatCapability(
        format_id="EML", name="Email Message", extensions=[".eml"], mime_types=["message/rfc822"],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._extract_eml",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_eml_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["headers", "body_text", "attachments"],
        notes="Preserves message headers, body, and attachment manifests.",
        unit_type="rfc822_part", unit_types=["email_header", "email_body", "email_attachment"],
        locator_grammar="header:{field_name}, body, attachment:{filename}", visual_escalation_rule="NEVER",
        preflight_rule="rfc822_headers", failure_disposition="FAIL_CLOSED",
        is_attachment_supported=True, converter_version="rfc822-eml-v1.1.0",
    ),
    "MSG_LEGACY": FormatCapability(
        format_id="MSG_LEGACY", name="Legacy Outlook Message", extensions=[".msg"], mime_types=["application/vnd.ms-outlook"],
        primary_adapter="blocked_pending_conversion", attachment_adapter="blocked_pending_conversion",
        status=SupportStatus.BLOCKED, capabilities=[],
        notes="Legacy Outlook .msg format is explicitly blocked; governed conversion required.",
        unit_type="none", unit_types=[], locator_grammar="none", visual_escalation_rule="BLOCKED",
        preflight_rule="legacy_binary_ole", failure_disposition="BLOCKED", is_attachment_supported=False,
    ),
    "CSV_TSV": FormatCapability(
        format_id="CSV_TSV", name="Tabular Delimited Data", extensions=[".csv", ".tsv"],
        mime_types=["text/csv", "text/tab-separated-values"],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._load_csv",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_csv_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["rows", "columns", "headers", "delimiter"],
        notes="Delimiter-header-row extraction with row-level locators.",
        unit_type="delimited_row", unit_types=["table_header", "delimited_row"],
        locator_grammar="R{row_index}", visual_escalation_rule="NEVER", preflight_rule="delimited_text_sniffer",
        failure_disposition="FAIL_CLOSED", is_attachment_supported=True, converter_version="csv-table-v1.0.0",
    ),
    "XLSX_WORKBOOK": FormatCapability(
        format_id="XLSX_WORKBOOK", name="Excel Workbook", extensions=[".xlsx", ".xlsm"],
        mime_types=[
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel.sheet.macroEnabled.12",
        ],
        primary_adapter="dual_pipeline.document_pipeline_core.content_loader._load_xlsx",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_spreadsheet_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["sheets", "rows", "columns", "cell_locators", "formulas"],
        notes="Safe openpyxl multi-sheet extraction with cell and formula provenance.",
        unit_type="sheet_cell_row", unit_types=["sheet_cell_row", "table_row"],
        locator_grammar="{sheet_name}!R{row}C{col}", visual_escalation_rule="NEVER",
        preflight_rule="ooxml_zip_container", failure_disposition="FAIL_CLOSED",
        is_attachment_supported=True, converter_version="openpyxl-all-sheets-v1.0.0",
    ),
    "XLS_LEGACY": FormatCapability(
        format_id="XLS_LEGACY", name="Legacy Excel Spreadsheet", extensions=[".xls"],
        mime_types=["application/vnd.ms-excel"], primary_adapter="blocked_pending_conversion",
        attachment_adapter="blocked_pending_conversion", status=SupportStatus.BLOCKED, capabilities=[],
        notes="Legacy binary .xls format is explicitly blocked; governed conversion required.",
        unit_type="none", unit_types=[], locator_grammar="none", visual_escalation_rule="BLOCKED",
        preflight_rule="legacy_binary_ole", failure_disposition="BLOCKED", is_attachment_supported=False,
    ),
    "SVG": FormatCapability(
        format_id="SVG", name="SVG Vector Diagram", extensions=[".svg"],
        mime_types=["image/svg+xml"],
        primary_adapter="dual_pipeline.document_pipeline_core.diagram_loader.load_svg_diagram",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_diagram_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["shapes", "connectors", "text_labels", "diagram_topology"],
        notes="Text-first vector diagram extraction for shapes, connectors, and labels.",
        unit_type="diagram_element", unit_types=["shape_label", "diagram_connector", "text_node"],
        locator_grammar="shape{id}#text or connector{id}", visual_escalation_rule="NEVER",
        preflight_rule="xml_svg_root", failure_disposition="FAIL_CLOSED",
        is_attachment_supported=True, converter_version="svg-diagram-v1.0.0",
    ),
    "DRAWIO": FormatCapability(
        format_id="DRAWIO", name="Draw.io Diagram", extensions=[".drawio"],
        mime_types=["application/vnd.jgraph.mxfile", "application/xml"],
        primary_adapter="dual_pipeline.document_pipeline_core.diagram_loader.load_drawio_diagram",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_diagram_attachment",
        status=SupportStatus.SUPPORTED, capabilities=["cells", "shapes", "connectors", "pages"],
        notes="Multi-page Draw.io graph extraction with vertex labels and directional edges.",
        unit_type="diagram_element", unit_types=["diagram_shape", "diagram_connector"],
        locator_grammar="page{page}#cell{id} or connector{id}", visual_escalation_rule="NEVER",
        preflight_rule="drawio_mxfile_or_diagram", failure_disposition="FAIL_CLOSED",
        is_attachment_supported=True, converter_version="drawio-diagram-v1.0.0",
    ),
    "VSDX": FormatCapability(
        format_id="VSDX", name="Visio Drawing", extensions=[".vsdx"],
        mime_types=["application/vnd.ms-visio.drawing.main+xml", "application/vnd.visio"],
        primary_adapter="dual_pipeline.document_pipeline_core.diagram_loader.load_vsdx_diagram",
        attachment_adapter="dual_pipeline.attachment_core.extractors.extract_diagram_attachment",
        status=SupportStatus.PARTIAL, capabilities=["pages", "shapes", "text_labels", "connectors"],
        notes="Text-first OpenXML Visio page and shape text extraction.",
        unit_type="visio_shape", unit_types=["page", "shape", "connector"],
        locator_grammar="page{page}#shape{id}", visual_escalation_rule="NEVER",
        preflight_rule="ooxml_zip_container", failure_disposition="FAIL_CLOSED",
        is_attachment_supported=True, converter_version="vsdx-drawing-v1.0.0",
    ),
}


def get_capability_by_format_id(format_id: str) -> Optional[FormatCapability]:
    """Retrieve capability definition by canonical format ID."""
    clean = format_id.upper()
    if clean == "MSG":
        clean = "MSG_LEGACY"
    return _REGISTRY.get(clean)


def get_capability_by_ext(ext: str) -> Optional[FormatCapability]:
    """Retrieve capability definition by file extension (e.g. '.docx' or 'docx')."""
    clean = f".{ext.lstrip('.').lower()}"
    for cap in _REGISTRY.values():
        if clean in cap.extensions:
            return cap
    return None


def get_capability_by_mime(mime: str) -> Optional[FormatCapability]:
    """Retrieve capability definition by MIME type."""
    clean = mime.strip().lower()
    for cap in _REGISTRY.values():
        if clean in cap.mime_types:
            return cap
    return None


def is_format_supported(format_or_ext: str) -> bool:
    """Return True only if the format has a SUPPORTED status."""
    cap = get_capability_by_format_id(format_or_ext) or get_capability_by_ext(format_or_ext)
    return cap is not None and cap.status == SupportStatus.SUPPORTED


def audit_matrix() -> Dict[str, Dict[str, Any]]:
    """Return an itemized dictionary of all registered formats and their statuses."""
    return {k: v.to_dict() for k, v in _REGISTRY.items()}


def get_all_registered_extensions() -> Set[str]:
    """Return all file extensions recognized across any registered format."""
    exts: Set[str] = set()
    for cap in _REGISTRY.values():
        exts.update(cap.extensions)
    return exts


def get_diagram_extensions() -> frozenset[str]:
    """Return diagram format extensions."""
    exts: Set[str] = set()
    for fid in ("SVG", "DRAWIO", "VSDX"):
        cap = _REGISTRY.get(fid)
        if cap:
            exts.update(cap.extensions)
    return frozenset(exts)


def get_document_extensions() -> frozenset[str]:
    """Return static non-audio/non-cue document extensions."""
    doc_ids = {
        "DOCX", "PDF", "PPTX", "PPT_LEGACY", "HTML_HTM", "EML", "MSG_LEGACY",
        "CSV_TSV", "XLSX_WORKBOOK", "XLS_LEGACY", "TXT_MD", "JSON", "SVG", "DRAWIO", "VSDX"
    }
    exts: Set[str] = set()
    for fid in doc_ids:
        cap = _REGISTRY.get(fid)
        if cap:
            exts.update(cap.extensions)
    return frozenset(exts)


def get_image_extensions() -> frozenset[str]:
    """Return image format extensions."""
    cap = _REGISTRY.get("PNG_JPEG")
    return frozenset(cap.extensions) if cap else frozenset()


def get_audio_extensions() -> frozenset[str]:
    """Return audio recording extensions."""
    cap = _REGISTRY.get("AUDIO")
    return frozenset(cap.extensions) if cap else frozenset()


def get_cue_extensions() -> frozenset[str]:
    """Return transcript subtitle cue extensions."""
    cap = _REGISTRY.get("VTT_SRT")
    return frozenset(cap.extensions) if cap else frozenset()


def get_presentation_extensions() -> frozenset[str]:
    """Return presentation format extensions."""
    exts: Set[str] = set()
    for fid in ("PPTX", "PPT_LEGACY"):
        cap = _REGISTRY.get(fid)
        if cap:
            exts.update(cap.extensions)
    return frozenset(exts)


def get_spreadsheet_extensions() -> frozenset[str]:
    """Return spreadsheet format extensions."""
    exts: Set[str] = set()
    for fid in ("XLSX_WORKBOOK", "XLS_LEGACY", "CSV_TSV"):
        cap = _REGISTRY.get(fid)
        if cap:
            exts.update(cap.extensions)
    return frozenset(exts)


def get_structured_extensions() -> frozenset[str]:
    """Return structured operational data format extensions."""
    exts: Set[str] = set(get_spreadsheet_extensions())
    cap = _REGISTRY.get("JSON")
    if cap:
        exts.update(cap.extensions)
    exts.add(".parquet")
    return frozenset(exts)


def get_correspondence_extensions() -> frozenset[str]:
    """Return email/correspondence format extensions."""
    exts: Set[str] = set()
    for fid in ("EML", "MSG_LEGACY"):
        cap = _REGISTRY.get(fid)
        if cap:
            exts.update(cap.extensions)
    return frozenset(exts)


def get_visual_extensions() -> frozenset[str]:
    """Return visual collateral format extensions (presentations, diagrams, images)."""
    return frozenset(
        get_presentation_extensions() | get_diagram_extensions() | get_image_extensions()
    )


def get_blocked_extensions() -> frozenset[str]:
    """Return extensions explicitly blocked by policy."""
    exts: Set[str] = set()
    for cap in _REGISTRY.values():
        if cap.status == SupportStatus.BLOCKED:
            exts.update(cap.extensions)
    return frozenset(exts)


def get_converter_versions() -> Dict[str, str]:
    """Return mapping of format extension (without dot) to converter version digest."""
    versions: Dict[str, str] = {}
    for cap in _REGISTRY.values():
        if cap.converter_version:
            for ext in cap.extensions:
                clean = ext.lstrip(".").lower()
                versions.setdefault(clean, cap.converter_version)
    versions["md"] = "markdown-passthrough-v1.0.0"
    versions["markdown"] = "markdown-passthrough-v1.0.0"
    return versions

