"""Candidate disposition building and admission evaluation."""

from __future__ import annotations

from typing import Any, Iterable

from dual_pipeline.contracts import create_artifact, verify_authority_contract
from dual_pipeline.core import stable_id, utc_now
from dual_pipeline.identity_core.records import NEW_NODE_ASSESSMENT_EXTENSION_KEY
from dual_pipeline.strategic_ingestion import candidate_strategic_profile

from .constants import (
    EXTERNAL_CONCEPT_OUTCOME,
    GRAPH_ADMISSION_EXTENSION_KEY,
    IDENTITY_REVIEW_EXTENSION_KEY,
)
from .hitl import (
    _candidate_governance,
    _decision_map,
    _forbid_a2a_approved_fact_promotion,
    _validate_a2a_source_span_review_binding,
)
from .proposals import (
    _a2a_guidance_anchor,
    _candidate_resolution_map,
    _external_concept_proposal,
    _new_internal_entity_proposal,
    _semantic_correction_control,
)

_BLOCKED_INLINE_PROMO_ROUTES = frozenset({
    "CONTRADICTION",
    "SUPERSESSION",
    "CANDIDATE_PROMOTION_REVIEW",
    "EXACT_DUPLICATE",
    "SOURCE_ONLY",
    "QUARANTINE",
    "ENTITY_ALIAS_REVIEW",
    "EXTERNAL_VALIDATION",
})


def _passes_inline_canonical_promotion_gate(
    *,
    inline_promo: bool,
    candidate: dict[str, Any],
    route: str,
    identity_resolved: bool,
    material_contradiction_resolved: bool,
    source_only: bool,
    forced_hitl: bool,
    correction: dict[str, Any],
    has_status_snapshot: bool,
) -> bool:
    """Evaluate Team Beta epistemic safety gate for inline canonical promotion."""
    if not inline_promo or forced_hitl or (isinstance(correction, dict) and correction.get("force_hitl")):
        return False
    if not identity_resolved or not candidate.get("evidence_ids") or not material_contradiction_resolved or source_only:
        return False
    if route in _BLOCKED_INLINE_PROMO_ROUTES:
        return False
    if candidate.get("candidate_type") in {"open_question", "source_chunk"}:
        return False
    if candidate.get("document_signal_disposition") in {"SOURCE_ONLY", "EVIDENCE_ONLY"}:
        return False
    conf = float(
        candidate.get("extraction_confidence")
        or candidate.get("confidence")
        or (0.85 if candidate.get("candidate_type") in {"action", "relationship", "architecture_relationship"} else 0.0)
    )
    is_speculative = (
        candidate.get("assertion_type") in {"assumption", "hypothesis"}
        or candidate.get("candidate_type") in {"assumption", "hypothesis"}
    )
    return conf >= (0.80 if is_speculative else 0.70)


def _resolve_hitl_and_authority(
    *,
    bundle: dict[str, Any],
    context: dict[str, Any],
    candidate: dict[str, Any],
    route: str,
    candidate_resolutions: list[dict[str, Any]],
    identity_resolved: bool,
    material_contradiction_resolved: bool,
    source_only: bool,
    forced_hitl: bool,
    correction: dict[str, Any],
    authority_rank: int,
) -> tuple[bool, bool]:
    ext = bundle.get("extensions", {})
    inline_promo = bool(context.get("inline_canonical_promotion") or ext.get("com.partner_advisory.nee.inline-canonical-promotion"))
    status_snapshots = ext.get("com.partner_advisory.nee.project-status-candidate-profiles") or {}
    inline_gate_passed = _passes_inline_canonical_promotion_gate(
        inline_promo=inline_promo,
        candidate=candidate,
        route=route,
        identity_resolved=identity_resolved,
        material_contradiction_resolved=material_contradiction_resolved,
        source_only=source_only,
        forced_hitl=forced_hitl,
        correction=correction,
        has_status_snapshot=candidate["candidate_id"] in status_snapshots,
    )
    inline_entity_ok = (
        inline_promo and candidate["candidate_type"] == "entity_mention"
        and identity_resolved and not forced_hitl and not (isinstance(correction, dict) and correction.get("force_hitl"))
    )
    base_hitl = (
        forced_hitl
        or any(v.get("requires_hitl") for v in candidate_resolutions)
        or route in {"CONTRADICTION", "SUPERSESSION", "CANDIDATE_PROMOTION_REVIEW"}
        or candidate["candidate_type"] in {"action", "relationship"}
    )
    hitl_required = not (inline_gate_passed or inline_entity_ok) and base_hitl
    source_auth = (authority_rank >= 7 and (inline_gate_passed or inline_entity_ok)) if inline_promo else (authority_rank >= 7)
    return hitl_required, source_auth


