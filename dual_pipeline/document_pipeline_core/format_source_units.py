"""
dual_pipeline/document_pipeline_core/format_source_units.py

Single Source of Truth (SSOT) for immutable source-unit records, format adapter
contracts, deterministic preflight checks, locator grammars, and visual escalation policies.
Governs multi-format attachment intake across Word, Excel, PDF, PPTX, CSV/TSV, HTML, EML,
and Markdown/Text.
"""
from __future__ import annotations

import email
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.source_classification.capability_registry import _REGISTRY, get_audio_extensions, get_cue_extensions


class VisualEscalationPolicy(str, Enum):
    """Governed policy for selective visual/OCR rendering."""
    NEVER = "NEVER"
    ON_LOW_TEXT_OR_SCANNED = "ON_LOW_TEXT_OR_SCANNED"
    ON_NON_TEXT_SHAPES = "ON_NON_TEXT_SHAPES"
    ALWAYS = "ALWAYS"
    BLOCKED = "BLOCKED"


class FailureDisposition(str, Enum):
    """Governed failure disposition when file is corrupt, malformed, or blocked."""
    FAIL_CLOSED = "FAIL_CLOSED"
    HITL_REQUIRED = "HITL_REQUIRED"
    QUARANTINE = "QUARANTINE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class SourceUnit:
    """Immutable source-unit record representing atomic evidence from an attachment."""
    unit_id: str
    source_hash: str
    format: str
    unit_type: str  # e.g. "delimited_row", "dom_block", "rfc822_part", "line_block", "paragraph", "cell"
    locator: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            h = hashlib.sha256(f"{self.locator}:{self.content}".encode("utf-8")).hexdigest()
            object.__setattr__(self, "digest", h)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "source_hash": self.source_hash,
            "format": self.format,
            "unit_type": self.unit_type,
            "locator": self.locator,
            "content": self.content,
            "metadata": dict(self.metadata),
            "digest": self.digest,
        }


@dataclass(frozen=True)
class PreflightResult:
    """Result of deterministic preflight validation for an attachment payload."""
    is_valid: bool
    format_id: str
    disposition: FailureDisposition
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    detected_mime: Optional[str] = None
    is_legacy: bool = False
    is_excluded_from_attachment: bool = False
    remediation_command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "is_valid": self.is_valid,
            "format_id": self.format_id,
            "disposition": self.disposition.value,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "detected_mime": self.detected_mime,
            "is_legacy": self.is_legacy,
            "is_excluded_from_attachment": self.is_excluded_from_attachment,
        }
        if self.remediation_command:
            res["remediation_command"] = self.remediation_command
        return res


@dataclass(frozen=True)
class FormatAdapterContract:
    """Compact format-adapter contract governing preflight, units, locators, and visual policy."""
    format_id: str
    name: str
    extensions: Tuple[str, ...]
    mime_types: Tuple[str, ...]
    locator_grammar: str
    visual_policy: VisualEscalationPolicy
    failure_disposition: FailureDisposition
    unit_types: Tuple[str, ...]
    is_attachment_supported: bool = True
    notes: str = ""

    def preflight(self, raw_bytes: bytes, filename: str = "") -> PreflightResult:
        return preflight_attachment(raw_bytes, filename)


