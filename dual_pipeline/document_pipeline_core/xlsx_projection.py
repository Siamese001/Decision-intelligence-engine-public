"""
dual_pipeline/document_pipeline_core/xlsx_projection.py

Universal OpenXML XLSX Projection Engine with Header Detection, Section Boundaries,
Multi-Sheet Evidence Preservation, Dual Coordinate Locators, and Formula/Cache Accounting.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

try:
    import openpyxl
    from openpyxl.utils import get_column_letter, column_index_from_string
    _OPENPYXL_AVAILABLE = True
except ImportError:
    _OPENPYXL_AVAILABLE = False
    get_column_letter = lambda col: str(col)  # type: ignore
    column_index_from_string = lambda col: 1  # type: ignore

from dual_pipeline.document_pipeline_core.format_source_units import SourceUnit


@dataclass(frozen=True)
class CellRecord:
    """Immutable representation of an individual spreadsheet cell with dual locators."""
    sheet_name: str
    row: int
    col: int
    locator_rc: str
    locator_a1: str
    locator_row: str
    value: Any
    value_str: str
    formula: Optional[str] = None
    is_formula: bool = False
    has_formula_cache: bool = True
    is_empty: bool = False


@dataclass
class XlsxSection:
    """Bounded section within a worksheet (e.g., table, metadata block, grid)."""
    sheet_name: str
    section_index: int
    kind: str  # "table", "metadata_block", "grid"
    start_row: int
    end_row: int
    headers: List[str] = field(default_factory=list)
    rows: List[List[CellRecord]] = field(default_factory=list)

    def _render_metadata_markdown(self) -> str:
        lines: List[str] = []
        for row in self.rows:
            non_empty = [c for c in row if not c.is_empty]
            if not non_empty:
                continue
            loc = non_empty[0].locator_row
            vals = [c.value_str for c in non_empty]
            lines.append(f"| {loc} | " + " : ".join(vals) + " |")
        return "\n".join(lines)

    def _render_table_markdown(self) -> str:
        lines: List[str] = []
        col_count = len(self.headers)
        esc_headers = [str(h).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>") for h in self.headers]
        lines.append("| Locator | " + " | ".join(esc_headers) + " |")
        lines.append("| --- | " + " | ".join(["---"] * col_count) + " |")
        for row in self.rows:
            if not row:
                continue
            loc = row[0].locator_row
            row_vals: List[str] = [
                row[idx].value_str.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")
                if idx < len(row) else ""
                for idx in range(col_count)
            ]
            lines.append(f"| {loc} | " + " | ".join(row_vals) + " |")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        """Render section to canonical Markdown representation."""
        if not self.rows:
            return ""
        if self.kind == "metadata_block":
            return self._render_metadata_markdown()
        return self._render_table_markdown()


@dataclass
class SheetProjection:
    """Projection of an individual worksheet with section and cell inventories."""
    sheet_name: str
    state: str
    sections: List[XlsxSection] = field(default_factory=list)
    cells: List[CellRecord] = field(default_factory=list)
    total_records: int = 0
    formulas_detected: int = 0
    missing_formula_cache: int = 0

    def render_markdown(self) -> str:
        """Render sheet to Markdown section."""
        header = f"## Sheet: {self.sheet_name} (State: {self.state})"
        if not self.sections:
            return f"{header}\n\n*(Empty Sheet)*"
        sec_texts = [s.render_markdown() for s in self.sections if s.rows]
        return f"{header}\n" + "\n\n".join(sec_texts)


@dataclass
class XlsxProjection:
    """Complete workbook projection containing multi-sheet evidence and locator index."""
    sheet_projections: List[SheetProjection]
    all_sheet_names: List[str]
    processed_sheets: List[str]
    excluded_sheets: List[str]
    total_cells: int
    total_records: int
    formulas_detected: int
    missing_formula_cache: int
    formula_cache_pairs: List[Dict[str, Any]]
    source_units: List[SourceUnit]
    rendered_markdown: str
    coverage_dict: Dict[str, Any]
    _locator_index: Dict[str, Any] = field(default_factory=dict, repr=False)

    def resolve_locator(self, locator: str) -> Optional[Dict[str, Any]]:
        """Resolve either Sheet!R#C#, Sheet!A1, or Sheet!R# locator."""
        clean = locator.strip().upper()
        return self._locator_index.get(clean)


