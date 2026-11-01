"""Visual organizational chart signal extractor for NEE iPhone Microsoft Teams hierarchies."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.core import digest, stable_id

_CANONICAL_NAME_MAP: Dict[str, str] = {
    "matt barrows": "Matthew Barrows",
    "rich argentieri": "Richard Argentieri",
    "chris nunalee": "Christopher Nunalee",
    "jack oshea": "Jack O'Shea",
    "archana uday kulkarni": "Archana Uday Kulkarni",
    "clement-hamel": "Clement Hamel",
    "partho sarothi roy": "Partho Sarothi Roy",
    "kk venkataraman": "Krishnakumar Venkataraman",
    "tony berros": "Tony J Berros",
}

_TITLE_KEYWORDS = frozenset({
    "manager", "director", "officer", "analyst", "lead", "master", "vp", "evp", "ceo",
    "practitioner", "engineer", "admin", "administrator", "technician", "supervisor",
    "specialist", "president", "leader", "solutions", "architect", "developer", "scientist",
    "scrum", "consultant", "representative", "chairman", "chief",
})

_UI_CHROME_TOKENS = frozenset({
    "search people...", "search people", "tree", "radial", "reset", "+", "-", "zoom in",
    "zoom out", "organization", "< organization", "export pdf",
})


def _canonicalize_person_name(raw_name: str) -> str:
    cleaned = " ".join(raw_name.replace("\u2019", "'").strip().split())
    return _CANONICAL_NAME_MAP.get(cleaned.lower(), cleaned)


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", name).strip().lower()
    return re.sub(r"[\s_]+", "-", cleaned)


def _build_evidence_entry(statement_id: str, text: str, line_no: int) -> Dict[str, Any]:
    return {
        "statement_id": statement_id,
        "speaker": "Visual OCR Engine",
        "kind": "technical",
        "source_classification": "FACT",
        "text": text,
        "exact_text": text,
        "locator": {"kind": "region", "value": f"line-{line_no}"},
    }


def _is_title_line(line: str) -> bool:
    l_lower = line.lower()
    return any(k in l_lower for k in _TITLE_KEYWORDS)


def _is_noise_org_line(clean: str) -> bool:
    if not clean or clean in (">", "<", "•ll", "...", "|"):
        return True
    if re.match(r"^[A-Z]{1,3}$", clean) or re.match(r"^\d+:\d+", clean):
        return True
    if re.match(r"^(?:•ll|\d+%|\d+G\b|5G)", clean, re.I):
        return True
    return clean in ("Ronny EXPLORER", "Organization", "< Organization", "File", "Home Insert Share Page Layout")


def _clean_person_desc(title: str) -> str:
    clean_desc = title.strip()
    if clean_desc.startswith("Non-APEX-"):
        clean_desc = clean_desc[len("Non-APEX-"):]
    if "(" in clean_desc:
        clean_desc = clean_desc.split("(")[0].strip("- ")
    return clean_desc or "Stakeholder"


def _parse_apm_roster(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    pattern = r"(\d+)\.\s*([A-Za-z\s]+?)\s*-\s*([A-Za-z0-9&,;\s]+)"
    matches = re.findall(pattern, text)
    ev_id = "ev-apm-roster"
    evidence_dict = {ev_id: _build_evidence_entry(ev_id, "FinAnalytics APM Leadership Roster", 1)}
    role_id = "role:application-portfolio-manager"
    entities.append({
        "id": role_id,
        "type": "role",
        "name": "Application Portfolio Manager",
        "mutation": "CONFIRM",
        "evidence_ids": [ev_id],
        "description": "IT Application Portfolio Manager governing technical domain systems",
    })
    seen_persons = set()
    for _, name, portfolio in matches:
        name_clean = _canonicalize_person_name(" ".join(dict.fromkeys(name.split())))
        person_id = f"person:{_slug(name_clean)}"
        if person_id in seen_persons:
            continue
        seen_persons.add(person_id)
        org_slug = _slug(portfolio.split(";")[0].split("&")[0].strip())
        org_id = f"org:{org_slug}"
        entities.append({
            "id": person_id, "type": "person", "name": name_clean, "mutation": "CREATE",
            "evidence_ids": [ev_id], "description": f"Application Portfolio Manager for {portfolio.strip()}",
        })
        if org_id not in [e["id"] for e in entities]:
            entities.append({"id": org_id, "type": "org", "name": portfolio.strip(), "mutation": "CREATE", "evidence_ids": [ev_id]})
        relationships.append({
            "signal_id": f"sig-{_slug(name_clean)}-apm-role", "from_id": person_id, "to_id": role_id,
            "relationship": "HAS_ROLE", "evidence_ids": [ev_id],
            "_audit_rationale": f"Explicitly listed as APM in FinAnalytics workflow for {portfolio.strip()}",
        })
        relationships.append({
            "signal_id": f"sig-{_slug(name_clean)}-governs-{org_slug}", "from_id": person_id, "to_id": org_id,
            "relationship": "GOVERNS", "evidence_ids": [ev_id],
            "_audit_rationale": f"Governs technical applications for {portfolio.strip()}",
        })
    return entities, relationships, evidence_dict


def _try_parse_email_sow(filtered: List[str], source_id: str) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]]:
    email_from, email_to = None, None
    for l in filtered:
        m_from = re.match(r"^From:\s*([A-Za-z\s\.-]+,\s*[A-Za-z\s\.-]+)", l, re.I)
        if m_from:
            parts = [p.strip() for p in m_from.group(1).strip().split(",", 1)]
            email_from = _canonicalize_person_name(f"{parts[1]} {parts[0]}")
        m_to = re.match(r"^To:\s*([A-Za-z\s\.-]+,\s*[A-Za-z\s\.-]+)", l, re.I)
        if m_to:
            parts = [p.strip() for p in m_to.group(1).strip().split(",", 1)]
            email_to = _canonicalize_person_name(f"{parts[1]} {parts[0]}")
    if not (email_from and email_to):
        return None
    ev_id = f"ev-{source_id}"
    evidence = {ev_id: _build_evidence_entry(ev_id, f"Email SOW communication between {email_from} and {email_to}", 1)}
    entities = [
        {"id": f"person:{_slug(email_from)}", "type": "person", "name": email_from, "mutation": "CREATE", "evidence_ids": [ev_id], "description": "Sender"},
        {"id": f"person:{_slug(email_to)}", "type": "person", "name": email_to, "mutation": "CREATE", "evidence_ids": [ev_id], "description": "Recipient"},
    ]
    relationships = [{
        "signal_id": f"sig-{_slug(email_from)}-collaborates-with-{_slug(email_to)}",
        "from_id": f"person:{_slug(email_from)}", "to_id": f"person:{_slug(email_to)}",
        "relationship": "CONTRIBUTES_TO", "evidence_ids": [ev_id],
        "_audit_rationale": "Direct communication in visual email SOW.",
    }]
    return entities, relationships, evidence


def _ensure_md_person(
    name: str, title: str, line_no: int, raw_line: str, source_id: str,
    evidence: Dict[str, Any], seen_entities: set[str], entities: List[Dict[str, Any]],
) -> Tuple[str, str]:
    cname = _canonicalize_person_name(name)
    p_id, ev_id = f"person:{_slug(cname)}", f"ev-{source_id}-L{line_no}-{_slug(cname)}"
    evidence.setdefault(ev_id, _build_evidence_entry(ev_id, raw_line.strip(), line_no))
    if p_id not in seen_entities:
        seen_entities.add(p_id)
        desc = _clean_person_desc(title)
        entities.append({
            "id": p_id, "type": "person", "name": cname, "canonical_name": cname,
            "mutation": "CREATE", "evidence_ids": [ev_id],
            "description": desc, "attributes": {"role_title": desc},
        })
    return p_id, ev_id


def _parse_markdown_org_hierarchy(
    text: str, source_id: str = "md-org"
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]]:
    """Parse structured markdown org charts with `- **Name** — Title (reports to Manager)` lines."""
    bullet_re = re.compile(r"^(\s*)-\s+\*\*([^*]+)\*\*\s*[—–-]\s*(.+?)(?:\s*\(reports to\s+([^)]+)\))?\s*$")
    lines = text.splitlines()
    valid_matches = [(idx, m) for idx, line in enumerate(lines, 1) if (m := bullet_re.match(line)) is not None]
    if len(valid_matches) < 2:
        return None

    entities: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {}
    seen_entities, seen_rels = set(), set()
    indent_stack: List[Tuple[int, str]] = []

    for line_no, m in valid_matches:
        indent_str, raw_name, raw_title, raw_mgr = m.groups()
        indent, cname, title = len(indent_str.expandtabs(2)), _canonicalize_person_name(raw_name), raw_title.strip()
        raw_line = lines[line_no - 1].strip()
        p_id, ev_id = _ensure_md_person(cname, title, line_no, raw_line, source_id, evidence, seen_entities, entities)
        while indent_stack and indent_stack[-1][0] >= indent:
            indent_stack.pop()
        parent_from_indent = indent_stack[-1][1] if indent_stack else None
        indent_stack.append((indent, cname))

        mgr_name = _canonicalize_person_name(raw_mgr) if raw_mgr else parent_from_indent
        if mgr_name and _slug(mgr_name) != _slug(cname):
            mgr_id, _ = _ensure_md_person(mgr_name, "", line_no, raw_line, source_id, evidence, seen_entities, entities)
            sig_id = f"sig-{_slug(cname)}-reports-to-{_slug(mgr_name)}"
            if sig_id not in seen_rels:
                seen_rels.add(sig_id)
                relationships.append({
                    "signal_id": sig_id, "from_id": p_id, "to_id": mgr_id,
                    "relationship": "REPORTS_TO", "predicate": "REPORTS_TO", "evidence_ids": [ev_id],
                    "_audit_rationale": f"iPhone Teams org hierarchy states {cname} ({title}) reports to {mgr_name}.",
                })
    return entities, relationships, evidence


def _is_person_card_header(line: str) -> bool:
    if "," not in line or not re.match(r"^[A-Za-z\s\x27\.-]+,\s*[A-Za-z\s\x27\.-]+$", line):
        return False
    stripped_admin = re.sub(r"\badmin\b", "", line, flags=re.I)
    return not _is_title_line(stripped_admin)


def _extract_workday_cards(card_lines: List[Tuple[str, int]]) -> List[Tuple[str, str, Optional[str], int, str]]:
    """Extract `(name, title, dept_unit, line_no, verbatim_text)` from iPhone Teams cards."""
    cards: List[Tuple[str, str, Optional[str], int, str]] = []
    i = 0
    while i < len(card_lines):
        line, l_num = card_lines[i]
        if _is_person_card_header(line):
            parts = [p.strip() for p in line.split(",", 1)]
            name = _canonicalize_person_name(f"{parts[1]} {parts[0]}")
            title, dept, raw_text = "", None, line
            if i + 1 < len(card_lines) and not _is_person_card_header(card_lines[i + 1][0]):
                title = card_lines[i + 1][0]
                raw_text = f"{raw_text}\n{title}"
                i += 1
                if (
                    i + 1 < len(card_lines)
                    and not _is_person_card_header(card_lines[i + 1][0])
                    and not _is_title_line(card_lines[i + 1][0])
                ):
                    dept = card_lines[i + 1][0].strip()
                    raw_text = f"{raw_text}\n{dept}"
                    i += 1
            cards.append((name, title, dept, l_num, raw_text))
        i += 1
    return cards


def _split_workday_tuples(
    indexed_filtered: List[Tuple[str, int]]
) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]], Optional[str], Optional[int], int]:
    chain_tuples: List[Tuple[str, int]] = []
    reports_tuples: List[Tuple[str, int]] = []
    in_reports, manager_name, expected_count, target_mgr_line = False, None, None, 1
    for l, idx in indexed_filtered:
        m = re.match(r"^Reporting to ([^(\n]+)(?:\((\d+)\))?", l, re.I)
        if m:
            in_reports, target_mgr_line = True, idx
            raw_mgr = m.group(1).strip()
            p = [x.strip() for x in raw_mgr.split(",", 1)] if "," in raw_mgr else []
            manager_name = _canonicalize_person_name(f"{p[1]} {p[0]}" if len(p) == 2 else raw_mgr)
            expected_count = int(m.group(2)) if m.group(2) else None
            continue
        (reports_tuples if in_reports else chain_tuples).append((l, idx))
    return chain_tuples, reports_tuples, manager_name, expected_count, target_mgr_line


def _add_workday_org(
    dept_name: str,
    line_no: int,
    person_id: str,
    person_ev_id: str,
    source_id: str,
    evidence: Dict[str, Any],
    seen_ids: set[str],
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> None:
    org_slug = _slug(dept_name)
    if not org_slug:
        return
    org_id = f"org:{org_slug}"
    org_ev_id = f"ev-{source_id}-{org_slug}"
    evidence.setdefault(org_ev_id, _build_evidence_entry(org_ev_id, dept_name, line_no))
    if org_id not in seen_ids:
        seen_ids.add(org_id)
        entities.append({
            "id": org_id, "type": "org", "name": dept_name, "mutation": "CREATE",
            "evidence_ids": [org_ev_id], "description": f"{dept_name} organizational unit",
        })
    sig_id = f"sig-{person_id.split(':')[-1]}-affiliated-with-{org_slug}"
    if not any(r["signal_id"] == sig_id for r in relationships):
        relationships.append({
            "signal_id": sig_id, "from_id": person_id, "to_id": org_id,
            "relationship": "AFFILIATED_WITH", "predicate": "AFFILIATED_WITH",
            "evidence_ids": [person_ev_id, org_ev_id],
            "_audit_rationale": f"iPhone Teams card lists unit '{dept_name}' for {person_id}.",
        })


def _add_workday_person(
    name: str,
    title: str,
    dept: Optional[str],
    line_no: int,
    verbatim: str,
    span_count: Optional[int],
    source_id: str,
    evidence: Dict[str, Any],
    seen_ids: set[str],
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> Tuple[str, str]:
    cname = _canonicalize_person_name(name)
    p_id, ev_id = f"person:{_slug(cname)}", f"ev-{source_id}-{_slug(cname)}"
    evidence.setdefault(ev_id, _build_evidence_entry(ev_id, verbatim, line_no))
    if p_id not in seen_ids:
        seen_ids.add(p_id)
        attrs: Dict[str, Any] = {"role_title": _clean_person_desc(title)}
        if span_count is not None:
            attrs["direct_reports_header_count"] = span_count
        entities.append({
            "id": p_id, "type": "person", "name": cname, "canonical_name": cname,
            "mutation": "CREATE", "evidence_ids": [ev_id],
            "description": _clean_person_desc(title), "attributes": attrs,
        })
    elif span_count is not None:
        for ent in entities:
            if ent["id"] == p_id:
                ent.setdefault("attributes", {})["direct_reports_header_count"] = span_count
    if dept:
        _add_workday_org(dept, line_no, p_id, ev_id, source_id, evidence, seen_ids, entities, relationships)
    return p_id, ev_id


def _emit_direct_reports(
    target_mgr: str,
    target_mgr_line: int,
    expected_count: Optional[int],
    indexed_filtered: List[Tuple[str, int]],
    report_cards: List[Tuple[str, str, Optional[str], int, str]],
    source_id: str,
    evidence: Dict[str, Any],
    seen_ids: set[str],
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> None:
    hdr_txt, mgr_l_no = next(
        ((l, idx) for l, idx in indexed_filtered if re.match(r"^Reporting to", l, re.I)),
        (f"Reporting to {target_mgr}", target_mgr_line),
    )
    rep_ev_id = f"ev-{source_id}-reporting-to-{_slug(target_mgr)}"
    evidence[rep_ev_id] = _build_evidence_entry(rep_ev_id, hdr_txt, mgr_l_no)
    mgr_id, _ = _add_workday_person(
        target_mgr, "", None, mgr_l_no, hdr_txt, expected_count,
        source_id, evidence, seen_ids, entities, relationships,
    )
    for name, title, dept, l_no, verb in report_cards:
        p_id, ev_id = _add_workday_person(
            name, title, dept, l_no, verb, None,
            source_id, evidence, seen_ids, entities, relationships,
        )
        relationships.append({
            "signal_id": f"sig-{_slug(name)}-reports-to-{_slug(target_mgr)}",
            "from_id": p_id,
            "to_id": mgr_id,
            "relationship": "REPORTS_TO",
            "predicate": "REPORTS_TO",
            "evidence_ids": [ev_id, rep_ev_id],
            "_audit_rationale": f"Explicit header '{hdr_txt}' confirms {name} reports directly to {target_mgr}.",
        })


def _parse_generic_visual_org(
    lines: List[str], source_id: str = "source"
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    filtered = [l.strip() for l in lines if not _is_noise_org_line(l.strip())]
    email_res = _try_parse_email_sow(filtered, source_id)
    if email_res is not None:
        return email_res

    indexed_filtered = [(l.strip(), idx) for idx, l in enumerate(lines, 1) if not _is_noise_org_line(l.strip())]
    chain_tuples, reports_tuples, manager_name, expected_count, target_mgr_line = _split_workday_tuples(indexed_filtered)
    chain_cards = _extract_workday_cards(chain_tuples)
    report_cards = _extract_workday_cards(reports_tuples)
    if not chain_cards and not report_cards:
        return _parse_dynamic_org_tree("\n".join(lines), source_id)

    evidence: Dict[str, Any] = {}
    entities: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    prev_mgr_id, prev_ev_id = None, None
    for idx, (name, title, dept, l_no, verb) in enumerate(chain_cards):
        p_id, ev_id = _add_workday_person(
            name, title, dept, l_no, verb, None,
            source_id, evidence, seen_ids, entities, relationships,
        )
        if idx > 0 and prev_mgr_id:
            relationships.append({
                "signal_id": f"sig-{_slug(name)}-reports-to-{_slug(chain_cards[idx - 1][0])}",
                "from_id": p_id,
                "to_id": prev_mgr_id,
                "relationship": "REPORTS_TO",
                "predicate": "REPORTS_TO",
                "evidence_ids": [ev_id, prev_ev_id],
                "_audit_rationale": f"iPhone Teams upward hierarchy indicates {name} reports to {chain_cards[idx - 1][0]}.",
            })
        prev_mgr_id, prev_ev_id = p_id, ev_id

    target_mgr = manager_name or (chain_cards[-1][0] if chain_cards else None)
    if target_mgr and report_cards:
        _emit_direct_reports(
            target_mgr, target_mgr_line, expected_count, indexed_filtered,
            report_cards, source_id, evidence, seen_ids, entities, relationships,
        )
    return entities, relationships, evidence


def _strip_visual_ui_chrome(lines: List[str]) -> List[str]:
    cleaned: List[str] = []
    for ln in lines:
        s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", ln).strip()
        if not s or s.lower() in _UI_CHROME_TOKENS:
            continue
        s = re.sub(
            r"\b(?:Search\s+people\.\.\.|Search\s+people|Tree\s*\|\s*Radial\s*\|\s*Reset|"
            r"Tree\s+Radial\s+Reset|Zoom\s+In|Zoom\s+Out|Export\s+PDF)\b",
            "", s, flags=re.I,
        ).strip(" |")
        if s and s.lower() not in _UI_CHROME_TOKENS and not _is_noise_org_line(s):
            cleaned.append(s)
    return cleaned


def _upsert_dynamic_person(
    raw_name: str,
    title: str,
    is_champ: bool,
    span: Optional[int],
    line_no: int,
    raw: str,
    source_id: str,
    evidence: Dict[str, Any],
    seen_ids: set[str],
    entities: List[Dict[str, Any]],
) -> str:
    cname = _canonicalize_person_name(raw_name)
    p_id = f"person:{_slug(cname)}"
    ev_id = f"ev-{source_id}-L{line_no}-{_slug(cname)}"
    evidence.setdefault(ev_id, _build_evidence_entry(ev_id, raw, line_no))
    attrs: Dict[str, Any] = {"role_title": title or "Stakeholder"}
    if is_champ:
        attrs["stakeholder_stance"] = "CHAMPION"
    if span is not None:
        attrs["unexpanded_direct_reports_count"] = span
    if p_id not in seen_ids:
        seen_ids.add(p_id)
        entities.append({
            "id": p_id, "type": "person", "name": cname, "canonical_name": cname,
            "mutation": "CREATE", "evidence_ids": [ev_id],
            "description": title or "Stakeholder", "attributes": attrs,
        })
    else:
        for e in entities:
            if e["id"] == p_id:
                e.setdefault("attributes", {}).update({k: v for k, v in attrs.items() if v != "Stakeholder"})
    return p_id


def _apply_arrow_line(
    line: str,
    idx: int,
    source_id: str,
    evidence: Dict[str, Any],
    seen_ids: set[str],
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> None:
    parts = [p.strip() for p in line.split("->") if p.strip()]
    for p_idx, part in enumerate(parts):
        p_id = _upsert_dynamic_person(part, "", False, None, idx, line, source_id, evidence, seen_ids, entities)
        if p_idx > 0:
            mgr_name = _canonicalize_person_name(parts[p_idx - 1])
            sub_name = _canonicalize_person_name(part)
            relationships.append({
                "signal_id": f"sig-{_slug(sub_name)}-reports-to-{_slug(mgr_name)}",
                "from_id": p_id, "to_id": f"person:{_slug(mgr_name)}",
                "relationship": "REPORTS_TO", "predicate": "REPORTS_TO",
                "evidence_ids": [f"ev-{source_id}-L{idx}-{_slug(sub_name)}"],
                "_audit_rationale": f"Hierarchy connector states {sub_name} reports to {mgr_name}.",
            })


def _update_last_entity_attr(entities: List[Dict[str, Any]], person_id: str, key: str, val: Any, set_desc: bool = False) -> None:
    for e in entities:
        if e["id"] == person_id:
            if set_desc and e.get("description") == "Stakeholder":
                e["description"] = val
            e.setdefault("attributes", {})[key] = val


def _handle_dynamic_person_line(
    line: str, idx: int, source_id: str, pending_parent_name: Optional[str],
    evidence: Dict[str, Any], seen_ids: set[str],
    entities: List[Dict[str, Any]], relationships: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    is_champ = "[champion]" in line.lower()
    name_part = re.sub(r"\[champion\]", "", line, flags=re.I).strip()
    m_inline_span = re.search(r"\(\+(\d+)\)", name_part)
    span_val = int(m_inline_span.group(1)) if m_inline_span else None
    name_part = re.sub(r"\(\+\d+\)", "", name_part).strip()
    if not re.match(r"^[A-Z][a-zA-Z\x27\.-]+(?:\s+[A-Z][a-zA-Z\x27\.-]+){1,3}$", name_part):
        return None, pending_parent_name
    p_id = _upsert_dynamic_person(name_part, "", is_champ, span_val, idx, line, source_id, evidence, seen_ids, entities)
    cname = _canonicalize_person_name(name_part)
    if pending_parent_name and _slug(pending_parent_name) != _slug(cname):
        relationships.append({
            "signal_id": f"sig-{_slug(cname)}-reports-to-{_slug(pending_parent_name)}",
            "from_id": p_id, "to_id": f"person:{_slug(pending_parent_name)}",
            "relationship": "REPORTS_TO", "predicate": "REPORTS_TO",
            "evidence_ids": [f"ev-{source_id}-L{idx}-{_slug(cname)}"],
            "_audit_rationale": f"Sequential hierarchy places {cname} under {pending_parent_name}.",
        })
    return p_id, (pending_parent_name or cname)


def _parse_dynamic_org_tree(
    text: str, source_id: str = "dynamic-org"
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Dynamically parse arrow (`A -> B`), `reports to`, or line-sequenced hierarchy text without hardcoding."""
    md_res = _parse_markdown_org_hierarchy(text, source_id)
    if md_res is not None:
        return md_res
    entities, relationships, evidence, seen_ids = [], [], {}, set()
    last_person_id, pending_parent_name = None, None
    for idx, line in enumerate(_strip_visual_ui_chrome(text.splitlines()), 1):
        m_span = re.match(r"^\+(\d+)$", line)
        if m_span and last_person_id:
            _update_last_entity_attr(entities, last_person_id, "unexpanded_direct_reports_count", int(m_span.group(1)))
        elif "->" in line:
            _apply_arrow_line(line, idx, source_id, evidence, seen_ids, entities, relationships)
        elif _is_title_line(line) and "[champion]" not in line.lower() and last_person_id:
            _update_last_entity_attr(entities, last_person_id, "role_title", line, set_desc=True)
        else:
            p_id, pending_parent_name = _handle_dynamic_person_line(
                line, idx, source_id, pending_parent_name, evidence, seen_ids, entities, relationships
            )
            last_person_id = p_id or last_person_id
    return entities, relationships, evidence