# Known signatures
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_AUDIO_EXTS = set(get_audio_extensions())
_CUE_EXTS = set(get_cue_extensions())
_EXEC_SIGS = (b"\x7fELF", b"MZ", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xce", b"\xcf\xfa\xed\xfe")


def preflight_attachment(raw_bytes: bytes, filename: str = "", is_collateral: bool = False) -> PreflightResult:
    """Deterministic preflight validator for document attachments (delegated to attachment_preflight)."""
    from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment as _pf
    return _pf(raw_bytes, filename=filename, is_collateral=is_collateral)



# --- Source Unit Builders ---

def build_csv_tsv_source_units(
    rows: List[List[str]], delimiter: str, header: List[str], source_hash: str, format_name: str = "csv"
) -> List[SourceUnit]:
    """Construct deterministic row units from tabular delimited data."""
    units: List[SourceUnit] = [
        SourceUnit(
            unit_id=f"{source_hash[:12]}:R0", source_hash=source_hash, format=format_name,
            unit_type="table_header", locator="R0", content=delimiter.join(header),
            metadata={"delimiter": delimiter, "column_count": len(header), "columns": list(header)},
        )
    ]
    for idx, row in enumerate(rows, start=1):
        col_map = {header[i]: row[i] for i in range(min(len(header), len(row)))}
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:R{idx}", source_hash=source_hash, format=format_name,
                unit_type="delimited_row", locator=f"R{idx}", content=delimiter.join(row),
                metadata={"row_index": idx, "cells": col_map},
            )
        )
    return units


_HTML_IGNORE_TAGS = frozenset({"head", "title", "script", "style", "noscript", "template"})


class _HtmlBlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Dict[str, Any]] = []
        self._curr_tag = ""
        self._curr_text: List[str] = []
        self._curr_attrs: Dict[str, str] = {}
        self._curr_links: List[Dict[str, str]] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in _HTML_IGNORE_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return
        if t in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "pre", "blockquote"}:
            self._flush()
            self._curr_tag = t
            self._curr_attrs = {k.lower(): (v or "") for k, v in attrs}
        elif t == "a":
            self._curr_attrs["href"] = next((v or "" for k, v in attrs if k.lower() == "href"), "")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        txt = data.strip()
        if txt:
            self._curr_text.append(txt)
            if "href" in self._curr_attrs:
                self._curr_links.append({"text": txt, "href": self._curr_attrs["href"]})

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _HTML_IGNORE_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if t == self._curr_tag:
            self._flush()
        elif t == "a" and "href" in self._curr_attrs:
            del self._curr_attrs["href"]

    def _flush(self) -> None:
        if self._curr_text:
            text = " ".join(self._curr_text).strip()
            if text:
                self.blocks.append({"tag": self._curr_tag or "p", "text": text, "links": list(self._curr_links)})
        self._curr_text, self._curr_links, self._curr_tag = [], [], ""


def build_html_source_units(html_text_or_bytes: Union[str, bytes], source_hash: str) -> List[SourceUnit]:
    """Construct DOM-text-link blocks from HTML source."""
    raw = html_text_or_bytes if isinstance(html_text_or_bytes, str) else html_text_or_bytes.decode("utf-8", errors="replace")
    parser = _HtmlBlockParser()
    parser.feed(raw)
    parser._flush()
    return [
        SourceUnit(
            unit_id=f"{source_hash[:12]}:block{idx}", source_hash=source_hash, format="html",
            unit_type="dom_block", locator=f"block{idx}", content=b["text"],
            metadata={"tag": b["tag"], "links": b["links"]},
        )
        for idx, b in enumerate(parser.blocks, start=1)
    ]


def _build_eml_headers(msg: Any, source_hash: str) -> List[SourceUnit]:
    from dual_pipeline.document_pipeline_core.eml_loader import _decode_header_val
    units = []
    fields = ("Message-ID", "In-Reply-To", "References", "From", "To", "Cc", "Date", "Subject")
    for f in fields:
        val = msg.get(f, "")
        if val:
            clean_val = _decode_header_val(val)
            units.append(
                SourceUnit(
                    unit_id=f"{source_hash[:12]}:header:{f}", source_hash=source_hash, format="eml",
                    unit_type="email_header", locator=f"header:{f}", content=clean_val,
                    metadata={"header_field": f},
                )
            )
    return units