def _extract_forced_hitl(
    governance: dict[str, Any], candidate: dict[str, Any], correction: Any, strategic_profile: Any, inline_promo: bool,
) -> bool:
    if governance.get("force_hitl") or candidate.get("force_hitl"):
        return True
    if isinstance(correction, dict) and correction.get("force_hitl"):
        return True
    return not inline_promo and strategic_profile is not None


def _is_explicit_approved_decision(decision: dict[str, Any] | None, identity_resolved: bool) -> bool:
    if not decision:
        return False
    d = decision.get("decision")
    return d in {"PROMOTE_APPROVED", "SUPERSEDE"} or (d in {"CONFIRM", "MERGE_ALIAS"} and identity_resolved)


def _corroborating_disposition(
    inline_promo: bool, source_auth: bool, contrad_res: bool, hitl_req: bool, decision: dict[str, Any] | None,
) -> tuple[str, str]:
    if inline_promo and source_auth and contrad_res and not hitl_req:
        return "APPROVED", "inline canonical promotion for corroborating evidence"
    disp = "APPROVED" if (decision and decision.get("decision") == "CONFIRM") else "CANDIDATE"
    return disp, "corroborating evidence for existing assertion"


def _is_contradiction_resolved(route: str, decision: dict[str, Any] | None) -> bool:
    if route != "CONTRADICTION":
        return True
    return bool(decision and decision.get("decision") in {"CONFIRM", "SUPERSEDE"})


def _check_decision_identity_resolved(decision: dict[str, Any] | None, default_resolved: bool) -> bool:
    if decision and decision.get("decision") in {"CONFIRM", "MERGE_ALIAS"}:
        return bool(decision.get("extensions", {}).get("com.partner_advisory.nee.selected-entity-id"))
    return default_resolved


