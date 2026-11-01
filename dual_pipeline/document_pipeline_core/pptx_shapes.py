"""
dual_pipeline/document_pipeline_core/pptx_shapes.py

OpenXML shape inspection, reading order geometry, chart data extraction, and relationship resolution.
Provides robust parsing for PowerPoint presentation XML structures without third-party dependencies.
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"

_P = f"{{{_P_NS}}}"
_A = f"{{{_A_NS}}}"
_R = f"{{{_R_NS}}}"
_C = f"{{{_C_NS}}}"


def clean_text(text: str) -> str:
    """Normalize text removing zero-width characters and collapsing whitespace."""
    if not text:
        return ""
    rendered = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    return unicodedata.normalize("NFC", re.sub(r"[ \t]+", " ", rendered)).strip()


@dataclass
class PPTXTable:
    """Represents a table extracted from a slide."""
    rows: List[List[str]] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render table as canonical Markdown table."""
        if not self.rows or not any(any(c for c in r) for r in self.rows):
            return ""
        col_count = max(len(r) for r in self.rows)
        if col_count == 0:
            return ""
        normalized = [r + [""] * (col_count - len(r)) for r in self.rows]
        cleaned_rows = [[clean_text(c).replace("|", "\\|").replace("\n", "<br>") for c in r] for r in normalized]

        header = cleaned_rows[0]
        separator = ["---"] * col_count
        data_rows = cleaned_rows[1:] if len(cleaned_rows) > 1 else []

        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(separator) + " |",
        ]
        for r in data_rows:
            lines.append("| " + " | ".join(r) + " |")
        return "\n".join(lines)


def extract_text_from_element(element: ET.Element) -> str:
    """Extract and concatenate all text within an element."""
    texts: List[str] = []
    for node in element.iter(f"{_A}t"):
        if node.text:
            texts.append(node.text)
    return "".join(texts)


def get_shape_position(elem: ET.Element) -> Tuple[int, int]:
    """Extract (y, x) coordinate for reading order sorting (top-to-bottom, left-to-right)."""
    xfrm = elem.find(f".//{{{_A_NS}}}xfrm")
    if xfrm is not None:
        off = xfrm.find(f"{{{_A_NS}}}off")
        if off is not None:
            try:
                return (int(off.get("y", "0")), int(off.get("x", "0")))
            except (ValueError, TypeError):
                pass
    ph = elem.find(f".//{{{_P_NS}}}ph")
    if ph is not None and ph.get("type", "") in ("title", "ctrTitle"):
        return (0, 0)
    return (999999999, 999999999)


def _read_smartart_paragraphs(archive: zipfile.ZipFile, t_path: str) -> List[str]:
    try:
        d_tree = ET.fromstring(archive.read(t_path))
    except Exception:
        return []
    out = [clean_text(extract_text_from_element(p)) for p in d_tree.iter(f"{{{_A_NS}}}p")]
    return [t for t in out if t]


def extract_smartart_diagram_text(
    archive: zipfile.ZipFile, elem: ET.Element, rels: Dict[str, str]
) -> List[str]:
    """Extract text paragraphs from SmartArt diagram data XML when referenced via dgm:relIds."""
    dgm_rel = elem.find(f".//{{{_DGM_NS}}}relIds")
    if dgm_rel is None:
        return []
    dm_id = dgm_rel.get(f"{{{_R_NS}}}dm") or dgm_rel.get("r:dm") or dgm_rel.get("dm")
    raw_targets = [rels[dm_id]] if (dm_id and dm_id in rels) else [v for v in rels.values() if "diagrams/data" in v]
    names = set(archive.namelist())
    paras: List[str] = []
    for raw_t in raw_targets:
        t_path = "ppt/" + raw_t.replace("../", "").lstrip("/")
        if t_path in names:
            for txt in _read_smartart_paragraphs(archive, t_path):
                if txt not in paras:
                    paras.append(txt)
    return paras


