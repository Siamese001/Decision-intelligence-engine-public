"""Provider-free assurance selection and v2 query artifact projections."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .contracts import create_versioned_artifact


def select_assurance_profile(
    *,
    destination: str,
    query_type: str,
    materiality: str,
    delivery_profile: str,
    artifact_type: str,
    question: str,
) -> str | None:
    if destination != "NEE_DECISION_INTELLIGENCE":
        return None
    enhanced = bool(
        materiality == "high"
        or delivery_profile in {"client_technical", "client_executive", "commercial"}
        or artifact_type in {"architecture_deck", "commercial_case"}
        or query_type == "candidate_promotion_review"
        or re.search(
            r"\b(?:architecture|investment|sign[- ]off|governance|conflict|budget|staffing|(?:resource|team) allocation|client-facing)\b",
            question,
            re.IGNORECASE,
        )
    )
    return "enhanced" if enhanced else "standard"


def create_l2_decision_artifact(
    *,
    decision: Any,
    destination_decision: Mapping[str, Any],
    query_plan: Mapping[str, Any],
    route_decision: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    l1_terminal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    destination = str(destination_decision["destination"])
    assurance = query_plan["assurance_profile"]
    incomplete = any(
        row["outcome"] not in {"covered", "excluded"}
        for row in evidence_report["requirement_outcomes"]
    )
    uncovered = [
        row["requirement_id"]
        for row in evidence_report["requirement_outcomes"]
        if row["outcome"] not in {"covered", "excluded"}
    ]
    graph_utility = destination == "GRAPH_EVIDENCE_UTILITY"
    required = bool(not graph_utility and not incomplete)
    reason_code = (
        "GRAPH_EVIDENCE_UTILITY" if graph_utility
        else "RETRIEVAL_COVERAGE_INSUFFICIENT" if incomplete
        else "NEE_REASONING_REQUIRED" if decision.provider_egress_allowed
        else "PROVIDER_EGRESS_NOT_AUTHORIZED"
    )
    selected_model = decision.selected_model if required else None
    enhanced = assurance == "enhanced"
    return create_versioned_artifact(
        "l2-execution-decision", "2.0.0", decision.decision_id,
        decision_id=decision.decision_id,
        run_id=decision.run_id,
        trace_id=decision.trace_id,
        validated_request_digest=decision.validated_request_digest,
        destination_decision_digest=destination_decision["canonical_digest"]["value"],
        query_plan_digest=query_plan["canonical_digest"]["value"],
        route_decision_digest=route_decision["canonical_digest"]["value"],
        coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"],
        evidence_report_digest=evidence_report["canonical_digest"]["value"],
        evidence_packet_hash=decision.evidence_packet_hash,
        policy_config_digest=decision.policy_config_digest,
        required=required,
        reason_code=reason_code,
        destination=destination,
        query_type=decision.query_type,
        route=decision.route,
        materiality=decision.materiality,
        assurance_profile=assurance,
        delivery_profile=query_plan["delivery_profile"],
        artifact_type=query_plan["artifact_type"],
        selected_model_profile=assurance if required else None,
        selected_model=selected_model,
        coverage_status="INCOMPLETE" if incomplete else "COMPLETE",
        uncovered_requirement_ids=uncovered,
        approved_evidence_count=decision.approved_evidence_count,
        candidate_evidence_count=decision.candidate_evidence_count,
        external_public_evidence_count=decision.external_public_evidence_count,
        conflict_count=decision.conflict_count,
        gap_count=decision.gap_count,
        graph_path_count=decision.graph_path_count,
        estimated_request_chars=decision.request_metrics["request_serialized_chars"],
        request_budget_status=(
            "PASS" if decision.budget_verdict["status"] == "ALLOW" else "BLOCK"
        ),
        provider_egress_allowed=bool(required and decision.provider_egress_allowed),
        maximum_primary_calls=0 if graph_utility or incomplete else 1,
        maximum_repair_calls=1 if required and enhanced else 0,
        maximum_challenger_calls=1 if required and enhanced else 0,
        maximum_judge_calls=2 if required and enhanced else (1 if required else 0),
        extensions=(
            {}
            if l1_terminal is None
            else {
                "com.partner_advisory.nee.l1-controller": {
                    "terminal_digest": l1_terminal["canonical_digest"]["value"],
                    "terminal_reason": l1_terminal["terminal_reason"],
                    "coverage_complete": l1_terminal["coverage_complete"],
                }
            }
        ),
    )
