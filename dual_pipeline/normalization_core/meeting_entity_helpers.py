"""Entity intent profile and mapping helpers for meeting entity normalization."""
from __future__ import annotations

import re
from typing import Any

from ..core import digest, normalize_space, stable_id
from .constants import SEMANTIC_CORRECTION_PACKAGE_KEY

PREFIX_OR_TYPE_TO_NODE_TYPE: dict[str, str] = {
    "stakeholder": "stakeholder", "person": "stakeholder", "internal_person": "internal_person",
    "org": "org", "organization": "org", "team": "org", "client": "org", "vendor": "org",
    "system": "technical_anchor", "technical_anchor": "technical_anchor", "model": "technical_anchor",
    "tool": "technical_anchor", "platform": "technical_anchor", "role": "role_group", "role_group": "role_group",
    "policy": "control", "control": "control", "control_protocol": "control_protocol",
    "project": "program", "program": "program", "initiative": "program", "workstream": "workstream",
    "forum": "workstream", "risk": "risk", "deliverable": "deliverable", "metric": "deliverable",
    "document": "deliverable", "milestone": "deliverable", "open_question": "open_question",
    "strategy": "strategy", "decision": "strategy", "proposal": "strategy", "opportunity": "opportunity",
    "capability": "capability", "pattern": "pattern", "operating_model": "operating_model",
    "pricing_model": "pricing_model", "sow": "sow", "staffing_pool": "staffing_pool",
    "staffing_footprint": "staffing_footprint", "audience": "audience", "glossary_term": "glossary_term",
}


def _extract_single_doc_entity(eu: Any) -> tuple[str, str]:
    if not isinstance(eu, dict):
        return "", ""
    ename = str(eu.get("canonical_name") or eu.get("name") or "").strip()
    eid = str(eu.get("entity_id") or eu.get("id") or "").strip()
    return (ename, eid) if len(ename) >= 3 else ("", "")