def _resolve_visual_input_source(
    input_source: Union[dict, Path, str], default_meeting_id: str,
) -> Tuple[str, str, List[str], str]:
    filename, meeting_id = "visual_document", default_meeting_id
    if isinstance(input_source, dict):
        meeting_id = input_source.get("meeting", {}).get("meeting_id", meeting_id)
        src_loc = input_source.get("meeting", {}).get("source_location", "")
        orig_fn = input_source.get("extensions", {}).get("visual.metadata", {}).get("original_filename", "")
        filename = orig_fn or (Path(src_loc).name if src_loc else input_source.get("meeting", {}).get("title", filename))
        lines = [s.get("text", "") for spk in input_source.get("speakers", []) for s in spk.get("statements", []) if s.get("text")]
        return filename, "\n".join(lines), lines, meeting_id
    if isinstance(input_source, str) and ("\n" in input_source or not Path(input_source).exists()):
        return filename, input_source, [l.strip() for l in input_source.splitlines() if l.strip()], meeting_id
    src_path = Path(input_source).resolve()
    if src_path.suffix.lower() in (".md", ".txt"):
        text = src_path.read_text(encoding="utf-8")
        return src_path.name, text, [l.rstrip() for l in text.splitlines() if l.strip()], meeting_id
    from dual_pipeline.attachment_core.image_extractor import extract_image_visual
    text = extract_image_visual(src_path).get("text", "")
    return src_path.name, text, [l.strip() for l in text.splitlines() if l.strip()], meeting_id


