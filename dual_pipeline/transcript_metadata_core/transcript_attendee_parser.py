"""
dual_pipeline/transcript_metadata_core/transcript_attendee_parser.py

Layer 1: Robust parser and formatter for meeting attendees and transcript speakers.
Extracts company-grouped attendee hierarchies from markdown headers, dialogue turns,
and metadata rosters while strictly eliminating bogus metadata tokens and duplicate rows.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

METADATA_HEADER_LABELS = {
    "date", "meeting date", "time", "start time", "end time", "meeting id", "status", "attendees", "attendee", "duration", "subject", "transcript", "ambiguity lock register", "notes", "agenda", "objective", "meeting objective", "next steps", "action items", "platform reference", "platform", "location", "organizer", "classification", "audiences", "participants", "verbatim forensic transcript feed", "discussion", "recap", "summary", "recorded transcript range", "source", "editorial note", "additional voices", "full transcript", "attendee roster", "invited / not present", "invited", "not present", "referenced stakeholders",
}
COMPANY_OR_GENERIC_TOKENS = {"partner_advisory", "apex_global", "apex_global energy", "nea", "apex", "florida power & light", "client", "partner", "delivery partner", "participant", "participants", "bcg", "boston consulting group"}
BLACKLISTED_TOKENS = METADATA_HEADER_LABELS | COMPANY_OR_GENERIC_TOKENS


def is_metadata_header_label(token: str) -> bool:
    """Check if token is a document header label like Date, Time, Attendees, etc."""
    clean = re.sub(r"[^\w\s]", "", re.sub(r"[*_#:`]", "", token).strip().lower()).strip()
    return clean in METADATA_HEADER_LABELS or not clean


def is_blacklisted_attendee(name: str) -> bool:
    """Check if token is a non-person metadata label or company header when parsing attendee names."""
    clean = re.sub(r"[^\w\s]", "", re.sub(r"[*_#:`]", "", name).strip().lower()).strip()
    return clean in BLACKLISTED_TOKENS or not clean


def strip_org_suffix_from_role(role: Optional[str], org: Optional[str] = None) -> str:
    """Strips trailing organization suffix from a role string and returns atomic role."""
    if not role or not isinstance(role, str):
        return ""
    clean = role.strip()
    target_orgs = {"partner_advisory", "apex_global", "apex_global energy", "nea", "google", "bcg", "client / partner"}
    if org and isinstance(org, str) and org.strip():
        target_orgs.add(org.strip().lower())
    for sep in (",", " -", " (", "/"):
        if sep == " (" and clean.endswith(")"):
            base, candidate = clean[:-1].rsplit(sep, 1)
        elif sep in clean:
            parts = clean.rsplit(sep, 1)
            base, candidate = parts[0], parts[1]
        else:
            continue
        if candidate.strip().lower() in target_orgs:
            clean = base.strip()
            break
    return clean


def normalize_company_name(org: str) -> str:
    """Normalize organization name to canonical format (e.g. Apex Global Enterprises (NEA) or PartnerAdvisory)."""
    low = org.strip().lower()
    if "partner_advisory" in low:
        return "PartnerAdvisory"
    if "bcg" in low or "boston consulting" in low:
        return "Boston Consulting Group (BCG)"
    if any(k in low for k in ("unverified", "not established", "unknown")):
        return "Organization unverified"
    return "Apex Global Enterprises (NEA)" if any(k in low for k in ("apex_global", "nea", "nee", "apex", "florida power")) else org.strip()


def split_outside_parens(text: str) -> List[str]:
    """Splits comma-separated string on commas strictly outside parentheses."""
    parts, cur, depth = [], [], 0
    for c in text:
        depth += 1 if c == "(" else (-1 if c == ")" else 0)
        depth = max(0, depth)
        if c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _parse_dash_attendee(clean: str) -> Optional[Tuple[str, str, str]]:
    if not re.search(r"\s*[—–]\s*", clean):
        return None
    parts = re.split(r"\s*[—–]\s*", clean, maxsplit=1)
    name_cand = re.sub(r"[*_#:`]", "", parts[0]).strip()
    rest = parts[1].strip()
    subparts = [p.strip() for p in rest.split(",")]
    org_cand = subparts[-1] if len(subparts) >= 2 else "Client / Partner"
    role_cand = ", ".join(subparts[:-1]) if len(subparts) >= 2 else rest
    return name_cand, role_cand, normalize_company_name(org_cand)


def _extract_effective_attendee_role(ent: Any, role: str, is_org: bool) -> str:
    from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import _is_placeholder

    if role and role not in ("Participant", "") and not is_org and not _is_placeholder(role):
        eff_role = role
    else:
        is_human = bool(
            getattr(ent, "is_human_confirmed", False)
            or getattr(ent, "source", "") == "authoritative_correction"
        )
        if is_human and ent and ent.role:
            eff_role = ent.role
        else:
            eff_role = ""
    eff_role = strip_org_suffix_from_role(eff_role)
    if getattr(ent, "status", "") == "AMBIGUOUS_CONFLICT":
        return str(eff_role).strip()
    return str(eff_role).split(" / ")[0].strip() if " / " in str(eff_role) else str(eff_role)


def _resolve_clean_attendee_entity(clean: str, name: str, role: str, is_org: bool) -> Optional[Tuple[str, str, str]]:
    try:
        from .entity_resolver import resolve_person_entity

        ent = resolve_person_entity(clean) or resolve_person_entity(name)
        if not ent:
            return None
        ent_parts = ent.canonical_name.split()
        if len(ent_parts) >= 2 and len(name.split()) == 1 and ent_parts[-1].lower() not in clean.lower():
            return None
        return ent.canonical_name, _extract_effective_attendee_role(ent, role, is_org), ent.org
    except Exception:
        return None


def _infer_org_fallback(clean: str, role: str) -> str:
    low = f"{clean} {role}".lower()
    if "partner_advisory" in low:
        return "PartnerAdvisory"
    if "bcg" in low or "boston consulting" in low:
        return "Boston Consulting Group (BCG)"
    if role.strip().upper() in ("NEA", "APEX", "PGD", "APEXS") or any(k in low for k in ("apex_global", "nea", "apex", "florida power", "pgd")):
        return "Apex Global Enterprises (NEA)"
    return "Client / Partner"


def clean_attendee_entry(raw_str: str) -> Tuple[str, str, str]:
    """Extracts (name, role, org) from raw attendee string, stripping email addresses."""
    clean = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "", raw_str)
    clean = re.sub(r"\s*-\s*$", "", clean).strip()
    clean = re.sub(r"^(?:Client\s*/\s*Partner|Partner|Client|Contractor|Consultant|Vendor|PartnerAdvisory|Apex Global(?:\s*Energy)?(?:\s*\(NEA\))?|NEA|BCG|Boston\s+Consulting(?:\s+Group)?)\s*[:—–-]\s*", "", clean, flags=re.I).strip()

    dash_parsed = _parse_dash_attendee(clean)
    if dash_parsed:
        return dash_parsed

    trailing_m = re.search(r"\(([^()]+)\)\s*$", clean)
    if trailing_m and clean[:trailing_m.start()].strip():
        cand_name = clean[:trailing_m.start()].strip()
        cand_role = trailing_m.group(1).strip()
        is_nick = (
            cand_role.lower() in ("ed", "alex", "dan", "jake", "mitch", "mandy")
            and not any(k in cand_role.lower() for k in ("lead", "manager", "specialist", "engineer", "director", "vp", "architect", "head", "consultant", "analyst", "admin", "product", "ui", "api", "strategist", "ot", "scada", "owner", "member", "/"))
        )
        if not is_nick:
            name = re.sub(r"[*_#:`]", "", cand_name).strip()
            role = cand_role
        else:
            name = re.sub(r"[*_#:`]", "", clean).strip()
            role = "Participant"
    else:
        name_m = re.match(r"^([^(\[]+)", clean)
        name = re.sub(r"[*_#:`]", "", name_m.group(1) if name_m else clean).strip()
        role = "Participant"

    is_org = (role.strip().upper() in ("NEA", "APEX", "partner_advisory", "CLIENT", "PARTNER", "PGD", "APEXS", "BCG") or role.strip().lower() in COMPANY_OR_GENERIC_TOKENS)

    ent_res = _resolve_clean_attendee_entity(clean, name, role, is_org)
    if ent_res:
        return ent_res

    org = _infer_org_fallback(clean, role)
    if is_org:
        role = ""

    return name, role, org


def _parse_sub_bullet_entry(line_s: str, current_org: str, grouped: Dict[str, List[str]]) -> None:
    if current_org.lower().startswith(("invited", "not present", "absent", "referenced")):
        return
    sub_m = re.match(r"^(?:[-*]|\s+[-*])\s+(.+)$", line_s)
    if not sub_m:
        return
    entry = sub_m.group(1).strip()
    if is_blacklisted_attendee(entry):
        return
    name, role, org_inferred = clean_attendee_entry(entry)
    if is_blacklisted_attendee(name):
        return
    formatted = f"{name} ({role})" if role and role != "Participant" else name
    target_org = current_org if current_org != "Key Stakeholders" else normalize_company_name(org_inferred)
    grouped.setdefault(target_org, [])
    if formatted not in grouped[target_org]:
        grouped[target_org].append(formatted)


def _parse_table_header_line(line_s: str, current_org: str, grouped: Dict[str, List[str]]) -> None:
    m = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line_s)
    if not m:
        return
    name = re.sub(r"\(.*?\)", "", m.group(2) or m.group(1)).strip()
    if not name or is_blacklisted_attendee(name) or name.lower() in ("standardized speaker name", "display name in teams", "---"):
        return
    org = normalize_company_name(m.group(3).strip()) if m.group(3).strip() else current_org
    grouped.setdefault(org, [])
    if name not in grouped[org]:
        grouped[org].append(name)


def _parse_header_line(line: str, current_org: str, grouped: Dict[str, List[str]]) -> str:
    line_s = line.strip()
    if not line_s or line_s.startswith(("#", "---")):
        return current_org
    if any(line_s.lower().startswith(p) for p in ("**invited", "invited / not present", "not present:", "absent:", "**referenced")):
        return "Invited / Not Present"
    if line_s.startswith("|"):
        _parse_table_header_line(line_s, current_org, grouped)
        return current_org

    comp_m = re.match(r"^[*-]\s*\*{0,2}([A-Za-z0-9\s/&—–\(\)]+?)(?:\*{0,2}:\*{0,2}|:\*{0,2})\s*$", line_s)
    if comp_m:
        raw_org = comp_m.group(1).strip()
        if not is_metadata_header_label(raw_org):
            new_org = normalize_company_name(raw_org)
            grouped.setdefault(new_org, [])
            return new_org
        return current_org

    inline_m = re.match(r"^[*-]\s+\*{0,2}([A-Za-z0-9\s/&—–\(\)]+?)(?:\*{0,2}:\*{0,2}|:\*{0,2})\s+(.+)$", line_s)
    if inline_m and any(kw in inline_m.group(1).lower() for kw in ("partner_advisory", "apex_global", "nea", "apex", "client", "partner", "energy", "bcg", "boston")):
        org = normalize_company_name(inline_m.group(1).strip())
        grouped.setdefault(org, [])
        for part in split_outside_parens(inline_m.group(2).strip()):
            p = part.strip()
            if p and not is_blacklisted_attendee(p):
                grouped[org].append(p)
        return org

    _parse_sub_bullet_entry(line_s, current_org, grouped)
    return current_org


def _parse_inline_attendees(text: str) -> Optional[Dict[str, List[str]]]:
    m_inline = re.search(r"(?:^|\n)\s*[*]{0,2}(?:Participants|Attendees)[:*]{1,3}\s*([^\n]+)", text, re.IGNORECASE)
    if not (m_inline and ";" in m_inline.group(1)):
        return None
    grouped: Dict[str, List[str]] = {}
    for entry in m_inline.group(1).split(";"):
        entry_s = entry.strip().rstrip(".")
        if not entry_s:
            continue
        name, role, org = clean_attendee_entry(entry_s)
        if not is_blacklisted_attendee(name):
            formatted = f"{name} ({role})" if role and role != "Participant" else name
            target_org = normalize_company_name(org)
            grouped.setdefault(target_org, []).append(formatted)
    return grouped or None


def extract_attendees_from_header_block(text: str) -> Optional[Dict[str, List[str]]]:
    """Parses structured Attendees block from markdown transcript or document header."""
    if not text:
        return None

    header_parts = re.split(r"\n\s*(?:---|___|\*\*\*)\s*(?:\n|$)", text, maxsplit=1)
    header_text = header_parts[0] if header_parts and header_parts[0].strip() else text

    inline_parsed = _parse_inline_attendees(header_text)
    if inline_parsed:
        return inline_parsed

    m_att = re.search(
        r"(?:^|\n)\s*(?!\|)#{0,6}\s*\*{0,2}(?:Attendees(?:\s*\([^)]*\))?|Attendee\s+roster|Participants(?:\s*\([^)]*\))?)[^\n|]*?\*{0,2}\s*\n([\s\S]*?)(?=\n\s*(?:---|___|\*\*\*|#{1,6}\s+[A-Za-z]|\*{0,2}Date:|\*{0,2}Time:|\*{0,2}Subject:|\*{0,2}Referenced\s+Stakeholders|\*{0,2}Presented\s+Collateral|\*{0,2}Collateral|\*{0,2}\[?\d{1,2}:\d{2})|\Z)",
        header_text,
        re.IGNORECASE,
    )
    if not m_att or not m_att.group(1).strip():
        return None

    grouped: Dict[str, List[str]] = {}
    current_org: str = "Key Stakeholders"
    for line in m_att.group(1).strip().splitlines():
        line_s = line.strip()
        if re.match(r"^#{1,6}\s+", line_s) or line_s.lower().startswith(("###", "##", "#", "**presented", "**collateral")):
            break
        current_org = _parse_header_line(line, current_org, grouped)

    result = {k: v for k, v in grouped.items() if v}
    return result or None


def _is_valid_turn_speaker(cand_clean: str, seen: set) -> Optional[str]:
    if is_blacklisted_attendee(cand_clean) or is_metadata_header_label(cand_clean):
        return None
    name, _, _ = clean_attendee_entry(cand_clean)
    if not name or is_blacklisted_attendee(name) or name in seen:
        return None
    if len(name.split()) > 4 or any(c in name for c in ("http", "www", ".com", "/", "\\")):
        return None
    return name


def extract_speakers_from_dialogue_turns(text: str) -> List[str]:
    """Extracts human speaker names from spoken turn markers (e.g. [00:00:04] Yan Gobeil: or harper Hoffman 02:00 PM)."""
    if not text:
        return []

    patterns = (
        r"(?:^|\n)\s*([A-Za-z][A-Za-z\s\.\,\'\-]{1,50}?)\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?\s*(?:\n|$)",
        r"(?:^|\n)\s*[*]{0,2}(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?[*]{0,2}([A-Za-z][A-Za-z0-9\s\.\,\'\-]{1,50}?)(?::[*]{2}|[*]{2}:|:)\s*",
        r"(?:^|\n)\s*#{2,5}\s+[*]{0,2}([A-Za-z][A-Za-z0-9\s\.\,\'\-]{1,50}?)[*]{0,2}\s*$",
    )
    seen: set = set()
    speakers: List[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.MULTILINE):
            cand = re.sub(r"\s+\d{1,2}$", "", re.sub(r"[*_#:`]", "", m.group(1)).strip()).strip()
            name = _is_valid_turn_speaker(cand, seen)
            if name:
                seen.add(name)
                speakers.append(name)
    return speakers


def _group_from_roster(roster: List[Dict[str, Any]], grouped: Dict[str, List[str]]) -> None:
    for r in roster:
        if isinstance(r, dict) and (name := r.get("name", "").strip()) and not is_blacklisted_attendee(name):
            org = normalize_company_name(r.get("org", r.get("company", "Client / Partner")))
            role = r.get("role", "")
            grouped.setdefault(org, []).append(f"{name} ({role})" if role and role != "Participant" else name)


def _add_dict_attendee(entry: Dict[str, Any], default_org: str, grouped: Dict[str, List[str]]) -> None:
    name = entry.get("name", "").strip()
    if name and not is_blacklisted_attendee(name):
        org = normalize_company_name(entry.get("org", entry.get("company", default_org)))
        role = entry.get("role", "")
        grouped.setdefault(org, []).append(f"{name} ({role})" if role and role != "Participant" else name)


def _add_str_attendee(entry_str: str, default_org: str, grouped: Dict[str, List[str]]) -> None:
    if not entry_str or is_blacklisted_attendee(entry_str):
        return
    name, role, raw_org = clean_attendee_entry(entry_str)
    if not is_blacklisted_attendee(name):
        org = normalize_company_name(raw_org if default_org == "Client / Partner" else default_org)
        grouped.setdefault(org, []).append(f"{name} ({role})" if role and role != "Participant" else name)


def _add_attendee_entry(entry: Any, default_org: str, grouped: Dict[str, List[str]]) -> None:
    if isinstance(entry, dict):
        _add_dict_attendee(entry, default_org, grouped)
    else:
        _add_str_attendee(str(entry).strip(), default_org, grouped)


def _comp_sort_key(item: Tuple[str, List[str]]) -> Tuple[int, str]:
    comp = item[0].lower()
    return (0, item[0]) if any(k in comp for k in ("apex_global", "nea", "client")) else ((1, item[0]) if "partner_advisory" in comp else (2, item[0]))


def _group_from_dict(att_dict: Dict[str, Any], grouped: Dict[str, List[str]]) -> None:
    for org_key, members in att_dict.items():
        if not is_metadata_header_label(org_key):
            for m in (members if isinstance(members, list) else [members]):
                _add_attendee_entry(m, org_key, grouped)


def _group_from_list(att_list: Any, grouped: Dict[str, List[str]]) -> None:
    for item in att_list:
        _add_attendee_entry(item, "Client / Partner", grouped)


def format_company_grouped_attendees(
    attendees: Any = None,
    roster: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Formats attendees into clean company-grouped markdown lines."""
    grouped: Dict[str, List[str]] = {}

    if roster:
        _group_from_roster(roster, grouped)
    elif isinstance(attendees, dict):
        _group_from_dict(attendees, grouped)
    elif isinstance(attendees, (list, tuple, str)):
        _group_from_list(split_outside_parens(attendees) if isinstance(attendees, str) else attendees, grouped)

    if not grouped:
        return []

    lines = []
    for org, members in sorted(grouped.items(), key=_comp_sort_key):
        deduped = list(dict.fromkeys(members))
        if deduped:
            lines.append(f"- {org}: {', '.join(deduped)}")
    return lines


