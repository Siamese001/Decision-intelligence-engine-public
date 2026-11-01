"""Deterministic structural census and row-level tabular/workbook extractor (Wave 1 Gate C1)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
    build_grounded_evidence_entry,
    slugify_entity_id,
)

_PAGE_HDR_RE = re.compile(r"(?:^|\n)\s*(?:##+\s*(?:Page|Slide)\s+(\d+)|---\s*Page\s+(\d+)\s*---)", re.I)
_SLIDE_HDR_RE = re.compile(r"(?:^|\n)\s*##+\s*Slide\s+(\d+)\b", re.I)
_SHEET_HDR_RE = re.compile(r"(?:^|\n)\s*##+\s*(?:Sheet|Tab|Worksheet)\s*:?\s*([^\n]+)", re.I)
_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")
_NULL_CELL_TOKENS = frozenset({"nan", "none", "null"})


def _split_md_row(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _is_separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL_RE.match(c) for c in cells if c)


def _non_empty_cells(cells: List[str]) -> List[str]:
    return [c for c in cells if c and c.lower() not in _NULL_CELL_TOKENS]


def _scan_tables_and_rows(lines: List[str]) -> Tuple[int, int, Dict[str, int]]:
    current_sheet = "default"
    sheet_rows: Dict[str, int] = {}
    total_data_rows, total_cells = 0, 0
    in_table = False

    for line in lines:
        s = line.strip()
        m_sheet = _SHEET_HDR_RE.match(s)
        if m_sheet:
            current_sheet = m_sheet.group(1).strip()
            sheet_rows.setdefault(current_sheet, 0)
            in_table = False
            continue
        if not (s.startswith("|") and s.endswith("|") and len(s) > 2):
            in_table = False
            continue
        cells = _split_md_row(s)
        if _is_separator_row(cells) or not in_table:
            in_table = True
            continue
        non_empty = _non_empty_cells(cells)
        if non_empty:
            total_data_rows += 1
            total_cells += len(non_empty)
            sheet_rows[current_sheet] = sheet_rows.get(current_sheet, 0) + 1
    return total_data_rows, total_cells, sheet_rows


def _collect_ev_sheet_names(meta: Dict[str, Any], sheet_names: List[str]) -> None:
    ev_index = meta.get("evidence_index") or {}
    for ev in (ev_index.values() if isinstance(ev_index, dict) else []):
        loc_val = str(((ev or {}).get("locator") or {}).get("value") or "")
        if "!" in loc_val:
            sh = loc_val.split("!", 1)[0].strip()
            if sh and sh not in sheet_names:
                sheet_names.append(sh)


def compute_raw_structural_census(
    document_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute pre-extraction structural unit census across pages, slides, sheets, and table rows."""
    text = document_text or ""
    meta = metadata or {}
    page_nums = [int(m.group(1) or m.group(2)) for m in _PAGE_HDR_RE.finditer(text)]
    slide_nums = [int(m.group(1)) for m in _SLIDE_HDR_RE.finditer(text)]
    sheet_names = [m.group(1).strip() for m in _SHEET_HDR_RE.finditer(text)]
    _collect_ev_sheet_names(meta, sheet_names)

    table_row_count, table_cell_count, sheet_rows = _scan_tables_and_rows(text.splitlines())
    for sh in sheet_names:
        sheet_rows.setdefault(sh, 0)

    has_text = 1 if text.strip() else 0
    page_count = max(page_nums) if page_nums else (int(meta.get("page_count") or 0) or has_text)
    slide_count = max(slide_nums) if slide_nums else 0
    substantive_units = max(page_count, slide_count, len(sheet_names) + table_row_count, has_text)

    return {
        "page_count": page_count,
        "slide_count": slide_count,
        "sheet_names": sheet_names,
        "non_empty_row_count_per_sheet": sheet_rows,
        "table_row_count": table_row_count,
        "table_cell_count": table_cell_count,
        "substantive_unit_count": substantive_units,
    }


