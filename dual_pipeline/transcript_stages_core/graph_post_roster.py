"""
dual_pipeline/transcript_stages_core/graph_post_roster.py

Attendee and organization extraction, typing, and GraphDB SSOT binding helpers
for post-extraction knowledge graph normalization.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_KNOWN_ORG_TOKENS = frozenset({
    "partner_advisory", "apex_global", "nea", "apex", "florida power and light", "florida power & light",
    "google", "gcp", "aws", "microsoft", "azure", "servicenow", "seagen", "mcdonalds", "mcdonald's"
})
_NEE_PROJECT_KEYS = frozenset({
    "", "apex_global", "nea", "apex", "sherpa", "project_beacon", "lens", "project_lens",
    "grid_modernization", "crow", "ce_portal", "network_model",
})


def _matches_entity_org(low: str, e: Dict[str, Any]) -> bool:
    if not (str(e.get("id", "")).startswith("org:") or str(e.get("type", "")).lower() in ("org", "organization")):
        return False
    ename = str(e.get("name", "")).strip().lower()
    eslug = str(e.get("id", ""))[4:].replace("_", " ").replace("-", " ").strip().lower()
    return low in (ename, eslug)


def is_org_token(candidate: Optional[str], entities: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
    """Checks if a string token corresponds to a known organization."""
    if not candidate or not candidate.strip():
        return False
    low = candidate.strip().lower()
    if low in _KNOWN_ORG_TOKENS or any(tok in low for tok in ("partner_advisory", "apex_global", "apex")):
        return True
    if any(isinstance(e, dict) and _matches_entity_org(low, e) for e in entities):
        return True
    res_map = metadata.get("entity_resolutions")
    if isinstance(res_map, dict):
        return any(str(k).strip().lower() == low and str(v).strip().lower().startswith("org:") for k, v in res_map.items())
    return False


def _extract_roster_items(metadata: Dict[str, Any]) -> List[Any]:
    roster = metadata.get("attendees") or metadata.get("roster") or []
    if isinstance(roster, dict):
        items: List[Any] = []
        for org_k, att_list in roster.items():
            if isinstance(att_list, list):
                items.extend((a, org_k) for a in att_list)
            elif isinstance(att_list, (str, dict)):
                items.append((att_list, org_k))
        return items
    if isinstance(roster, str):
        return [(p.strip(), None) for p in re.split(r"[,;\n]+(?![^(]*\))", roster) if p.strip()]
    if isinstance(roster, list):
        return [(p, None) for p in roster]
    return []


def _is_roster_evidence_entry(v: Dict[str, Any], p_tokens: List[str]) -> bool:
    loc_val = str((v.get("locator") or {}).get("value") or "").lower()
    is_roster = loc_val in ("participants", "roster") or str(v.get("attribution_mode") or "").upper() == "ROSTER"
    txt = (str(v.get("text") or "") + " " + str(v.get("exact_text") or "")).lower()
    return is_roster and all(t in txt for t in p_tokens)


def _resolve_roster_evidence_id(
    clean_name: str,
    target_slug: str,
    org_hint: Optional[str],
    default_ev_id: Optional[str],
    evidence_index: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not evidence_index:
        return None
    p_tokens = clean_name.lower().split()
    roster_ev_match = next(
        (k for k, v in evidence_index.items() if isinstance(v, dict) and _is_roster_evidence_entry(v, p_tokens)),
        None,
    )
    if roster_ev_match:
        return roster_ev_match
    base_slug = target_slug.replace("person:", "")
    candidates = [
        f"ev_roster_role_{base_slug.replace('-', '_')}",
        f"ev_roster_role_{base_slug.replace('_', '-')}",
        f"ev-roster-{target_slug.replace(':', '-')}",
    ]
    return next((c for c in candidates if c and c in evidence_index), default_ev_id)


def _has_any_role_for_person(signals: List[Dict[str, Any]], target_slug: str) -> bool:
    norm_target = target_slug.lower().replace("_", "-")
    return any(
        isinstance(s, dict)
        and str(s.get("from_id") or "").lower().replace("_", "-") == norm_target
        and str(s.get("relationship") or s.get("predicate") or "").upper() == "HAS_ROLE"
        for s in signals
    )


def _resolve_active_project_key(metadata: Dict[str, Any]) -> str:
    project = str(metadata.get("project") or metadata.get("workstream") or "").strip().lower()
    if project:
        return project
    title_low = str(metadata.get("meeting_name") or metadata.get("title") or "").lower()
    return "project_beacon" if "sherpa" in title_low else ("project_lens" if "lens" in title_low else "")


def _is_non_nee_or_internal_scope(metadata: Dict[str, Any], entities: List[Dict[str, Any]]) -> bool:
    project = _resolve_active_project_key(metadata)
    title_low = str(metadata.get("meeting_name") or metadata.get("title") or "").lower()
    if project.startswith("internal") or "partner_advisory" in project or ("partner_advisory" in title_low and "internal" in title_low):
        return True
    if project and project not in _NEE_PROJECT_KEYS:
        has_nee = any(
            isinstance(e, dict) and any(k in str(e.get("id", "")).lower() for k in ("apex_global", "nea", "apex"))
            for e in entities
        )
        return not has_nee
    return False


def _is_exclusive_other_project_member(name_low: str, project: str) -> bool:
    from dual_pipeline.transcript_metadata_core.entity_resolver import DEFAULT_PROJECT_ROSTERS
    active_roster = DEFAULT_PROJECT_ROSTERS.get(project)
    if not active_roster or name_low in {n.strip().lower() for grp in active_roster.values() for n in grp}:
        return False
    aliases = {"sherpa", "project_beacon"} if project in ("sherpa", "project_beacon") else ({"lens", "project_lens"} if project in ("lens", "project_lens") else {project})
    return name_low in {
        n.strip().lower()
        for p_key, p_roster in DEFAULT_PROJECT_ROSTERS.items()
        if p_key not in aliases
        for grp in p_roster.values()
        for n in grp
    }


def _clean_speaker_lower(raw_val: Any) -> str:
    return re.sub(r"\s*\([^)]*\)", "", str(raw_val or "")).strip().lower()


def _is_spoken_evidence_item(ev_id: Any, ev: Any) -> bool:
    if not isinstance(ev, dict) or str(ev_id).startswith(("ev_roster_role_", "ev-roster-")):
        return False
    loc = ev.get("locator")
    return not (isinstance(loc, dict) and str(loc.get("kind") or "").lower() == "section")


def _has_evidence_activity(name_low: str, evidence_index: Optional[Dict[str, Any]]) -> bool:
    for ev_id, ev in (evidence_index or {}).items():
        if _is_spoken_evidence_item(ev_id, ev):
            q_txt = str(ev.get("quote") or ev.get("direct_quote") or ev.get("text") or "").lower()
            if _clean_speaker_lower(ev.get("speaker")) == name_low or name_low in q_txt:
                return True
    return False


def _has_spoken_or_mentioned_activity(
    name_low: str, metadata: Dict[str, Any], evidence_index: Optional[Dict[str, Any]]
) -> bool:
    turn_counts = metadata.get("speaker_turn_counts")
    if isinstance(turn_counts, dict) and any(_clean_speaker_lower(k) == name_low and isinstance(v, int) and v > 0 for k, v in turn_counts.items()):
        return True
    for t in metadata.get("turns") or []:
        if isinstance(t, dict) and (_clean_speaker_lower(t.get("speaker")) == name_low or name_low in str(t.get("text") or t.get("utterance") or "").lower()):
            return True
    return _has_evidence_activity(name_low, evidence_index)


def is_cross_project_silent_attendee(
    clean_name: str, metadata: Dict[str, Any], evidence_index: Optional[Dict[str, Any]] = None
) -> bool:
    """Return True if attendee belongs exclusively to another workstream roster and has 0 spoken turns/mentions."""
    if not clean_name or not isinstance(metadata, dict):
        return False
    project = _resolve_active_project_key(metadata)
    name_low = clean_name.strip().lower()
    if not project or not _is_exclusive_other_project_member(name_low, project):
        return False
    return not _has_spoken_or_mentioned_activity(name_low, metadata, evidence_index)


def _upsert_conference_endpoint(
    clean_name: str, entities: List[Dict[str, Any]], existing_ids: set[str], default_ev_id: Optional[str]
) -> bool:
    if not any(t in clean_name.lower() for t in ("mole-vc", "mole_vc", "nea-mole", "vc", "endpoint", "conference room")):
        return False
    ep_slug = "system:" + re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
    bad_hy = "person:" + re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
    bad_us = "person:" + re.sub(r"[^a-z0-9]+", "_", clean_name.lower()).strip("_")
    entities[:] = [e for e in entities if not (isinstance(e, dict) and str(e.get("id", "")).lower() in (bad_hy, bad_us))]
    existing_ids.discard(bad_hy)
    existing_ids.discard(bad_us)
    if ep_slug not in existing_ids:
        entities.append({
            "id": ep_slug,
            "type": "system",
            "name": clean_name,
            "description": f"Conference room video conferencing endpoint ({clean_name}).",
            "mutation": "CONFIRM",
            "evidence_ids": [default_ev_id] if default_ev_id else [],
        })
        existing_ids.add(ep_slug)
    return True


def _resolve_target_person_slug(
    clean_name: str, existing_ids: set[str], existing_person_by_name: Dict[str, str]
) -> str:
    slug_us = "person:" + re.sub(r"[^a-z0-9]+", "_", clean_name.lower()).strip("_")
    slug_hy = "person:" + re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
    name_key = clean_name.strip().lower()
    if name_key in existing_person_by_name:
        return existing_person_by_name[name_key]
    if slug_us in existing_ids:
        return slug_us
    if slug_hy in existing_ids:
        return slug_hy
    last_token = name_key.split()[-1] if len(name_key.split()) >= 2 else ""
    matched_by_last = next(
        (eid for eid in existing_ids if eid.startswith("person:") and last_token and eid.endswith((f"-{last_token}", f"_{last_token}"))),
        None,
    )
    return matched_by_last or slug_us


def _classify_org_keyword(raw_text: Optional[str]) -> Optional[str]:
    if not raw_text:
        return None
    low = str(raw_text).lower()
    if "partner_advisory" in low:
        return "partner_advisory"
    if "apex_global" in low or "nea" in low or "apex" in low:
        return "apex_global"
    if "google" in low:
        return "google"
    return None


def _lookup_ssot_person(clean_name: str, non_nee_scope: bool) -> Tuple[Optional[str], Optional[str]]:
    if non_nee_scope:
        return None, None
    try:
        from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity
        res = resolve_person_entity(clean_name)
        if res and res.status in ("RESOLVED", "AMBIGUOUS_CONFLICT"):
            return (res.org or None), (res.role or None)
    except Exception:
        pass
    return None, None


def _resolve_org_entity_id(org_hint: Optional[str], entities: List[Dict[str, Any]]) -> Optional[str]:
    if not org_hint:
        return None
    defaults = {"apex_global": "org:apex_global-energy", "partner_advisory": "org:partner_advisory", "google": "org:google"}
    tokens = ("apex_global", "nea") if org_hint == "apex_global" else (org_hint,)
    existing_org = next(
        (
            str(e.get("id"))
            for e in entities
            if isinstance(e, dict)
            and str(e.get("id", "")).startswith("org:")
            and any(t in str(e.get("id", "")).lower() for t in tokens)
        ),
        None,
    )
    return existing_org or defaults.get(org_hint)


def _extract_inline_role(
    att_name: str, resolved_role: Optional[str], entities: List[Dict[str, Any]], metadata: Dict[str, Any]
) -> Optional[str]:
    m_role = re.search(r"\(([^,)]+)(?:,\s*([^)]+))?\)", att_name)
    cand_p1 = m_role.group(1).strip() if m_role and m_role.group(1) else None
    cand_p2 = m_role.group(2).strip() if m_role and m_role.group(2) else None
    if cand_p1 and is_org_token(cand_p1, entities, metadata):
        role = cand_p2 if (cand_p2 and not is_org_token(cand_p2, entities, metadata)) else None
    else:
        role = cand_p1
    return role or resolved_role


def _upsert_org_and_member_signal(
    target_slug: str,
    org: str,
    ev_id: Optional[str],
    entities: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    existing_ids: set[str],
) -> None:
    if org not in existing_ids:
        entities.append({
            "id": org,
            "type": "org",
            "name": org.replace("org:", "").replace("_", " ").title(),
            "description": f"Organization entity for {org}.",
            "mutation": "CONFIRM",
            "evidence_ids": [ev_id] if ev_id else [],
        })
        existing_ids.add(org)

    sig_id = f"rel-{target_slug.replace(':', '-')}--{org.replace(':', '-')}--MEMBER_OF"
    has_member_sig = any(
        isinstance(s, dict) and (s.get("signal_id") == sig_id or (s.get("from_id") == target_slug and s.get("relationship") == "MEMBER_OF"))
        for s in signals
    )
    if not has_member_sig:
        signals.append({
            "from_id": target_slug,
            "to_id": org,
            "relationship": "MEMBER_OF",
            "signal_id": sig_id,
            "source": "roster",
            "evidence_ids": [ev_id] if ev_id else [],
        })


def _matches_role_signal(s: Any, role_sig_id: str, target_slug: str, role_slug: str) -> bool:
    if not isinstance(s, dict):
        return False
    if s.get("signal_id") == role_sig_id:
        return True
    rel = s.get("relationship") or s.get("predicate")
    return s.get("from_id") == target_slug and s.get("to_id") == role_slug and rel == "HAS_ROLE"


def _resolve_role_slug(r_part: str, existing_ids: set[str]) -> str:
    r_slug_us = "role:" + re.sub(r"[^a-z0-9]+", "_", r_part.lower()).strip("_")
    r_slug_hy = "role:" + re.sub(r"[^a-z0-9]+", "-", r_part.lower()).strip("-")
    if r_slug_hy in existing_ids:
        return r_slug_hy
    return r_slug_us if r_slug_us in existing_ids else r_slug_hy


def _upsert_person_role_signals(
    target_slug: str,
    role_parts: List[str],
    ev_id: Optional[str],
    entities: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    existing_ids: set[str],
) -> None:
    if _has_any_role_for_person(signals, target_slug):
        return
    ev_list = [ev_id] if ev_id else []
    for r_part in role_parts:
        role_slug = _resolve_role_slug(r_part, existing_ids)
        if role_slug not in existing_ids:
            entities.append({
                "id": role_slug, "type": "role", "name": r_part,
                "description": f"Professional role: {r_part}.", "mutation": "CONFIRM",
                "evidence_ids": ev_list,
            })
            existing_ids.add(role_slug)
        role_sig_id = f"fact-rel-3--{target_slug.replace(':', '-')}--{role_slug.replace(':', '-')}--HAS_ROLE"
        if not any(_matches_role_signal(s, role_sig_id, target_slug, role_slug) for s in signals):
            signals.append({
                "signal_id": role_sig_id, "from_id": target_slug, "to_id": role_slug,
                "relationship": "HAS_ROLE", "predicate": "HAS_ROLE", "evidence_ids": ev_list,
            })


def _format_person_description(
    role: Optional[str], org: Optional[str], resolved_org: Optional[str], non_nee_scope: bool, active_project: str
) -> str:
    role_desc = role or "Project Stakeholder"
    if org:
        org_title = org.replace("org:", "").replace("_", " ").replace("-", " ").title()
        return f"{role_desc} at {org_title}."
    if resolved_org:
        return f"{role_desc} at {resolved_org}."
    if non_nee_scope:
        return f"{role_desc} at PartnerAdvisory." if "partner_advisory" in active_project else f"{role_desc}."
    return f"{role_desc} at Apex Global Enterprises."


def _resolve_attendee_org_hint(
    att_name: str, org_dict_hint: Any, resolved_org: Optional[str], non_nee_scope: bool, active_project: str
) -> Optional[str]:
    hint = _classify_org_keyword(att_name) or _classify_org_keyword(org_dict_hint) or _classify_org_keyword(resolved_org)
    if hint:
        return hint
    return "partner_advisory" if (non_nee_scope and "partner_advisory" in active_project) else None


def _process_single_roster_attendee(
    att: Any,
    org_dict_hint: Any,
    metadata: Dict[str, Any],
    evidence_index: Optional[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    existing_ids: set[str],
    existing_person_by_name: Dict[str, str],
    default_ev_id: Optional[str],
    non_nee_scope: bool,
    active_project: str,
) -> None:
    att_name = att.get("name") if isinstance(att, dict) else str(att)
    clean_name = re.sub(r"\s*\([^)]*\)", "", att_name).strip()
    if not clean_name or is_cross_project_silent_attendee(clean_name, metadata, evidence_index):
        return
    if _upsert_conference_endpoint(clean_name, entities, existing_ids, default_ev_id):
        return
    target_slug = _resolve_target_person_slug(clean_name, existing_ids, existing_person_by_name)
    resolved_org, resolved_role = _lookup_ssot_person(clean_name, non_nee_scope)
    org_hint = _resolve_attendee_org_hint(att_name, org_dict_hint, resolved_org, non_nee_scope, active_project)
    org = _resolve_org_entity_id(org_hint, entities)
    role = _extract_inline_role(att_name, resolved_role, entities, metadata)
    role_parts = [p.strip() for p in re.split(r"\s*/\s*", role) if p.strip()] if role else []
    ev_id = _resolve_roster_evidence_id(clean_name, target_slug, org_hint, default_ev_id, evidence_index)
    if target_slug not in existing_ids:
        entities.append({
            "id": target_slug, "type": "person", "name": clean_name,
            "description": _format_person_description(role, org, resolved_org, non_nee_scope, active_project),
            "mutation": "CONFIRM", "evidence_ids": [ev_id] if ev_id else [],
        })
        existing_ids.add(target_slug)
    if org:
        _upsert_org_and_member_signal(target_slug, org, ev_id, entities, signals, existing_ids)
    _upsert_person_role_signals(target_slug, role_parts, ev_id, entities, signals, existing_ids)


def ensure_roster_attendees(
    mutations: Dict[str, Any],
    metadata: Dict[str, Any],
    evidence_index: Optional[Dict[str, Any]] = None,
) -> None:
    """Materializes person and organization nodes from roster metadata with GraphDB SSOT binding."""
    roster_items = _extract_roster_items(metadata)
    if not roster_items:
        return
    entities = mutations.setdefault("entity_upserts", [])
    signals = mutations.setdefault("relationship_signals", [])
    existing_ids = {str(e.get("id", "")).lower() for e in entities if isinstance(e, dict)}
    existing_person_by_name = {
        str(e.get("name", "")).strip().lower(): str(e.get("id", "")).lower()
        for e in entities
        if isinstance(e, dict) and str(e.get("id", "")).lower().startswith("person:") and e.get("name")
    }
    default_ev_id = list(evidence_index.keys())[0] if evidence_index else None
    non_nee_scope = _is_non_nee_or_internal_scope(metadata, entities)
    active_project = _resolve_active_project_key(metadata)
    for att, org_dict_hint in roster_items:
        _process_single_roster_attendee(
            att, org_dict_hint, metadata, evidence_index, entities, signals,
            existing_ids, existing_person_by_name, default_ev_id, non_nee_scope, active_project,
        )