def _resolve_entry_org(explicit_org: str, c_org: str, default_org: str) -> str:
    generic = {"client / partner", "client", "partner", "contractor", "consultant", "vendor", ""}
    for o in (explicit_org, c_org, default_org):
        if o and o.strip().lower() not in generic:
            return o.strip()
    return c_org or default_org or "Client / Partner"


def _roster_item_from_entry(entry: Any, default_org: str) -> Optional[Dict[str, Any]]:
    if isinstance(entry, dict):
        raw_name = entry.get("name", "").strip()
        if not raw_name or is_blacklisted_attendee(raw_name):
            return None
        c_name, c_role, c_org = clean_attendee_entry(raw_name)
        role = entry.get("role") if entry.get("role") and entry.get("role") != "Participant" else c_role
        return {"name": c_name, "org": _resolve_entry_org(entry.get("org", ""), c_org, default_org), "role": role, "spoke": entry.get("spoke", True)}
    entry_str = str(entry).strip()
    if not entry_str or is_blacklisted_attendee(entry_str):
        return None
    name, role, raw_org = clean_attendee_entry(entry_str)
    spoke = not bool(re.search(r"\b(?:did\s+not\s+speak|non-?speaking|silent|listener\s+only)\b", entry_str, re.I))
    return None if is_blacklisted_attendee(name) else {"name": name, "org": _resolve_entry_org("", raw_org, default_org), "role": role, "spoke": spoke}