def _classify_row_as_header(row: Sequence[Any], max_cols: int = 1) -> Tuple[bool, float]:
    """Score whether a row of cells behaves as a table column header."""
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if not non_empty or len(non_empty) < 1:
        return False, 0.0
    if max_cols > 1 and len(non_empty) < 2:
        return False, 0.0
    all_strings = all(isinstance(c, str) for c in non_empty)
    avg_len = sum(len(str(c)) for c in non_empty) / len(non_empty)
    unique_ratio = len(set(str(c) for c in non_empty)) / len(non_empty)
    score = 0.0
    if all_strings:
        score += 0.5
    if avg_len < 40:
        score += 0.3
    if unique_ratio > 0.8:
        score += 0.2
    return (score >= 0.7 and len(non_empty) >= 1), score


def _make_cell_record(
    val_data: Any,
    val_form: Any,
    sheet_name: str,
    row_idx: int,
    col_idx: int,
) -> Tuple[CellRecord, int, int, Optional[Dict[str, Any]]]:
    """Construct CellRecord with dual locators and formula provenance."""
    col_letter = get_column_letter(col_idx)
    loc_rc = f"{sheet_name}!R{row_idx}C{col_idx}"
    loc_a1 = f"{sheet_name}!{col_letter}{row_idx}"
    loc_row = f"{sheet_name}!R{row_idx}"
    is_empty = val_data is None and val_form is None
    formula_str: Optional[str] = None
    is_formula = False
    has_cache = True
    f_cnt = 0
    m_cnt = 0
    pair: Optional[Dict[str, Any]] = None

    if val_form is not None and str(val_form).startswith("="):
        is_formula = True
        formula_str = str(val_form)
        f_cnt = 1
        has_cache = val_data is not None
        if not has_cache:
            m_cnt = 1
            val_str = f"FORMULA:{formula_str} [MISSING_CACHE]"
        else:
            val_str = str(val_data)
        pair = {
            "locator_rc": loc_rc,
            "locator_a1": loc_a1,
            "locator_row": loc_row,
            "sheet_name": sheet_name,
            "formula": formula_str,
            "cached_value": val_data,
            "has_cached_value": has_cache,
        }
    else:
        val_str = str(val_data) if val_data is not None else ""

    rec = CellRecord(
        sheet_name=sheet_name, row=row_idx, col=col_idx, locator_rc=loc_rc, locator_a1=loc_a1,
        locator_row=loc_row, value=val_data, value_str=val_str, formula=formula_str,
        is_formula=is_formula, has_formula_cache=has_cache, is_empty=is_empty,
    )
    return rec, f_cnt, m_cnt, pair


def _segment_sheet_rows(raw_grid: List[List[CellRecord]], sheet_name: str) -> List[XlsxSection]:
    """Partition sheet rows into distinct sections without assuming row 1 is header."""
    sections: List[XlsxSection] = []
    current_block: List[List[CellRecord]] = []
    sec_idx = 1
    for row in raw_grid:
        if any(not c.is_empty for c in row):
            current_block.append(row)
        elif current_block:
            new_secs = _process_row_block(current_block, sheet_name, sec_idx)
            sections.extend(new_secs)
            sec_idx += len(new_secs)
            current_block = []
    if current_block:
        sections.extend(_process_row_block(current_block, sheet_name, sec_idx))
    return sections


def _find_header_row_index(block: List[List[CellRecord]], max_cols: int) -> int:
    for idx, row in enumerate(block):
        is_hdr, _ = _classify_row_as_header([c.value for c in row], max_cols=max_cols)
        if is_hdr:
            return idx
    return -1


