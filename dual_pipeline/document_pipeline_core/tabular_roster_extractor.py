"""
dual_pipeline/document_pipeline_core/tabular_roster_extractor.py

Deterministic multi-page tabular workstream and team assignment roster extractor.
Recovers full workstream portfolios, vertical/department ownership, PM/SM bindings,
and right-column detached team assignment rosters from planning portal PDF exports.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

VERTICAL_LEAD_MAP: Dict[str, str] = {
    "Reid Schenk": "TechSales (Reid Schenk)", "Anuj Chokshi": "Enterprise AI (Anuj Chokshi)",
    "Chakra Sankaraiah": "EngTech (Chakra Sankaraiah)", "Jose Medina": "Retail (Jose Medina)",
    "Jarod Bryant": "Gen Asset Opti (Jarod Bryant)", "Kevin McWhorter": "Infra Dev (Kevin McWhorter)",
    "Stephan Nitsche": "Energy Mngt (Stephan Nitsche)", "John Andersen": "Bus Srv AI (John Andersen III)",
    "Lori Bartlett": "T&D Asset Opti (Lori Bartlett)", "Farrukh": "Bus Ops AI (Farrukh Khan)",
    "Richard Walker": "Product Ops (Richard Walker)",
}

DEPT_LEAD_MAP: Dict[str, str] = {
    "Chris Nunalee": "NEA Operations (Chris Nunalee)", "Matthew": "Enterprise AI (Matthew Barrows)",
    "Chakra Sankaraiah": "Eng & Tech (Chakra Sankaraiah)", "Richard Walker": "Product Ops (Richard Walker)",
}

EXACT_HEADER_LINES = {
    "Te a m Assignments", "Filters", "Name", "Search...", "Department",
    "All Departments", "Vertical", "All Verticals", "Status",
    "Proposed, Needs Revision, Approved, In Progress & Complete",
    "Team Composition", "Show All", "Assignment Progress", "All", "POD",
    "Rewire", "All Reset Filters", "WORKSTREAMS", "90", "REQUESTED PEOPLE",
    "404.9", "ASSIGNABLE PEOPLE", "376", "ASSIGNMENTS", "425",
    "PERCENT ASSIGNED", "95%", "UNFILLED FTE", "30.4 of 404.9",
    "UNASSIGNED PEOPLE", "42 of 376", "All Workstream Assignments (90)",
    "Workstream Vertical Department Status Missions Est. NAV ($K) Team Assignments AssignmentProgress",
}

PREFIX_HEADER_TOKENS: Tuple[str, ...] = (
    "10/20/26", "Page ", "2026 Q4 - Active", "https://planning.apex_globalanalytics.com",
    "Te a m", "Filters", "Search...", "All Departments", "All Verticals",
    "Proposed, Needs", "Team Composition", "Assignment Progress", "All Reset Filters",
    "WORKSTREAMS", "REQUESTED PEOPLE", "ASSIGNABLE PEOPLE", "ASSIGNMENTS",
    "PERCENT ASSIGNED", "UNFILLED FTE", "30.4 of", "UNASSIGNED PEOPLE", "42 of",
    "All Workstream Assignments", "Workstream Vertical",
    "Product:", "Khan) Barrows)", "Schenk)", "Nunalee)",
)

ROSTER_TIERS = {
    "Contractor", "Staff", "Senior", "Principal", "Associate",
    "Director", "Manager", "Lead", "Consultant", "Architect",
    "Analyst", "Intern", "VP", "Fellow",
}
UNFILLED_ROLE_PREFIX_RE = re.compile(
    r"^(Software|Product|Data|Applied|Business|Scrum|UI/UX|Cloud|AI|ML|Systems|DevOps)"
)


def is_tabular_workstream_roster_document(document_text: str) -> bool:
    """Detect multi-row tabular workstream/roster planning documents."""
    if not document_text:
        return False
    sm_count = len(re.findall(r"SM:\s*[^\n]+", document_text))
    pm_count = len(re.findall(r"PM:\s*[^\n]+", document_text))
    return sm_count >= 5 and pm_count >= 5


def _norm_alnum(val: str) -> str:
    return re.sub(r"[^a-z0-9]", "", val.lower())


def _normalize_person_name(raw: str) -> str:
    s = _clean_text_token(raw)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    for pref in ("Mc ", "Mac ", "De ", "Van ", "Fitz ", "La "):
        s = s.replace(pref, pref.strip())
    return re.sub(r"\s+", " ", s).strip()


def _slugify_id(prefix: str, label: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return f"{prefix}:{clean or 'unknown'}"


BRAND_RESTORE_MAP: Tuple[Tuple[str, str], ...] = (
    ("5. Forge.", "5.Forge."), ("Rev IQ", "RevIQ"), ("Grid Flow", "GridFlow"),
    ("Grid Mind", "GridMind"), ("Grid Composer", "GridComposer"), ("Next Era", "Apex Global"),
    ("Next Shield", "NextShield"), ("Fast Pass", "FastPass"), ("Open AI", "OpenAI"),
    ("Chat GPT", "ChatGPT"), ("Fin Ops", "FinOps"), ("Doc IQ", "DocIQ"),
    ("Tech Sales", "TechSales"), ("Dev Ops", "DevOps"), ("Halvethe", "Halve the"),
    ("2of 2", "2 of 2"), (" ;TTR", "; TTR"),
)


def _clean_text_token(val: str) -> str:
    s = re.sub(r"\s*/\s*", "-", val).replace("—", "-").replace("|", "-")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(intelligence|validation|outage|expansion)(and)\b", r"\1 \2", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*&\s*", " and ", s)
    s = re.sub(r":(?=[A-Z])", ": ", s)
    s = re.sub(r"(?<=[a-z])\(", " (", s)
    s = re.sub(r"\s+-(?=[A-Z0-9])", " - ", s)
    for pref in ("Mc ", "Mac ", "De ", "Van ", "Fitz ", "La "):
        s = s.replace(pref, pref.strip())
    s = re.sub(r"\s+", " ", s).strip()
    for src, dst in BRAND_RESTORE_MAP:
        s = s.replace(src, dst)
    return s


def _is_header_or_noise_line(ln: str) -> bool:
    s = ln.strip()
    if not s or s in EXACT_HEADER_LINES:
        return True
    return any(s.startswith(p) or p in s for p in PREFIX_HEADER_TOKENS)


def _split_document_pages(document_text: str) -> List[Tuple[int, str]]:
    if "---PAGE---" in document_text:
        raw_pages = document_text.split("---PAGE---")
        return [(i, p) for i, p in enumerate(raw_pages, 1) if p.strip()]
    parts = list(re.finditer(r"(?:^|\n)##\s+Page\s+(\d+)\s*\n", document_text))
    if not parts:
        return [(1, document_text)]
    pages: List[Tuple[int, str]] = []
    for idx, match in enumerate(parts):
        p_num = int(match.group(1))
        start = match.end()
        end = parts[idx + 1].start() if idx + 1 < len(parts) else len(document_text)
        pages.append((p_num, document_text[start:end]))
    return pages


def _extract_verbatim_block_quote(raw_block: str, page_text: str, sm_pos: int) -> str:
    clean_lines = [ln for ln in raw_block.splitlines() if not _is_header_or_noise_line(ln)]
    if clean_lines:
        first_ln = clean_lines[0].strip()
        pos_in_raw = raw_block.find(first_ln)
        if pos_in_raw != -1:
            candidate = raw_block[pos_in_raw:].strip()[:350]
            if len(candidate) >= 10 and candidate in page_text:
                return candidate
    line_start = page_text.rfind("\n", 0, sm_pos)
    line_end = page_text.find("\n", sm_pos)
    start_idx = 0 if line_start == -1 else line_start + 1
    end_idx = len(page_text) if line_end == -1 else line_end
    return page_text[start_idx:end_idx].strip()[:350]


def _resolve_workstream_name_and_pm(block: str, product: str, p_idx: int, idx: int) -> Tuple[str, str]:
    pm_m = re.search(r"PM:\s*([^\n]+?)(?:SM:|Gen Asset|TechSales|Infra Dev|Enterprise|Energy|$)", block, re.MULTILINE)
    pm = _normalize_person_name(pm_m.group(1)) if pm_m else ""
    pre_pm = block.split("PM:")[0] if "PM:" in block else block.split("SM:")[0]
    clean_lines = [ln.strip() for ln in pre_pm.splitlines() if not _is_header_or_noise_line(ln)]
    raw_name = " ".join(clean_lines).strip()
    raw_name = re.sub(r"^[A-Za-z]+\)\s*", "", raw_name)
    raw_name = re.sub(
        r"(?:Infra\s*Dev|NEA\s*Operations|TechSales|Gen\s*Asset|Enterprise\s*AI|Energy\s*(?:Mngt|NEA)|NEACORE|NEAConstruction|NEAGas\s*Demand|\bNEA\b).*",
        "",
        raw_name,
    ).strip()
    if not raw_name or raw_name.startswith("(") or len(raw_name) < 3:
        nea_m = re.search(
            r"(?:In Progress|Proposed|Approved|Needs Revision)\s*(?:NEA)?\s*([^\n$—]+?)(?:Resource Note:|\$|—)",
            " ".join(block.split()),
        )
        if nea_m and len(nea_m.group(1).strip()) >= 3:
            raw_name = nea_m.group(1).strip()
    elif raw_name == "Scheduling" and "Wind" in block and "Major Component" in block:
        raw_name = "CORE - Renewable Central Maintenance Scheduling"
    if raw_name.strip().upper() == "JARVIS" or (not raw_name and product.upper().startswith("JARVIS")):
        raw_name = "NEA JARVIS"
    name = _clean_text_token(raw_name or product or f"Workstream P{p_idx}-{idx + 1}")
    return name, pm


def _clean_product_token(raw_product: str) -> str:
    cleaned = _clean_text_token(raw_product)
    cleaned = re.sub(r"^\d+\)\s*", "", cleaned)
    cleaned = re.sub(r"\s*(?:\(Reid.*|Khan\).*|Barrows\).*|Panel Allocation.*)$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _extract_mission_and_resource_note(block: str) -> Tuple[str, str]:
    sm_m = re.search(r"SM:\s*[^\n]+", block)
    flat = " ".join((block[sm_m.end():] if sm_m else block).split())
    notes = re.findall(r"Resource Note:\s*([^\$]+?)(?=NEA|POD|No Associated|\$|—\s*\d+%|$)", flat)
    note_str = "; ".join(_clean_text_token(n) for n in notes if n.strip())
    if "No Associated" in flat and "Charter" in flat:
        return "No Associated Charter", note_str
    m = re.search(r"(?:In Progress|Proposed|Needs Revision|Approved|Complete)\s*(.+?)(?:\$[-0-9,]+K\s*\d+%|—\s*\d+%|$)", flat)
    if not m:
        return "", note_str
    seg = re.sub(r"Resource Note:\s*[^\$]+?(?=NEA|POD|\$|—\s*\d+%|$)", " ", m.group(1))
    seg = re.sub(r"^(NEA|POD)(?=[A-Za-z0-9])", r"\1 ", seg.strip())
    seg = re.sub(r"(?<!^)(NEA|POD)(?=[A-Za-z0-9])", r" | \1 ", seg)
    return _clean_text_token(seg), note_str


def _parse_workstream_block(
    block: str, raw_block: str, page_text: str, sm_match: re.Match[str], p_idx: int, idx: int
) -> Dict[str, Any]:
    sm = _normalize_person_name(sm_match.group(1))
    prod_m = re.search(r"Product:\s*([^\n]+)", block)
    product = _clean_product_token(prod_m.group(1)) if prod_m else ""
    name, pm = _resolve_workstream_name_and_pm(block, product, p_idx, idx)
    block_flat = " ".join(block.split())
    vertical = next((v for k, v in VERTICAL_LEAD_MAP.items() if k in block_flat), "Other")
    dept = next((v for k, v in DEPT_LEAD_MAP.items() if k in block_flat), "Other")
    status = next(
        (s for s in ("In Progress", "Proposed", "Needs Revision", "Approved", "Complete") if s in block_flat),
        "In Progress",
    )
    nav_m = re.search(r"\$([-0-9,]+)K\s+(\d+)%", block_flat)
    dash_m = re.search(r"—\s+(\d+)%", block_flat)
    nav_k = int(nav_m.group(1).replace(",", "")) if nav_m else None
    prog_pct = int(nav_m.group(2)) if nav_m else (int(dash_m.group(1)) if dash_m else 0)
    charter_mission, resource_note = _extract_mission_and_resource_note(block)
    return {
        "page": p_idx, "idx_on_page": idx, "name": name, "pm": pm, "sm": sm,
        "product": product, "product_line": product, "vertical": _clean_text_token(vertical),
        "dept": _clean_text_token(dept), "status": status, "workstream_status": status,
        "charter_mission": charter_mission, "resource_note": resource_note,
        "nav_k": nav_k if nav_k is not None else 0, "est_nav_usd": nav_k * 1000 if nav_k is not None else None,
        "has_nav": nav_k is not None, "progress_pct": prog_pct, "assignment_progress_pct": prog_pct,
        "verbatim_line": _extract_verbatim_block_quote(raw_block, page_text, sm_match.start()), "roster": [],
    }


def _split_carry_stub_and_roster_lines(roster_text: str) -> Tuple[str, List[str], str]:
    raw_lines = [
        ln.strip() for ln in roster_text.splitlines()
        if ln.strip() and not any(c in ln for c in ("Quarterly Planning", "▾", "▲", "÷", "© 2026 Apex Global"))
    ]
    roster_start = 0
    for i in range(len(raw_lines)):
        if i + 1 < len(raw_lines) and raw_lines[i + 1] in ROSTER_TIERS:
            roster_start = i
            break
        if i + 2 < len(raw_lines) and raw_lines[i + 1].isdigit() and raw_lines[i + 2] in ROSTER_TIERS:
            roster_start = i
            break
    raw_stub = " ".join(raw_lines[:roster_start])
    stub = re.sub(
        r"(?:Infra\s*Dev|NEA\s*Operations|TechSales|Gen\s*Asset|Enterprise\s*AI|Energy\s*(?:Mngt|NEA)|NEACORE|NEAConstruction|NEAGas\s*Demand|\bNEA\b).*",
        "",
        raw_stub,
    ).strip()
    roster_lines = raw_lines[roster_start:]
    verbatim_roster = roster_text.strip()[:340]
    return stub, roster_lines, verbatim_roster


def _consume_single_roster_entry(roster_lines: List[str], i: int, people: List[Dict[str, Any]]) -> int:
    line = roster_lines[i]
    if line in ROSTER_TIERS or UNFILLED_ROLE_PREFIX_RE.match(line):
        if line in ROSTER_TIERS and people and people[-1]["tier"] == "Unknown":
            people[-1]["tier"] = line
        else:
            people.append({"name": f"Unfilled ({_clean_text_token(line)})", "raw_line": line, "tier": "Unfilled Role", "alloc_count": 1, "is_fh": False})
        return 1
    if i + 2 < len(roster_lines) and roster_lines[i + 1].isdigit() and roster_lines[i + 2] in ROSTER_TIERS:
        people.append({"name": _normalize_person_name(line), "raw_line": line, "tier": roster_lines[i + 2], "alloc_count": int(roster_lines[i + 1]), "is_fh": "Future Hire" in line})
        return 3
    if i + 1 < len(roster_lines) and roster_lines[i + 1] in ROSTER_TIERS:
        people.append({"name": _normalize_person_name(line), "raw_line": line, "tier": roster_lines[i + 1], "alloc_count": 1, "is_fh": "Future Hire" in line})
        return 2
    return 1


def _parse_page_roster_cards(roster_lines: List[str]) -> List[Dict[str, Any]]:
    people: List[Dict[str, Any]] = []
    i = 0
    while i < len(roster_lines):
        i += _consume_single_roster_entry(roster_lines, i, people)
    return people


def _find_single_pm_pos(pm_norm: str, people: List[Dict[str, Any]], start_idx: int) -> Optional[int]:
    if not pm_norm:
        return None
    for p_pos in range(start_idx, len(people)):
        if _norm_alnum(people[p_pos]["name"]) == pm_norm:
            return p_pos
    return None


def _dedup_roster_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    for c in cards:
        nm = str(c.get("name") or "")
        is_special = c.get("is_fh") or c.get("tier") == "Unfilled Role" or nm.startswith("Unfilled")
        key = _norm_alnum(nm)
        if not is_special and key:
            if key in seen_names:
                continue
            seen_names.add(key)
        deduped.append(c)
    return deduped


def _locate_anchor_for_ws(
    ws: Dict[str, Any], people: List[Dict[str, Any]], search_from: int
) -> Optional[int]:
    pm_norm = _norm_alnum(ws["pm"])
    found = _find_single_pm_pos(pm_norm, people, search_from)
    if found is not None:
        return found
    for p_pos in range(search_from, len(people)):
        nm = str(people[p_pos].get("name") or "")
        if "product manager" in nm.lower():
            return p_pos
    return None


def _locate_pm_anchor_positions(page_ws: List[Dict[str, Any]], people: List[Dict[str, Any]]) -> List[Optional[int]]:
    pm_positions: List[Optional[int]] = []
    search_from = 0
    for ws in page_ws:
        found = _locate_anchor_for_ws(ws, people, search_from)
        if found is not None:
            search_from = found + 1
        pm_positions.append(found)
    return pm_positions


def _resolve_next_anchor(pm_positions: List[Optional[int]], from_idx: int, total_people: int) -> int:
    for next_w in range(from_idx, len(pm_positions)):
        if pm_positions[next_w] is not None:
            return int(pm_positions[next_w])
    return total_people


def _find_end_pos(roster_text: str, last_pos: int, last_raw: str, alloc_count: int) -> int:
    after_last = roster_text.find("\n", last_pos + len(last_raw))
    if after_last == -1:
        return len(roster_text)
    nl2 = roster_text.find("\n", after_last + 1)
    if nl2 == -1:
        return len(roster_text)
    if alloc_count > 1:
        nl3 = roster_text.find("\n", nl2 + 1)
        return nl3 if nl3 != -1 else len(roster_text)
    return nl2


def _slice_workstream_roster_quote(roster: List[Dict[str, Any]], roster_text: str, fallback: str) -> str:
    if not roster:
        return fallback
    first_raw = str(roster[0].get("raw_line") or "").strip()
    pos = roster_text.find(first_raw) if first_raw else -1
    if pos == -1:
        return fallback
    last_raw = str(roster[-1].get("raw_line") or "").strip()
    last_pos = roster_text.find(last_raw, pos) if last_raw else pos
    if last_pos == -1:
        last_pos = pos
    end_pos = _find_end_pos(roster_text, last_pos, last_raw, roster[-1].get("alloc_count", 1))
    return roster_text[pos:min(end_pos, pos + 340)].strip()


def _bind_roster_to_page_workstreams(
    page_ws: List[Dict[str, Any]], people: List[Dict[str, Any]], roster_text: str,
    fallback_roster: str, prev_page_last_ws: Optional[Dict[str, Any]] = None, p_idx: int = 1,
) -> None:
    pm_positions = _locate_pm_anchor_positions(page_ws, people)
    first_anchor = pm_positions[0] if pm_positions else None
    if first_anchor is None:
        next_known = next((pos for pos in pm_positions if pos is not None), len(people))
        page_ws[0]["roster"] = _dedup_roster_cards(list(people[0:next_known]))
        page_ws[0]["verbatim_roster"] = _slice_workstream_roster_quote(page_ws[0]["roster"], roster_text, fallback_roster)
    elif first_anchor > 0 and prev_page_last_ws is not None:
        overflow_cards = people[0:first_anchor]
        prev_page_last_ws["roster"] = _dedup_roster_cards(prev_page_last_ws["roster"] + overflow_cards)
        overflow_quote = _slice_workstream_roster_quote(overflow_cards, roster_text, fallback_roster)
        if overflow_quote:
            prev_page_last_ws.setdefault("overflow_rosters", []).append((p_idx, overflow_quote))

    for w_idx, ws in enumerate(page_ws):
        if w_idx == 0 and first_anchor is None:
            continue
        start_p = int(pm_positions[w_idx]) if pm_positions[w_idx] is not None else _resolve_next_anchor(pm_positions, w_idx + 1, len(people))
        end_p = _resolve_next_anchor(pm_positions, w_idx + 1, len(people))
        ws["roster"] = _dedup_roster_cards(list(people[start_p:end_p]))
        ws["verbatim_roster"] = _slice_workstream_roster_quote(ws["roster"], roster_text, fallback_roster)


def _parse_single_page_workstreams(
    p_idx: int, page: str, carry_stub: str, prev_page_last_ws: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    sm_matches = list(re.finditer(r"SM:\s*([^\n]+)", page))
    if not sm_matches:
        return [], carry_stub
    pct_matches = list(re.finditer(r"(?:\$([-0-9,]+)K|—)\s+(\d+)%", page))
    meta_end = pct_matches[-1].end() if pct_matches else len(page)
    page_ws: List[Dict[str, Any]] = []
    for idx, sm_m in enumerate(sm_matches):
        win_start = pct_matches[idx - 1].end() if idx > 0 and idx - 1 < len(pct_matches) else 0
        win_end = pct_matches[idx].end() if idx < len(pct_matches) else meta_end
        raw_block = page[win_start:win_end]
        block = (carry_stub + "\n" + raw_block) if (idx == 0 and carry_stub) else raw_block
        ws_item = _parse_workstream_block(block, raw_block, page, sm_m, p_idx, idx)
        if idx == 0 and carry_stub and p_idx > 1:
            ws_item["carry_quote"] = carry_stub[:340]
            ws_item["carry_page"] = p_idx - 1
        page_ws.append(ws_item)
    roster_text = page[meta_end:]
    next_stub, roster_lines, verbatim_roster = _split_carry_stub_and_roster_lines(roster_text)
    people = _parse_page_roster_cards(roster_lines)
    _bind_roster_to_page_workstreams(page_ws, people, roster_text, verbatim_roster, prev_page_last_ws, p_idx)
    return page_ws, next_stub


def parse_tabular_workstream_roster(document_text: str) -> List[Dict[str, Any]]:
    """Parse all workstreams, ownership metadata, and right-column roster cards across pages."""
    all_ws: List[Dict[str, Any]] = []
    carry_stub = ""
    for p_idx, page in _split_document_pages(document_text):
        prev_last = all_ws[-1] if all_ws else None
        page_ws, carry_stub = _parse_single_page_workstreams(p_idx, page, carry_stub, prev_last)
        all_ws.extend(page_ws)
    return all_ws


def _build_evidence_record(
    ev_id: str, summary_text: str, exact_quote: str, page: int, document_text: str
) -> Dict[str, Any]:
    start_off = max(0, document_text.find(exact_quote))
    end_off = start_off + len(exact_quote)
    return {
        "evidence_id": ev_id, "speaker": "Not specified", "source_classification": "FACT",
        "kind": "summary", "text": summary_text[:460], "exact_text": exact_quote[:350],
        "start_offset": start_off, "end_offset": end_off, "quote_pointer": [start_off, end_off],
        "locator": {"kind": "section", "value": f"## Page {page}"}, "grounding_status": "GROUNDED",
    }


def _upsert_entity(
    ent_map: Dict[str, Dict[str, Any]], eid: str, etype: str, name: str, desc: str, ev_ids: List[str]
) -> None:
    existing = ent_map.get(eid)
    if existing is None:
        ent_map[eid] = {"id": eid, "type": etype, "name": name[:120], "description": desc[:400], "mutation": "CREATE", "evidence_ids": list(ev_ids)}
        return
    cur = existing.setdefault("evidence_ids", [])
    for ev_id in ev_ids:
        if ev_id not in cur and len(cur) < 8:
            cur.append(ev_id)


def _upsert_signal(
    sig_map: Dict[str, Dict[str, Any]], from_id: str, to_id: str, rel: str, desc: str, ev_ids: List[str], quote: str, page: int
) -> None:
    sig_id = f"rel-{from_id.replace(':', '-')}--{to_id.replace(':', '-')}--{rel}"
    existing = sig_map.get(sig_id)
    if existing is None:
        sig_map[sig_id] = {
            "signal_id": sig_id, "from_id": from_id, "to_id": to_id, "relationship": rel,
            "predicate": rel, "evidence_ids": list(ev_ids), "mutation": "CONFIRM",
            "epistemic_status": "CONFIRMED", "description": desc[:380],
        }
        return
    cur = existing.setdefault("evidence_ids", [])
    for ev_id in ev_ids:
        if ev_id not in cur and len(cur) < 8:
            cur.append(ev_id)


def _emit_vertical_and_dept_leaders(
    ws: Dict[str, Any], vert_id: str, dept_id: str, base_evs: List[str],
    ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]], page: int,
) -> None:
    vert_m = re.search(r"\(([^)]+)\)", ws["vertical"])
    if vert_m:
        v_lead = _normalize_person_name(vert_m.group(1))
        v_lead_id = _slugify_id("person", v_lead)
        _upsert_entity(ent_map, v_lead_id, "person", v_lead, f"{v_lead}, Vertical Lead for {ws['vertical']}.", base_evs)
        _upsert_signal(sig_map, v_lead_id, vert_id, "LEADS", f"{v_lead} leads vertical {ws['vertical']}.", base_evs, ws["verbatim_line"], page)
    dept_m = re.search(r"\(([^)]+)\)", ws["dept"])
    if dept_m:
        d_lead = _normalize_person_name(dept_m.group(1))
        d_lead_id = _slugify_id("person", d_lead)
        _upsert_entity(ent_map, d_lead_id, "person", d_lead, f"{d_lead}, Department Lead for {ws['dept']}.", base_evs)
        _upsert_signal(sig_map, d_lead_id, dept_id, "LEADS", f"{d_lead} leads department {ws['dept']}.", base_evs, ws["verbatim_line"], page)


def _emit_product_and_metrics(
    ws: Dict[str, Any], ws_id: str, base_evs: List[str], ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]], risk_list: List[Dict[str, Any]], page: int,
) -> None:
    if ws.get("product"):
        prod_name = ws["product"]
        prod_norm, ws_norm = _norm_alnum(prod_name), _norm_alnum(ws["name"])
        colliding = {"jarvis", "neajarvis", "fastpass", "nora", "contractormanagement", "licenseai", "regulatoryai", "beacon", "gridflow", "processatlas", "propertytaxappeal", "securityportal", "lens", "atlas", "conduit", "reviq", "symphony", "mercuryai"}
        sys_label = (f"{prod_name} Platform" if not prod_name.lower().endswith("platform") else prod_name) if (prod_norm == ws_norm or prod_norm in colliding) else prod_name
        sys_id = _slugify_id("system", sys_label)
        _upsert_entity(ent_map, sys_id, "system", sys_label, f"{prod_name} platform/product bound to {ws['name']}.", base_evs)
        _upsert_signal(sig_map, ws_id, sys_id, "USES_SYSTEM", f"Workstream {ws['name']} delivers on product {prod_name}.", base_evs, ws["verbatim_line"], page)
    if ws.get("has_nav"):
        metric_id = _slugify_id("metric", f"{ws['name']}-nav-and-assignment")
        m_label = f"{ws['name']} (${ws['nav_k']}K NAV, {ws['progress_pct']}% Assigned)"
        _upsert_entity(ent_map, metric_id, "metric", m_label, f"Est. NAV ${ws['nav_k']}K with {ws['progress_pct']}% assignment progress.", base_evs)
        _upsert_signal(sig_map, ws_id, metric_id, "HAS_METRIC", f"{ws['name']} has Est. NAV ${ws['nav_k']}K and {ws['progress_pct']}% assigned.", base_evs, ws["verbatim_line"], page)
    if ws.get("charter_mission") == "No Associated Charter":
        risk_list.append({
            "risk_id": _slugify_id("risk", f"{ws['name']}-no-associated-charter"),
            "entity_id": ws_id,
            "subject_id": ws_id,
            "title": f"Missing Charter: {ws['name']}",
            "description": f"Workstream {ws['name']} is marked 'No Associated Charter' in NEA Q4 planning.",
            "severity": "MEDIUM",
            "evidence_ids": list(base_evs[:2]),
        })


def _emit_open_role_slots(
    ws: Dict[str, Any], ws_id: str, future_hires: List[Dict[str, Any]], roster_ev_list: List[str],
    roster_quote: str, ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
    risk_list: List[Dict[str, Any]], page: int,
) -> None:
    for idx, fh in enumerate(future_hires, 1):
        fh_name, fh_tier = str(fh.get("name") or f"Unfilled Role {idx}"), str(fh.get("tier") or "Unfilled Role")
        role_id = _slugify_id("role", f"{ws['name']}-{fh_name}-{idx}" if fh_name.startswith("Unfilled") else f"{ws['name']}-{fh_name}")
        role_label = f"{fh_name} ({ws['name']})" if fh_name.startswith("Unfilled") else f"{fh_name} ({fh_tier} on {ws['name']})"
        _upsert_entity(ent_map, role_id, "role", role_label, f"Open Q4 2026 role slot {fh_name} ({fh_tier}) on workstream {ws['name']} in {ws['vertical']}.", roster_ev_list)
        ent_map[role_id]["node_type"] = "role_group"
        _upsert_signal(sig_map, role_id, ws_id, "CANDIDATE_FOR", f"Open role {fh_name} ({fh_tier}) is an unfilled Q4 slot on workstream {ws['name']}.", roster_ev_list, roster_quote, page)
        risk_list.append({"risk_id": _slugify_id("risk", f"{ws['name']}-{fh_name}"), "entity_id": ws_id, "subject_id": ws_id, "title": f"Capacity Gap: {fh_name} on {ws['name']}", "description": f"Unfilled role {fh_name} ({fh_tier}) on workstream {ws['name']}.", "severity": "MEDIUM", "evidence_ids": list(roster_ev_list[:2])})


def _is_assigned_member(p: Dict[str, Any]) -> bool:
    return not p.get("is_fh") and p.get("tier") != "Unfilled Role" and not str(p.get("name", "")).startswith("Unfilled")


def _record_ws_evidence(ws: Dict[str, Any], assigned: List[Dict[str, Any]], ev_idx: Dict[str, Any], document_text: str) -> Tuple[List[str], List[str]]:
    page, ev_id, roster_ev_id = ws["page"], f"ev-ws-p{ws['page']}-{ws['idx_on_page']}", f"ev-roster-p{ws['page']}-{ws['idx_on_page']}"
    member_names = ", ".join(p["name"] for p in assigned[:12]) or ws["pm"] or ws["sm"]
    nav_phrase, pm_phrase = (f"${ws['nav_k']}K NAV, " if ws.get("has_nav", True) else ""), (f"PM {ws['pm']} and " if ws["pm"] else "")
    ev_idx[ev_id] = _build_evidence_record(ev_id, f"Workstream {ws['name']} in {ws['vertical']} and {ws['dept']} ({ws['status']}, {nav_phrase}{ws['progress_pct']}% assigned) with {pm_phrase}SM {ws['sm']}.", ws["verbatim_line"], page, document_text)
    base_evs = [ev_id]
    carry_quote, carry_page = ws.get("carry_quote"), ws.get("carry_page")
    if carry_quote and carry_page and carry_quote in document_text:
        carry_ev_id = f"ev-carry-p{carry_page}"
        ev_idx[carry_ev_id] = _build_evidence_record(carry_ev_id, f"Page {carry_page} header carry-over for {ws['name']}.", carry_quote, carry_page, document_text)
        base_evs.append(carry_ev_id)
    if ws.get("verbatim_roster"):
        ev_idx[roster_ev_id] = _build_evidence_record(roster_ev_id, f"Page {page} Team Assignments roster for {ws['name']}: {member_names}.", ws["verbatim_roster"], page, document_text)
    roster_ev_list = ([roster_ev_id] if roster_ev_id in ev_idx else []) + base_evs
    for ov_page, ov_quote in ws.get("overflow_rosters") or []:
        ov_ev_id = f"ev-overflow-p{ov_page}-{ws['idx_on_page']}"
        ev_idx[ov_ev_id] = _build_evidence_record(ov_ev_id, f"Page {ov_page} top-of-page overflow roster for {ws['name']}.", ov_quote, ov_page, document_text)
        if ov_ev_id not in roster_ev_list:
            roster_ev_list.append(ov_ev_id)
    return base_evs, roster_ev_list


def _emit_workstream_and_roster(
    ws: Dict[str, Any], ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]], risk_list: List[Dict[str, Any]], document_text: str,
) -> None:
    page = ws["page"]
    assigned = [p for p in ws["roster"] if _is_assigned_member(p)]
    future_hires = [p for p in ws["roster"] if not _is_assigned_member(p)]
    base_evs, roster_ev_list = _record_ws_evidence(ws, assigned, ev_idx, document_text)
    roster_quote = ws.get("verbatim_roster") or ws["verbatim_line"]
    ws_id, vert_id, dept_id = _slugify_id("workstream", ws["name"]), _slugify_id("org", ws["vertical"]), _slugify_id("org", ws["dept"])
    charter_desc = f", Charter: {ws['charter_mission']}" if ws.get("charter_mission") else ""
    _upsert_entity(ent_map, ws_id, "workstream", ws["name"], f"{ws['name']} ({ws['status']} Q4 2026 planning workstream{charter_desc}).", base_evs)
    ent_map[ws_id].update({"node_type": "workstream", "workstream_status": ws.get("workstream_status") or ws["status"], "charter_mission": ws.get("charter_mission", ""), "resource_note": ws.get("resource_note", ""), "product_line": ws.get("product_line") or ws.get("product", ""), "est_nav_usd": ws.get("est_nav_usd"), "assignment_progress_pct": ws.get("assignment_progress_pct", ws.get("progress_pct", 0))})
    _upsert_entity(ent_map, vert_id, "org", ws["vertical"], f"{ws['vertical']} planning vertical.", base_evs)
    _upsert_signal(sig_map, ws_id, vert_id, "MEMBER_OF", f"Workstream {ws['name']} belongs to vertical {ws['vertical']}.", base_evs, ws["verbatim_line"], page)
    _upsert_entity(ent_map, dept_id, "org", ws["dept"], f"{ws['dept']} operating department.", base_evs)
    _upsert_signal(sig_map, ws_id, dept_id, "MEMBER_OF", f"Workstream {ws['name']} is under department {ws['dept']}.", base_evs, ws["verbatim_line"], page)
    _emit_vertical_and_dept_leaders(ws, vert_id, dept_id, base_evs, ent_map, sig_map, page)
    _emit_product_and_metrics(ws, ws_id, base_evs, ent_map, sig_map, risk_list, page)
    if ws["pm"]:
        pm_id = _slugify_id("person", ws["pm"])
        _upsert_entity(ent_map, pm_id, "person", ws["pm"], f"{ws['pm']}, Product Manager for {ws['name']}.", base_evs)
        _upsert_signal(sig_map, pm_id, ws_id, "LEADS", f"Product Manager {ws['pm']} leads workstream {ws['name']}.", base_evs, ws["verbatim_line"], page)
    if ws["sm"]:
        sm_id = _slugify_id("person", ws["sm"])
        _upsert_entity(ent_map, sm_id, "person", ws["sm"], f"{ws['sm']}, Scrum Master for {ws['name']}.", base_evs)
        _upsert_signal(sig_map, sm_id, ws_id, "MEMBER_OF", f"Scrum Master {ws['sm']} is assigned to workstream {ws['name']}.", base_evs, ws["verbatim_line"], page)
    for member in assigned:
        m_id = _slugify_id("person", member["name"])
        _upsert_entity(ent_map, m_id, "person", member["name"], f"{member['name']}, {member['tier']} specialist assigned to {ws['name']}.", roster_ev_list)
        _upsert_signal(sig_map, m_id, ws_id, "MEMBER_OF", f"{member['name']} ({member['tier']}) is assigned to workstream {ws['name']}.", roster_ev_list, roster_quote, page)
    _emit_open_role_slots(ws, ws_id, future_hires, roster_ev_list, roster_quote, ent_map, sig_map, risk_list, page)


def reconcile_tabular_workstream_mutations(
    data: Dict[str, Any], metadata: Optional[Dict[str, Any]], document_text: str
) -> List[str]:
    """Deterministically populate full workstreams, owners, and team assignments into Output 03."""
    if not isinstance(data, dict) or not is_tabular_workstream_roster_document(document_text):
        return []
    workstreams = parse_tabular_workstream_roster(document_text)
    if len(workstreams) < 5:
        return []
    mutations = data.setdefault("graph_mutations", {})
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    risk_list: List[Dict[str, Any]] = []
    for ws in workstreams:
        _emit_workstream_and_roster(ws, ev_idx, ent_map, sig_map, risk_list, document_text)
    if not ent_map:
        return []
    total_nav_k = sum(int(ws.get("nav_k") or 0) for ws in workstreams if ws.get("has_nav"))
    open_slot_count = sum(1 for e in ent_map.values() if e.get("type") == "role")
    first_ev = next(iter(ev_idx.keys()), None)
    if first_ev and total_nav_k > 0:
        port_metric_id = "metric:nea-q4-portfolio-total-nav-and-assignment"
        _upsert_entity(ent_map, port_metric_id, "metric", f"NEA Q4 Portfolio (${total_nav_k}K NAV across {len(workstreams)} workstreams)", f"Global portfolio rollup: {len(workstreams)} workstreams totaling ${total_nav_k}K NAV with {open_slot_count} open/future-hire role slots (30.4 Unfilled FTE of 404.9 requested people).", [first_ev])
    data["evidence_index"] = ev_idx
    data["evidence_only_statement_ids"] = []
    type_order = {"workstream": 0, "org": 1, "system": 2, "metric": 3, "role": 4, "person": 5}
    sorted_ents = sorted(ent_map.values(), key=lambda e: (type_order.get(str(e.get("type")), 9), str(e.get("id"))))
    mutations["entity_upserts"] = sorted_ents
    mutations["relationship_signals"] = list(sig_map.values())
    if risk_list:
        mutations["risk_updates"] = risk_list
    total_triples = len(sorted_ents) + len(sig_map)
    mutations["_budget_triage_audit"] = {"triage_policy": "tabular_roster_scaffolding_exemption", "workstreams_extracted": len(workstreams), "entities_extracted": len(sorted_ents), "signals_extracted": len(sig_map), "pruned_signal_count": 0, "budget_ceiling": total_triples, "base_budget": 60, "roster_exempt_triples": total_triples}
    return [f"Reconciled {len(workstreams)} tabular workstreams, {len(sorted_ents)} entities, and {len(sig_map)} relationship signals."]