def _collect_raw_roster_items(att_obj: Any) -> List[Tuple[Any, str]]:
    if isinstance(att_obj, dict):
        return [(m, org) for org, members in att_obj.items() if not is_metadata_header_label(org) for m in (members if isinstance(members, list) else [members])]
    if isinstance(att_obj, (list, tuple)):
        return [(item, "Client / Partner") for item in att_obj]
    if isinstance(att_obj, str):
        return [(item.strip(), "Client / Partner") for item in split_outside_parens(att_obj) if item.strip()]
    return []


def build_roster_from_attendees(att_obj: Any) -> List[Dict[str, Any]]:
    """Build standardized roster list from attendees dict or list, filtering blacklisted tokens."""
    if not att_obj:
        return []
    roster: List[Dict[str, Any]] = []
    seen = set()
    for entry, default_org in _collect_raw_roster_items(att_obj):
        item = _roster_item_from_entry(entry, default_org)
        if item and item["name"] not in seen:
            seen.add(item["name"])
            roster.append(item)
    return roster


def _is_attendees_block_malformed(lines: List[str]) -> bool:
    if any(re.search(r"^-\s*\*\*(?:Participant|PartnerAdvisory|Apex Global|NEA|Client|Partner)\*\*:", line, re.IGNORECASE) for line in lines):
        return True
    bad_tokens = {"date", "attendees", "participant", "subject", "transcript"}
    for line in lines:
        if any(tok.lower() in bad_tokens for tok in re.findall(r"\b[A-Za-z]+\b", line)):
            return True
    return not all(re.match(r"^-\s+[A-Za-z0-9\s\(\)]+:\s+", line) for line in lines)