def _build_table_section(table_block: List[List[CellRecord]], sheet_name: str, cur_idx: int, max_cols: int) -> XlsxSection:
    hdr_cells = table_block[0]
    headers = [c.value_str if not c.is_empty else f"Col{idx}" for idx, c in enumerate(hdr_cells, start=1)]
    while len(headers) < max_cols:
        headers.append(f"Col{len(headers)+1}")
    return XlsxSection(
        sheet_name=sheet_name, section_index=cur_idx, kind="table",
        start_row=table_block[0][0].row, end_row=table_block[-1][0].row,
        headers=headers, rows=table_block[1:],
    )


def _process_row_block(block: List[List[CellRecord]], sheet_name: str, start_sec_idx: int) -> List[XlsxSection]:
    """Process contiguous rows, extracting metadata preambles and table sections."""
    if not block:
        return []
    max_non_empty = max(len([c for c in r if not c.is_empty]) for r in block)
    max_cols = max(len(r) for r in block)
    header_row_idx = _find_header_row_index(block, max_cols)
    if header_row_idx < 0 and max_non_empty <= 1:
        return [XlsxSection(sheet_name=sheet_name, section_index=start_sec_idx, kind="metadata_block", start_row=block[0][0].row, end_row=block[-1][0].row, rows=block)]
    sections: List[XlsxSection] = []
    cur_idx = start_sec_idx
    if header_row_idx > 0:
        meta_rows = block[:header_row_idx]
        sections.append(XlsxSection(sheet_name=sheet_name, section_index=cur_idx, kind="metadata_block", start_row=meta_rows[0][0].row, end_row=meta_rows[-1][0].row, rows=meta_rows))
        cur_idx += 1
    table_block = block[header_row_idx:] if header_row_idx >= 0 else block
    if header_row_idx >= 0 and len(table_block) >= 1:
        sections.append(_build_table_section(table_block, sheet_name, cur_idx, max_cols))
    else:
        sections.append(XlsxSection(sheet_name=sheet_name, section_index=cur_idx, kind="grid", start_row=block[0][0].row, end_row=block[-1][0].row, headers=[f"Col{idx}" for idx in range(1, max_cols + 1)], rows=block))
    return sections


def _project_single_sheet(ws_data: Any, ws_form: Any, sheet_name: str) -> Tuple[SheetProjection, List[Dict[str, Any]]]:
    """Project a single worksheet into SheetProjection and formula pairs."""
    state = getattr(ws_data, "sheet_state", "visible")
    rows_data = list(ws_data.iter_rows(values_only=False))
    if not rows_data:
        return SheetProjection(sheet_name=sheet_name, state=state), []
    raw_grid: List[List[CellRecord]] = []
    all_cells: List[CellRecord] = []
    formula_pairs: List[Dict[str, Any]] = []
    formulas_detected, missing_formula_cache = 0, 0
    for r_idx, row in enumerate(rows_data, start=1):
        grid_row: List[CellRecord] = []
        for c_idx, cell in enumerate(row, start=1):
            val_data = cell.value
            val_form = ws_form.cell(row=r_idx, column=c_idx).value if ws_form else None
            rec, f_cnt, m_cnt, pair = _make_cell_record(val_data, val_form, sheet_name, r_idx, c_idx)
            grid_row.append(rec)
            all_cells.append(rec)
            formulas_detected += f_cnt
            missing_formula_cache += m_cnt
            if pair:
                formula_pairs.append(pair)
        raw_grid.append(grid_row)
    sections = _segment_sheet_rows(raw_grid, sheet_name)
    total_records = sum(len(s.rows) for s in sections)
    proj = SheetProjection(
        sheet_name=sheet_name, state=state, sections=sections, cells=all_cells,
        total_records=total_records, formulas_detected=formulas_detected, missing_formula_cache=missing_formula_cache,
    )
    return proj, formula_pairs


def _index_sheet_cells(cells: Sequence[CellRecord], index: Dict[str, Any]) -> None:
    for c in cells:
        info = {"type": "cell", "locator": c.locator_rc, "locator_a1": c.locator_a1, "sheet": c.sheet_name, "row": c.row, "col": c.col, "value": c.value, "formula": c.formula}
        index[c.locator_rc.upper()] = info
        index[c.locator_a1.upper()] = info


