"""Deterministic, source-bound PowerPoint (.pptx) extraction and visual escalation.

Provides zero-dependency OpenXML extraction for PowerPoint presentations.
Extracts slide sequence, titles, text frames, bullet hierarchies, reading order geometry,
Markdown tables, hyperlinks, chart data, and speaker notes in document order with exact SHA-256 digests.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from dual_pipeline.document_pipeline_core.pptx_shapes import (
    PPTXTable, clean_text, extract_shape_metadata, extract_slide_notes, extract_slide_tables,
    extract_smartart_diagram_text, extract_text_from_element, find_hyperlinks_in_element,
    get_shape_position, inspect_chart_graphic_frame, load_slide_relationships,
)
from dual_pipeline.document_pipeline_core.visual_escalation import (
    SlideVisualAssessment, VisualDisposition, assess_pptx_slide, validate_visual_routing_invariants,
)

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_P, _A, _R = f"{{{_P_NS}}}", f"{{{_A_NS}}}", f"{{{_R_NS}}}"
_MAX_SLIDE_XML_BYTES = 16 * 1024 * 1024  # 16MB per slide safety ceiling


def sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of bytes."""
    return sha256(data).hexdigest()


def _render_chart_block(charts: List[Dict[str, Any]]) -> str:
    lines = []
    for c in charts:
        lines.append(f"### {c.get('title') or 'Chart Data'}")
        for ser in c.get("series", []):
            lines.append(f"- **{ser.get('name', 'Series')}**: {', '.join(ser.get('values', []))}")
    return "\n".join(lines)


def _render_links_block(links: List[str]) -> str:
    return "**Links:**\n" + "\n".join(f"- <{l}>" for l in sorted(set(links))) if links else ""


@dataclass
class PPTXSlide:
    """Represents an extracted slide in presentation order with shape provenance."""
    slide_number: int
    slide_target: str
    title: str = ""
    paragraphs: List[str] = field(default_factory=list)
    tables: List[PPTXTable] = field(default_factory=list)
    speaker_notes: str = ""
    hyperlinks: List[str] = field(default_factory=list)
    chart_data: List[Dict[str, Any]] = field(default_factory=list)
    shape_identifiers: List[str] = field(default_factory=list)
    disposition: str = "TEXT_ONLY"
    disposition_reason: str = "TEXT_NATIVE_CONFIRMED"
    flagged_shape_locators: List[str] = field(default_factory=list)

    @property
    def locator(self) -> str:
        return f"slide{self.slide_number}"

    def to_markdown(self) -> str:
        """Render slide as structured Markdown block."""
        parts: List[str] = []
        heading = f"## Slide {self.slide_number}: {self.title}" if self.title else f"## Slide {self.slide_number}"
        parts.append(f"<!-- SLIDE {self.slide_number} -->")
        parts.append(heading)

        for p in self.paragraphs:
            p_clean = clean_text(p)
            if p_clean and p_clean != self.title:
                parts.append(p_clean)

        for tbl in self.tables:
            tbl_md = tbl.to_markdown()
            if tbl_md:
                parts.append(tbl_md)

        if self.chart_data:
            parts.append(_render_chart_block(self.chart_data))

        if self.speaker_notes:
            parts.append(f"**Presenter Notes:** {clean_text(self.speaker_notes)}")

        if self.hyperlinks:
            parts.append(_render_links_block(self.hyperlinks))

        return "\n\n".join(parts)


