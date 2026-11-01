"""Deterministic reconciliation and validation of speaker role resolutions for Stage 03."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.core import digest
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    compute_resolution_digest,
)
from dual_pipeline.normalization_core.meeting_entity_helpers import (
    ALLOWED_RELATIONSHIP_PREDICATES,
)

logger = logging.getLogger(__name__)

_GENERIC_NAMES = frozenset({
    "speaker", "attendee", "participant", "someone", "unknown", "person",
    "unidentified", "user", "operator", "host", "session context", "unresolved", "undiarized_quarantined",
})


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned or "unknown"


def _get_res_str(res: Any, field: str, default: str = "") -> str:
    val = getattr(res, field, None)
    if val is None and isinstance(res, dict):
        val = res.get(field)
    return str(val) if val is not None else default


def _get_res_val(res: Any, field: str, default: Any = None) -> Any:
    val = getattr(res, field, None)
    if val is None and isinstance(res, dict):
        val = res.get(field, default)
    return val if val is not None else default


def _validate_identity_and_role(clean_name: str, clean_role: str) -> None:
    if not clean_name or clean_name.lower() in _GENERIC_NAMES:
        raise ValueError(f"Ambiguous identity rejected: '{clean_name}' is not an authoritative person identity.")
    if not clean_role:
        raise ValueError(f"Empty or missing role for identity '{clean_name}'.")


def _validate_predicate(res: Any) -> None:
    pred = _get_res_str(res, "predicate", "HAS_ROLE")
    if pred and pred.upper() not in ALLOWED_RELATIONSHIP_PREDICATES:
        raise ValueError(f"Unsupported predicate '{pred}' rejected for speaker role resolution.")


def _validate_source_run(source_input: Any, metadata: Dict[str, Any]) -> None:
    if not isinstance(source_input, dict):
        return
    res_run_id = source_input.get("run_id")
    meta_run_id = metadata.get("run_id")
    if res_run_id and meta_run_id and str(res_run_id) != str(meta_run_id):
        raise ValueError(
            f"Foreign-run evidence rejected: resolution run_id '{res_run_id}' does not match metadata run_id '{meta_run_id}'."
        )


def _validate_digest(
    res_digest: str,
    clean_name: str,
    clean_role: str,
    org: Optional[str],
    prov: Any,
    is_human: bool,
    source_input: Any,
) -> None:
    if not res_digest:
        return
    prov_str = prov.value if isinstance(prov, ResolutionProvenance) else str(prov)
    src_dict = source_input if isinstance(source_input, dict) else {}
    expected_digest = compute_resolution_digest(
        person_name=clean_name,
        resolved_role=clean_role,
        organization=org,
        provenance=prov_str,
        is_human_confirmed=bool(is_human),
        source_input=src_dict,
    )
    if res_digest != expected_digest:
        raise ValueError(
            f"Stale or corrupted answer digest for '{clean_name}': expected {expected_digest[:16]}..., got {res_digest[:16]}..."
        )


def _validate_conflicting_facts(
    clean_name: str,
    clean_role: str,
    seen_roles: Optional[Dict[str, str]],
) -> None:
    if seen_roles is None:
        return
    norm_name = clean_name.lower()
    if norm_name in seen_roles and seen_roles[norm_name].lower() != clean_role.lower():
        raise ValueError(
            f"Conflicting facts detected for '{clean_name}': '{seen_roles[norm_name]}' vs '{clean_role}'."
        )
    seen_roles[norm_name] = clean_role


def validate_speaker_role_resolution_for_projection(
    res: Any,
    metadata: Dict[str, Any],
    seen_roles: Optional[Dict[str, str]] = None,
) -> None:
    """Validates that a captured speaker role resolution satisfies projection invariants."""
    clean_name = _get_res_str(res, "person_name").strip()
    clean_role = _get_res_str(res, "resolved_role").strip()
    org = _get_res_val(res, "organization") or (_lookup_roster_org(metadata, clean_name) if metadata else None)
    prov = _get_res_val(res, "provenance", "")
    is_human = bool(_get_res_val(res, "is_human_confirmed", False))
    res_digest = _get_res_str(res, "digest")
    source_input = _get_res_val(res, "source_input", {})

    _validate_identity_and_role(clean_name, clean_role)
    _validate_predicate(res)
    _validate_source_run(source_input, metadata)
    if isinstance(res, dict) and res.get("_unauthenticated_human_claim"):
        raise ValueError(
            f"Unauthenticated HUMAN_OPERATOR role resolution for '{clean_name}': missing cryptographic digest and resolution_id."
        )
    prov_str = prov.value if isinstance(prov, ResolutionProvenance) else str(prov)
    if (prov_str == "HUMAN_OPERATOR" or is_human) and not res_digest and not _get_res_str(res, "resolution_id"):
        raise ValueError(
            f"Unauthenticated HUMAN_OPERATOR role resolution for '{clean_name}': missing cryptographic digest and resolution_id."
        )
    _validate_digest(res_digest, clean_name, clean_role, org, prov, is_human, source_input)
    _validate_conflicting_facts(clean_name, clean_role, seen_roles)


def _norm_id(val: Any) -> str:
    return str(val or "").strip().lower().replace("-", "_")


def _update_matched_person_entity(matched: Dict[str, Any], person_id: str, name: str, role: str, is_human: bool, prov_str: str, res_id: str, ev_id: str) -> str:
    resolved_pid = str(matched.get("id") or person_id)
    atomic_r = _split_compound_roles(role)
    for k in ("role", "professional_title"):
        matched.pop(k, None) if len(atomic_r) > 1 else matched.__setitem__(k, role)
    matched.update({"is_human_confirmed": bool(is_human), "provenance": prov_str, "resolution_id": res_id})
    ev_list = matched.setdefault("evidence_ids", [])
    if ev_id not in ev_list:
        ev_list.append(ev_id)
    desc = str(matched.get("description") or "")
    if not desc or any(t in desc for t in ("Participant", "Attendee", " / ", "/")):
        matched["description"] = f"{name} is {atomic_r[0] if atomic_r else role}."
    return resolved_pid


def _upsert_person_entity(entity_upserts: List[Dict[str, Any]], person_id: str, name: str, role: str, is_human: bool, prov_str: str, res_id: str, ev_id: str) -> str:
    target_id, target_name = _norm_id(person_id), name.lower()
    matched = next((e for e in entity_upserts if _norm_id(e.get("id")) == target_id or str(e.get("name") or "").lower() == target_name), None)
    if matched is not None:
        return _update_matched_person_entity(matched, person_id, name, role, is_human, prov_str, res_id, ev_id)
    atomic_r = _split_compound_roles(role)
    ent = {
        "id": person_id, "name": name, "type": "person",
        "description": f"{name} is {atomic_r[0] if atomic_r else role}.", "is_human_confirmed": bool(is_human),
        "provenance": prov_str, "resolution_id": res_id, "evidence_ids": [ev_id],
    }
    if len(atomic_r) <= 1:
        ent["role"], ent["professional_title"] = role, role
    entity_upserts.append(ent)
    return person_id


def _sync_signal_evidence(sig: Dict[str, Any], ev_id: str, ev_entry: Dict[str, Any]) -> None:
    sig["evidence_ids"] = [ev_id]
    if isinstance(sig.get("evidence"), dict):
        sig["evidence"] = {
            "statement_id": ev_entry.get("statement_id", ev_id),
            "direct_quote": ev_entry.get("exact_text") or ev_entry.get("text", ""),
            "locator": ev_entry.get("locator", {}),
            "turn_offset": 0,
        }


def _has_matching_existing_member_sig(relationship_signals: List[Dict[str, Any]], target_person_id: str, target_org_id: str, raw_org: str) -> bool:
    existing_member_sig = next((r for r in relationship_signals if _norm_id(r.get("from_id")) == target_person_id and str(r.get("relationship") or r.get("predicate") or "").upper() == "MEMBER_OF"), None)
    if existing_member_sig is None:
        return False
    existing_to_id = _norm_id(existing_member_sig.get("to_id") or "")
    org_tokens = [tok for tok in re.findall(r"[a-z0-9]+", raw_org.lower()) if len(tok) >= 3]
    return bool(existing_to_id and (existing_to_id == target_org_id or any(tok in existing_to_id for tok in org_tokens)))


def _upsert_org_relationship(entity_upserts: List[Dict[str, Any]], relationship_signals: List[Dict[str, Any]], org: Optional[str], person_id: str, p_slug: str, ev_id: str, ev_entry: Dict[str, Any], is_human: bool, prov_str: str) -> None:
    if not org or not str(org).strip():
        return
    raw_org = str(org).strip()
    if raw_org.lower() in ("organization unverified", "unverified", "unknown", "none", "n/a", "internal", "client / partner"):
        return
    clean_org = re.sub(r"\s*\([^)]*\)", "", raw_org).strip() or raw_org
    org_id = f"org:{_slugify(clean_org)}"
    target_org_id, target_person_id = _norm_id(org_id), _norm_id(person_id)
    if _has_matching_existing_member_sig(relationship_signals, target_person_id, target_org_id, raw_org):
        return
    matched_org = next((e for e in entity_upserts if _norm_id(e.get("id")) == target_org_id), None)
    if matched_org is not None:
        org_id = str(matched_org.get("id") or org_id)
        ev_list = matched_org.setdefault("evidence_ids", [])
        if ev_id not in ev_list:
            ev_list.append(ev_id)
    else:
        entity_upserts.append({"id": org_id, "name": clean_org, "type": "org", "description": f"Organization: {clean_org}", "evidence_ids": [ev_id]})
    matched_sig = next((r for r in relationship_signals if _norm_id(r.get("from_id")) == target_person_id and _norm_id(r.get("to_id")) == target_org_id and str(r.get("relationship") or r.get("predicate") or "").upper() == "MEMBER_OF"), None)
    if matched_sig is not None:
        matched_sig.update({"is_human_confirmed": bool(is_human), "provenance": prov_str})
        _sync_signal_evidence(matched_sig, ev_id, ev_entry)
    else:
        relationship_signals.append({"signal_id": f"sig_member_{p_slug}", "from_id": person_id, "to_id": org_id, "relationship": "MEMBER_OF", "evidence_ids": [ev_id], "is_human_confirmed": bool(is_human), "provenance": prov_str})


def _split_compound_roles(role_text: str) -> List[str]:
    if not role_text or role_text.strip().lower() in ("client / partner", "n/a"):
        return [role_text.strip()] if role_text and role_text.strip() else []
    atomic = [p.strip() for p in re.split(r"\s*/\s*", role_text.strip()) if p.strip() and len(p.strip()) >= 3]
    return atomic if len(atomic) > 1 else [role_text.strip()]


def _prune_stale_roles_and_orphans(entity_upserts: List[Dict[str, Any]], relationship_signals: List[Dict[str, Any]], target_person_id: str, target_role_ids: set) -> None:
    stale_role_ids, to_keep = set(), []
    for r in relationship_signals:
        is_role_rel = str(r.get("relationship") or r.get("predicate") or "").upper() in ("HAS_ROLE", "HAS_ROLE_IN")
        if _norm_id(r.get("from_id")) == target_person_id and is_role_rel and _norm_id(r.get("to_id")) not in target_role_ids:
            stale_role_ids.add(_norm_id(r.get("to_id")))
        else:
            to_keep.append(r)
    relationship_signals[:] = to_keep
    if not stale_role_ids:
        return
    active_role_targets = {_norm_id(r.get("to_id")) for r in relationship_signals if str(r.get("relationship") or r.get("predicate") or "").upper() in ("HAS_ROLE", "HAS_ROLE_IN")}
    orphans = stale_role_ids - active_role_targets
    if orphans:
        entity_upserts[:] = [e for e in entity_upserts if not (str(e.get("type") or "").lower() == "role" and _norm_id(e.get("id")) in orphans)]


def _upsert_single_atomic_role(entity_upserts: List[Dict[str, Any]], relationship_signals: List[Dict[str, Any]], sub_role: str, person_id: str, target_person_id: str, p_slug: str, ev_id: str, ev_entry: Dict[str, Any], is_human: bool, prov_str: str, multi_role: bool) -> str:
    role_slug = _slugify(sub_role)
    role_id = f"role:{role_slug}"
    target_role_id = _norm_id(role_id)
    matched_role = next((e for e in entity_upserts if _norm_id(e.get("id")) == target_role_id), None)
    if matched_role is not None:
        role_id = str(matched_role.get("id") or role_id)
        ev_list = matched_role.setdefault("evidence_ids", [])
        if ev_id not in ev_list:
            ev_list.append(ev_id)
    else:
        entity_upserts.append({"id": role_id, "name": sub_role, "type": "role", "description": f"Professional role: {sub_role}", "evidence_ids": [ev_id]})
    matched_sig = next((r for r in relationship_signals if _norm_id(r.get("from_id")) == target_person_id and _norm_id(r.get("to_id")) == target_role_id and str(r.get("relationship") or r.get("predicate") or "").upper() in ("HAS_ROLE", "HAS_ROLE_IN")), None)
    if matched_sig is not None:
        matched_sig.update({"is_human_confirmed": bool(is_human), "provenance": prov_str})
        _sync_signal_evidence(matched_sig, ev_id, ev_entry)
    else:
        sig_suffix = f"_{role_slug}" if multi_role else ""
        relationship_signals.append({"signal_id": f"sig_role_{p_slug}{sig_suffix}", "from_id": person_id, "to_id": role_id, "relationship": "HAS_ROLE", "evidence_ids": [ev_id], "is_human_confirmed": bool(is_human), "provenance": prov_str})
    return target_role_id


def _upsert_role_relationship(entity_upserts: List[Dict[str, Any]], relationship_signals: List[Dict[str, Any]], role: str, person_id: str, p_slug: str, ev_id: str, ev_entry: Dict[str, Any], is_human: bool, prov_str: str) -> None:
    atomic_roles = _split_compound_roles(role)
    target_person_id = _norm_id(person_id)
    target_role_ids = {_upsert_single_atomic_role(entity_upserts, relationship_signals, sub_role, person_id, target_person_id, p_slug, ev_id, ev_entry, is_human, prov_str, len(atomic_roles) > 1) for sub_role in atomic_roles}
    _prune_stale_roles_and_orphans(entity_upserts, relationship_signals, target_person_id, target_role_ids)


def _build_resolution_evidence_entry(
    name: str, role: str, org: Any, is_human: bool, res_id: str, ledger_id: str, p_slug: str
) -> tuple[str, str, Dict[str, Any]]:
    ev_prefix = "hitl" if is_human else "roster"
    ev_id, stmt_id = f"ev_{ev_prefix}_role_{p_slug}", f"stmt_{ev_prefix}_role_{p_slug}"
    clean_org = str(org).strip() if org and str(org).strip() else ""
    valid_org = clean_org if clean_org.lower() not in ("", "organization unverified", "unverified", "unknown", "none", "n/a", "internal", "client / partner") else ""
    actor_lead = "Human operator verified" if is_human else "Session roster recorded"
    ev_text = f"{actor_lead} professional title '{role}' and organization '{valid_org}' for {name}." if valid_org else f"{actor_lead} professional title '{role}' for {name}."
    src_type = "TRANSCRIPT_HITL" if is_human else "ROSTER_PRIOR"
    ev_entry = {
        "statement_id": stmt_id, "evidence_id": ev_id, "kind": "summary", "text": ev_text, "exact_text": ev_text,
        "grounding_status": "GROUNDED", "speaker": "Operator" if is_human else "Session Roster",
        "speaker_role": "Human Operator" if is_human else "Metadata Roster", "speaker_org": valid_org or "Unverified",
        "source": src_type, "source_type": src_type, "resolution_id": res_id, "ledger_entry_id": ledger_id,
        "locator": {"kind": "hitl_decision_ledger" if is_human else "session_roster", "value": res_id or ledger_id or ev_prefix},
    }
    return ev_id, valid_org, ev_entry


def _reconcile_single_resolution(
    res: Any,
    entity_upserts: List[Dict[str, Any]],
    relationship_signals: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
) -> None:
    name, role, org = _get_res_str(res, "person_name"), _get_res_str(res, "resolved_role"), _get_res_val(res, "organization")
    is_human = bool(_get_res_val(res, "is_human_confirmed", False))
    prov = _get_res_val(res, "provenance", "HUMAN_OPERATOR" if is_human else "ROSTER_PRIOR")
    prov_str = prov.value if isinstance(prov, ResolutionProvenance) else str(prov)
    res_id, ledger_id, p_slug = _get_res_str(res, "resolution_id"), _get_res_str(res, "ledger_entry_id"), _slugify(name)
    ev_id, valid_org, ev_entry = _build_resolution_evidence_entry(name, role, org, is_human, res_id, ledger_id, p_slug)
    evidence_index[ev_id] = ev_entry

    resolved_pid = _upsert_person_entity(entity_upserts, f"person:{p_slug}", name, role, is_human, prov_str, res_id, ev_id)
    _upsert_org_relationship(entity_upserts, relationship_signals, valid_org or None, resolved_pid, p_slug, ev_id, ev_entry, is_human, prov_str)
    _upsert_role_relationship(entity_upserts, relationship_signals, role, resolved_pid, p_slug, ev_id, ev_entry, is_human, prov_str)


def _find_item_by_id(items: List[Any], item_id: str) -> Optional[Any]:
    for it in items:
        cur_id = getattr(it, "item_id", None)
        if cur_id is None and isinstance(it, dict):
            cur_id = it.get("item_id")
        if cur_id == item_id:
            return it
    return None


def _touch_existing_manifest_item(existing: Any) -> None:
    from dual_pipeline.canonical_pipeline.evidence_coverage import EvidenceState
    if hasattr(existing, "transition_to"):
        existing.transition_to(EvidenceState.PROPOSED, "Grounded in Output 03 canonical projection")
    elif isinstance(existing, dict):
        existing["state"] = EvidenceState.PROPOSED.value
        existing["disposition_reason"] = "Grounded in Output 03 canonical projection"


def _create_role_manifest_item(
    item_id: str, person_id: str, name: str, role: str, as_dict: bool, is_human: bool = False
) -> Any:
    from dual_pipeline.canonical_pipeline.evidence_coverage import EvidenceItem, EvidenceState, ItemType
    ev_item = EvidenceItem(
        item_id=item_id,
        item_type=ItemType.SPEAKER_ROLE.value,
        is_eligible_graph_fact=True,
        state=EvidenceState.PROPOSED.value,
        source_origin={
            "source_type": "TRANSCRIPT_HITL" if is_human else "ROSTER_PRIOR",
            "source_hash": digest({"name": name, "role": role}),
            "clarification_kind": "SPEAKER_ROLE",
        },
        disposition_reason="Grounded in Output 03 canonical projection",
        proposed_fact={
            "subject": person_id,
            "predicate": "HAS_ROLE",
            "canonical_predicate": "HAS_ROLE",
            "statement_kind": "ROLE_ASSIGNMENT",
            "object": role,
        },
    )
    return ev_item.to_dict() if as_dict else ev_item


def _update_evidence_manifest_items(manifest_obj: Any, resolutions: Dict[str, Any]) -> None:
    items = getattr(manifest_obj, "items", None)
    if items is None and isinstance(manifest_obj, dict):
        items = manifest_obj.setdefault("items", [])
    if not isinstance(items, list):
        return

    is_dict_manifest = isinstance(manifest_obj, dict)
    for name, res in resolutions.items():
        role = _get_res_str(res, "resolved_role")
        is_human = bool(_get_res_val(res, "is_human_confirmed", False))
        p_slug = _slugify(name)
        person_id = f"person:{p_slug}"
        item_id = f"cov-{'hitl' if is_human else 'roster'}-role-{p_slug}"

        existing = _find_item_by_id(items, item_id) or _find_item_by_id(items, f"cov-hitl-role-{p_slug}")
        if existing is not None:
            _touch_existing_manifest_item(existing)
        else:
            items.append(_create_role_manifest_item(item_id, person_id, name, role, is_dict_manifest, is_human=is_human))


def _lookup_roster_org(metadata: Dict[str, Any], name: str) -> Optional[str]:
    hitl_orgs = metadata.get("hitl_org_resolutions")
    if isinstance(hitl_orgs, dict) and name in hitl_orgs:
        val = str(hitl_orgs[name]).strip()
        if val.lower() in ("organization unverified", "unverified", "unknown", "none", "n/a"):
            return None
        return val
    roster = metadata.get("roster") or metadata.get("structured_attendees") or []
    if not isinstance(roster, list):
        return None
    for r in roster:
        if isinstance(r, dict) and r.get("name") == name:
            return r.get("org") or r.get("organization")
    return None


def _is_operator_confirmed_role(metadata: Optional[Dict[str, Any]], name: Optional[str] = None) -> bool:
    if not metadata:
        return False
    receipts = metadata.get("hitl_role_receipts")
    if isinstance(receipts, dict) and name and name in receipts and receipts[name]:
        return True
    if name and name in (metadata.get("hitl_role_resolutions") or {}) and metadata.get("attendee_roles_confirmed_by_operator"):
        return True
    receipt = metadata.get("attendee_confirmation_receipt")
    return bool(
        isinstance(receipt, dict)
        and receipt.get("status") == "RESOLVED_HITL"
        and receipt.get("subject_sha256")
        and not str(receipt.get("subject_sha256")).startswith("urn:nee:")
    )


def _normalize_string_resolution(name: str, role_str: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    full_res = (metadata.get("speaker_role_resolutions") or {}).get(name)
    if isinstance(full_res, dict):
        updated = dict(full_res)
        if role_str and str(role_str).strip():
            updated["resolved_role"] = role_str.strip()
            updated["role"] = role_str.strip()
        return _normalize_dict_resolution(name, updated, metadata)
    if full_res is not None:
        return full_res
    org = _lookup_roster_org(metadata, name)
    is_operator_confirmed = _is_operator_confirmed_role(metadata, name)
    prov = "HUMAN_OPERATOR" if is_operator_confirmed else "ROSTER_PRIOR"
    is_human = is_operator_confirmed
    receipts = metadata.get("hitl_role_receipts") if isinstance(metadata.get("hitl_role_receipts"), dict) else {}
    res_id = str(receipts.get(name) or "") if is_operator_confirmed else ""
    return {
        "person_name": name,
        "resolved_role": role_str,
        "organization": org,
        "provenance": prov,
        "is_human_confirmed": is_human,
        "resolution_id": res_id,
        "digest": compute_resolution_digest(
            person_name=name,
            resolved_role=role_str,
            organization=org,
            provenance=prov,
            is_human_confirmed=is_human,
            source_input={},
        ),
        "source_input": {},
    }


def _has_explicit_role_auth(name: str, res: Dict[str, Any], metadata: Optional[Dict[str, Any]], receipts: Dict[str, Any]) -> bool:
    if res.get("digest") or res.get("resolution_id") or res.get("provenance") == "HITL_LEDGER":
        return True
    if not metadata:
        return False
    return bool(metadata.get("attendee_roles_confirmed_by_operator") or receipts.get(name))


def _resolve_dict_provenance_and_auth(
    name: str, res: Dict[str, Any], norm: Dict[str, Any], metadata: Optional[Dict[str, Any]]
) -> tuple[str, bool]:
    explicit_prov = norm.get("provenance")
    non_human_provs = ("ROSTER_PRIOR", "ENTERPRISE_DIRECTORY", "knowledge_graph_assertion_conflict", "knowledge_graph")
    if explicit_prov in non_human_provs:
        prov = str(explicit_prov)
        is_human = bool(norm.get("is_human_confirmed", False))
    else:
        is_op = _is_operator_confirmed_role(metadata, name)
        prov = str(explicit_prov or ("HUMAN_OPERATOR" if is_op else "ROSTER_PRIOR"))
        is_human = bool(norm.get("is_human_confirmed", is_op))
    raw_rec = metadata.get("hitl_role_receipts") if metadata else None
    receipts = raw_rec if isinstance(raw_rec, dict) else {}
    if (prov == "HUMAN_OPERATOR" or is_human) and not _has_explicit_role_auth(name, res, metadata, receipts):
        norm["_unauthenticated_human_claim"] = True
    if not norm.get("resolution_id"):
        norm["resolution_id"] = str(receipts.get(name) or "")
    return prov, is_human


def _normalize_dict_resolution(name: str, res: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    norm = dict(res)
    norm.setdefault("person_name", name)
    hitl_roles = (metadata.get("hitl_role_resolutions") or {}) if metadata else {}
    raw_hitl = hitl_roles.get(name)
    hitl_role_str = raw_hitl if isinstance(raw_hitl, str) else None
    resolved_role = str(hitl_role_str or norm.get("resolved_role") or norm.get("role") or "")
    norm["resolved_role"] = resolved_role
    prov, is_human = _resolve_dict_provenance_and_auth(name, res, norm, metadata)
    norm["provenance"] = prov
    norm["is_human_confirmed"] = is_human
    org = norm.get("organization") or (_lookup_roster_org(metadata, name) if metadata else None)
    if org and str(org).strip().lower() not in ("organization unverified", "unverified", "unknown", "none", "n/a"):
        norm.setdefault("organization", org)
    else:
        norm["organization"] = None
        org = None
    norm["digest"] = compute_resolution_digest(
        person_name=norm["person_name"],
        resolved_role=resolved_role,
        organization=org,
        provenance=prov,
        is_human_confirmed=is_human,
        source_input=norm.get("source_input", {}),
    )
    return norm


def _coerce_speaker_resolution(name: str, res: Any, metadata: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(res, str):
        return _normalize_string_resolution(name, res, metadata)
    if isinstance(res, dict):
        return _normalize_dict_resolution(name, res, metadata)
    return res


def _apply_entity_role_update(ent: Dict[str, Any], res: Any) -> None:
    r_str = _get_res_str(res, "resolved_role") or _get_res_str(res, "role")
    if not r_str:
        return
    if len(_split_compound_roles(r_str)) > 1:
        ent.pop("role", None)
        ent.pop("professional_title", None)
    else:
        ent["role"] = r_str
        ent["professional_title"] = r_str
    ent["is_human_confirmed"] = bool(_get_res_val(res, "is_human_confirmed", False))
    ent["provenance"] = _get_res_str(res, "provenance", "ROSTER_PRIOR")


def _sync_entities_from_resolutions(
    entities: List[Dict[str, Any]], normalized_resolutions: Dict[str, Any]
) -> None:
    for ent in entities:
        e_name = ent.get("name")
        if e_name and e_name in normalized_resolutions:
            _apply_entity_role_update(ent, normalized_resolutions[e_name])


def _match_lineage_speaker_role(spk: str, normalized_resolutions: Dict[str, Any]) -> Optional[str]:
    for r_name, res in normalized_resolutions.items():
        r_role = _get_res_str(res, "resolved_role") or _get_res_str(res, "role")
        r_low = r_name.lower()
        p_slug = "person:" + re.sub(r"[^a-z0-9]+", "_", r_low).strip("_")
        p_slug_hy = "person:" + re.sub(r"[^a-z0-9]+", "-", r_low).strip("-")
        if r_role and (spk in (r_low, p_slug, p_slug_hy) or r_low in spk):
            return r_role
    return None


def _sync_lineage_speaker_roles(
    mutations: Dict[str, Any], normalized_resolutions: Dict[str, Any]
) -> None:
    for bucket in mutations.values():
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            lin = item.get("lineage") if isinstance(item, dict) else None
            if isinstance(lin, dict):
                matched_role = _match_lineage_speaker_role(str(lin.get("speaker", "")).lower(), normalized_resolutions)
                if matched_role:
                    lin["speaker_role"] = matched_role


def _should_skip_speaker_resolution(
    clean_name: str, clean_role: str, metadata: Dict[str, Any], evidence_index: Dict[str, Any]
) -> bool:
    unverified_set = {str(u).strip().lower() for u in (metadata.get("unverified_speakers") or [])}
    if not clean_name or clean_name.lower() in _GENERIC_NAMES or clean_name.lower() in unverified_set or not clean_role or metadata.get("diarization_status") == "UNDIARIZED_QUARANTINED" or bool(re.fullmatch(r"(?:speaker|voice)[_\s]+\w+", clean_name, re.IGNORECASE)):
        return True
    if any(t in clean_name.lower() for t in ("mole-vc", "mole_vc", "nea-mole", "conference room")) or "endpoint" in clean_role.lower():
        return True
    from dual_pipeline.transcript_stages_core.graph_post_roster import is_cross_project_silent_attendee
    return is_cross_project_silent_attendee(clean_name, metadata, evidence_index)


def _collect_roster_org_resolutions(
    metadata: Dict[str, Any], resolutions: Dict[str, Any]
) -> None:
    roster = metadata.get("roster")
    if not isinstance(roster, list) or not resolutions:
        return
    for item in roster:
        if not isinstance(item, dict):
            continue
        r_name = str(item.get("name") or "").strip()
        r_role = str(item.get("role") or "").strip()
        r_org = str(item.get("org") or item.get("organization") or "").strip()
        if r_name and r_role and r_org and r_name not in resolutions:
            resolutions[r_name] = {"person_name": r_name, "role": r_role, "organization": r_org}


def _reconcile_single_resolution_if_eligible(
    name: str,
    res: Any,
    metadata: Dict[str, Any],
    seen_roles: Dict[str, str],
    normalized_resolutions: Dict[str, Any],
    entity_upserts: List[Dict[str, Any]],
    relationship_signals: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
) -> None:
    res = _coerce_speaker_resolution(name, res, metadata)
    clean_name = _get_res_str(res, "person_name").strip()
    clean_role = _get_res_str(res, "resolved_role").strip()
    if _should_skip_speaker_resolution(clean_name, clean_role, metadata, evidence_index):
        return
    normalized_resolutions[name] = res
    validate_speaker_role_resolution_for_projection(res, metadata, seen_roles=seen_roles)
    _reconcile_single_resolution(res, entity_upserts, relationship_signals, evidence_index)


def reconcile_speaker_role_resolutions(
    final_data: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Reconciles captured speaker role resolutions into Stage 03 graph projection."""
    if metadata is None:
        return final_data

    resolutions: Dict[str, Any] = {}
    for k in ("auto_role_resolutions", "speaker_role_resolutions", "hitl_role_resolutions"):
        if metadata.get(k):
            resolutions.update(metadata[k])
    _collect_roster_org_resolutions(metadata, resolutions)
    if not resolutions:
        return final_data

    mutations = final_data.setdefault("graph_mutations", {})
    entity_upserts = mutations.setdefault("entity_upserts", [])
    relationship_signals = mutations.setdefault("relationship_signals", [])
    evidence_index = final_data.setdefault("evidence_index", {})

    seen_roles: Dict[str, str] = {}
    normalized_resolutions: Dict[str, Any] = {}
    for name, res in resolutions.items():
        if not name or not str(name).strip():
            continue
        _reconcile_single_resolution_if_eligible(
            name, res, metadata, seen_roles, normalized_resolutions,
            entity_upserts, relationship_signals, evidence_index,
        )

    if isinstance(final_data.get("entities"), list):
        _sync_entities_from_resolutions(final_data["entities"], normalized_resolutions)

    ev_manifest = metadata.get("evidence_manifest") or metadata.get("evidence_coverage_manifest")
    if ev_manifest is not None:
        _update_evidence_manifest_items(ev_manifest, normalized_resolutions)

    _sync_lineage_speaker_roles(mutations, normalized_resolutions)
    return final_data