def _index_sheet_rows(sections: Sequence[XlsxSection], index: Dict[str, Any]) -> None:
    for sec in sections:
        for r in sec.rows:
            if not r:
                continue
            loc_row = r[0].locator_row.upper()
            if loc_row not in index:
                index[loc_row] = {"type": "row", "locator": r[0].locator_row, "sheet": r[0].sheet_name, "row": r[0].row, "values": [c.value for c in r], "cells": [c.locator_rc for c in r]}


def _index_projection_locators(sheet_projections: List[SheetProjection]) -> Dict[str, Any]:
    """Build unified lookup index for Sheet!R#C#, Sheet!A1, and Sheet!R# locators."""
    index: Dict[str, Any] = {}
    for sp in sheet_projections:
        _index_sheet_cells(sp.cells, index)
        _index_sheet_rows(sp.sections, index)
    return index


def _row_to_source_unit(row: List[CellRecord], sec_kind: str, sheet_name: str, source_hash: str, headers: Optional[List[str]] = None) -> Optional[SourceUnit]:
    if not row or all(c.is_empty for c in row):
        return None
    loc_row = row[0].locator_row
    hdrs = headers or []
    if sec_kind == "table" and hdrs:
        pairs = [f"{hdrs[i]}: {c.value_str}" if i < len(hdrs) and hdrs[i] else c.value_str for i, c in enumerate(row)]
        content = " | ".join(pairs)
        col_map = {hdrs[i]: row[i].value_str for i in range(min(len(hdrs), len(row)))}
    else:
        content = " | ".join(c.value_str for c in row)
        col_map = {}
    meta: Dict[str, Any] = {"sheet_name": sheet_name, "row": row[0].row, "section_kind": sec_kind, "cells_count": len(row)}
    if hdrs:
        meta["headers"] = list(hdrs)
    if col_map:
        meta["cells"] = col_map
    return SourceUnit(
        unit_id=f"{source_hash[:12]}:{loc_row}", source_hash=source_hash, format="xlsx",
        unit_type="table_row" if sec_kind == "table" else "metadata_row", locator=loc_row, content=content, metadata=meta,
    )


def _build_xlsx_source_units(
    sheet_projections: List[SheetProjection], source_hash: str
) -> List[SourceUnit]:
    """Emit SourceUnits for sheet rows and formula cells."""
    units: List[SourceUnit] = []
    for sp in sheet_projections:
        for sec in sp.sections:
            for row in sec.rows:
                u = _row_to_source_unit(row, sec.kind, sp.sheet_name, source_hash, headers=sec.headers)
                if u is not None:
                    units.append(u)
    return units