@dataclass
class PPTXPresentation:
    """Represents a complete extracted PowerPoint presentation."""
    title: str
    slides: List[PPTXSlide]
    raw_sha256: str
    extracted_markdown: str
    extracted_markdown_sha256: str
    slide_count: int
    text_native_slides: List[int] = field(default_factory=list)
    escalated_slides: List[int] = field(default_factory=list)
    hitl_slides: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title, "slide_count": self.slide_count, "raw_sha256": self.raw_sha256,
            "extracted_markdown_sha256": self.extracted_markdown_sha256,
            "text_native_slides": list(self.text_native_slides),
            "escalated_slides": list(self.escalated_slides), "hitl_slides": list(self.hitl_slides),
            "slides": [
                {
                    "slide_number": s.slide_number, "title": s.title, "paragraphs_count": len(s.paragraphs),
                    "tables_count": len(s.tables), "has_speaker_notes": bool(s.speaker_notes),
                    "disposition": s.disposition, "reason": s.disposition_reason,
                    "flagged_shape_locators": list(s.flagged_shape_locators), "shape_count": len(s.shape_identifiers),
                }
                for s in self.slides
            ],
        }


def _extract_shape_text(sp: ET.Element) -> Tuple[List[str], bool]:
    ph = sp.find(f".//{{{_P_NS}}}ph")
    ph_type = ph.get("type", "") if ph is not None else ""
    is_title = ph_type in ("title", "ctrTitle")
    paragraphs: List[str] = []
    for p in sp.iter(f"{{{_A_NS}}}p"):
        t = clean_text(extract_text_from_element(p))
        if not t:
            continue
        lvl = 0
        pPr = p.find(f"{{{_A_NS}}}pPr")
        if pPr is not None:
            lvl_attr = pPr.get("lvl")
            if lvl_attr and lvl_attr.isdigit():
                lvl = int(lvl_attr)
        prefix = ("  " * lvl) + "- " if lvl > 0 else ""
        paragraphs.append(prefix + t)
    return paragraphs, is_title


def _extract_group_shape_text(grp: ET.Element) -> Tuple[List[str], bool]:
    """Recursively extract text from all child <p:sp> and nested <p:grpSp> inside a group shape."""
    paras: List[str] = []
    has_title = False
    children = list(grp)
    children.sort(key=get_shape_position)
    for child in children:
        c_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if c_tag == "sp":
            c_paras, c_title = _extract_shape_text(child)
            paras.extend(c_paras)
            if c_title:
                has_title = True
        elif c_tag == "grpSp":
            g_paras, g_title = _extract_group_shape_text(child)
            paras.extend(g_paras)
            if g_title:
                has_title = True
    return paras, has_title


def _classify_child_shape(elem: ET.Element, archive: zipfile.ZipFile, rels: Dict[str, str]) -> Dict[str, Any]:
    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
    shape_id, shape_name, _ = extract_shape_metadata(elem)
    links = find_hyperlinks_in_element(elem, rels)
    if tag == "sp":
        paras, is_title = _extract_shape_text(elem)
        return {"kind": "sp", "id": shape_id, "name": shape_name, "paras": paras, "is_title": is_title, "links": links}
    if tag == "graphicFrame":
        is_c, c_dict, is_inacc = inspect_chart_graphic_frame(archive, elem, rels)
        if is_c:
            return {"kind": "chart", "id": shape_id, "name": shape_name, "chart": c_dict, "inaccessible": is_inacc, "links": links}
        if elem.find(f".//{{{_DGM_NS}}}relIds") is not None:
            dgm_paras = extract_smartart_diagram_text(archive, elem, rels)
            return {"kind": "diagram", "id": shape_id, "name": shape_name, "paras": dgm_paras, "inaccessible": False, "links": links}
    if tag == "pic":
        return {"kind": "picture", "id": shape_id, "name": shape_name, "inaccessible": False, "links": links}
    if tag == "grpSp":
        g_paras, g_title = _extract_group_shape_text(elem)
        return {"kind": "diagram", "id": shape_id, "name": shape_name, "paras": g_paras, "is_title": g_title, "inaccessible": False, "links": links}
    if tag == "cxnSp":
        return {"kind": "diagram", "id": shape_id, "name": shape_name, "inaccessible": False, "links": links}
    return {"kind": "other", "id": shape_id, "name": shape_name, "inaccessible": False, "links": links}


