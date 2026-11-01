"""
Heuristic tabular and milestone parser for OCR text lines extracted from slide and visual collateral.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


def parse_vision_milestones(lines: List[str]) -> List[Dict[str, Any]]:
    """Extracts explicit contractual milestone statements from OCR text lines."""
    milestones: List[Dict[str, Any]] = []
    seen = set()
    for line in lines:
        clean = line.strip()
        m = re.search(r"(?:•|-|\*|\b)\s*Milestone\s*(\d+)[:\s\-]+(.+)", clean, re.I)
        if m:
            m_id = f"M{m.group(1)}"
            m_name = f"Milestone {m.group(1)}"
            desc = m.group(2).strip()
            if m_id not in seen:
                seen.add(m_id)
                milestones.append({
                    "id": m_id,
                    "name": m_name,
                    "description": desc,
                    "status": "IN_PROGRESS",
                })
    return milestones


def _extract_bullet_text(clean: str) -> Optional[str]:
    if clean.startswith(("•", "-", "*")):
        text = clean.lstrip("•-* ").strip()
        if text and not re.search(r"^Milestone\s*\d+", text, re.I):
            return text
    return None


def parse_vision_claims(lines: List[str]) -> List[Dict[str, Any]]:
    """Extracts bulleted notes, technical updates, or claims from OCR text lines."""
    claims: List[Dict[str, Any]] = []
    in_notes = False
    for line in lines:
        clean = line.strip()
        if re.match(r"^Notes\b", clean, re.I):
            in_notes = True
            continue
        bullet = _extract_bullet_text(clean)
        if bullet:
            claims.append({"claim": bullet})
        elif in_notes and clean and claims and not clean.startswith("Iteration"):
            claims[-1]["claim"] += f" {clean}"
    return claims


_EXCLUDED_HEADER_TOKENS = {
    "total", "assets", "auto-approved", "rate", "extraction",
    "accuracy % (m1)", "ocr accuracy", "% (m2)", "% (m1)", "accuracy"
}


def _extract_iteration_markers(sub: List[str]) -> List[Tuple[int, str]]:
    markers: List[Tuple[int, str]] = []
    for i, l in enumerate(sub):
        s = l.strip()
        if s.lower() == "baseline" or (s.isdigit() and int(s) in range(1, 20)):
            markers.append((i, s))
    return markers


def _filter_chunk_values(chunk: List[str]) -> List[str]:
    filtered: List[str] = []
    for item in chunk[1:]:
        clean = item.strip()
        if clean.lower() not in _EXCLUDED_HEADER_TOKENS:
            filtered.append(clean)
    return filtered


def _extract_iteration_rows(sub: List[str]) -> List[List[str]]:
    markers = _extract_iteration_markers(sub)
    rows: List[List[str]] = []
    for idx, (m_i, m_val) in enumerate(markers):
        next_m_i = markers[idx + 1][0] if idx + 1 < len(markers) else len(sub)
        filtered = _filter_chunk_values(sub[m_i:next_m_i])
        if filtered:
            rows.append([m_val] + filtered[:4])
    return rows


def parse_vision_tables(lines: List[str]) -> List[Dict[str, Any]]:
    """Identifies and reconstructs structured iteration/waterfall tables from OCR lines."""
    tables: List[Dict[str, Any]] = []
    has_iteration = any(re.match(r"^Iteration\b", l.strip(), re.I) for l in lines)
    if not has_iteration:
        return tables

    it_idx = next(i for i, l in enumerate(lines) if re.match(r"^Iteration\b", l.strip(), re.I))
    notes_idx = next((i for i, l in enumerate(lines) if re.match(r"^Notes\b", l.strip(), re.I)), len(lines))
    rows = _extract_iteration_rows(lines[it_idx:notes_idx])
    if rows:
        tables.append({
            "table_name": "Deliverable Metrics Performance",
            "headers": ["Iteration", "Total Assets", "Auto-Approved Rate", "Extraction Accuracy (M1)", "OCR Accuracy (M2)"],
            "rows": rows,
        })
    return tables


def extract_tabular_and_semantic_structures(
    lines: List[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Combines table, milestone, and claim parsers over raw OCR text lines."""
    tables = parse_vision_tables(lines)
    milestones = parse_vision_milestones(lines)
    claims = parse_vision_claims(lines)
    return tables, milestones, claims
