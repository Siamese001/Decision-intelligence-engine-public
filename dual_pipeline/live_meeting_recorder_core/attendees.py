"""
dual_pipeline/live_meeting_recorder_core/attendees.py

Normalizes attendee rosters into structured records with organization and role attribution.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

KNOWN_ROLES: Dict[str, str] = {
    "traci": "Delivery Lead / PM",
    "justin": "Principal Consultant / Solution Architect",
    "reid": "Director of Product & Quantitative Strategy",
    "miles": "Software Engineer / Technical Contributor",
    "erickson": "Software Engineer / Technical Contributor",
    "jake": "UI and Prompting Lead",
    "jen": "Product Manager / Lead Engineer",
    "alex": "Tech Lead / Senior Engineer",
    "karyn": "LENS Team Contributor / API Integrations",
    "nozim": "PartnerAdvisory Team Contributor",
}

try:
    from dual_pipeline.transcript_metadata_core.entity_resolver import (
        _CANONICAL_ENTERPRISE_DIRECTORY,
    )
except ImportError:
    _CANONICAL_ENTERPRISE_DIRECTORY = {}


def _lookup_canonical_directory(clean_name: str) -> Optional[Tuple[str, str, str]]:
    low = clean_name.lower().strip()
    if low in _CANONICAL_ENTERPRISE_DIRECTORY:
        return _CANONICAL_ENTERPRISE_DIRECTORY[low]
    words = low.split()
    if len(words) >= 2:
        return None
    for k, (c_name, org, role) in _CANONICAL_ENTERPRISE_DIRECTORY.items():
        if k == low or (len(low) >= 4 and (low in k or k.startswith(low))):
            return (c_name, org, role)
    first_token = words[0] if words else ""
    if len(first_token) >= 3:
        for k, (c_name, org, role) in _CANONICAL_ENTERPRISE_DIRECTORY.items():
            if k.split()[0] == first_token:
                return (c_name, org, role)
    return None


def _clean_attendee_input(raw: str) -> str:
    clean = re.sub(r"<[^>]*>", "", raw)
    clean = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "", clean)
    clean = re.sub(r"[*_#:`<>]", "", clean).strip()
    if "," in clean:
        parts = [p.strip() for p in clean.split(",") if p.strip()]
        if len(parts) == 2 and all(" " not in p for p in parts):
            clean = f"{parts[1]} {parts[0]}"
    return clean


def _infer_org_and_name(line: str, project_name: Optional[str] = None) -> Tuple[str, str]:
    org = "Unknown"
    clean_name = _clean_attendee_input(line)
    m_org = re.search(r"\(([^)]+)\)", clean_name)
    if m_org:
        org = m_org.group(1).strip()
        clean_name = re.sub(r"\([^)]+\)", "", clean_name).strip()

    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
        ent = resolve_person_entity(clean_name, project_name=project_name)
        if ent and ent.org and ent.org != "Unknown":
            return ent.canonical_name or clean_name, ent.org
    except Exception:
        pass

    canon = _lookup_canonical_directory(clean_name)
    if canon and org == "Unknown":
        _, canon_org, _ = canon
        org = canon_org

    lower_name = clean_name.lower()
    if org == "Unknown":
        if "partner_advisory" in line.lower() or any(n in lower_name for n in ("amit", "traci", "justin", "miles", "erickson", "jake", "jen", "alex", "karyn", "nozim")):
            org = "PartnerAdvisory"
        elif "nea" in line.lower() or "apex" in line.lower() or any(n in lower_name for n in ("reid", "mandy", "anthony")):
            org = "NEA"
    return clean_name, org


def _infer_role(clean_name: str, project_name: Optional[str] = None) -> str:
    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
        ent = resolve_person_entity(clean_name, project_name=project_name)
        if ent and ent.role and ent.role.lower() not in ("unknown", "participant", "placeholder"):
            if " / " in str(ent.role):
                if getattr(ent, "candidates", None) and len(ent.candidates) > 0:
                    return ent.candidates[0].role
                return str(ent.role).split(" / ")[0].strip()
            return ent.role
    except Exception:
        pass

    canon = _lookup_canonical_directory(clean_name)
    if canon and canon[2] and canon[2].lower() not in ("unknown", "participant", "placeholder"):
        return canon[2]

    lower_name = clean_name.lower()
    tokens = set(re.findall(r"\b[a-z0-9]+\b", lower_name))
    for k, r in KNOWN_ROLES.items():
        if k in tokens or k == lower_name:
            return r
    return "Participant"


def _parse_single_line(line: str, project_name: Optional[str] = None) -> Dict[str, str]:
    clean_name, org = _infer_org_and_name(line, project_name=project_name)
    role = _infer_role(clean_name, project_name=project_name)
    return {"name": clean_name, "role": role, "org": org}


def _parse_list_items(items: List[Any], project_name: Optional[str] = None) -> List[Dict[str, str]]:
    parsed: List[Dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name", "Unknown")
            raw_org = item.get("org", "Unknown")
            _, inferred_org = _infer_org_and_name(name, project_name=project_name)
            org = inferred_org if inferred_org != "Unknown" else raw_org
            role = item.get("role") if item.get("role") and item.get("role") != "Participant" else _infer_role(name, project_name=project_name)
            parsed.append({
                "name": name,
                "role": role,
                "org": org,
            })
        elif isinstance(item, str):
            parsed.extend(parse_attendees(item, project_name=project_name))
    return parsed


def _default_attendees() -> List[Dict[str, str]]:
    name, org = _infer_org_and_name("Amit Ayer")
    role = _infer_role("Amit Ayer")
    return [{"name": name, "role": role if role != "Participant" else "Lead Client Partner", "org": org if org != "Unknown" else "PartnerAdvisory"}]


def _resolve_graph_attendees(
    raw: Union[str, List[Any]], project_name: Optional[str] = None
) -> Optional[List[Dict[str, str]]]:
    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_attendee_list

        resolved = resolve_attendee_list(raw, project_name=project_name)
        if not resolved:
            return None
        res: List[Dict[str, str]] = []
        seen_names: set[str] = set()
        for ent in resolved:
            c_name = ent.canonical_name or ent.display_name
            if c_name and c_name not in seen_names:
                seen_names.add(c_name)
                res.append({
                    "name": c_name,
                    "display_name": ent.display_name or c_name,
                    "role": ent.role or "Participant",
                    "org": ent.org or "Unknown",
                })
        return res if res else None
    except Exception:
        return None


def _is_empty_or_whitespace_input(raw: Union[str, List[Any], None]) -> bool:
    if not raw:
        return True
    return not re.sub(r"[\s,;]+", "", str(raw))


def _split_candidate_lines(text: str) -> List[str]:
    raw_candidates = [p.strip() for p in text.split(";") if p.strip()] if ";" in text else [
        p.strip() for p in re.split(r"[\n,]+", text) if p.strip()
    ]
    expanded: List[str] = []
    for c in raw_candidates:
        if "," in c and "<" in c and not re.search(r"^\w+,\s*\w+\s*<", c):
            expanded.extend([s.strip() for s in c.split(",") if s.strip()])
        else:
            expanded.append(c)
    return expanded


def _deduplicate_parsed_candidates(lines: List[str], project_name: Optional[str] = None) -> List[Dict[str, str]]:
    seen: set[str] = set()
    result: List[Dict[str, str]] = []
    for line in lines:
        parsed = _parse_single_line(line, project_name=project_name)
        if parsed["name"] not in seen:
            seen.add(parsed["name"])
            result.append(parsed)
    return result


def _resolve_empty_project_attendees(project_name: Optional[str]) -> Optional[List[Dict[str, str]]]:
    if not project_name:
        return None
    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver import _RESOLVER
        roster_ents = _RESOLVER.get_project_roster_entities(project_name)
        if roster_ents:
            return [
                {
                    "name": e.canonical_name,
                    "display_name": e.display_name or e.canonical_name,
                    "role": e.role or "Participant",
                    "org": e.org or "Unknown",
                }
                for e in roster_ents
            ]
    except Exception:
        pass
    return None


def parse_attendees(
    raw: Union[str, List[Any], None], project_name: Optional[str] = None
) -> List[Dict[str, str]]:
    """Normalizes free-form text, comma-separated strings, or JSON lists into structured attendee dicts."""
    default_attendee = _default_attendees()
    if _is_empty_or_whitespace_input(raw):
        return _resolve_empty_project_attendees(project_name) or default_attendee

    text_rep = json.dumps(raw) if isinstance(raw, list) else str(raw)
    if project_name or any(k in text_rep.lower() for k in ("team", "roster")):
        graph_res = _resolve_graph_attendees(raw, project_name=project_name)
        if graph_res:
            return graph_res

    if isinstance(raw, list):
        return _parse_list_items(raw, project_name=project_name) or default_attendee

    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            return parse_attendees(json.loads(text), project_name=project_name)
        except Exception:
            pass

    expanded = _split_candidate_lines(text)
    return _deduplicate_parsed_candidates(expanded, project_name=project_name) or default_attendee


def _matches_attendee(clean_lower: str, att_lower: str) -> bool:
    if clean_lower == att_lower:
        return True
    from .copilot_roster_bridge import are_same_speaker_identity
    if are_same_speaker_identity(clean_lower, att_lower):
        return True
    parts = att_lower.split()
    if len(parts) >= 2:
        if clean_lower == parts[0]:
            return True
        from .copilot_turn_signals import COMMON_NAME_ALIASES
        if parts[0] in COMMON_NAME_ALIASES.get(clean_lower, set()):
            return True
        c_parts = clean_lower.split()
        if len(c_parts) == 2 and (
            c_parts[0] == parts[0] or parts[0] in COMMON_NAME_ALIASES.get(c_parts[0], set())
        ) and parts[1].startswith(c_parts[1]):
            return True
    return False


def resolve_roster_name(candidate_name: str, attendees: Optional[List[Any]] = None) -> str:
    """Expands partial or first name against roster attendees, or returns candidate_name."""
    clean = candidate_name.strip()
    clean_lower = clean.lower().rstrip(".")
    if not attendees:
        return clean
    for a in attendees:
        att_name = (a.get("name") if isinstance(a, dict) else str(a)).strip()
        if _matches_attendee(clean_lower, att_name.lower()):
            return att_name
    return clean