def load_slide_relationships(archive: zipfile.ZipFile, slide_target: str) -> Dict[str, str]:
    """Parse slide relationship targets from .rels file."""
    slide_dir, slide_name = slide_target.rsplit("/", 1) if "/" in slide_target else ("", slide_target)
    rels_path = f"{slide_dir}/_rels/{slide_name}.rels" if slide_dir else f"_rels/{slide_name}.rels"
    rels: Dict[str, str] = {}
    if rels_path in archive.namelist():
        try:
            tree = ET.fromstring(archive.read(rels_path))
            for elem in tree.iter():
                r_id = elem.attrib.get("Id")
                target = elem.attrib.get("Target")
                if r_id and target:
                    rels[r_id] = target
        except Exception:
            pass
    return rels


def find_hyperlinks_in_element(elem: ET.Element, rels: Dict[str, str]) -> List[str]:
    """Find all hyperlink target URLs in a shape or run."""
    links: List[str] = []
    for hl in elem.iter(f"{{{_A_NS}}}hlinkClick"):
        r_id = hl.get(f"{{{_R_NS}}}id") or hl.get("r:id") or hl.get("id")
        if r_id and r_id in rels:
            links.append(rels[r_id])
    return links


def extract_slide_tables(slide_tree: ET.Element) -> List[PPTXTable]:
    """Extract all tables from a slide XML tree."""
    tables: List[PPTXTable] = []
    for tbl_elem in slide_tree.iter(f"{_A}tbl"):
        rows: List[List[str]] = []
        for tr in tbl_elem.iter(f"{_A}tr"):
            row: List[str] = []
            for tc in tr.iter(f"{_A}tc"):
                cell_text = clean_text(extract_text_from_element(tc))
                row.append(cell_text)
            if any(row):
                rows.append(row)
        if rows:
            tables.append(PPTXTable(rows=rows))
    return tables


def _parse_notes_xml(notes_xml: bytes) -> str:
    tree = ET.fromstring(notes_xml)
    notes: List[str] = []
    for sp in tree.iter(f"{_P}sp"):
        ph = sp.find(f".//{{{_P_NS}}}ph")
        ph_type = ph.get("type", "") if ph is not None else ""
        if ph_type in ("body", ""):
            for p in sp.iter(f"{_A}p"):
                txt = clean_text(extract_text_from_element(p))
                if txt and not txt.startswith("Slide ") and not re.match(r"^\d+$", txt):
                    notes.append(txt)
    return "\n".join(notes).strip()


def extract_slide_notes(archive: zipfile.ZipFile, slide_target: str) -> str:
    """Extract speaker notes associated with a given slide target if present."""
    rels = load_slide_relationships(archive, slide_target)
    for raw_target in rels.values():
        if "notesSlide" in raw_target:
            target = "ppt/" + raw_target.replace("../", "").lstrip("/")
            if target in archive.namelist():
                try:
                    return _parse_notes_xml(archive.read(target))
                except Exception:
                    pass
    return ""


def inspect_chart_graphic_frame(
    archive: zipfile.ZipFile, elem: ET.Element, rels: Dict[str, str]
) -> Tuple[bool, Optional[Dict[str, Any]], bool]:
    """
    Inspect a graphicFrame element for chart content.
    Returns (is_chart, chart_dict_or_none, is_inaccessible).
    """
    chart_elem = elem.find(f".//{{{_C_NS}}}chart")
    if chart_elem is None:
        return False, None, False
    r_id = chart_elem.get(f"{{{_R_NS}}}id") or chart_elem.get("r:id")
    if not r_id or r_id not in rels:
        return True, None, True
    raw_target = rels[r_id]
    chart_path = "ppt/" + raw_target.replace("../", "").lstrip("/")
    if chart_path not in archive.namelist():
        return True, None, True
    try:
        from dual_pipeline.document_pipeline_core.visual_escalation import parse_chart_xml
        res = parse_chart_xml(archive.read(chart_path))
        return True, res, res.get("inaccessible", False)
    except Exception:
        return True, None, True


def extract_shape_metadata(elem: ET.Element) -> Tuple[str, str, str]:
    """Extract (shape_id, shape_name, shape_kind) from any shape element."""
    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
    cNvPr = elem.find(f".//{{{_P_NS}}}cNvPr")
    if cNvPr is None:
        cNvPr = elem.find(f".//{{{_A_NS}}}cNvPr")
    shape_id = cNvPr.get("id", "0") if cNvPr is not None else "0"
    shape_name = cNvPr.get("name", tag) if cNvPr is not None else tag
    return shape_id, shape_name, tag