def _process_eml_part(part: Any, body_idx: int, source_hash: str) -> Tuple[Optional[SourceUnit], int]:
    from dual_pipeline.document_pipeline_core.eml_loader import _decode_bytes_safely, _decode_header_val
    if part.get_content_maintype() == "multipart":
        return None, body_idx
    fn = _decode_header_val(part.get_filename() or "")
    payload = part.get_payload(decode=True) or b""
    if fn:
        u = SourceUnit(
            unit_id=f"{source_hash[:12]}:attachment:{fn}", source_hash=source_hash, format="eml",
            unit_type="email_attachment", locator=f"attachment:{fn}",
            content=f"Attachment: {fn} ({len(payload)} bytes)",
            metadata={"filename": fn, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
        )
        return u, body_idx
    if part.get_content_type() in {"text/plain", "text/html"}:
        txt = _decode_bytes_safely(payload, charset=part.get_content_charset()).strip()
        if txt:
            new_idx = body_idx + 1
            loc = f"body:{new_idx}" if new_idx > 1 else "body"
            u = SourceUnit(
                unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="eml",
                unit_type="email_body", locator=loc, content=txt,
                metadata={"content_type": part.get_content_type()},
            )
            return u, new_idx
    return None, body_idx


def build_eml_source_units(raw_bytes: bytes, source_hash: str) -> List[SourceUnit]:
    """Construct RFC-822 header-body-attachment units from email message."""
    import email.policy
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    units = _build_eml_headers(msg, source_hash)
    parts = msg.walk() if msg.is_multipart() else [msg]
    body_idx = 0
    for part in parts:
        unit, body_idx = _process_eml_part(part, body_idx, source_hash)
        if unit is not None:
            units.append(unit)
    return units


def _try_parse_front_matter(lines: List[str], source_hash: str, format_name: str) -> Optional[Tuple[SourceUnit, int]]:
    """Parse YAML or TOML front matter at start of document."""
    if len(lines) < 3:
        return None
    fence = lines[0].strip()
    if fence not in {"---", "+++"}:
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == fence:
            content = "\n".join(lines[: idx + 1])
            u = SourceUnit(
                unit_id=f"{source_hash[:12]}:L1-L{idx+1}", source_hash=source_hash, format=format_name,
                unit_type="front_matter", locator=f"L1-L{idx+1}", content=content,
                metadata={"start_line": 1, "end_line": idx + 1, "fence": fence},
            )
            return u, idx + 1
    return None


def _try_parse_fenced_code(lines: List[str], start_idx: int, source_hash: str, format_name: str) -> Optional[Tuple[SourceUnit, int]]:
    """Parse fenced code block starting at start_idx."""
    line = lines[start_idx].strip()
    if not (line.startswith("```") or line.startswith("~~~")):
        return None
    fence, lang = line[:3], line[3:].strip()
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].strip().startswith(fence):
            start_line, end_line = start_idx + 1, idx + 1
            u = SourceUnit(
                unit_id=f"{source_hash[:12]}:L{start_line}-L{end_line}", source_hash=source_hash, format=format_name,
                unit_type="code_block", locator=f"L{start_line}-L{end_line}", content="\n".join(lines[start_idx : idx + 1]),
                metadata={"start_line": start_line, "end_line": end_line, "language": lang},
            )
            return u, idx + 1
    start_line, end_line = start_idx + 1, len(lines)
    u = SourceUnit(
        unit_id=f"{source_hash[:12]}:L{start_line}-L{end_line}", source_hash=source_hash, format=format_name,
        unit_type="code_block", locator=f"L{start_line}-L{end_line}", content="\n".join(lines[start_idx:]),
        metadata={"start_line": start_line, "end_line": end_line, "language": lang},
    )
    return u, len(lines)