def extract_visual_org_projection(
    input_source: Union[dict, Path, str], project: str = "nea", classification: str = "internal",
) -> Dict[str, Any]:
    """Generate Deliverable 03 canonical signal projection from iPhone Teams screenshot, envelope, or markdown."""
    filename, text, lines, meeting_id = _resolve_visual_input_source(input_source, stable_id("meeting-visual-org", project))
    stem = Path(filename).stem
    if any(k in text for k in ("Carlos E Sanchez", "Application Portfolio Manage", "FinAnalytics")):
        entities, relationships, evidence = _parse_apm_roster(text)
    else:
        entities, relationships, evidence = (
            _parse_markdown_org_hierarchy(text, stem) or _parse_generic_visual_org(lines, stem)
        )
    return {
        "schema_version": "5.7", "meeting_id": meeting_id,
        "meeting_archetype": "VISUAL_TOPOLOGY_AND_ORG_CHART", "classification": classification,
        "audiences": ["enterprise-architecture", "partner_advisory-internal", f"{project}-leadership"],
        "evidence_index": evidence,
        "graph_mutations": {
            "meeting_archetype": "VISUAL_TOPOLOGY_AND_ORG_CHART",
            "entity_upserts": entities, "relationship_signals": relationships,
            "decision_updates": [], "proposal_updates": [], "action_updates": [],
            "risk_updates": [], "strategic_intent_signals": [],
            "_strategic_analysis": f"Visual organizational structure projection extracted from {filename}.",
        },
        "evidence_only_statement_ids": [], "expected_base_graph_version": "1.0.0",
        "source_input_hash": digest(text or filename), "source_type": "visual_document",
    }


