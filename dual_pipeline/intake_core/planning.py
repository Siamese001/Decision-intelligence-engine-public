"""Ingestion planning phase."""

from __future__ import annotations

from typing import Any

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import stable_id


def _is_role_change(candidate: dict[str, Any], candidate_type: str) -> bool:
    return bool(
        candidate.get("has_role_change")
        or candidate.get("has_role_resolution")
        or candidate.get("assertion_type") in ("speaker_role", "role_assignment")
        or candidate_type in ("role_assertion", "role_change")
        or (
            candidate_type == "architecture_relationship"
            and (
                " HAS_ROLE_IN " in str(candidate.get("normalized_text", ""))
                or " HAS_ROLE " in str(candidate.get("normalized_text", ""))
            )
        )
    )


_STANDARD_OPERATIONS: dict[str, tuple[str, str, str]] = {
    "entity_mention": ("none", "none", "none"),
    "architecture_component": ("create_entity", "architecture_component", "candidate"),
    "architecture_relationship": ("add_relationship", "architecture_relationship", "candidate"),
    "action": ("add_action", "action_item", "candidate"),
    "open_question": ("none", "none", "none"),
}


def _resolve_candidate_operation(candidate: dict[str, Any], candidate_type: str) -> tuple[str, str, str]:
    if _is_role_change(candidate, candidate_type):
        return "add_assertion", "assertion", "candidate"
    return _STANDARD_OPERATIONS.get(candidate_type, ("add_assertion", "assertion", "candidate"))


def _build_planned_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate_type = candidate["candidate_type"]
    is_role_change = _is_role_change(candidate, candidate_type)
    expected_operation, family, lane = _resolve_candidate_operation(candidate, candidate_type)
    external_validation = (
        "allowed_public_context"
        if candidate["classification"] == "public"
        else "not_authoritative_for_internal_fact"
    )
    source_authority = (
        "Governed internal evidence or explicit authorized confirmation"
        if candidate["classification"] != "public"
        else "Official first-party or governed external-public evidence"
    )
    dispositions = (
        ["QUARANTINE", "REJECTED_NOOP"]
        if candidate_type == "open_question"
        else ["APPROVED", "CANDIDATE", "QUARANTINE", "REJECTED_NOOP"]
    )
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_type": candidate_type,
        "materiality": candidate["materiality"],
        "entities_requiring_resolution": list(
            dict.fromkeys(
                mention["reported_text"]
                for mention in candidate["entity_mentions"]
                if mention.get("reported_text")
            )
        ),
        "exact_graph_lookups": [
            "exact_assertion_lookup",
            "same_subject_predicate_lookup",
            "current_state_lookup",
        ],
        "semantic_comparison_required": (
            candidate_type not in {"entity_mention", "open_question"}
            or is_role_change
        ),
        "contradiction_checks": [
            "semantic_duplicate_lookup",
            "contradiction_lookup",
        ],
        "external_validation": external_validation,
        "source_authority_requirement": source_authority,
        "expected_graph_operation": expected_operation,
        "expected_evidence_lane": lane,
        "expected_retrieval_record_family": family,
        "expected_dispositions": dispositions,
        "hitl_triggers": [
            "ambiguous identity",
            "material contradiction",
            "insufficient source authority",
        ],
        "required_exit_gates": ["X1", "X2", "X3"],
    }


def build_ingestion_plan(bundle: dict[str, Any]) -> dict[str, Any]:
    candidates = [_build_planned_candidate(c) for c in bundle["candidates"]]
    artifact_id = stable_id("ingestion-plan", bundle["run_id"], length=26)
    return create_artifact(
        "ingestion-plan",
        artifact_id,
        plan_id=artifact_id,
        run_id=bundle["run_id"],
        trace_id=bundle["trace_id"],
        problem="Evaluate every source-bound candidate without allowing L1 to approve, write, or persist an embedding.",
        candidates=candidates,
        allowed_tools=[
            "graph_schema_lookup",
            "entity_catalog_lookup",
            "alias_lookup",
            "relationship_vocabulary_lookup",
            "similar_record_statistics",
            "source_authority_policy",
        ],
        exit_conditions=[
            "Every candidate has one X3 disposition",
            "Every entity mention has one resolution outcome",
            "No graph or index mutation occurs during planning",
        ],
        proposal_only=True,
        extensions={},
    )
