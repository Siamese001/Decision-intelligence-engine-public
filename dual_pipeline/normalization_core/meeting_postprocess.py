"""Post-processing helpers for normalized meeting candidates and action items."""
from __future__ import annotations

import re
from typing import Any, Callable

from ..core import digest, normalize_space, stable_id
from .meeting_entity_helpers import _extension, _mention


def resolve_meeting_source_version(
    package: dict[str, Any],
    meeting: dict[str, Any],
    source_id: str,
    package_digest: str,
) -> tuple[str, str]:
    """Derive deterministic source_content_digest and source_version_id from canonical transcript content."""
    explicit_digest = _extension(package, "com.partner_advisory.nee.source-content-digest", None)
    if isinstance(explicit_digest, str) and explicit_digest.strip():
        content_digest = explicit_digest.strip()
    elif meeting.get("source_hash") and not _extension(package, "com.partner_advisory.nee.semantic-correction", None):
        stmts = [
            (
                s.get("speaker_ref") or s.get("reported_name"),
                st.get("statement_id"),
                st.get("text"),
            )
            for s in package.get("speakers", [])
            for st in s.get("statements", [])
        ]
        content_digest = digest({"source_hash": meeting.get("source_hash"), "statements": stmts})
    else:
        content_digest = package_digest
    return content_digest, stable_id("source-version", source_id, content_digest, length=24)


def resolve_meeting_posture_and_authority(
    package: dict[str, Any], authority_ranks: dict[str, int]
) -> tuple[bool, str, int, float, Any]:
    inline_promo = bool(_extension(package, "com.partner_advisory.nee.inline-canonical-promotion", False))
    default_posture = "governed_internal" if inline_promo else "derivative_internal"
    posture = str(_extension(package, "com.partner_advisory.nee.source-posture", default_posture))
    authority_rank = int(
        _extension(package, "com.partner_advisory.nee.authority-rank", authority_ranks.get(posture, 7 if inline_promo else 5))
    )
    doc_routing_ext = _extension(package, "com.partner_advisory.nee.document-signal-routing", None)
    if isinstance(doc_routing_ext, dict) and isinstance(doc_routing_ext.get("policy"), dict):
        pol = doc_routing_ext["policy"]
        if _extension(package, "com.partner_advisory.nee.authority-rank", None) is None and pol.get("authority_rank") is not None:
            authority_rank = int(pol["authority_rank"])
        if _extension(package, "com.partner_advisory.nee.source-posture", None) is None and pol.get("source_posture"):
            posture = str(pol["source_posture"])
    default_stmt_conf = 0.85 if inline_promo else 0.75
    return inline_promo, posture, authority_rank, default_stmt_conf, doc_routing_ext


def _build_action_mentions(
    candidate_id: str, owner: str | None, meeting_title: str
) -> list[dict[str, Any]]:
    if not owner:
        return []
    return [
        _mention(
            candidate_id,
            1,
            owner,
            stable_identifier=owner if "@" in owner else None,
            context=f"Action owner / {meeting_title}",
        )
    ]


def _format_action_text(owner: str | None, raw_action: str) -> str:
    owner_prefix = f"{owner} will " if owner else "An owner must "
    normalized_action = raw_action.strip()
    if normalized_action:
        normalized_action = normalized_action[0].lower() + normalized_action[1:]
    return normalize_space(owner_prefix + normalized_action)


def _collect_action_ev_ids(
    action: dict[str, Any],
    primary_ev_id: str,
    action_ev_ext: dict[str, Any],
    evidence_by_identifier: dict[str, str],
) -> list[str]:
    act_ev_ids = [primary_ev_id]
    for extra_eid in action_ev_ext.get(action["action_id"], []):
        mapped_ev = evidence_by_identifier.get(extra_eid)
        if mapped_ev and mapped_ev not in act_ev_ids:
            act_ev_ids.append(mapped_ev)
    return act_ev_ids


def _merge_action_ev_ids(existing_act: dict[str, Any], act_ev_ids: list[str]) -> None:
    for eid_val in act_ev_ids:
        if eid_val not in existing_act["evidence_ids"]:
            existing_act["evidence_ids"].append(eid_val)


def extract_action_candidates(
    package: dict[str, Any],
    *,
    run_id: str,
    meeting_title: str,
    classification: str,
    default_audiences: list[str],
    add_evidence: Callable[[str, str, dict[str, Any]], str],
    evidence_by_identifier: dict[str, str],
    candidates: list[dict[str, Any]],
) -> None:
    """Extract normalized action candidates from package['actions']."""
    seen_action_candidates: dict[tuple[str, str], dict[str, Any]] = {}
    action_ev_ext = _extension(package, "com.partner_advisory.nee.action-evidence-ids", {}) or {}
    for action in package["actions"]:
        evidence_id = add_evidence(action["action_id"], action["action"], action["locator"])
        act_ev_ids = _collect_action_ev_ids(action, evidence_id, action_ev_ext, evidence_by_identifier)
        candidate_id = stable_id("candidate", run_id, action["action_id"], length=26)
        owner = action.get("reported_owner")
        act_text = _format_action_text(owner, action["action"])
        action_key = (owner or "", act_text)
        if action_key in seen_action_candidates:
            _merge_action_ev_ids(seen_action_candidates[action_key], act_ev_ids)
            continue
        act_entry = {
            "candidate_id": candidate_id,
            "candidate_type": "action",
            "assertion_type": "action_item",
            "normalized_text": act_text,
            "materiality": "medium",
            "entity_mentions": _build_action_mentions(candidate_id, owner, meeting_title),
            "evidence_ids": act_ev_ids,
            "source_locator": action["locator"],
            "classification": classification,
            "audiences": default_audiences,
            "extraction_confidence": 0.9,
        }
        seen_action_candidates[action_key] = act_entry
        candidates.append(act_entry)