def _build_table_entry(sheet: str, t_lines: List[Tuple[int, str]]) -> Optional[Dict[str, Any]]:
    if len(t_lines) < 2:
        return None
    headers = _split_md_row(t_lines[0][1])
    rows = []
    for line_no, raw_ln in t_lines[1:]:
        cells = _split_md_row(raw_ln)
        if not _is_separator_row(cells) and _non_empty_cells(cells):
            rows.append((line_no, raw_ln, cells))
    return {"sheet": sheet, "headers": headers, "rows": rows} if rows else None


def _parse_contiguous_md_tables(document_text: str) -> List[Dict[str, Any]]:
    lines = (document_text or "").splitlines()
    tables: List[Dict[str, Any]] = []
    current_sheet = "Document"
    t_lines: List[Tuple[int, str]] = []

    for idx, raw_line in enumerate(lines):
        s = raw_line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 2:
            t_lines.append((idx + 1, s))
            continue
        if t_lines:
            tbl = _build_table_entry(current_sheet, t_lines)
            if tbl:
                tables.append(tbl)
            t_lines = []
        m_sh = _SHEET_HDR_RE.match(s)
        if m_sh:
            current_sheet = m_sh.group(1).strip()

    if t_lines:
        tbl = _build_table_entry(current_sheet, t_lines)
        if tbl:
            tables.append(tbl)
    return tables


def _format_row_description(sheet: str, headers: List[str], cells: List[str]) -> Tuple[str, str]:
    pairs: List[str] = []
    primary_name = ""
    for col_idx, val in enumerate(cells):
        clean_val = val.strip()
        if not clean_val or clean_val.lower() in _NULL_CELL_TOKENS:
            continue
        hdr = headers[col_idx].strip() if col_idx < len(headers) and headers[col_idx].strip() else f"Col{col_idx + 1}"
        if not primary_name and len(clean_val) <= 120:
            primary_name = clean_val
        pairs.append(f"{hdr}: {clean_val}")
    return (primary_name or f"{sheet} Row"), (f"[{sheet}] " + " | ".join(pairs))[:480]


def _infer_row_entity_type(headers: List[str], cells: List[str]) -> str:
    first_hdr = headers[0].strip().lower() if headers else ""
    if "program" in first_hdr:
        return "program"
    if "product" in first_hdr and "manager" not in first_hdr:
        return "product"
    if any(k in first_hdr for k in ("workstream", "mission", "charter")):
        return "workstream"
    is_metric = any("%" in c or c.replace(",", "").replace(".", "").isdigit() for c in cells[1:] if c)
    return "metric" if is_metric else "system"