def _try_parse_markdown_table(lines: List[str], start_idx: int, source_hash: str, format_name: str) -> Optional[Tuple[SourceUnit, int]]:
    """Parse contiguous Markdown table rows starting at start_idx with markdown-it validation."""
    first = lines[start_idx].strip()
    if not (first.startswith("|") and first.endswith("|") and len(first) > 1):
        return None
    import markdown_it
    table_lines: List[str] = []
    end_idx = start_idx
    while end_idx < len(lines):
        line = lines[end_idx].strip()
        if line.startswith("|") and line.endswith("|") and len(line) > 1:
            table_lines.append(lines[end_idx])
            end_idx += 1
        else:
            break
    if not table_lines:
        return None
    md = markdown_it.MarkdownIt("gfm-like")
    tokens = md.parse("\n".join(table_lines))
    rows_count = sum(1 for t in tokens if t.type == "tr_open") or len(table_lines)
    data_rows = max(0, rows_count - 1) if rows_count > 1 else rows_count
    start_line, end_line = start_idx + 1, end_idx
    locator = f"L{start_line}-L{end_line}" if start_line != end_line else f"L{start_line}"
    u = SourceUnit(
        unit_id=f"{source_hash[:12]}:{locator}", source_hash=source_hash, format=format_name,
        unit_type="table_block", locator=locator, content="\n".join(table_lines),
        metadata={"start_line": start_line, "end_line": end_line, "rows_count": rows_count, "data_rows_count": data_rows},
    )
    return u, end_idx


def _parse_single_text_line(line: str, line_no: int, source_hash: str, format_name: str) -> Optional[SourceUnit]:
    """Classify heading, list item, or general text line unit."""
    clean = line.strip()
    if not clean:
        return None
    m_h = re.match(r"^(#{1,6})\s+(.*)$", clean)
    if m_h:
        hashes, title = m_h.groups()
        return SourceUnit(
            unit_id=f"{source_hash[:12]}:L{line_no}", source_hash=source_hash, format=format_name,
            unit_type="heading", locator=f"L{line_no}", content=clean,
            metadata={"line_number": line_no, "level": len(hashes), "title": title.strip()},
        )
    m_l = re.match(r"^(\s*[-*+]|\s*\d+\.)\s+(.*)$", line)
    if m_l:
        return SourceUnit(
            unit_id=f"{source_hash[:12]}:L{line_no}", source_hash=source_hash, format=format_name,
            unit_type="list_item", locator=f"L{line_no}", content=clean,
            metadata={"line_number": line_no, "marker": m_l.group(1).strip()},
        )
    return SourceUnit(
        unit_id=f"{source_hash[:12]}:L{line_no}", source_hash=source_hash, format=format_name,
        unit_type="line_block", locator=f"L{line_no}", content=clean, metadata={"line_number": line_no},
    )


def build_text_source_units(raw_text: str, source_hash: str, format_name: str = "txt") -> List[SourceUnit]:
    """Construct structural line/block units with exact line locators."""
    lines = raw_text.splitlines()
    if not lines:
        return []
    units: List[SourceUnit] = []
    idx = 0

    fm_res = _try_parse_front_matter(lines, source_hash, format_name)
    if fm_res:
        u, next_idx = fm_res
        units.append(u)
        idx = next_idx

    while idx < len(lines):
        code_res = _try_parse_fenced_code(lines, idx, source_hash, format_name)
        if code_res:
            u, next_idx = code_res
            units.append(u)
            idx = next_idx
            continue

        tbl_res = _try_parse_markdown_table(lines, idx, source_hash, format_name)
        if tbl_res:
            u, next_idx = tbl_res
            units.append(u)
            idx = next_idx
            continue

        single = _parse_single_text_line(lines[idx], idx + 1, source_hash, format_name)
        if single is not None:
            units.append(single)
        idx += 1

    return units


def _extract_unit_title(content: str, meta: Dict[str, Any]) -> str:
    title = meta.get("title")
    if title:
        return str(title)[:50].replace("|", "/")
    for line in content.splitlines():
        clean = line.strip().lstrip("#-* ").strip()
        if clean:
            return clean[:50].replace("|", "/")
    return "Untitled"