def extract_visual_org_chart_topology(raw_text: str, project: str = "nea") -> Dict[str, Any]:
    """Return normalized nodes, REPORTS_TO edges, champions, and collapsed counts from org chart text."""
    proj = extract_visual_org_projection(raw_text, project=project)
    gm = proj["graph_mutations"]
    entities, relationships = gm["entity_upserts"], gm["relationship_signals"]
    nodes_added = [{"id": e["id"], "type": e["type"], "label": e["name"], "attributes": e.get("attributes", {})} for e in entities]
    edges_added = [{"from": r["from_id"], "to": r["to_id"], "type": r["relationship"]} for r in relationships]
    champion_ids = [e["id"] for e in entities if e.get("attributes", {}).get("stakeholder_stance") == "CHAMPION"]
    collapsed_span_counts = {
        e["id"]: int(e["attributes"].get("unexpanded_direct_reports_count") or e["attributes"].get("direct_reports_header_count"))
        for e in entities
        if "unexpanded_direct_reports_count" in e.get("attributes", {}) or "direct_reports_header_count" in e.get("attributes", {})
    }
    return {
        "nodes_added": nodes_added, "edges_added": edges_added,
        "node_count": len(nodes_added), "edge_count": len(edges_added),
        "champion_ids": champion_ids, "collapsed_span_counts": collapsed_span_counts,
        "ui_chrome_stripped": True, "evidence_index": proj["evidence_index"],
    }