def _wire_portfolio_table_columns(
    row_counter: int, headers: List[str], cells: List[str], ent_id: str, ev_id: str,
    ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
) -> None:
    for idx, hdr_raw in enumerate(headers):
        if idx >= len(cells):
            continue
        val = cells[idx].strip()
        if not val or val.lower() in _NULL_CELL_TOKENS or val.lower() in ("n/a", "unassigned", "tbd"):
            continue
        hdr = hdr_raw.strip().lower()
        if hdr in ("owner", "pm", "product manager", "program owner") and len(val) <= 60:
            pid = slugify_entity_id("person", val)
            ent_map.setdefault(pid, {"id": pid, "type": "person", "name": val, "description": f"{hdr_raw.strip()}: {val}", "mutation": "CONFIRM", "epistemic_status": "CONFIRMED", "evidence_ids": [ev_id]})
            sig_map[f"sig-tbl-own-{row_counter:03d}-{idx}"] = {
                "signal_id": f"sig-tbl-own-{row_counter:03d}-{idx}", "from_id": pid, "to_id": ent_id,
                "relationship": "OWNS_OR_LEADS", "predicate": "OWNS_OR_LEADS",
                "description": f"{val} OWNS_OR_LEADS {ent_id}", "evidence_ids": [ev_id],
                "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            }


def _append_table_row_projection(
    row_counter: int, sheet: str, headers: List[str], line_no: int, raw_ln: str,
    cells: List[str], root_id: str, document_text: str,
    ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
) -> None:
    primary_name, desc = _format_row_description(sheet, headers, cells)
    ev_id = f"ev-tbl-row-{row_counter:03d}"
    loc_val = f"{sheet}!R{line_no}" if sheet != "Document" else f"L{line_no}"
    ev_idx[ev_id] = build_grounded_evidence_entry(
        ev_id, desc, raw_ln, loc_val, document_text, locator_kind="table_row", resolve_verbatim=True
    )
    ent_type = _infer_row_entity_type(headers, cells)
    ent_id = slugify_entity_id(ent_type, f"{sheet}-{primary_name}-{row_counter}")
    ent_map[ent_id] = {
        "id": ent_id, "type": ent_type, "name": primary_name, "description": desc,
        "mutation": "CREATE", "epistemic_status": "CONFIRMED", "evidence_ids": [ev_id],
    }
    sig_id = f"sig-tbl-{row_counter:03d}"
    sig_map[sig_id] = {
        "signal_id": sig_id, "from_id": root_id, "to_id": ent_id,
        "relationship": "HAS_COMPONENT", "predicate": "HAS_COMPONENT",
        "description": desc, "evidence_ids": [ev_id],
        "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
    }
    _wire_portfolio_table_columns(row_counter, headers, cells, ent_id, ev_id, ent_map, sig_map)


def extract_tabular_workbook_and_table_projection(
    document_text: str,
    document_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract row-level evidence, entities, and edges from markdown tables and multi-sheet workbooks."""
    meta = dict(document_metadata or {})
    tables = _parse_contiguous_md_tables(document_text)
    if not tables:
        return {
            "schema_version": "1.0.0", "is_document": True, "evidence_index": {},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        }

    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    doc_title = str(meta.get("document_title") or meta.get("title") or meta.get("meeting_id") or "Tabular Document").strip()
    root_id = slugify_entity_id("system", doc_title)
    ev_root = build_grounded_evidence_entry(
        "ev-tabular-root", f"{doc_title} structural table root.", tables[0]["rows"][0][1], "L1", document_text, locator_kind="section", resolve_verbatim=True
    )
    ev_idx[ev_root["evidence_id"]] = ev_root
    ent_map[root_id] = {
        "id": root_id, "type": "system", "name": doc_title,
        "description": f"Authoritative tabular specification and dataset for {doc_title}.",
        "mutation": "CONFIRM", "epistemic_status": "CONFIRMED", "evidence_ids": [ev_root["evidence_id"]],
    }

    row_counter = 0
    for tbl in tables:
        for line_no, raw_ln, cells in tbl["rows"]:
            row_counter += 1
            _append_table_row_projection(row_counter, tbl["sheet"], tbl["headers"], line_no, raw_ln, cells, root_id, document_text, ev_idx, ent_map, sig_map)

    return {
        "schema_version": "1.0.0", "is_document": True, "output3_only": True,
        "max_mutation_budget": max(120, len(ent_map) + len(sig_map) + 20),
        "evidence_index": ev_idx,
        "graph_mutations": {
            "entity_upserts": list(ent_map.values()),
            "relationship_signals": list(sig_map.values()),
            "decision_updates": [], "proposal_updates": [], "risk_updates": [], "strategic_intent_signals": [],
        },
    }


def _ev_kind(ev: Dict[str, Any]) -> str:
    loc = ev.get("locator")
    return str(loc.get("kind") or "") if isinstance(loc, dict) else ""


def _ev_text(ev: Dict[str, Any]) -> str:
    return str(ev.get("exact_text") or ev.get("text") or "").strip()


def _restore_entity_ampersand(ent: Dict[str, Any], evs: List[Dict[str, Any]], changes: List[str]) -> None:
    row_ev = next((e for e in evs if _ev_kind(e) == "table_row"), None)
    if not row_ev:
        return
    row_txt = _ev_text(row_ev)
    if row_txt and " & " in row_txt and " & " not in str(ent.get("name") or ""):
        ent["name"] = row_txt
        changes.append(f"Restored ampersand in tabular entity name '{row_txt}'")


def _enrich_single_entity_from_evidence(ent: Dict[str, Any], ev_idx: Dict[str, Any], changes: List[str]) -> None:
    eids = ent.get("evidence_ids") or []
    evs = [ev_idx[eid] for eid in eids if isinstance(ev_idx.get(eid), dict)]
    _restore_entity_ampersand(ent, evs, changes)
    vals = [_ev_text(e) for e in evs if _ev_kind(e) == "table_value" and _ev_text(e)]
    cur_desc = str(ent.get("description") or "").strip()
    if any(v not in cur_desc for v in vals):
        label = str(ent.get("name") or "Metric").strip()
        joined = " / ".join(vals)
        ent["description"] = f"{label}: {joined} ({cur_desc})" if cur_desc else f"{label}: {joined}"
        changes.append(f"Enriched tabular metric '{ent.get('id')}' with values {vals}")


def _connect_metric_entities(entities: List[Dict[str, Any]], signals: List[Dict[str, Any]], changes: List[str]) -> None:
    last_id = str(entities[-1].get("id") or "")
    root_ent = entities[-1] if "total" in last_id else entities[0]
    root_id = str(root_ent.get("id") or "")
    for idx, ent in enumerate(entities):
        eid = str(ent.get("id") or "")
        if eid and eid != root_id:
            signals.append({
                "signal_id": f"sig-metric-part-{idx:02d}", "from_id": root_id, "to_id": eid,
                "relationship": "MEASURES", "predicate": "MEASURES",
                "description": str(ent.get("description") or ent.get("name") or ""),
                "evidence_ids": list(ent.get("evidence_ids") or []),
                "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
    changes.append(f"Connected {len(signals)} tabular metric entities to '{root_id}'")


def _enrich_table_value_entities(final_data: Dict[str, Any]) -> List[str]:
    changes: List[str] = []
    ev_idx = final_data.get("evidence_index")
    muts = final_data.get("graph_mutations")
    if not isinstance(ev_idx, dict) or not isinstance(muts, dict):
        return changes
    entities = [e for e in (muts.get("entity_upserts") or []) if isinstance(e, dict)]
    signals = muts.setdefault("relationship_signals", [])
    for ent in entities:
        _enrich_single_entity_from_evidence(ent, ev_idx, changes)
    if len(entities) >= 2 and not signals:
        _connect_metric_entities(entities, signals, changes)
    return changes


def reconcile_tabular_row_mutations(
    final_data: Dict[str, Any],
    document_metadata: Dict[str, Any],
    document_text: str,
) -> List[str]:
    """Ensure tabular metrics and multi-sheet workbooks preserve row-level values and names."""
    changes = _enrich_table_value_entities(final_data)
    census = compute_raw_structural_census(document_text, {**(document_metadata or {}), "evidence_index": final_data.get("evidence_index")})
    document_metadata["structural_census"] = census
    muts = final_data.get("graph_mutations") or {}
    ent_count = len(muts.get("entity_upserts") or [])
    if census["table_row_count"] >= 4 and ent_count < max(3, int(census["table_row_count"] * 0.5)):
        from dual_pipeline.document_pipeline_core.signal_merger import merge_specialized_mutations_additive
        tbl_proj = extract_tabular_workbook_and_table_projection(document_text, document_metadata)
        if (tbl_proj.get("graph_mutations") or {}).get("entity_upserts"):
            merge_specialized_mutations_additive(final_data, tbl_proj)
            changes.append(f"Merged {len(tbl_proj['graph_mutations']['entity_upserts'])} row-level tabular entities")
    return changes