def _extract_unit_cues(content: str) -> str:
    nums = re.findall(r"\$?\b\d+(?:\.\d+)?[BMKk%+]\b|\b20\d\d\b", content)
    stop_words = {"THE", "AND", "FOR", "WITH", "THIS", "FROM", "WHAT", "THEY", "YOUR"}
    caps = [w for w in re.findall(r"\b[A-Z]{2,}\b", content) if w not in stop_words]
    cues = list(dict.fromkeys(nums + caps))[:4]
    return ", ".join(cues).replace("|", "/") if cues else "general"


def _format_unit_index_row(u: Any) -> str:
    loc = getattr(u, "locator", None) or (u.get("locator") if isinstance(u, dict) else "")
    if not loc:
        return ""
    content = getattr(u, "content", None) or (u.get("content") if isinstance(u, dict) else "") or ""
    meta = getattr(u, "metadata", None) or (u.get("metadata") if isinstance(u, dict) else {}) or {}

    title = _extract_unit_title(content, meta)
    cues_str = _extract_unit_cues(content)
    disp = meta.get("disposition") or getattr(u, "unit_type", None) or (u.get("unit_type") if isinstance(u, dict) else "TEXT_ONLY")
    return f"| {loc} | {title} | {cues_str} | {disp} |"


def generate_document_structural_index(units: Any) -> str:
    """Generate a compact deterministic markdown structural index table across slide or page locators."""
    if not units:
        return ""
    lines = [
        "| Locator | Title / Section | Cues & Metrics | Disposition |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for u in units:
        row = _format_unit_index_row(u)
        if row:
            lines.append(row)
    return "\n".join(lines)


def build_pdf_source_units(report: Any, source_hash: str) -> List[SourceUnit]:
    """Construct page-level source units from PDF extraction report."""
    from dual_pipeline.document_pipeline_core.unit_decomposition import (
        UnitDispositionClass,
        _is_explicit_template_worksheet_unit,
        classify_unit_sieve,
        is_summary_section_title,
        is_template_appendix_trigger,
    )
    units: List[SourceUnit] = []
    in_template_appendix = False
    for p in report.pages:
        title = next(
            (line.strip().lstrip("#-* ").strip() for line in p.text.splitlines() if line.strip()),
            "",
        )[:60]
        is_sum = is_summary_section_title(title)
        if is_template_appendix_trigger(title, p.text):
            in_template_appendix = True
        img_count = getattr(p, "image_count", 0)
        sieve_class, materiality, is_sub = classify_unit_sieve(f"Slide {p.page_num}", f"## Page {p.page_num}\n{p.text}", img_count)
        if in_template_appendix and _is_explicit_template_worksheet_unit(title, p.text):
            sieve_class = UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value
            materiality = "QUARANTINED"
            is_sub = False
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:p{p.page_num}",
                source_hash=source_hash,
                format="pdf",
                unit_type="page",
                locator=f"p{p.page_num}",
                content=p.text,
                metadata={
                    "page_num": p.page_num,
                    "title": title,
                    "char_count": p.char_count,
                    "image_count": img_count,
                    "quality_score": p.quality_score,
                    "disposition": p.disposition,
                    "pdf_visual_disposition": p.disposition,
                    "sieve_class": sieve_class,
                    "reason": p.reason,
                    "flagged_regions": list(p.flagged_regions),
                    "is_summary_unit": is_sum,
                    "is_substantive": is_sub,
                    "materiality": materiality,
                },
            )
        )
    return units