def _handle_sp_shape(info: Dict[str, Any], titles: List[str], paragraphs: List[str]) -> None:
    for p in info.get("paras", []):
        if info.get("is_title") or (not titles and len(p) < 120):
            titles.append(p)
        else:
            paragraphs.append(p)


def _aggregate_shape_info(
    children: List[ET.Element], archive: zipfile.ZipFile, rels: Dict[str, str]
) -> Tuple[List[str], List[str], List[str], List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    titles, paragraphs, hyperlinks, shape_ids, visual_shapes, chart_data = [], [], [], [], [], []
    for elem in children:
        info = _classify_child_shape(elem, archive, rels)
        shape_ids.append(info["id"])
        hyperlinks.extend(info.get("links", []))
        kind = info["kind"]
        if kind == "sp":
            _handle_sp_shape(info, titles, paragraphs)
        elif kind == "chart":
            visual_shapes.append(info)
            c = info.get("chart")
            if c and c.get("has_data"):
                chart_data.append(c)
        elif kind in ("diagram", "picture"):
            if info.get("paras"):
                _handle_sp_shape(info, titles, paragraphs)
            visual_shapes.append(info)
    return titles, paragraphs, hyperlinks, shape_ids, visual_shapes, chart_data


def _extract_single_slide(archive: zipfile.ZipFile, slide_number: int, slide_target: str) -> PPTXSlide:
    """Parse one slide XML from archive in geometric reading order."""
    try:
        content = archive.read(slide_target)
    except KeyError as exc:
        raise ValueError(f"PPTX missing slide file {slide_target}") from exc

    if len(content) > _MAX_SLIDE_XML_BYTES:
        raise ValueError(f"PPTX slide {slide_target} exceeds maximum byte ceiling")

    try:
        tree = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"PPTX slide {slide_target} XML is malformed") from exc

    rels = load_slide_relationships(archive, slide_target)
    sp_tree = tree.find(f".//{{{_P_NS}}}spTree")
    children = list(sp_tree) if sp_tree is not None else []
    children.sort(key=get_shape_position)

    titles, paragraphs, hyperlinks, shape_ids, visual_shapes, chart_data = _aggregate_shape_info(
        children, archive, rels
    )
    tables = extract_slide_tables(tree)
    notes = extract_slide_notes(archive, slide_target)
    total_chars = sum(len(p) for p in paragraphs) + sum(len(t) for t in titles)

    assessment = assess_pptx_slide(
        slide_number, total_chars, len(shape_ids), len(paragraphs) + len(titles),
        visual_shapes, has_tables=bool(tables), has_notes=bool(notes)
    )

    return PPTXSlide(
        slide_number=slide_number,
        slide_target=slide_target,
        title=clean_text(titles[0]) if titles else "",
        paragraphs=paragraphs,
        tables=tables,
        speaker_notes=notes,
        hyperlinks=hyperlinks,
        chart_data=chart_data,
        shape_identifiers=shape_ids,
        disposition=assessment.disposition.value,
        disposition_reason=assessment.reason,
        flagged_shape_locators=assessment.flagged_shape_locators,
    )


def _load_pres_rels(archive: zipfile.ZipFile) -> Dict[str, str]:
    rels_map: Dict[str, str] = {}
    if "ppt/_rels/presentation.xml.rels" in archive.namelist():
        rels_xml = archive.read("ppt/_rels/presentation.xml.rels")
        rels_tree = ET.fromstring(rels_xml)
        for elem in rels_tree.iter():
            r_id = elem.attrib.get("Id")
            target = elem.attrib.get("Target")
            if r_id and target:
                rels_map[r_id] = target
    return rels_map


def _fallback_slide_targets(archive: zipfile.ZipFile) -> List[str]:
    pattern = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
    found = []
    for name in archive.namelist():
        m = pattern.match(name)
        if m:
            found.append((int(m.group(1)), name))
    found.sort(key=lambda x: x[0])
    return [f[1] for f in found]