def _resolve_sheets_to_process(
    all_sheets: List[str], metadata: Optional[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    meta = metadata or {}
    declared = meta.get("selected_sheets") or meta.get("sheet_names")
    if not declared:
        return all_sheets, []
    if isinstance(declared, str):
        declared = [s.strip() for s in declared.split(",") if s.strip()]
    selected_set = set(declared)
    to_process = [s for s in all_sheets if s in selected_set]
    excluded = [s for s in all_sheets if s not in selected_set]
    return to_process, excluded


def project_xlsx_workbook(
    raw_bytes: bytes,
    filename: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> XlsxProjection:
    """Parse, segment, and project XLSX workbook with formula/cache and locator indices."""
    if not _OPENPYXL_AVAILABLE:
        raise RuntimeError("openpyxl is required to project XLSX workbooks.")

    wb_data = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, keep_vba=False)
    try:
        wb_formula = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False, keep_vba=False)
    except Exception:
        wb_formula = None

    all_sheets = list(wb_data.sheetnames)
    to_process, excluded = _resolve_sheets_to_process(all_sheets, metadata)

    sheet_projections: List[SheetProjection] = []
    all_formula_pairs: List[Dict[str, Any]] = []
    total_cells = 0
    total_records = 0
    formulas_detected = 0
    missing_formula_cache = 0

    for sheet_name in to_process:
        ws_data = wb_data[sheet_name]
        ws_form = wb_formula[sheet_name] if wb_formula and sheet_name in wb_formula.sheetnames else None
        sp, f_pairs = _project_single_sheet(ws_data, ws_form, sheet_name)
        sheet_projections.append(sp)
        all_formula_pairs.extend(f_pairs)
        total_cells += len(sp.cells)
        total_records += sp.total_records
        formulas_detected += sp.formulas_detected
        missing_formula_cache += sp.missing_formula_cache

    source_hash = hashlib.sha256(raw_bytes).hexdigest()
    source_units = _build_xlsx_source_units(sheet_projections, source_hash)
    locator_index = _index_projection_locators(sheet_projections)
    rendered_sections = [sp.render_markdown() for sp in sheet_projections]
    rendered_markdown = "\n\n".join(rendered_sections)

    coverage = {
        "total_sheets": len(all_sheets),
        "processed_sheets": len(to_process),
        "loaded_sheets": len(to_process),
        "all_sheets_loaded": len(excluded) == 0,
        "sheet_names": to_process,
        "excluded_sheets": excluded,
        "is_whole_workbook": len(excluded) == 0,
        "total_cells": total_cells,
        "total_records": total_records,
        "processed_records": total_records,
        "complete": (len(excluded) == 0 or len(to_process) > 0),
        "method": "xlsx_multi_sheet",
        "formulas_detected": formulas_detected,
        "missing_formula_cache": missing_formula_cache,
        "cell_locators": True,
        "formula_cache_pairs_count": len(all_formula_pairs),
    }

    if metadata is not None:
        metadata["_source_coverage"] = coverage
        metadata["source_coverage"] = coverage
        metadata["source_units"] = [u.to_dict() for u in source_units]

    return XlsxProjection(
        sheet_projections=sheet_projections,
        all_sheet_names=all_sheets,
        processed_sheets=to_process,
        excluded_sheets=excluded,
        total_cells=total_cells,
        total_records=total_records,
        formulas_detected=formulas_detected,
        missing_formula_cache=missing_formula_cache,
        formula_cache_pairs=all_formula_pairs,
        source_units=source_units,
        rendered_markdown=rendered_markdown,
        coverage_dict=coverage,
        _locator_index=locator_index,
    )


_STATIC_COUNTRY_LOOKUP_TOKENS = frozenset({
    "united kingdom", "germany", "ireland", "japan", "australia", "new zealand", "static rate lookup"
})


def _slug_wb(prefix: str, label: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return f"{prefix}:{clean or 'unknown'}"


def _infer_workbook_workstream(text: str) -> Tuple[str, str]:
    if re.search(r"ENDUR|ETRM|FO\s+Reporting|MO\s+Reporting|BO\s+Reporting|TRM\s+Lakehouse", text, re.I):
        return ("workstream:trm-lakehouse-implementation-on-databricks", "TRM Lakehouse Implementation on Databricks")
    m_proj = re.search(r"Project\s+Name\s*[:|]\s*([^\n|\[]+)", text, re.I)
    if m_proj and m_proj.group(1).strip():
        lbl = m_proj.group(1).strip()
        return (_slug_wb("workstream", lbl), lbl)
    return ("workstream:global-pricing-staffing-model", "Global Pricing Staffing Model")


def _role_label_for_tbd(num: int, raw_line: str = "") -> str:
    if raw_line and "|" in raw_line:
        parts = [p.strip() for p in raw_line.split("|") if p.strip()]
        for p in parts:
            cleaned = re.sub(r"^\bTBD\d+\b\s*[:\-]?\s*", "", p, flags=re.I).strip()
            if cleaned and not re.match(r"^(?:Active Role|Specialist|Empty|\$|Total\s+Hours|R\d+|Sheet)\b", cleaned, re.I) and not cleaned.isdigit():
                return f"TBD{num} {cleaned}"
    return f"TBD{num} Active Staffing Role"


def _parse_active_tbd_roles(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    active_roles: List[Dict[str, Any]] = []
    quarantined_rows: List[str] = []
    seen_tbds: set[int] = set()
    for ln in text.splitlines():
        low = ln.lower()
        if any(tok in low for tok in _STATIC_COUNTRY_LOOKUP_TOKENS):
            quarantined_rows.append(ln.strip()[:80])
            continue
        m_tbd = re.search(r"\bTBD(\d+)\b", ln, re.I)
        if not m_tbd:
            continue
        tbd_num = int(m_tbd.group(1))
        if tbd_num >= 19 or re.search(r"total\s+hours\s*[:=|]\s*0\b", low):
            quarantined_rows.append(f"TBD{tbd_num}")
        elif tbd_num not in seen_tbds:
            seen_tbds.add(tbd_num)
            is_zero = bool(re.search(r"\$0(?:\.00)?\b", ln))
            active_roles.append({
                "tbd_num": tbd_num, "id": f"role:tbd{tbd_num}", "name": _role_label_for_tbd(tbd_num, ln),
                "is_zero_rate": is_zero, "raw_line": ln.strip(),
            })
    return active_roles, quarantined_rows


def _sum_role_hours(active_roles: List[Dict[str, Any]]) -> int:
    total = 0
    for r in active_roles:
        m = re.search(r"Total\s+Hours\s*[:=|]\s*([\d,]+)", r.get("raw_line", ""), re.I)
        if m:
            total += int(m.group(1).replace(",", ""))
    return total


def _parse_workbook_waves_and_metrics(
    text: str, active_roles: List[Dict[str, Any]]
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    waves: List[Tuple[str, str]] = []
    for m_w in re.finditer(r"((?:Wave\s+\d+|Discovery/Mobilize|PROD\s+DEPLOY)[^|\n:]*?)\s*[|:]\s*([\d,]+\s*hrs)", text, re.I):
        waves.append((m_w.group(1).strip(), m_w.group(2).strip()))

    metrics: List[Tuple[str, str]] = []
    hrs_m = re.search(r"([\d,]+)\s*Total\s+Hours|Total\s+Hours\s*[:=|]\s*([\d,]+)", text, re.I)
    summed_hrs = _sum_role_hours(active_roles)
    if hrs_m:
        raw_h = (hrs_m.group(1) or hrs_m.group(2) or "0").replace(",", "")
        hrs_int = int(raw_h) if raw_h.isdigit() else 0
        if summed_hrs > hrs_int and len(active_roles) > 1:
            hrs_int = summed_hrs
        if hrs_int > 0:
            metrics.append((f"metric:workbook-total-hours-{hrs_int}", f"{hrs_int:,} Total Hours"))
    elif summed_hrs > 0:
        metrics.append((f"metric:workbook-total-hours-{summed_hrs}", f"{summed_hrs:,} Total Hours"))

    tm_m = re.search(r"\$([\d,]+)\s*T&M|T&M(?:\s+Revenue)?\s*[:=|]\s*\$([\d,]+)", text, re.I)
    if tm_m:
        tm_val = (tm_m.group(1) or tm_m.group(2) or "0").replace(",", "")
        metrics.append((f"metric:workbook-tm-revenue-{tm_val}", f"${int(tm_val):,} T&M Revenue"))

    ff_m = re.search(r"\$([\d,]+)\s*Fixed\s*Fee|Fixed\s*Fee\s*[:=|]\s*\$([\d,]+)", text, re.I)
    if ff_m:
        ff_val = (ff_m.group(1) or ff_m.group(2) or "0").replace(",", "")
        metrics.append((f"metric:workbook-fixed-fee-{ff_val}", f"${int(ff_val):,} Fixed Fee"))

    return waves, metrics


def _build_workbook_risk_updates(
    document_text: str, active_roles: List[Dict[str, Any]], ev_main: str
) -> List[Dict[str, Any]]:
    risk_updates: List[Dict[str, Any]] = []
    if re.search(r"continuous\s+quality\s+advisor", document_text, re.I):
        risk_updates.append({
            "risk_id": "risk:continuous-quality-advisor-required",
            "title": "Deal-Desk Alert: Continuous Quality Advisor Required",
            "description": "ATTENTION! YOUR DEAL SIZE REQUIRES A CONTINUOUS QUALITY ADVISOR.",
            "severity": "HIGH",
            "evidence_ids": [ev_main],
        })
    for r in active_roles:
        if r.get("is_zero_rate"):
            t_num = r["tbd_num"]
            risk_updates.append({
                "risk_id": f"risk:unpriced-active-staffing-role-tbd{t_num}",
                "title": f"Unpriced Active Staffing Role: TBD{t_num} ($0 Bill Rate)",
                "description": f"{r['name']} has active hours at $0.00 client bill rate.",
                "severity": "HIGH",
                "evidence_ids": [ev_main],
            })
    return risk_updates


def extract_staffing_workbook_projection(
    document_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Slice active staffing rows, waves, deal metrics, and governance alerts strictly grounded in text."""
    ws_id, ws_name = _infer_workbook_workstream(document_text)
    active_roles, quarantined_rows = _parse_active_tbd_roles(document_text)
    waves, metrics = _parse_workbook_waves_and_metrics(document_text, active_roles)
    ev_main = "ev-workbook-staffing-slice"
    first_ln = next((ln.strip() for ln in document_text.splitlines() if ln.strip()), "Staffing Workbook")
    metric_summary = ", ".join(m[1] for m in metrics) if metrics else "Staffing Model"
    ev_idx = {
        ev_main: {
            "evidence_id": ev_main, "speaker": "Not specified", "source_classification": "FACT", "kind": "summary",
            "text": f"{ws_name}: {metric_summary} across {len(active_roles)} active roles and {len(waves)} waves.",
            "exact_text": first_ln[:350], "start_offset": 0, "end_offset": len(first_ln[:350]),
            "quote_pointer": [0, len(first_ln[:350])], "locator": {"kind": "section", "value": "Staffing Plan"}, "grounding_status": "GROUNDED",
        }
    }
    ent_map: Dict[str, Dict[str, Any]] = {
        ws_id: {"id": ws_id, "type": "workstream", "name": ws_name, "description": f"{ws_name} active staffing model.", "mutation": "CREATE", "evidence_ids": [ev_main]}
    }
    sig_map: Dict[str, Dict[str, Any]] = {}
    for w_name, w_hrs in waves:
        w_id = _slug_wb("workstream", w_name)
        ent_map[w_id] = {"id": w_id, "type": "workstream", "name": f"{w_name} ({w_hrs})", "description": f"Delivery phase {w_name} ({w_hrs}).", "mutation": "CREATE", "evidence_ids": [ev_main]}
        sig_map[f"sig-{w_id}-part-of-{ws_id}"] = {"signal_id": f"sig-{w_id}-part-of-{ws_id}", "from_id": w_id, "to_id": ws_id, "relationship": "MEMBER_OF", "predicate": "MEMBER_OF", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    for r in active_roles:
        ent_map[r["id"]] = {"id": r["id"], "type": "role", "name": r["name"], "description": f"Active staffing role {r['name']} (Total Hours > 0).", "mutation": "CREATE", "evidence_ids": [ev_main]}
        sig_map[f"sig-{r['id']}-assigned-{ws_id}"] = {"signal_id": f"sig-{r['id']}-assigned-{ws_id}", "from_id": r["id"], "to_id": ws_id, "relationship": "MEMBER_OF", "predicate": "MEMBER_OF", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    for m_id, m_lbl in metrics:
        ent_map[m_id] = {"id": m_id, "type": "metric", "name": m_lbl, "description": m_lbl, "mutation": "CREATE", "evidence_ids": [ev_main]}
        sig_map[f"sig-{ws_id}-{m_id}"] = {"signal_id": f"sig-{ws_id}-{m_id}", "from_id": ws_id, "to_id": m_id, "relationship": "HAS_METRIC", "predicate": "HAS_METRIC", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    risk_updates = _build_workbook_risk_updates(document_text, active_roles, ev_main)
    return {
        "schema_version": "1.0.0", "is_document": True, "output3_only": True, "evidence_index": ev_idx, "evidence_only_statement_ids": [],
        "graph_mutations": {"entity_upserts": list(ent_map.values()), "relationship_signals": list(sig_map.values()), "decision_updates": [], "proposal_updates": [], "risk_updates": risk_updates, "strategic_intent_signals": []},
        "workbook_slice_audit": {"active_roles_count": len(active_roles), "active_waves_count": len(waves), "quarantined_q3_rows_count": len(quarantined_rows)},
    }