def build_pptx_source_units(presentation: Any, source_hash: str) -> List[SourceUnit]:
    """Construct slide-level source units from PPTX presentation."""
    from dual_pipeline.document_pipeline_core.unit_decomposition import (
        UnitDispositionClass,
        _is_explicit_template_worksheet_unit,
        classify_unit_sieve,
        is_summary_section_title,
        is_template_appendix_trigger,
    )
    units: List[SourceUnit] = []
    in_template_appendix = False
    for s in presentation.slides:
        md_content = s.to_markdown()
        is_sum = is_summary_section_title(s.title)
        if is_template_appendix_trigger(s.title, md_content):
            in_template_appendix = True
        sieve_class, materiality, is_sub = classify_unit_sieve(
            s.title or f"Slide {s.slide_number}",
            md_content,
            len(getattr(s, "flagged_shape_locators", [])),
        )
        if in_template_appendix and _is_explicit_template_worksheet_unit(s.title, md_content):
            sieve_class = UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value
            materiality = "QUARANTINED"
            is_sub = False
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:slide{s.slide_number}",
                source_hash=source_hash,
                format="pptx",
                unit_type="slide",
                locator=f"slide{s.slide_number}",
                content=md_content,
                metadata={
                    "slide_number": s.slide_number,
                    "title": s.title,
                    "paragraphs_count": len(s.paragraphs),
                    "tables_count": len(s.tables),
                    "disposition": s.disposition,
                    "sieve_class": sieve_class,
                    "materiality": materiality,
                    "is_summary_unit": is_sum,
                    "is_substantive": is_sub,
                    "reason": s.disposition_reason,
                    "flagged_shape_locators": list(s.flagged_shape_locators),
                    "shape_identifiers": list(s.shape_identifiers),
                },
            )
        )
    return units


def build_docx_source_units(coverage: Dict[str, Any], source_hash: str) -> List[SourceUnit]:
    """Construct paragraph and table-cell source units from DOCX coverage."""
    units: List[SourceUnit] = []
    for p in coverage.get("paragraphs") or []:
        loc = p.get("locator", f"p{len(units)+1}")
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="docx",
                unit_type="paragraph", locator=loc, content=p.get("text") or p.get("plain_text") or "",
                metadata={"plain_text": p.get("plain_text", ""), "is_list": bool(p.get("is_list")), "heading_level": p.get("heading_level")},
            )
        )
    for c in coverage.get("cells") or []:
        loc_obj = c.get("locator")
        loc_val = str(loc_obj.get("value", "")) if isinstance(loc_obj, dict) else str(loc_obj or "")
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:{loc_val}", source_hash=source_hash, format="docx",
                unit_type="table_cell", locator=loc_val, content=c.get("text", ""),
                metadata={"char_count": c.get("char_count", len(c.get("text", ""))), "text_sha256": c.get("text_sha256", "")},
            )
        )
    return units


def _build_format_adapters() -> Dict[str, FormatAdapterContract]:
    adapters: Dict[str, FormatAdapterContract] = {}
    for cap in _REGISTRY.values():
        adapters[cap.format_id] = FormatAdapterContract(
            format_id=cap.format_id,
            name=cap.name,
            extensions=tuple(cap.extensions),
            mime_types=tuple(cap.mime_types),
            locator_grammar=cap.locator_grammar,
            visual_policy=VisualEscalationPolicy(cap.visual_escalation_rule),
            failure_disposition=FailureDisposition(cap.failure_disposition),
            unit_types=tuple(cap.unit_types),
            is_attachment_supported=cap.is_attachment_supported,
            notes=cap.notes,
        )
    return adapters


# Canonical Format Adapter Declarations derived from Capability Registry SSOT
FORMAT_ADAPTERS: Dict[str, FormatAdapterContract] = _build_format_adapters()


def get_format_adapter(format_or_ext: str) -> Optional[FormatAdapterContract]:
    """Retrieve format adapter contract by format ID or file extension."""
    clean = format_or_ext.upper()
    if clean == "MSG":
        clean = "MSG_LEGACY"
    if clean in FORMAT_ADAPTERS:
        return FORMAT_ADAPTERS[clean]
    ext_clean = f".{format_or_ext.lstrip('.').lower()}"
    for adapter in FORMAT_ADAPTERS.values():
        if ext_clean in adapter.extensions:
            return adapter
    return None