def extract_declared_doc_entities(projection_ext: dict[str, Any]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    declared_doc_entities: list[tuple[str, str]] = []
    declared_entity_by_name: dict[str, str] = {}
    for eu in projection_ext.get("entity_upserts") or []:
        ename, eid = _extract_single_doc_entity(eu)
        if ename:
            declared_doc_entities.append((ename, eid))
            if eid:
                declared_entity_by_name[ename.lower()] = eid
                declared_entity_by_name.setdefault(ename.lower().split()[0], eid)
    return declared_doc_entities, declared_entity_by_name

ALLOWED_RELATIONSHIP_PREDICATES: set[str] = {
    "ACCELERATES", "AFFILIATED_WITH", "ASSIGNED_TO", "CANDIDATE_FOR", "CONSTRAINS", "CONTAINS", "CONTEXT_FOR",
    "CONTRIBUTES_TO", "DECIDED_AGAINST", "DELIVERS", "DELIVERS_VALUE_TO", "DELIVERS_VIA", "DEPENDS_ON",
    "GOVERNS", "HAS_CLAIM_TEXT", "HAS_METRIC", "HAS_RISK", "HAS_ROLE", "HAS_ROLE_IN", "INTEGRATES_WITH",
    "LEADS", "MANAGES", "MEMBER_OF", "OWNS", "OWNS_OR_LEADS", "PART_OF", "REJECTED", "RELATED_TO",
    "REPORTS_TO", "ROUTES_TO", "SPONSORS", "STANDARDIZES_ON", "SUPPORTED_BY", "TRANSITIONS_TO",
    "USES_PATTERN", "USES_SYSTEM", "VALIDATES",
}


def map_entity_type(raw_type_or_prefix: str | None) -> str:
    """Map a raw domain prefix or entity type to an active creatable registry type."""
    if not raw_type_or_prefix:
        return "stakeholder"
    cleaned = raw_type_or_prefix.strip().lower()
    if cleaned in PREFIX_OR_TYPE_TO_NODE_TYPE:
        return PREFIX_OR_TYPE_TO_NODE_TYPE[cleaned]
    if ":" in cleaned:
        prefix = cleaned.split(":", 1)[0]
        if prefix in PREFIX_OR_TYPE_TO_NODE_TYPE:
            return PREFIX_OR_TYPE_TO_NODE_TYPE[prefix]
        if prefix in ("dept", "department", "team", "division", "client", "vendor", "agency"):
            return "org"
        if prefix in ("sys", "system", "app", "tool", "infra", "service", "db"):
            return "technical_anchor"
        if prefix in ("person", "speaker", "lead", "owner"):
            return "stakeholder"
    if any(k in cleaned for k in ("dept", "department", "company", "vendor", "client")):
        return "org"
    if any(k in cleaned for k in ("person", "speaker", "lead", "manager")):
        return "stakeholder"
    if any(k in cleaned for k in ("sys", "tool", "infra", "app", "db", "database", "service")):
        return "technical_anchor"
    return "pattern"


def create_entity_intent_profile(
    *,
    stable_identifier: str,
    node_type: str,
    entity_intent: str = "resolve_or_create",
    declaration_status: str = "DECLARED",
) -> dict[str, Any]:
    """Build a contract-valid entity intent profile for an entity mention."""
    sid_str = str(stable_identifier or "")
    return {
        "stable_identifier": stable_identifier,
        "entity_intent": entity_intent,
        "node_type": node_type,
        "declaration_status": declaration_status,
        "cross_source_dedup": bool(":" in sid_str and not sid_str.startswith("entity-")),
        "source_profile_digest": digest(
            {"stable_identifier": stable_identifier, "node_type": node_type, "entity_intent": entity_intent}
        ),
    }


def _resolve_entity_evidence_ids(
    entity: dict[str, Any],
    default_evidence_id: str,
    evidence_by_id: dict[str, str],
    evidence_by_identifier: dict[str, str],
) -> list[str]:
    """Resolve evidence IDs for an entity dictionary."""
    rel_ev_ids: list[str] = []
    for eid in entity.get("evidence_ids", []):
        if eid in evidence_by_id:
            rel_ev_ids.append(eid)
        elif eid in evidence_by_identifier:
            rel_ev_ids.append(evidence_by_identifier[eid])
    if not rel_ev_ids and default_evidence_id:
        return [default_evidence_id]
    return rel_ev_ids


def _resolve_rel_evidence_ids(
    rel: dict[str, Any],
    evidence_by_id: dict[str, str],
    evidence_by_identifier: dict[str, str],
    first_ev_id: str | None,
) -> list[str]:
    """Resolve evidence IDs for a relationship signal."""
    rel_ev_ids: list[str] = [
        eid for eid in rel.get("evidence_ids", [])
        if eid in evidence_by_id or eid in evidence_by_identifier
    ]
    mapped = [evidence_by_identifier.get(eid, eid) for eid in rel_ev_ids]
    if not mapped and first_ev_id:
        return [first_ev_id]
    return mapped


def resolve_profile_index_entry(
    profile_index: dict[str, Any], entity_id: str
) -> tuple[str, str, dict[str, Any]] | None:
    if entity_id in profile_index:
        return profile_index[entity_id]
    slug = entity_id.split(":", 1)[-1].strip().lower().replace("-", "_")
    for k, entry in profile_index.items():
        if k.split(":", 1)[-1].strip().lower().replace("-", "_") == slug or (
            len(entry) >= 3 and str(entry[1] or "").strip().lower().replace(" ", "_") == slug
        ):
            return entry
    return None


def _assign_mention_profile(
    mention: dict[str, Any],
    entity_id: str,
    profile_index: dict[str, tuple[str, dict[str, Any]]],
    entity_intent_profiles: dict[str, dict[str, Any]],
) -> None:
    """Assign or synthesize entity intent profile for a relationship mention."""
    mention_id = mention["mention_id"]
    matched = resolve_profile_index_entry(profile_index, entity_id)
    if matched is not None:
        entity_intent_profiles[mention_id] = dict(matched[-1])
        return
    prefix = entity_id.split(":", 1)[0] if ":" in entity_id else None
    node_type = map_entity_type(prefix)
    entity_intent_profiles[mention_id] = create_entity_intent_profile(
        stable_identifier=entity_id, node_type=node_type, entity_intent="resolve_or_create", declaration_status="DECLARED"
    )


def _extract_profile_tuple(entry: Any) -> tuple[str, dict[str, Any]] | None:
    if isinstance(entry, tuple) and len(entry) >= 3 and isinstance(entry[-1], dict):
        label_low = str(entry[1] or "").strip().lower()
        return (label_low, entry[-1]) if label_low else None
    return None


def _build_profile_label_lookup(profile_index: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    by_label: dict[str, tuple[str, dict[str, Any]]] = {}
    first_tok_map: dict[str, tuple[str, dict[str, Any]] | None] = {}
    for raw_id, entry in profile_index.items():
        extracted = _extract_profile_tuple(entry)
        if not extracted:
            continue
        label_low, prof = extracted
        by_label[label_low] = (raw_id, prof)
        first_tok = label_low.split()[0]
        if len(first_tok) >= 3:
            first_tok_map[first_tok] = None if first_tok in first_tok_map else (raw_id, prof)
    for tok, val in first_tok_map.items():
        if val is not None:
            by_label.setdefault(tok, val)
    return by_label


def _backfill_single_mention(
    m: dict[str, Any],
    by_label: dict[str, tuple[str, dict[str, Any]]],
    profile_index: dict[str, Any],
    entity_intent_profiles: dict[str, dict[str, Any]],
) -> None:
    m_id = m.get("mention_id")
    if not m_id:
        return
    s_id = m.get("stable_identifier")
    if not s_id or (s_id not in profile_index and "@" in str(s_id)):
        rep_low = str(m.get("reported_text") or "").strip().lower()
        if rep_low in by_label:
            s_id, matched_prof = by_label[rep_low]
            m["stable_identifier"] = s_id
            entity_intent_profiles.setdefault(m_id, dict(matched_prof))
    if m_id not in entity_intent_profiles and s_id and s_id in profile_index:
        entity_intent_profiles[m_id] = dict(profile_index[s_id][-1])


def _backfill_candidate_profiles(
    candidates: list[dict[str, Any]],
    profile_index: dict[str, Any],
    entity_intent_profiles: dict[str, dict[str, Any]],
) -> None:
    """Backfill mention profiles from profile_index for any candidate mentions lacking profiles."""
    if not profile_index:
        return
    by_label = _build_profile_label_lookup(profile_index)
    for c in candidates:
        for m in c.get("entity_mentions", []):
            _backfill_single_mention(m, by_label, profile_index, entity_intent_profiles)


def _extension(value: dict[str, Any], key: str, default: Any) -> Any:
    extensions = value.get("extensions")
    return extensions.get(key, default) if isinstance(extensions, dict) else default


def _context_extension(
    *, source_kind: str, attendees: list[str], workstream: str | None, source_posture: str, authority_rank: int, inline_canonical_promotion: bool = False
) -> dict[str, Any]:
    ctx: dict[str, Any] = {"source_kind": source_kind, "attendees": attendees, "workstream": workstream, "source_posture": source_posture, "authority_rank": authority_rank}
    if inline_canonical_promotion:
        ctx["inline_canonical_promotion"] = True
    return {"com.partner_advisory.nee.context": ctx}


def _validate_control_anchor(anchor: Any, chunk_id: str) -> dict[str, str]:
    if not isinstance(anchor, dict) or set(anchor) != {"reported_text", "stable_identifier", "context"}:
        raise ValueError(f"semantic correction anchor is invalid: {chunk_id}")
    if any(not isinstance(anchor[key], str) or not anchor[key].strip() for key in anchor):
        raise ValueError(f"semantic correction anchor is incomplete: {chunk_id}")
    return dict(anchor)


def _validate_control_semantics_and_binding(control: dict[str, Any], chunk_id: str) -> None:
    candidate_type = control.get("candidate_type")
    assertion_type = control.get("assertion_type")
    if (candidate_type, assertion_type) not in {
        ("source_chunk", "candidate_assertion"),
        ("action", "action_item"),
    }:
        raise ValueError(f"semantic correction candidate semantics are invalid: {chunk_id}")
    supersedes = control.get("supersedes_assertion_id")
    retire_only = control.get("retire_only")
    if (
        not isinstance(supersedes, str)
        or not supersedes.startswith("assertion-")
        or not isinstance(retire_only, bool)
        or (retire_only and candidate_type != "source_chunk")
    ):
        raise ValueError(f"semantic correction replacement binding is invalid: {chunk_id}")


def _validate_chunk_control(control: Any, chunk_id: str) -> dict[str, Any]:
    expected_keys = {
        "anchor_entity",
        "assertion_type",
        "candidate_type",
        "retire_only",
        "supersedes_assertion_id",
    }
    if not isinstance(control, dict) or set(control) != expected_keys:
        raise ValueError(f"semantic correction control has invalid shape: {chunk_id}")
    anchor_dict = _validate_control_anchor(control["anchor_entity"], chunk_id)
    _validate_control_semantics_and_binding(control, chunk_id)
    return {**control, "anchor_entity": anchor_dict}


def _markdown_semantic_correction(
    package: dict[str, Any],
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """Validate optional per-chunk controls for a governed semantic correction."""
    raw = _extension(package, SEMANTIC_CORRECTION_PACKAGE_KEY, None)
    if raw is None:
        return None, {}
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "replaces_run_id",
        "controls",
    }:
        raise ValueError("semantic correction extension has an invalid shape")
    if raw["schema_version"] != "1.0.0":
        raise ValueError("semantic correction extension has an unsupported version")
    replaces_run_id = raw["replaces_run_id"]
    controls = raw["controls"]
    if (
        not isinstance(replaces_run_id, str)
        or not replaces_run_id.startswith(("run-intake-", "extraction-run-"))
        or not isinstance(controls, dict)
        or not controls
    ):
        raise ValueError("semantic correction extension has invalid bindings")
    chunk_ids = {chunk["chunk_id"] for chunk in package["chunks"]}
    if set(controls) != chunk_ids:
        raise ValueError("semantic correction controls must bind every exact chunk")
    normalized = {
        chunk_id: _validate_chunk_control(control, chunk_id)
        for chunk_id, control in controls.items()
    }
    return replaces_run_id, normalized


VALID_CAND_ASSERTION_TYPES = frozenset({
    "approved_factual_assertion", "candidate_assertion", "decision", "action_item",
    "risk", "dependency", "proposal", "hypothesis", "opinion", "question",
    "architecture_component", "architecture_relationship", "contradiction", "supersession",
})


def extract_edge_assertion_type(rel: dict[str, Any]) -> str:
    raw_type = str(rel.get("assertion_type") or "architecture_relationship")
    return raw_type if raw_type in VALID_CAND_ASSERTION_TYPES else "architecture_relationship"


def extract_edge_confidence(rel: dict[str, Any]) -> tuple[float, str, str | None]:
    rel_conf = rel.get("confidence")
    if isinstance(rel_conf, dict):
        score = float(rel_conf.get("score", 0.9))
        tier = str(rel_conf.get("tier") or ("HIGH" if score >= 0.8 else "MEDIUM"))
        return score, tier, rel_conf.get("rationale")
    if isinstance(rel_conf, (float, int)):
        score = float(rel_conf)
        tier = "HIGH" if score >= 0.8 else ("MEDIUM" if score >= 0.6 else "LOW")
        return score, tier, None
    score = float(rel.get("extraction_confidence") or 0.9)
    tier = "HIGH" if score >= 0.8 else "MEDIUM"
    return score, tier, None


def _normalize_locator_dict(loc: Any) -> dict[str, Any] | None:
    if not isinstance(loc, dict):
        return None
    kind = loc.get("kind")
    val = str(loc.get("value") or "")
    if not kind or not val:
        return None
    res = dict(loc)
    if kind == "attachment":
        res["kind"] = "region"
        if not val.startswith("attachment:"):
            res["value"] = f"attachment:{val}"
    elif kind == "transcript_timestamp":
        res["kind"] = "timestamp"
    return res


def extract_edge_sticky_locator(rel: dict[str, Any], default_loc: dict[str, Any]) -> dict[str, Any]:
    sticky_note = rel.get("evidence")
    if isinstance(sticky_note, dict):
        norm = _normalize_locator_dict(sticky_note.get("locator"))
        if norm is not None:
            return norm
    norm_default = _normalize_locator_dict(default_loc)
    return norm_default if norm_default is not None else default_loc


def _role_workstream(role_or_org: str | None) -> str | None:
    if not role_or_org:
        return None
    match = re.match(r"^(.+?)\s+(?:workstream|team|program|project)\b", role_or_org, re.I)
    return normalize_space(match.group(1)) if match else None


def _statement_semantics(kind: str) -> tuple[str, str, str]:
    return {
        "decision": ("decision", "decision", "high"), "risk": ("risk", "risk", "high"),
        "dependency": ("dependency", "dependency", "medium"), "assumption": ("assertion", "hypothesis", "medium"),
        "proposal": ("assertion", "proposal", "medium"), "hypothesis": ("assertion", "hypothesis", "medium"),
        "open_question": ("open_question", "question", "medium"), "open question": ("open_question", "question", "medium"),
        "question": ("open_question", "question", "medium"), "opinion": ("assertion", "opinion", "medium"),
        "technical": ("assertion", "candidate_assertion", "medium"), "strategic": ("assertion", "candidate_assertion", "high"),
        "other": ("assertion", "candidate_assertion", "medium"),
    }.get(kind, ("assertion", "candidate_assertion", "medium"))


def _mention(candidate_id: str, ordinal: int, reported_text: str, *, stable_identifier: str | None, context: str) -> dict[str, Any]:
    return {"mention_id": stable_id("mention", candidate_id, ordinal, reported_text, length=24), "reported_text": normalize_space(reported_text), "stable_identifier": stable_identifier, "context": normalize_space(context)}
