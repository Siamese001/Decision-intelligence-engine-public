"""HITL batching, decision mapping, and candidate governance."""

from __future__ import annotations

from typing import Any, Iterable

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import ZERO_HASH, digest, stable_id, utc_now
from dual_pipeline.identity import NEW_NODE_ASSESSMENT_EXTENSION_KEY
from dual_pipeline.strategic_ingestion import candidate_strategic_profile

from .constants import (
    A2A_SOURCE_SPAN_REVIEW_BINDING_KEY,
    EXTERNAL_CONCEPT_OUTCOME,
    FACT_PROMOTION_READY_STATUSES,
    GRAPH_ADMISSION_EXTENSION_KEY,
    IDENTITY_REVIEW_EXTENSION_KEY,
)
from .proposals import (
    _candidate_resolution_map,
    _external_concept_proposal,
    _is_a2a_source_bundle,
    _semantic_correction_control,
    _validated_a2a_finalization_binding,
)


def _decision_map(decisions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        validate_artifact(decision)
        if not decision["contract_id"].endswith(":hitl-decision:1.0.0"):
            raise ValueError("only HITL decision artifacts are allowed")
        for candidate_id in decision["affected_candidate_ids"]:
            if candidate_id in result:
                raise ValueError(f"candidate has multiple HITL decisions: {candidate_id}")
            result[candidate_id] = decision
    return result


_REQUIRED_GOV_KEYS = {
    "source_item_id", "retrieval_kind", "answer_use", "fact_promotion_eligible",
    "review_questions", "promotion_status", "force_hitl",
}
_OPTIONAL_GOV_KEYS = {"evidence_verification_states", "source_selection_bindings", "finalization_binding"}
_VALID_VERIFICATION_STATES = {
    "captured_excerpt_present_refetch_required", "locator_only_refetch_required", "fresh_capture_verified",
    "fresh_capture_preview_mismatch", "fresh_capture_locator_pending", "fresh_capture_selected_span",
    "derived_packet_content",
}
_REQUIRED_SELECTION_BINDING_KEYS = {
    "selection_ledger_sha256", "span_ledger_sha256", "reviewer", "decided_at", "human_attestation",
    "reviewer_authorization_reference", "fact_id", "decision", "span_proposal_id", "evidence_id",
    "source_id", "rationale", "quote", "quote_sha256", "visible_start_offset", "visible_end_offset",
    "raw_response_sha256", "visible_text_sha256", "canonical_locator",
}


def _validate_gov_shape_and_questions(value: dict[str, Any], a2a_source: bool) -> None:
    if not set(value) >= _REQUIRED_GOV_KEYS or not set(value) <= _REQUIRED_GOV_KEYS | _OPTIONAL_GOV_KEYS:
        raise ValueError("A2A candidate governance item has an invalid shape")
    questions = value["review_questions"]
    if (
        not isinstance(value["force_hitl"], bool)
        or not isinstance(value["fact_promotion_eligible"], bool)
        or not isinstance(questions, list)
        or not questions
        or not all(isinstance(q, str) and q.strip() for q in questions)
    ):
        raise ValueError("A2A candidate governance item has invalid HITL questions")
    if a2a_source and value["force_hitl"] is not True:
        raise ValueError("every A2A candidate must retain its forced individual HITL route")


def _validate_gov_retrieval_and_promotion(bundle: dict[str, Any], candidate_id: str, value: dict[str, Any]) -> None:
    cand_type = next((c["candidate_type"] for c in bundle["candidates"] if c["candidate_id"] == candidate_id), None)
    expected = {
        "assertion": ("assertion", "candidate_hypothesis"),
        "action": ("guidance", "guidance"),
        "source_chunk": ("evidence_span", "citation_only"),
    }.get(cand_type)
    if expected is None or (value["retrieval_kind"], value["answer_use"]) != expected:
        raise ValueError("A2A candidate governance does not match its candidate type")
    if value["fact_promotion_eligible"] and expected != ("assertion", "candidate_hypothesis"):
        raise ValueError("only factual assertion candidates can be promotion eligible")
    if value["fact_promotion_eligible"] and value["promotion_status"] not in FACT_PROMOTION_READY_STATUSES:
        raise ValueError("fact promotion eligibility requires source-bound ready evidence")


def _validate_gov_verification_states(verification_states: Any) -> None:
    if verification_states is None:
        return
    if not isinstance(verification_states, dict) or not verification_states:
        raise ValueError("A2A candidate governance item has invalid evidence verification states")
    for eid, state in verification_states.items():
        if not isinstance(eid, str) or not eid or state not in _VALID_VERIFICATION_STATES:
            raise ValueError("A2A candidate governance item has invalid evidence verification states")


def _is_valid_selection_binding(binding: Any, ev_ids: set[str]) -> bool:
    return (
        isinstance(binding, dict)
        and set(binding) == _REQUIRED_SELECTION_BINDING_KEYS
        and binding.get("evidence_id") in ev_ids
        and isinstance(binding.get("quote"), str)
        and bool(binding["quote"].strip())
        and isinstance(binding.get("rationale"), str)
        and bool(binding["rationale"].strip())
    )


def _validate_gov_selection_bindings(value: dict[str, Any]) -> None:
    bindings = value.get("source_selection_bindings")
    if bindings is None:
        return
    if not isinstance(bindings, list):
        raise ValueError("A2A candidate governance item has invalid source selection bindings")
    ev_ids = set(value.get("evidence_verification_states", {}))
    if (
        len(bindings) != len(ev_ids)
        or not all(_is_valid_selection_binding(b, ev_ids) for b in bindings)
        or {b["evidence_id"] for b in bindings} != ev_ids
    ):
        raise ValueError("A2A candidate governance item has invalid source selection bindings")


def _extract_raw_candidate_governance(bundle: dict[str, Any], candidate_id: str, a2a_source: bool) -> dict[str, Any] | None:
    raw = bundle.get("extensions", {}).get("com.partner_advisory.nee.a2a-packet-candidate-governance")
    if raw is None:
        if a2a_source:
            raise ValueError("A2A candidate is missing its required source governance")
        return None
    if not isinstance(raw, dict):
        raise ValueError("A2A candidate governance extension must be an object")
    value = raw.get(candidate_id)
    if value is None:
        if a2a_source:
            raise ValueError("A2A candidate is missing its required source governance item")
        return None
    if not isinstance(value, dict):
        raise ValueError("A2A candidate governance item must be an object")
    return value


def _candidate_governance(bundle: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    """Read optional source-bound candidate controls without accepting drift."""
    a2a_source = _is_a2a_source_bundle(bundle)
    value = _extract_raw_candidate_governance(bundle, candidate_id, a2a_source)
    if value is None:
        return {}
    _validate_gov_shape_and_questions(value, a2a_source)
    _validate_gov_retrieval_and_promotion(bundle, candidate_id, value)
    _validate_gov_verification_states(value.get("evidence_verification_states"))
    _validate_gov_selection_bindings(value)
    finalization_binding = value.get("finalization_binding")
    if finalization_binding is not None:
        finalization_binding = _validated_a2a_finalization_binding(finalization_binding)
    if a2a_source and value["fact_promotion_eligible"]:
        if (
            value["promotion_status"] != "candidate_evidence_selected_span_hitl_required"
            or set(value.get("evidence_verification_states", {}).values()) != {"fresh_capture_selected_span"}
            or not value.get("source_selection_bindings")
            or finalization_binding is None
        ):
            raise ValueError("A2A fact promotion eligibility requires replayed, selected source-span bindings")
    return value


def _forbid_a2a_approved_fact_promotion(
    bundle: dict[str, Any], decision_by_candidate: dict[str, dict[str, Any]]
) -> None:
    """Keep A2A source intake candidate-only outside the engine review entrypoint."""

    if _is_a2a_source_bundle(bundle) and any(
        decision.get("decision") == "PROMOTE_APPROVED"
        for decision in decision_by_candidate.values()
    ):
        raise ValueError("A2A source HITL may create only a candidate, never an approved fact")


def _validate_a2a_source_span_review_binding(
    governance: dict[str, Any], decision: dict[str, Any] | None
) -> None:
    """Require every formal A2A decision to echo the selected frozen spans.

    The source-selection ledger is pre-intake evidence review, whereas the
    later ``hitl-decision`` is the governed decision that permits candidate
    staging.  Keeping their exact binding in this shared helper prevents a
    direct Python caller from bypassing the equivalent IntakeEngine check.
    """

    bindings = governance.get("source_selection_bindings")
    if bindings is None:
        return
    if decision is None:
        raise ValueError("A2A selected source spans require an exact formal HITL decision")
    actual = decision.get("extensions", {}).get(A2A_SOURCE_SPAN_REVIEW_BINDING_KEY)
    expected = {"bindings": bindings}
    if governance.get("finalization_binding") is not None:
        expected["finalization_binding"] = governance["finalization_binding"]
    if actual != expected:
        raise ValueError(
            "HITL decision does not match its source-span selection, quote, and finalization bindings"
        )


def _build_reported_entities(member_resolutions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reported_entities: list[dict[str, Any]] = []
    seen_reported: set[tuple[str, str, str]] = set()
    for value in member_resolutions:
        assessment = value.get("extensions", {}).get(NEW_NODE_ASSESSMENT_EXTENSION_KEY, {})
        key = (value["reported_name"], value["outcome"], assessment.get("group_id", value["mention_id"]))
        if key in seen_reported:
            continue
        seen_reported.add(key)
        reported_entities.append({
            "reported_name": value["reported_name"],
            "recommended_outcome": value["outcome"],
            "alternatives": value["alternatives"],
            "identity_review": value.get("extensions", {}).get(IDENTITY_REVIEW_EXTENSION_KEY),
            "new_node_assessment": assessment or None,
            "technical_resolution_ids": sorted(
                res["resolution_id"] for res in member_resolutions
                if res["reported_name"] == value["reported_name"] and res["outcome"] == value["outcome"]
            ),
        })
    return reported_entities


def _resolve_grouped_member_ids(
    candidate_id: str,
    candidate_resolutions: list[dict[str, Any]],
    candidate_by_id: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, Any] | None]:
    grouped = next(
        (
            v.get("extensions", {}).get(NEW_NODE_ASSESSMENT_EXTENSION_KEY)
            for v in candidate_resolutions if v["outcome"] in {"CREATE_NEW_ENTITY_PROPOSAL", "HITL_CONFIRM_ENTITY"}
        ),
        None,
    )
    if isinstance(grouped, dict):
        proposed = grouped.get("affected_candidate_ids")
        if isinstance(proposed, list) and proposed and all(v in candidate_by_id for v in proposed):
            return sorted(set(proposed)), grouped
    return [candidate_id], grouped


def _classify_hitl_tier_and_reasons(
    member_candidates: list[dict[str, Any]],
    member_routes: list[dict[str, Any]],
    member_resolutions: list[dict[str, Any]],
    external_concept_proposals: list[dict[str, Any]],
    forced_hitl: bool,
) -> tuple[int, list[str]]:
    unresolved = [v for v in member_resolutions if v["outcome"] not in {"AUTO_LINK_EXACT", "SOURCE_ONLY"}]
    high_risk_routes = [
        r["intake_route"]
        for r in member_routes
        if r.get("intake_route") in {"CONTRADICTION", "SUPERSESSION", "CANDIDATE_PROMOTION_REVIEW"}
    ]
    reasons = [
        label for flag, label in (
            (bool(unresolved), "ambiguous or unbounded identity"),
            (bool(external_concept_proposals), "source-scoped external concept requires creation"),
            (any(c["candidate_type"] in {"architecture_component", "architecture_relationship"} for c in member_candidates), "architecture change"),
            (bool(high_risk_routes), high_risk_routes[0].casefold().replace("_", " ") if high_risk_routes else ""),
            (any("perspective_owner_shift" in r.get("reason_codes", []) for r in member_routes), "perspective owner shift"),
        ) if flag
    ]
    if forced_hitl:
        reasons.append("strategic role or source package requires individual evidence review")
        return 2, reasons
    if reasons:
        return 2, reasons
    if any(c["candidate_type"] in {"action", "relationship"} for c in member_candidates):
        reasons.append("related low-risk candidate review")
        return 1, reasons
    reasons.append("automatic candidate/no-op controls")
    return 0, reasons


def _attach_item_optional_fields(
    item: dict[str, Any],
    member_ids: list[str],
    disposition_by_candidate: dict[str, dict[str, Any]],
    reported_entities: list[dict[str, Any]],
    governance: dict[str, Any],
    external_concept_proposals: list[dict[str, Any]],
    correction: dict[str, Any],
    strategic_profile: Any,
) -> None:
    admissions = [
        disposition_by_candidate[v].get("extensions", {}).get(GRAPH_ADMISSION_EXTENSION_KEY)
        for v in member_ids if v in disposition_by_candidate
    ]
    if admissions:
        item["graph_admissions"] = admissions
        if len(admissions) == 1:
            item["graph_admission"] = admissions[0]
    proposals = [
        v["new_node_assessment"]["proposal"]
        for v in reported_entities
        if isinstance(v.get("new_node_assessment"), dict) and isinstance(v["new_node_assessment"].get("proposal"), dict)
    ]
    if proposals:
        item["new_node_proposals"] = proposals
    if governance:
        for k in ("retrieval_kind", "answer_use", "source_item_id", "promotion_status", "review_questions"):
            item[k] = governance[k]
        if governance.get("source_selection_bindings"):
            item["source_selection_bindings"] = governance["source_selection_bindings"]
    if external_concept_proposals:
        item["external_concept_proposals"] = external_concept_proposals
    if correction:
        item["semantic_correction"] = correction
    if strategic_profile is not None:
        item["strategic_profile"] = strategic_profile


def _build_hitl_review_item(
    bundle: dict[str, Any],
    candidate_id: str,
    member_ids: list[str],
    grouped_assessment: Any,
    candidate_by_id: dict[str, dict[str, Any]],
    route_by_candidate: dict[str, dict[str, Any]],
    resolution_by_candidate: dict[str, list[dict[str, Any]]],
    disposition_by_candidate: dict[str, dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    member_candidates = [candidate_by_id[value] for value in member_ids]
    member_routes = [route_by_candidate[mid] for mid in member_ids if mid in route_by_candidate]
    member_resolutions = [v for mid in member_ids for v in resolution_by_candidate[mid]]
    member_governances = [g for mid in member_ids if (g := _candidate_governance(bundle, mid))]
    member_corrections = [c for mid in member_ids if (c := _semantic_correction_control(bundle, mid))]
    member_profiles = [p for mid in member_ids if (p := candidate_strategic_profile(bundle, mid)) is not None]
    governance = member_governances[0] if member_governances else {}
    correction = member_corrections[0] if member_corrections else {}
    strategic_profile = member_profiles[0] if member_profiles else None
    ext = bundle.get("extensions", {})
    inline_promo = bool(ext.get("com.partner_advisory.nee.context", {}).get("inline_canonical_promotion") or ext.get("com.partner_advisory.nee.inline-canonical-promotion"))
    forced_hitl = bool(
        any(g.get("force_hitl") is True for g in member_governances)
        or any(c.get("force_hitl") is True for c in member_corrections)
        or (not inline_promo and bool(member_profiles))
    )
    external_concept_proposals = [
        _external_concept_proposal(v, bundle) for v in member_resolutions if v["outcome"] == EXTERNAL_CONCEPT_OUTCOME
    ]
    tier, reasons = _classify_hitl_tier_and_reasons(
        member_candidates, member_routes, member_resolutions, external_concept_proposals, forced_hitl
    )
    tier_0_eligible = (
        disposition_by_candidate and not forced_hitl and inline_promo and all(
            disposition_by_candidate.get(m, {}).get("disposition") in {"APPROVED", "CANDIDATE"}
            and not disposition_by_candidate.get(m, {}).get("hitl_required", True) for m in member_ids
        )
    )
    if tier_0_eligible:
        tier, reasons = 0, ["inline canonical promotion gate approved"]
    review_group_id = grouped_assessment.get("group_id") if isinstance(grouped_assessment, dict) else candidate_id
    reported_entities = _build_reported_entities(member_resolutions)
    item = {
        "review_item_id": stable_id("review-item", bundle["run_id"], review_group_id, length=26),
        "tier": tier,
        "candidate_ids": member_ids,
        "reason_codes": reasons,
        "reported_entities": reported_entities,
        "evidence_ids": sorted({eid for v in member_candidates for eid in v["evidence_ids"]}),
        "before_state_digest": ZERO_HASH,
        "proposed_after_state_digest": digest(member_candidates if len(member_candidates) > 1 else member_candidates[0]),
    }
    _attach_item_optional_fields(
        item, member_ids, disposition_by_candidate, reported_entities,
        governance, external_concept_proposals, correction, strategic_profile,
    )
    return tier, item


def build_hitl_batches(
    bundle: dict[str, Any],
    routes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    dispositions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    route_by_candidate = {value["subject_id"]: value for value in routes}
    resolution_by_candidate = _candidate_resolution_map(bundle, resolutions)
    candidate_by_id = {value["candidate_id"]: value for value in bundle["candidates"]}
    disposition_by_candidate = {value["candidate_id"]: value for value in (dispositions or [])}
    buckets: dict[int, list[dict[str, Any]]] = {0: [], 1: [], 2: []}
    processed: set[str] = set()
    for candidate in bundle["candidates"]:
        candidate_id = candidate["candidate_id"]
        if candidate_id in processed:
            continue
        member_ids, grouped_assessment = _resolve_grouped_member_ids(
            candidate_id, resolution_by_candidate[candidate_id], candidate_by_id
        )
        processed.update(member_ids)
        tier, item = _build_hitl_review_item(
            bundle, candidate_id, member_ids, grouped_assessment,
            candidate_by_id, route_by_candidate, resolution_by_candidate, disposition_by_candidate,
        )
        buckets.get(tier, buckets[0]).append(item)
    tier_one, tier_two, automatic = buckets[1], buckets[2], buckets[0]
    batches: list[dict[str, Any]] = []
    if tier_one:
        batches.append({
            "batch_id": stable_id("hitl-batch", bundle["run_id"], 1, length=24),
            "tier": 1, "review_mode": "batch", "items": tier_one,
        })
    for item in tier_two:
        batches.append({
            "batch_id": stable_id("hitl-batch", bundle["run_id"], item["review_item_id"], length=24),
            "tier": 2, "review_mode": "individual", "items": [item],
        })
    return {
        "run_id": bundle["run_id"], "automatic_items": automatic, "review_batches": batches,
        "tier_0_count": len(automatic), "tier_1_count": len(tier_one), "tier_2_count": len(tier_two),
    }

from .shift_left import build_shift_left_exception_digest, create_shift_left_hitl_decision  # noqa: F401