def _merge_duplicate_candidate(prior_cand: dict[str, Any], cand: dict[str, Any]) -> None:
    for eid in cand.get("evidence_ids", []):
        if eid not in prior_cand["evidence_ids"]:
            prior_cand["evidence_ids"].append(eid)
    existing_m_texts = {
        (m.get("reported_text"), m.get("stable_identifier"))
        for m in prior_cand.get("entity_mentions", [])
    }
    for m in cand.get("entity_mentions", []):
        m_key = (m.get("reported_text"), m.get("stable_identifier"))
        if m_key not in existing_m_texts:
            prior_cand["entity_mentions"].append(m)
            existing_m_texts.add(m_key)


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate normalized candidates by type and normalized text."""
    deduped_candidates: list[dict[str, Any]] = []
    seen_text_by_type: dict[tuple[str, str], dict[str, Any]] = {}
    grouped_types = {"assertion", "decision", "risk", "dependency"}
    for cand in candidates:
        c_type = cand.get("candidate_type", "")
        raw_norm = re.sub(r"\s+in\s+[^.]+?\.\s*$", "", str(cand.get("normalized_text", ""))).casefold().strip(" .")
        if c_type == "action":
            raw_norm = re.sub(r"^(?:an owner must|[^.]+?\s+will)\s+", "", raw_norm).strip(" .")
        norm_key = (
            c_type if c_type not in grouped_types else "assertion_group",
            raw_norm,
        )
        if norm_key[1] and norm_key in seen_text_by_type and c_type != "entity_mention":
            _merge_duplicate_candidate(seen_text_by_type[norm_key], cand)
            continue
        seen_text_by_type[norm_key] = cand
        deduped_candidates.append(cand)
    return deduped_candidates


def apply_doc_routing_budget(
    candidates: list[dict[str, Any]], doc_routing_ext: Any
) -> list[dict[str, Any]]:
    """Apply document signal routing budget when doc_routing_ext is present."""
    if not isinstance(doc_routing_ext, dict):
        return candidates
    from ..document_pipeline_core.document_signal_router import (
        ARCHETYPE_POLICIES,
        DocumentArchetype,
        DocumentSignalRoutingDecision,
        SectionSignalScore,
        apply_document_signal_budget_to_candidates,
    )
    arch_name = str(doc_routing_ext.get("archetype") or "").upper()
    if arch_name not in DocumentArchetype.__members__:
        return candidates
    arch_enum = DocumentArchetype[arch_name]
    sec_objs = [
        SectionSignalScore(
            section_heading=str(s.get("section_heading") or ""),
            word_count=int(s.get("word_count") or 0),
            signal_score=float(s.get("signal_score") or 0.5),
            is_boilerplate=bool(s.get("is_boilerplate", False)),
            recommended_lane=str(s.get("recommended_lane") or "CANDIDATE"),
            section_archetype=DocumentArchetype[str(s.get("section_archetype") or arch_name)],
        )
        for s in (doc_routing_ext.get("sections") or [])
        if isinstance(s, dict)
    ]
    routing_obj = DocumentSignalRoutingDecision(
        archetype=arch_enum,
        policy=ARCHETYPE_POLICIES[arch_enum],
        word_count=int(doc_routing_ext.get("word_count") or 100),
        max_promoted_mutations=int(doc_routing_ext.get("max_promoted_mutations") or 10),
        sections=sec_objs,
        rationale=list(doc_routing_ext.get("rationale") or []),
    )
    return apply_document_signal_budget_to_candidates(candidates, routing_obj)


def build_extra_bundle_extensions(
    package: dict[str, Any],
    candidates: list[dict[str, Any]],
    doc_routing_ext: Any,
) -> dict[str, Any]:
    """Build optional project-status and document-routing bundle extensions."""
    extra_bundle_ext: dict[str, Any] = {}
    snapshot_ext = _extension(package, "com.partner_advisory.nee.project-status-snapshot", None)
    if isinstance(snapshot_ext, dict):
        from ..project_status import PROJECT_STATUS_CANDIDATE_PROFILES_KEY, validate_snapshot_profile
        valid_snap = validate_snapshot_profile(snapshot_ext)
        extra_bundle_ext[PROJECT_STATUS_CANDIDATE_PROFILES_KEY] = {
            c["candidate_id"]: valid_snap for c in candidates
        }
    if isinstance(doc_routing_ext, dict):
        extra_bundle_ext["com.partner_advisory.nee.document-signal-routing"] = doc_routing_ext
    if _extension(package, "com.partner_advisory.nee.inline-canonical-promotion", False):
        extra_bundle_ext["com.partner_advisory.nee.inline-canonical-promotion"] = True
    return extra_bundle_ext
