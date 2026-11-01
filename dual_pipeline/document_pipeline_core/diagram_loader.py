"""
dual_pipeline/document_pipeline_core/diagram_loader.py

Text-first Vector and Architecture Diagram Loaders for SVG, Draw.io, and Visio (.vsdx).
Extracts shape labels, directional connectors, pages, and diagram topology without vision calls.
Enforces strict XXE mitigation, script stripping, depth limits, and zip-bomb protection.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.document_pipeline_core.format_source_units import SourceUnit
from dual_pipeline.parameters import get_document_security_parameters

logger = logging.getLogger(__name__)

_DOC_SEC_PARAMS = get_document_security_parameters()
MAX_DIAGRAM_BYTES: int = 50 * 1024 * 1024  # 50 MiB
MAX_XML_DEPTH: int = int(_DOC_SEC_PARAMS["max_xml_depth"])
MAX_XML_ELEMENTS: int = int(_DOC_SEC_PARAMS["max_xml_elements"])
MAX_ZIP_UNCOMPRESSED_BYTES: int = 50 * 1024 * 1024
MAX_ZIP_RATIO: float = float(_DOC_SEC_PARAMS["max_zip_ratio"])

_XXE_PATTERNS = (
    re.compile(rb"<!DOCTYPE[^>]*\[", re.IGNORECASE),
    re.compile(rb"<!ENTITY", re.IGNORECASE),
    re.compile(rb"SYSTEM\s+[\"']", re.IGNORECASE),
    re.compile(rb"PUBLIC\s+[\"']", re.IGNORECASE),
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_DANGEROUS_URI_RE = re.compile(r"^(javascript|vbscript|data\s*:\s*text\/html)\s*:", re.IGNORECASE)
_SAFE_XMLNS = frozenset({
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1999/xhtml",
    "http://schemas.microsoft.com/office/visio/2012/main",
    "http://schemas.openxmlformats.org/package/2006/relationships",
})


def _assert_safe_xml_bytes(raw_bytes: bytes, fmt: str) -> None:
    for pat in _XXE_PATTERNS:
        if pat.search(raw_bytes):
            raise ValueError(f"Malicious XML detected in {fmt}: DTD and external entities are strictly prohibited.")


def _clean_text(val: str) -> str:
    no_html = _HTML_TAG_RE.sub(" ", val)
    return re.sub(r"\s+", " ", no_html).strip()


def _is_dangerous_attr(attr_name: str, val: str) -> bool:
    if attr_name.lower().startswith("on"):
        return True
    if _DANGEROUS_URI_RE.search(val):
        return True
    if val.startswith(("http://", "https://")) and val not in _SAFE_XMLNS:
        return True
    return False


def _strip_dangerous_attributes(root: ET.Element) -> None:
    count = 0
    for elem in root.iter():
        count += 1
        if count > MAX_XML_ELEMENTS:
            raise ValueError(f"XML structure exceeds maximum element threshold of {MAX_XML_ELEMENTS}.")
        tag = elem.tag.split("}", 1)[-1].lower() if "}" in elem.tag else elem.tag.lower()
        if tag == "script":
            elem.text = ""
            elem.clear()
            continue
        for attr, val in list(elem.attrib.items()):
            if _is_dangerous_attr(attr, val):
                del elem.attrib[attr]


def _check_element_depth(elem: ET.Element, current_depth: int = 1) -> None:
    if current_depth > MAX_XML_DEPTH:
        raise ValueError(f"XML depth exceeds maximum allowed limit of {MAX_XML_DEPTH}.")
    for child in elem:
        _check_element_depth(child, current_depth + 1)


# --- SVG Extraction Helpers ---

def _find_svg_titles(root: ET.Element, source_hash: str) -> Tuple[str, List[SourceUnit]]:
    svg_title = ""
    units: List[SourceUnit] = []
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1].lower()
        if tag == "title" and elem.text and not svg_title:
            svg_title = _clean_text(elem.text)
        elif tag == "desc" and elem.text:
            desc_txt = _clean_text(elem.text)
            if desc_txt:
                units.append(
                    SourceUnit(
                        unit_id=f"{source_hash[:12]}:desc", source_hash=source_hash, format="svg",
                        unit_type="diagram_desc", locator="desc", content=desc_txt,
                    )
                )
    return svg_title, units


def _extract_svg_node_text(elem: ET.Element, tag: str) -> str:
    if tag == "foreignobject":
        return _clean_text("".join(elem.itertext()))
    if tag == "text":
        return _clean_text(" ".join(t.strip() for t in elem.itertext() if t and t.strip()))
    return _clean_text(elem.text or "")


def _find_svg_text_nodes(root: ET.Element, source_hash: str) -> Tuple[List[Tuple[str, str]], List[SourceUnit]]:
    nodes: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    idx = 0
    seen_locs: Set[str] = set()
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1].lower()
        if tag not in ("text", "tspan", "foreignobject"):
            continue
        clean = _extract_svg_node_text(elem, tag)
        if not clean:
            continue
        idx += 1
        sid = elem.get("id")
        loc = f"shape{sid}#text" if sid else f"text{idx}"
        if loc in seen_locs and clean in {v for _, v in nodes}:
            continue
        seen_locs.add(loc)
        nodes.append((loc, clean))
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="svg",
                unit_type="shape_label", locator=loc, content=clean,
                metadata={"tag": tag, "id": sid},
            )
        )
    return nodes, units


def _find_svg_shapes(root: ET.Element, source_hash: str) -> Tuple[List[Tuple[str, str]], List[SourceUnit]]:
    shapes: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1].lower()
        if tag in ("rect", "circle", "ellipse", "polygon", "path", "g"):
            sid = elem.get("id") or elem.get("data-id")
            label = elem.get("aria-label") or elem.get("data-label") or ""
            if sid and label:
                clean_lbl = _clean_text(label)
                loc = f"shape{sid}"
                shapes.append((loc, clean_lbl))
                units.append(
                    SourceUnit(
                        unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="svg",
                        unit_type="diagram_shape", locator=loc, content=clean_lbl,
                        metadata={"shape_type": tag, "id": sid},
                    )
                )
    return shapes, units


def _find_svg_connectors(root: ET.Element, source_hash: str) -> Tuple[List[Tuple[str, str]], List[SourceUnit]]:
    conns: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    idx = 0
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1].lower()
        is_conn = tag in ("line", "polyline") or (tag == "path" and ("marker-end" in elem.attrib or "marker-start" in elem.attrib))
        if is_conn:
            idx += 1
            cid = elem.get("id") or f"conn{idx}"
            loc = f"connector{cid}"
            f_ref, t_ref = elem.get("data-from") or "", elem.get("data-to") or ""
            desc = f"Connector from {f_ref} to {t_ref}" if f_ref and t_ref else f"Connector {cid}"
            conns.append((loc, desc))
            units.append(
                SourceUnit(
                    unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="svg",
                    unit_type="diagram_connector", locator=loc, content=desc,
                    metadata={"connector_id": cid, "from": f_ref, "to": t_ref},
                )
            )
    return conns, units


def _format_svg_markdown(title: str, text_nodes: List[Tuple[str, str]], shapes: List[Tuple[str, str]], conns: List[Tuple[str, str]]) -> str:
    lines = [f"# SVG Diagram: {title}", "", "## Overview"]
    lines.append(f"- **Labels Extracted**: {len(text_nodes)}")
    lines.append(f"- **Shapes Identified**: {len(shapes)}")
    lines.append(f"- **Connectors Identified**: {len(conns)}")
    lines.append("")
    if text_nodes:
        lines.append("## Text Labels & Locators")
        for loc, val in text_nodes:
            lines.append(f"- **`{loc}`**: {val}")
        lines.append("")
    if shapes:
        lines.append("## Shapes")
        for loc, val in shapes:
            lines.append(f"- **`{loc}`**: {val}")
        lines.append("")
    if conns:
        lines.append("## Connections")
        for loc, val in conns:
            lines.append(f"- **`{loc}`**: {val}")
        lines.append("")
    return "\n".join(lines)


def _extract_svg(raw_bytes: bytes, source_hash: str, title: str) -> Tuple[str, List[SourceUnit], Dict[str, Any]]:
    _assert_safe_xml_bytes(raw_bytes, "SVG")
    try:
        root = ET.fromstring(raw_bytes)
    except Exception as exc:
        raise ValueError(f"Malformed SVG XML: {exc}") from exc

    _check_element_depth(root)
    _strip_dangerous_attributes(root)

    doc_title, title_units = _find_svg_titles(root, source_hash)
    text_nodes, text_units = _find_svg_text_nodes(root, source_hash)
    shapes, shape_units = _find_svg_shapes(root, source_hash)
    conns, conn_units = _find_svg_connectors(root, source_hash)

    all_units = title_units + text_units + shape_units + conn_units
    markdown = _format_svg_markdown(doc_title or title, text_nodes, shapes, conns)
    coverage = {
        "total_records": len(all_units), "processed_records": len(all_units),
        "text_labels_count": len(text_nodes), "labels_extracted": len(text_nodes),
        "shapes_count": len(shapes), "total_shapes": len(shapes),
        "connectors_count": len(conns), "pages_count": 1, "complete": True,
    }
    return markdown, all_units, coverage


# --- Draw.io Extraction Helpers ---

def _decompress_drawio_payload(data_str: str) -> str:
    import zlib
    raw = base64.b64decode(data_str)
    try:
        decompressed = zlib.decompress(raw, -15)
    except Exception:
        decompressed = zlib.decompress(raw)
    return urllib.parse.unquote(decompressed.decode("utf-8"))


def _parse_drawio_model_xml(diag: ET.Element, p_name: str) -> ET.Element:
    if diag.text and diag.text.strip():
        try:
            decoded = _decompress_drawio_payload(diag.text.strip())
        except Exception as e:
            logger.debug("Failed decompressing Draw.io page %s: %s", p_name, e)
            return diag
        _assert_safe_xml_bytes(decoded.encode("utf-8"), "Draw.io inner XML")
        try:
            model = ET.fromstring(decoded)
        except Exception as exc:
            raise ValueError(f"Malformed Draw.io inner XML: {exc}") from exc
        _check_element_depth(model)
        _strip_dangerous_attributes(model)
        return model
    return diag


def _unwrap_drawio_object(elem: ET.Element, wrapped_cells: Set[int]) -> Optional[Dict[str, str]]:
    oid = elem.get("id") or ""
    lbl = elem.get("label") or elem.get("value") or ""
    child_cell = elem.find(".//mxCell")
    if child_cell is not None:
        wrapped_cells.add(id(child_cell))
        return {
            "id": oid or child_cell.get("id") or "",
            "value": lbl or child_cell.get("value") or "",
            "edge": child_cell.get("edge") or "",
            "source": child_cell.get("source") or "",
            "target": child_cell.get("target") or "",
        }
    if oid and lbl:
        return {"id": oid, "value": lbl, "edge": "", "source": "", "target": ""}
    return None


def _collect_drawio_cells(model: ET.Element) -> List[Dict[str, str]]:
    """Collect mxCell records, resolving <object label='...'> and <UserObject label='...'> wrappers."""
    records: List[Dict[str, str]] = []
    wrapped_cells: Set[int] = set()
    for elem in model.iter():
        if elem.tag.split("}", 1)[-1] in ("object", "UserObject"):
            rec = _unwrap_drawio_object(elem, wrapped_cells)
            if rec:
                records.append(rec)
    for cell in model.findall(".//mxCell"):
        if id(cell) not in wrapped_cells:
            records.append({
                "id": cell.get("id") or "",
                "value": cell.get("value") or "",
                "edge": cell.get("edge") or "",
                "source": cell.get("source") or "",
                "target": cell.get("target") or "",
            })
    return records


def _extract_page_shapes(cells: List[Dict[str, str]], clean_p_name: str, p_name: str, source_hash: str) -> Tuple[Dict[str, str], List[Tuple[str, str]], List[SourceUnit]]:
    shapes_map: Dict[str, str] = {}
    page_shapes: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    for cell in cells:
        cid = cell.get("id") or ""
        val = cell.get("value") or ""
        clean_v = _clean_text(val)
        if clean_v and cell.get("edge") != "1":
            shapes_map[cid] = clean_v
            loc = f"page{clean_p_name}#cell{cid}"
            page_shapes.append((loc, clean_v))
            units.append(
                SourceUnit(
                    unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="drawio",
                    unit_type="diagram_shape", locator=loc, content=clean_v,
                    metadata={"page": p_name, "cell_id": cid},
                )
            )
    return shapes_map, page_shapes, units


def _extract_page_edges(cells: List[Dict[str, str]], shapes_map: Dict[str, str], clean_p_name: str, p_name: str, source_hash: str) -> Tuple[List[Tuple[str, str]], List[SourceUnit]]:
    edges: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    for cell in cells:
        cid = cell.get("id") or ""
        val = cell.get("value") or ""
        clean_v = _clean_text(val)
        src = cell.get("source") or ""
        tgt = cell.get("target") or ""
        if cell.get("edge") == "1" and (src or tgt):
            s_lbl = shapes_map.get(src, f"node_{src}")
            t_lbl = shapes_map.get(tgt, f"node_{tgt}")
            desc = f"{s_lbl} -> {t_lbl}" + (f": {clean_v}" if clean_v else "")
            loc = f"page{clean_p_name}#connector{cid}"
            edges.append((loc, desc))
            units.append(
                SourceUnit(
                    unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="drawio",
                    unit_type="diagram_connector", locator=loc, content=desc,
                    metadata={"page": p_name, "connector_id": cid, "source": src, "target": tgt},
                )
            )
    return edges, units


def _format_drawio_markdown(title: str, page_reports: List[Dict[str, Any]]) -> str:
    lines = [f"# Draw.io Diagram: {title}", "", "## Overview"]
    lines.append(f"- **Pages**: {len(page_reports)}")
    lines.append(f"- **Total Shapes**: {sum(len(p['shapes']) for p in page_reports)}")
    lines.append(f"- **Total Connectors**: {sum(len(p['edges']) for p in page_reports)}")
    lines.append("")
    for prep in page_reports:
        lines.append(f"## Page: {prep['name']}")
        if prep["shapes"]:
            lines.append("### Shapes & Nodes")
            for loc, val in prep["shapes"]:
                lines.append(f"- **`{loc}`**: {val}")
        if prep["edges"]:
            lines.append("### Connectors & Edges")
            for loc, val in prep["edges"]:
                lines.append(f"- **`{loc}`**: {val}")
        lines.append("")
    return "\n".join(lines)


def _extract_drawio(raw_bytes: bytes, source_hash: str, title: str) -> Tuple[str, List[SourceUnit], Dict[str, Any]]:
    _assert_safe_xml_bytes(raw_bytes, "Draw.io")
    try:
        root = ET.fromstring(raw_bytes)
    except Exception as exc:
        raise ValueError(f"Malformed Draw.io XML: {exc}") from exc

    _check_element_depth(root)
    _strip_dangerous_attributes(root)

    diagram_nodes = root.findall(".//diagram") or [root]
    all_units: List[SourceUnit] = []
    page_reports: List[Dict[str, Any]] = []

    for p_idx, diag in enumerate(diagram_nodes, start=1):
        p_name = diag.get("name") or f"Page-{p_idx}"
        clean_p_name = re.sub(r"[^\w\-]+", "_", p_name)
        model = _parse_drawio_model_xml(diag, p_name)
        cells = _collect_drawio_cells(model)

        shapes_map, p_shapes, s_units = _extract_page_shapes(cells, clean_p_name, p_name, source_hash)
        p_edges, e_units = _extract_page_edges(cells, shapes_map, clean_p_name, p_name, source_hash)

        all_units.extend(s_units + e_units)
        page_reports.append({"name": p_name, "shapes": p_shapes, "edges": p_edges})

    markdown = _format_drawio_markdown(title, page_reports)
    coverage = {
        "total_records": len(all_units), "processed_records": len(all_units),
        "shapes_count": sum(len(p["shapes"]) for p in page_reports),
        "connectors_count": sum(len(p["edges"]) for p in page_reports),
        "pages_count": len(page_reports), "complete": True,
    }
    return markdown, all_units, coverage


# --- Visio (.vsdx) Extraction Helpers ---

def _validate_vsdx_zip_safety(raw_bytes: bytes) -> zipfile.ZipFile:
    if len(raw_bytes) > MAX_DIAGRAM_BYTES:
        raise ValueError(f"VSDX file exceeds maximum allowed size of {MAX_DIAGRAM_BYTES // (1024*1024)} MiB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise ValueError(f"Invalid or corrupted VSDX container: {exc}") from exc

    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError("VSDX container exceeds maximum uncompressed size limit.")
    if raw_bytes and (total_uncompressed / max(1, len(raw_bytes))) > MAX_ZIP_RATIO:
        raise ValueError("VSDX archive has an unsafe compression ratio (zip bomb protection).")
    return zf


def _discover_vsdx_pages(zf: zipfile.ZipFile, names: Set[str]) -> List[Tuple[str, str]]:
    page_entries: List[Tuple[str, str]] = []
    if "visio/pages/pages.xml" in names:
        pages_xml = zf.read("visio/pages/pages.xml")
        _assert_safe_xml_bytes(pages_xml, "VSDX pages.xml")
        root_pages = ET.fromstring(pages_xml)
        for p_elem in root_pages.findall(".//{http://schemas.microsoft.com/office/visio/2012/main}Page"):
            p_name = p_elem.get("Name") or f"Page-{p_elem.get('ID', '0')}"
            page_entries.append((f"visio/pages/page{len(page_entries)+1}.xml", p_name))
    if not page_entries:
        for name in sorted(names):
            if re.match(r"^visio/pages/page\d+\.xml$", name):
                page_entries.append((name, Path(name).stem))
    return page_entries


def _extract_vsdx_page_shapes(root_p: ET.Element, clean_p_name: str, p_name: str, source_hash: str) -> Tuple[List[Tuple[str, str]], List[SourceUnit]]:
    shapes: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    for shape in root_p.findall(".//{http://schemas.microsoft.com/office/visio/2012/main}Shape"):
        sid = shape.get("ID") or ""
        text_elem = shape.find(".//{http://schemas.microsoft.com/office/visio/2012/main}Text")
        if text_elem is not None:
            clean_s = _clean_text("".join(text_elem.itertext()))
            if clean_s:
                loc = f"page{clean_p_name}#shape{sid}"
                shapes.append((loc, clean_s))
                units.append(
                    SourceUnit(
                        unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="vsdx",
                        unit_type="visio_shape", locator=loc, content=clean_s,
                        metadata={"page": p_name, "shape_id": sid},
                    )
                )
    return shapes, units


def _format_vsdx_connector_endpoints(
    f_sheet: str,
    endpoints: List[Tuple[str, str]],
    p_name: str,
    s_map: Dict[str, str],
) -> Tuple[str, Dict[str, Any]]:
    if len(endpoints) >= 2:
        begin_tgt = next((t for c, t in endpoints if "begin" in c.lower()), endpoints[0][1])
        end_tgt = next((t for c, t in endpoints if "end" in c.lower()), endpoints[-1][1])
        b_lbl = s_map.get(begin_tgt, f"shape {begin_tgt}")
        e_lbl = s_map.get(end_tgt, f"shape {end_tgt}")
        desc = f"Connection from shape {f_sheet} to shape {begin_tgt} and shape {end_tgt} ({b_lbl} -> {e_lbl})"
        return desc, {"page": p_name, "from_sheet": f_sheet, "source_shape": begin_tgt, "target_shape": end_tgt, "to_sheet": end_tgt}
    t_sheet = endpoints[0][1]
    t_lbl = s_map.get(t_sheet, f"shape {t_sheet}")
    return f"Connection from shape {f_sheet} to shape {t_sheet} ({t_lbl})", {"page": p_name, "from_sheet": f_sheet, "to_sheet": t_sheet}


def _extract_vsdx_page_connectors(
    root_p: ET.Element,
    clean_p_name: str,
    p_name: str,
    source_hash: str,
    shapes_by_id: Optional[Dict[str, str]] = None,
) -> Tuple[List[Tuple[str, str]], List[SourceUnit]]:
    connectors: List[Tuple[str, str]] = []
    units: List[SourceUnit] = []
    s_map = shapes_by_id or {}
    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for conn in root_p.findall(".//{http://schemas.microsoft.com/office/visio/2012/main}Connect"):
        f_sheet, t_sheet = conn.get("FromSheet") or "", conn.get("ToSheet") or ""
        if f_sheet and t_sheet:
            grouped.setdefault(f_sheet, []).append((conn.get("FromCell") or "", t_sheet))

    for f_sheet, endpoints in grouped.items():
        loc = f"page{clean_p_name}#connector{f_sheet}"
        desc, meta = _format_vsdx_connector_endpoints(f_sheet, endpoints, p_name, s_map)
        connectors.append((loc, desc))
        units.append(
            SourceUnit(
                unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format="vsdx",
                unit_type="visio_connector", locator=loc, content=desc,
                metadata=meta,
            )
        )
    return connectors, units


def _format_vsdx_markdown(title: str, page_reports: List[Dict[str, Any]]) -> str:
    lines = [f"# Visio Diagram: {title}", "", "## Overview"]
    lines.append(f"- **Pages**: {len(page_reports)}")
    lines.append(f"- **Total Shapes**: {sum(len(p['shapes']) for p in page_reports)}")
    lines.append(f"- **Total Connectors**: {sum(len(p['connectors']) for p in page_reports)}")
    lines.append("")
    for prep in page_reports:
        lines.append(f"## Page: {prep['name']}")
        if prep["shapes"]:
            lines.append("### Shapes & Text")
            for loc, val in prep["shapes"]:
                lines.append(f"- **`{loc}`**: {val}")
        if prep["connectors"]:
            lines.append("### Connectors")
            for loc, val in prep["connectors"]:
                lines.append(f"- **`{loc}`**: {val}")
        lines.append("")
    return "\n".join(lines)


def _extract_vsdx(raw_bytes: bytes, source_hash: str, title: str) -> Tuple[str, List[SourceUnit], Dict[str, Any]]:
    zf = _validate_vsdx_zip_safety(raw_bytes)
    names = set(zf.namelist())
    if not any(k.startswith("visio/") for k in names):
        raise ValueError("Invalid VSDX container: missing 'visio/' directory structures.")

    page_entries = _discover_vsdx_pages(zf, names)
    all_units: List[SourceUnit] = []
    page_reports: List[Dict[str, Any]] = []

    for page_file, p_name in page_entries:
        if page_file not in names:
            continue
        p_bytes = zf.read(page_file)
        _assert_safe_xml_bytes(p_bytes, f"VSDX {page_file}")
        root_p = ET.fromstring(p_bytes)
        _check_element_depth(root_p)
        _strip_dangerous_attributes(root_p)

        clean_p_name = re.sub(r"[^\w\-]+", "_", p_name)
        p_shapes, s_units = _extract_vsdx_page_shapes(root_p, clean_p_name, p_name, source_hash)
        s_by_id = {u.metadata.get("shape_id", ""): u.content for u in s_units if u.metadata.get("shape_id")}
        p_conns, c_units = _extract_vsdx_page_connectors(root_p, clean_p_name, p_name, source_hash, shapes_by_id=s_by_id)

        all_units.extend(s_units + c_units)
        page_reports.append({"name": p_name, "shapes": p_shapes, "connectors": p_conns})

    markdown = _format_vsdx_markdown(title, page_reports)
    coverage = {
        "total_records": len(all_units), "processed_records": len(all_units),
        "shapes_count": sum(len(p["shapes"]) for p in page_reports),
        "total_shapes": sum(len(p["shapes"]) for p in page_reports),
        "connectors_count": sum(len(p["connectors"]) for p in page_reports),
        "pages_count": len(page_reports), "complete": True,
    }
    return markdown, all_units, coverage


# --- Main Dispatcher & Adapters ---

def extract_diagram_with_locators(
    source_p: Path,
    raw_bytes: bytes,
    metadata: Optional[Dict[str, Any]] = None,
    ext: str = "",
) -> str:
    """Extract structured diagram markdown and atomic source units with locators."""
    if len(raw_bytes) > MAX_DIAGRAM_BYTES:
        raise ValueError(f"Diagram payload exceeds maximum allowed size of {MAX_DIAGRAM_BYTES // (1024*1024)} MiB.")

    clean_ext = (ext or source_p.suffix).lower()
    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    title = source_p.stem.replace("_", " ").title()

    if clean_ext == ".svg":
        markdown, units, coverage = _extract_svg(raw_bytes, source_hash, title)
    elif clean_ext == ".drawio":
        markdown, units, coverage = _extract_drawio(raw_bytes, source_hash, title)
    elif clean_ext == ".vsdx":
        markdown, units, coverage = _extract_vsdx(raw_bytes, source_hash, title)
    else:
        raise ValueError(f"Unsupported diagram format '{clean_ext}'. Allowed: .svg, .drawio, .vsdx")

    if metadata is not None:
        metadata["_source_coverage"] = coverage
        metadata["source_coverage"] = coverage
        metadata["source_units"] = [u.to_dict() for u in units]

    return markdown


def build_diagram_source_units(ext: str, raw_bytes: bytes, source_hash: str) -> List[SourceUnit]:
    """Helper to construct source units for diagram formats."""
    dummy_meta: Dict[str, Any] = {}
    extract_diagram_with_locators(Path(f"diagram{ext}"), raw_bytes, dummy_meta, ext=ext)
    units_dict = dummy_meta.get("source_units", [])
    return [
        SourceUnit(
            unit_id=u["unit_id"], source_hash=source_hash, format=u["format"],
            unit_type=u["unit_type"], locator=u["locator"], content=u["content"],
            metadata=u.get("metadata", {}), digest=u.get("digest", ""),
        )
        for u in units_dict
    ]


def load_svg_diagram(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    meta: Dict[str, Any] = {}
    text = extract_diagram_with_locators(path, raw, meta, ext=".svg")
    return {"text": text, "source_coverage": meta.get("source_coverage", {}), "source_units": meta.get("source_units", [])}


def load_drawio_diagram(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    meta: Dict[str, Any] = {}
    text = extract_diagram_with_locators(path, raw, meta, ext=".drawio")
    return {"text": text, "source_coverage": meta.get("source_coverage", {}), "source_units": meta.get("source_units", [])}


def load_vsdx_diagram(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    meta: Dict[str, Any] = {}
    text = extract_diagram_with_locators(path, raw, meta, ext=".vsdx")
    return {"text": text, "source_coverage": meta.get("source_coverage", {}), "source_units": meta.get("source_units", [])}