def _insert_attendees_block(text: str, block: str) -> str:
    for pat in (r"((?:^|\n)\*{0,2}Time:\*{0,2}.*?\n)", r"((?:^|\n)\*{0,2}Date:\*{0,2}.*?\n)"):
        m = re.search(pat, text)
        if m:
            pos = m.end()
            return text[:pos] + block + text[pos:]
    return block + text


def _extract_roster_from_body_lines(body_lines: List[str]) -> List[Dict[str, Any]]:
    extracted = []
    for line in body_lines:
        clean = re.sub(r"^-\s*(?:\*\*[^*]+\*\*:\s*)?", "", line).strip()
        for item in split_outside_parens(clean):
            name, role, org = clean_attendee_entry(item.strip())
            if name and not is_blacklisted_attendee(name) and not is_metadata_header_label(name):
                extracted.append({"name": name, "role": role, "org": org})
    return extracted


def _resolve_heal_lines(
    body_lines: List[str],
    roster: Optional[List[Dict[str, Any]]],
    attendees: Any,
) -> List[str]:
    new_lines = format_company_grouped_attendees(attendees=attendees, roster=roster)
    if not new_lines and body_lines:
        extracted = _extract_roster_from_body_lines(body_lines)
        new_lines = format_company_grouped_attendees(attendees=None, roster=extracted)
    return new_lines or ["- Apex Global Enterprises (NEA): Key Leadership", "- PartnerAdvisory: Delivery Team"]


def heal_attendees_block(
    text: str,
    roster: Optional[List[Dict[str, Any]]] = None,
    attendees: Any = None,
) -> Tuple[str, bool]:
    """Heals malformed or missing Attendees block in Output 02 Executive Recap."""
    if not text:
        return text, False

    m_att = re.search(r"(?:^|\n)(\*{0,2}Attendees:\*{0,2})\s*([\s\S]*?)(?=\n\s*(?:---|___|\*\*\*|##|#|\Z))", text)
    if not m_att:
        new_lines = format_company_grouped_attendees(attendees=attendees, roster=roster)
        if not new_lines:
            return text, False
        return _insert_attendees_block(text, "Attendees:\n" + "\n".join(new_lines) + "\n\n"), True

    body_lines = [line.strip() for line in m_att.group(2).strip().splitlines() if line.strip()]
    if not _is_attendees_block_malformed(body_lines):
        return text, False

    new_lines = _resolve_heal_lines(body_lines, roster, attendees)
    new_block = "Attendees:\n" + "\n".join(new_lines) + "\n"
    start, end = m_att.span()
    prefix = "\n" if text[start] == "\n" else ""
    return text[:start] + prefix + new_block + text[end:], True