def _resolve_slide_targets(archive: zipfile.ZipFile) -> List[str]:
    try:
        pres_xml = archive.read("ppt/presentation.xml")
    except KeyError as exc:
        raise ValueError("PPTX source has no ppt/presentation.xml") from exc
    try:
        pres_tree = ET.fromstring(pres_xml)
    except ET.ParseError as exc:
        raise ValueError("PPTX presentation.xml is malformed") from exc

    rels_map = _load_pres_rels(archive)
    slide_targets: List[str] = []
    for sldId in pres_tree.iter(f"{_P}sldId"):
        r_id = sldId.get(f"{_R}id")
        if r_id and r_id in rels_map:
            target = rels_map[r_id]
            norm = "ppt/" + target.lstrip("/") if not target.startswith("ppt/") else target
            slide_targets.append(norm)

    if not slide_targets:
        slide_targets = _fallback_slide_targets(archive)
    if not slide_targets:
        raise ValueError("PPTX source has no slides")
    return slide_targets


def extract_pptx_presentation(pptx_source: bytes | str | Path) -> PPTXPresentation:
    """Extract a complete structured PPTXPresentation object from PPTX bytes or file path."""
    if isinstance(pptx_source, (str, Path)):
        p = Path(pptx_source)
        if not p.exists():
            raise ValueError(f"PPTX file not found: {p}")
        pptx_bytes = p.read_bytes()
    else:
        pptx_bytes = pptx_source

    if not pptx_bytes:
        raise ValueError("PPTX source must not be empty")

    raw_hash = sha256_bytes(pptx_bytes)

    try:
        with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as archive:
            slide_targets = _resolve_slide_targets(archive)
            slides = [_extract_single_slide(archive, idx, target) for idx, target in enumerate(slide_targets, start=1)]
    except zipfile.BadZipFile as exc:
        raise ValueError("source is not a valid PPTX zip package") from exc

    pres_title = slides[0].title if slides and slides[0].title else "PowerPoint Presentation"
    md_blocks: List[str] = [f"# {pres_title}\n"]
    for s in slides:
        md_blocks.append(s.to_markdown())
    extracted_md = "\n\n".join(md_blocks).strip() + "\n"
    extracted_md_hash = sha256_bytes(extracted_md.encode("utf-8"))

    text_native = [s.slide_number for s in slides if s.disposition == "TEXT_ONLY"]
    escalated = [s.slide_number for s in slides if s.disposition == "ESCALATE_VISUAL"]
    hitl = [s.slide_number for s in slides if s.disposition == "HITL_REQUIRED"]

    return PPTXPresentation(
        title=pres_title,
        slides=slides,
        raw_sha256=raw_hash,
        extracted_markdown=extracted_md,
        extracted_markdown_sha256=extracted_md_hash,
        slide_count=len(slides),
        text_native_slides=text_native,
        escalated_slides=escalated,
        hitl_slides=hitl,
    )


def extract_pptx_markdown(pptx_source: bytes | str | Path) -> str:
    """Convenience function to extract canonical Markdown directly from PPTX bytes or file path."""
    pres = extract_pptx_presentation(pptx_source)
    return pres.extracted_markdown


def render_pptx_slide_visual(slide_number: int, presentation: PPTXPresentation) -> Dict[str, Any]:
    """Request visual analysis for an escalated slide, enforcing text-native avoidance."""
    if slide_number < 1 or slide_number > len(presentation.slides):
        raise ValueError(f"Slide number {slide_number} out of bounds (1-{len(presentation.slides)})")
    slide = presentation.slides[slide_number - 1]
    if slide.disposition == "TEXT_ONLY":
        raise ValueError(
            f"Invariant violation: PPTX slide {slide_number} is text-native; routing to visual processing is prohibited"
        )
    return {
        "slide_number": slide_number,
        "locator": f"slide{slide_number}",
        "disposition": slide.disposition,
        "reason": slide.disposition_reason,
        "flagged_shape_locators": list(slide.flagged_shape_locators),
        "visual_action": "RENDER_AND_INSPECT_SHAPES",
    }