def build_dispositions(
    bundle: dict[str, Any],
    routes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    decisions: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    route_by_candidate = {value["subject_id"]: value for value in routes}
    resolution_by_candidate = _candidate_resolution_map(bundle, resolutions)
    decision_by_candidate = _decision_map(decisions)
    _forbid_a2a_approved_fact_promotion(bundle, decision_by_candidate)
    ext = bundle.get("extensions", {})
    context = ext.get("com.partner_advisory.nee.context", {})
    authority_rank = int(context.get("authority_rank") or ext.get("com.partner_advisory.nee.authority-rank") or 1)
    inline_promo = bool(context.get("inline_canonical_promotion") or ext.get("com.partner_advisory.nee.inline-canonical-promotion"))
    results: list[dict[str, Any]] = []
    for candidate in bundle["candidates"]:
        candidate_id = candidate["candidate_id"]
        route = route_by_candidate[candidate_id]["intake_route"]
        governance = _candidate_governance(bundle, candidate_id)
        strategic_profile = candidate_strategic_profile(bundle, candidate_id)
        correction = _semantic_correction_control(bundle, candidate_id)
        forced_hitl = _extract_forced_hitl(governance, candidate, correction, strategic_profile, inline_promo)
        candidate_resolutions = resolution_by_candidate[candidate_id]
        decision = decision_by_candidate.get(candidate_id)
        if decision is not None:
            _validate_a2a_source_span_review_binding(governance, decision)
        is_evidence_envelope = context.get("source_kind") == "evidence_envelope"
        identity_resolved = all(
            value["outcome"] in ("AUTO_LINK_EXACT", "AUTO_LINK_HIGH_CONFIDENCE")
            or (
                is_evidence_envelope
                and value["outcome"] == "CREATE_NEW_ENTITY_PROPOSAL"
                and value.get("extensions", {}).get(NEW_NODE_ASSESSMENT_EXTENSION_KEY, {}).get("status") == "PROPOSE_NEW_CANDIDATE"
            )
            for value in candidate_resolutions
        )
        source_only = bool(candidate_resolutions) and all(
            value["outcome"] == "SOURCE_ONLY" for value in candidate_resolutions
        )
        identity_resolved = _check_decision_identity_resolved(decision, identity_resolved)
        if decision and decision["decision"] == "CREATE_NEW":
            approved_proposal = _new_internal_entity_proposal(
                candidate=candidate, resolutions=candidate_resolutions, decision=decision,
            )
            identity_resolved = approved_proposal is not None and all(
                value["outcome"] == "AUTO_LINK_EXACT"
                or (
                    value.get("stable_identifier", {}).get("value") == approved_proposal["source_stable_identifier"]
                    and value.get("reported_name") == approved_proposal["canonical_name"]
                )
                or (
                    is_evidence_envelope
                    and value["outcome"] == "CREATE_NEW_ENTITY_PROPOSAL"
                    and value.get("extensions", {}).get(NEW_NODE_ASSESSMENT_EXTENSION_KEY, {}).get("status") == "PROPOSE_NEW_CANDIDATE"
                )
                for value in candidate_resolutions
            )
        guidance_anchor = _a2a_guidance_anchor(bundle, candidate, decision)
        if guidance_anchor is not None:
            identity_resolved = True
        external_resolutions = [
            value for value in candidate_resolutions if value["outcome"] == EXTERNAL_CONCEPT_OUTCOME
        ]
        if external_resolutions:
            for value in external_resolutions:
                _external_concept_proposal(value, bundle)
            identity_resolved = bool(decision and decision["decision"] == EXTERNAL_CONCEPT_OUTCOME)
        material_contradiction_resolved = _is_contradiction_resolved(route, decision)
        hitl_required, source_authority_sufficient = _resolve_hitl_and_authority(
            bundle=bundle, context=context, candidate=candidate, route=route,
            candidate_resolutions=candidate_resolutions, identity_resolved=identity_resolved,
            material_contradiction_resolved=material_contradiction_resolved,
            source_only=source_only, forced_hitl=forced_hitl,
            correction=correction, authority_rank=authority_rank,
        )
        hitl_complete = bool(decision and decision["decision"] not in {"HOLD", "QUARANTINE"})
        reasons: list[str] = []

        if decision and decision["decision"] == "REJECT":
            disposition = "REJECTED_NOOP"
            reasons.append("HITL rejected the candidate")
        elif decision and decision["decision"] in {"HOLD", "QUARANTINE"}:
            disposition = "CANDIDATE" if decision["decision"] == "HOLD" else "QUARANTINE"
            reasons.append("HITL held candidate for candidate staging" if disposition == "CANDIDATE" else "HITL quarantined candidate")
        elif route == "CORROBORATING_EVIDENCE":
            disposition, reason = _corroborating_disposition(inline_promo, source_authority_sufficient, material_contradiction_resolved, hitl_required, decision)
            reasons.append(reason)
        elif route == "EXACT_DUPLICATE":
            disposition = "REJECTED_NOOP"
            reasons.append("exact duplicate with no governed new-evidence promotion")
        elif route == "SOURCE_ONLY" or candidate.get("document_signal_disposition") in {"SOURCE_ONLY", "EVIDENCE_ONLY"} or source_only:
            disposition = "REJECTED_NOOP"
            reasons.append(
                "source profile retains this item as evidence only and forbids a knowledge-node write"
            )
        elif candidate["candidate_type"] == "open_question":
            disposition = "QUARANTINE"
            reasons.append("open ambiguity requires governed resolution")
        elif not identity_resolved:
            disposition = "QUARANTINE"
            reasons.append("identity is unresolved")
        elif not candidate["evidence_ids"]:
            disposition = "REJECTED_NOOP"
            reasons.append("evidence is missing")
        elif not material_contradiction_resolved:
            disposition = "QUARANTINE"
            reasons.append("material contradiction is unresolved")
        elif _is_explicit_approved_decision(decision, identity_resolved):
            source_authority_sufficient = True
            disposition = "APPROVED"
            reasons.append(f"explicit HITL {decision['decision'].lower()} decision approved")
        elif source_authority_sufficient and material_contradiction_resolved and not hitl_required:
            disposition = "APPROVED"
            reasons.append("authoritative source authority rank with no contradictions")
        elif not source_authority_sufficient:
            disposition = "CANDIDATE"
            reasons.append("source authority rank is insufficient for approved fact")
        else:
            disposition = "CANDIDATE"
            reasons.append("default governed intake disposition")

        mapping = {
            "APPROVED": ("X3D_ALLOW_APPROVED_WRITE", "approved"),
            "CANDIDATE": ("X3D_ALLOW_CANDIDATE_STAGE", "candidate"),
            "QUARANTINE": ("X3Q_QUARANTINE", "none"),
            "REJECTED_NOOP": ("X3R_REJECT_NOOP", "none"),
        }
        x3_outcome, persistent_lane = mapping[disposition]
        graph_admission = {
            "profile_version": "1.0.0",
            "current_disposition": disposition,
            "current_graph_action": (
                "stage_approved_assertion"
                if disposition == "APPROVED"
                else "stage_candidate_assertion"
                if disposition == "CANDIDATE"
                else "no_graph_write"
            ),
            "current_graph_write_allowed": disposition in {"APPROVED", "CANDIDATE"},
            "decision_reasons": reasons,
            "decision_inputs": {
                "route": route,
                "route_reason_codes": route_by_candidate[candidate_id].get("reason_codes", []),
                "identity_resolved": identity_resolved,
                "identity_reviews": [
                    value.get("extensions", {}).get(IDENTITY_REVIEW_EXTENSION_KEY)
                    for value in candidate_resolutions
                ],
                "evidence_count": len(candidate["evidence_ids"]),
                "extraction_confidence": candidate["extraction_confidence"],
                "classification": candidate["classification"],
                "classification_valid": candidate["classification"]
                in {"public", "internal", "confidential", "restricted"},
                "audiences": candidate["audiences"],
                "audience_valid": bool(candidate["audiences"]),
                "source_authority_rank": authority_rank,
                "approved_fact_authority_threshold": 7,
                "source_authority_sufficient_for_approved_fact": source_authority_sufficient,
                "material_contradiction_resolved": material_contradiction_resolved,
                "hitl_required": hitl_required,
                "hitl_complete": hitl_complete,
            },
            "next_actions": (
                [
                    "Select one source-bound graph-label option and record an exact HITL decision.",
                    "Re-run review against the unchanged graph binding; do not edit the planned candidate in place.",
                ]
                if disposition == "QUARANTINE" and not identity_resolved
                else []
            ),
        }
        disp_extensions = {GRAPH_ADMISSION_EXTENSION_KEY: graph_admission}
        corroborate_id = (
            route_by_candidate.get(candidate_id, {})
            .get("extensions", {})
            .get("com.partner_advisory.nee.corroborate-assertion-id")
        )
        if corroborate_id:
            disp_extensions["com.partner_advisory.nee.corroborate-assertion-id"] = corroborate_id
        artifact_id = stable_id("disposition", bundle["run_id"], candidate_id, length=26)
        results.append(
            create_artifact(
                "assertion-disposition",
                artifact_id,
                disposition_id=artifact_id,
                run_id=bundle["run_id"],
                trace_id=bundle["trace_id"],
                candidate_id=candidate_id,
                assertion_type=candidate["assertion_type"],
                disposition=disposition,
                x3_outcome=x3_outcome,
                persistent_lane=persistent_lane,
                identity_resolved=identity_resolved,
                evidence_ids=candidate["evidence_ids"],
                source_authority_sufficient=source_authority_sufficient,
                currentness_valid=True,
                classification_valid=candidate["classification"]
                in {"public", "internal", "confidential", "restricted"},
                audience_valid=bool(candidate["audiences"]),
                material_contradiction_resolved=material_contradiction_resolved,
                hitl_required=hitl_required,
                hitl_complete=hitl_complete,
                reason_codes=reasons,
                decided_at=utc_now(),
                extensions=disp_extensions,
            )
        )
    if len(results) != len(bundle["candidates"]):
        raise ValueError("every candidate must receive exactly one disposition")
    return results


__all__ = ["build_dispositions", "verify_authority_contract"]
