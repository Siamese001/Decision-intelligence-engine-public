"""Deterministic L2 decision compilation and routing."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, Mapping

from dual_pipeline.core import canonical_json, digest, stable_id
from dual_pipeline.l2_policy import (
    CallPurpose,
    L2Config,
    L2ExecutionDecision,
    L2Policy,
    L2ReasonCode,
    ModelPolicy,
    RequestIntent,
    analyze_request_intent,
    canonical_query_type,
    determine_materiality,
    external_provider_offline,
    query_model_policy_for_assurance,
    query_retrieval_policy,
    query_type_policy,
    resolve_openai_model_id,
)
from dual_pipeline.provider_request import (
    measure_responses_request,
    provider_request_budget_verdict,
)

from .payload import build_l2_provider_body
from .types import L2ProviderRequest

_ROUTE_REASON: Mapping[str, L2ReasonCode] = MappingProxyType(
    {
        "relationship_explanation": L2ReasonCode.RELATIONSHIP_EXPLANATION_REQUIRED,
        "cross_workstream_synthesis": L2ReasonCode.CROSS_WORKSTREAM_SYNTHESIS_REQUIRED,
        "gap_risk_inconsistency": L2ReasonCode.GAP_OR_CONTRADICTION_REASONING_REQUIRED,
        "opportunity_nba": L2ReasonCode.OPPORTUNITY_RECOMMENDATION_REQUIRED,
        "candidate_promotion_review": L2ReasonCode.CANDIDATE_REVIEW_REASONING_REQUIRED,
    }
)

_EXPECTED_ROUTE: Mapping[str, str] = MappingProxyType(
    {
        "exact_lookup": "EXACT_LOOKUP",
        "current_state_corroboration": "CURRENT_STATE_CORROBORATION",
        "project_status_current": "PROJECT_STATUS_CURRENT",
        "project_status_history": "PROJECT_STATUS_HISTORY",
        "relationship_explanation": "RELATIONSHIP_EXPLANATION",
        "cross_workstream_synthesis": "CROSS_WORKSTREAM_SYNTHESIS",
        "gap_risk_inconsistency": "GAP_RISK_INCONSISTENCY",
        "opportunity_nba": "OPPORTUNITY_NBA",
        "candidate_promotion_review": "CANDIDATE_PROMOTION_REVIEW",
    }
)


def _direct_approved_citations_available(packet: Mapping[str, Any]) -> bool:
    approved = packet.get("approved", [])
    if not isinstance(approved, list) or not approved:
        return False
    for item in approved:
        if not isinstance(item, Mapping):
            continue
        record = item.get("record")
        if isinstance(record, Mapping) and isinstance(record.get("record_id"), str):
            return True
    return False


def _intent_reason(intent: RequestIntent) -> L2ReasonCode | None:
    if intent.defense_or_rationale:
        return L2ReasonCode.USER_REQUESTED_DEFENSE_OR_RATIONALE
    if intent.prioritization:
        return L2ReasonCode.USER_REQUESTED_PRIORITIZATION
    if intent.recommendation:
        return L2ReasonCode.USER_REQUESTED_RECOMMENDATION
    if intent.comparison:
        return L2ReasonCode.USER_REQUESTED_COMPARISON
    if intent.explanation:
        return L2ReasonCode.USER_REQUESTED_EXPLANATION
    return None


def _artifact_digest(value: Mapping[str, Any]) -> str:
    canonical = value.get("canonical_digest")
    if not isinstance(canonical, Mapping) or not isinstance(canonical.get("value"), str):
        raise ValueError("governed artifact is missing its canonical digest")
    actual = digest({key: item for key, item in value.items() if key != "canonical_digest"})
    if canonical["value"] != actual:
        raise ValueError("governed artifact canonical digest does not match its content")
    return actual


def _verified_evidence_packet_hash(value: Mapping[str, Any]) -> str:
    declared = value.get("packet_digest")
    if not isinstance(declared, str) or not re.fullmatch(r"[a-f0-9]{64}", declared):
        raise ValueError("sealed evidence packet is missing its SHA-256 digest")
    actual = digest({key: item for key, item in value.items() if key != "packet_digest"})
    if declared != actual:
        raise ValueError("sealed evidence packet digest does not match its content")
    return actual


def decide_l2_execution(
    *,
    validated_request: Mapping[str, Any],
    plan: Mapping[str, Any],
    route: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    config: L2Config,
    prompt: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> L2ExecutionDecision:
    """Make the deterministic post-C0 L2 decision without constructing a provider."""

    query_type = canonical_query_type(str(plan["query_type"]))
    expected_retrieval_mode, expected_lanes = query_retrieval_policy(query_type)
    if (
        str(validated_request.get("run_id", "")) != str(plan.get("run_id", ""))
        or str(validated_request.get("trace_id", "")) != str(plan.get("trace_id", ""))
        or str(validated_request.get("request_id", "")) != str(plan.get("request_id", ""))
        or str(route.get("run_id", "")) != str(plan.get("run_id", ""))
        or str(route.get("trace_id", "")) != str(plan.get("trace_id", ""))
        or str(route.get("subject_id", "")) != str(plan.get("request_id", ""))
        or str(route.get("query_route", "")) != _EXPECTED_ROUTE[query_type]
        or str(route.get("materiality", "")) != str(plan.get("materiality", ""))
        or str(validated_request.get("retrieval_mode", "")) != expected_retrieval_mode
        or tuple(validated_request.get("allowed_lanes", [])) != expected_lanes
        or str(plan.get("retrieval_mode", "")) != expected_retrieval_mode
        or tuple(plan.get("allowed_lanes", [])) != expected_lanes
        or tuple(route.get("allowed_lanes", [])) != expected_lanes
    ):
        raise ValueError(
            "validated request, L1 plan, and L0 route bindings are inconsistent"
        )
    query_type_policy(query_type)
    policy = L2Policy(str(plan["l2_policy"]))
    assurance_profile = plan.get("assurance_profile")
    if policy is L2Policy.FORBIDDEN:
        if assurance_profile is not None:
            raise ValueError("provider-free graph utility cannot carry query assurance")
    elif assurance_profile not in {"standard", "enhanced"}:
        raise ValueError("NEE reasoning requires standard or enhanced assurance")
    question = str(
        validated_request.get("normalized_question", validated_request.get("question", ""))
    )
    intent = analyze_request_intent(question)
    if policy is L2Policy.FORBIDDEN and intent.semantic_synthesis_requested and query_type == "exact_lookup":
        raise ValueError("L2-forbidden exact lookup cannot carry a semantic-synthesis request")
    approved = evidence_packet.get("approved", [])
    candidates = evidence_packet.get("candidate", [])
    public = evidence_packet.get("external_public", [])
    conflicts = evidence_packet.get("counterevidence", evidence_packet.get("conflicts", []))
    gaps = evidence_packet.get("gaps", [])
    graph_paths = evidence_packet.get("graph_paths", [])
    approved_count = len(approved) if isinstance(approved, list) else 0
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    public_count = len(public) if isinstance(public, list) else 0
    conflict_count = len(conflicts) if isinstance(conflicts, list) else 0
    gap_count = len(gaps) if isinstance(gaps, list) else 0
    graph_path_count = len(graph_paths) if isinstance(graph_paths, list) else 0
    if prompt is not None:
        size_probe_request = L2ProviderRequest(
            purpose=CallPurpose.PRIMARY,
            question=question,
            plan=plan,
            evidence_packet=evidence_packet,
            prompt=prompt,
        )
        sizing_policy = (
            query_model_policy_for_assurance(config, str(assurance_profile))
            if assurance_profile is not None
            else config.standard
        )
        size_probe_body = build_l2_provider_body(
            config=config,
            model_policy=sizing_policy,
            request=size_probe_request,
        )
        request_metrics = measure_responses_request(size_probe_body)
        estimated_prompt_size_chars = int(
            request_metrics["request_serialized_chars"]
        )
        prompt_size_source = "SERIALIZED_PROVIDER_REQUEST"
        budget_verdict = provider_request_budget_verdict(
            request_metrics=request_metrics,
            maximum_provider_request_chars=(
                config.request_budget.maximum_provider_request_chars
            ),
        )
    else:
        estimated_prompt_size_chars = len(
            canonical_json(
                {
                    "question": question,
                    "query_plan": plan,
                    "evidence_packet": evidence_packet,
                }
            )
        )
        prompt_size_source = "LEGACY_PACKET_PROXY"
        request_metrics = {
            "request_payload_digest": None,
            "request_input_chars": 0,
            "request_system_chars": 0,
            "request_user_chars": 0,
            "request_schema_chars": 0,
            "request_serialized_chars": estimated_prompt_size_chars,
        }
        budget_verdict = {
            "budget_version": "nee-provider-request-budget-1.0.0",
            "status": "UNMEASURED",
            "reason_code": "PROMPT_NOT_AVAILABLE_AT_L2D",
            "request_serialized_chars": estimated_prompt_size_chars,
            "maximum_provider_request_chars": (
                config.request_budget.maximum_provider_request_chars
            ),
            "remaining_chars": max(
                0,
                config.request_budget.maximum_provider_request_chars
                - estimated_prompt_size_chars,
            ),
            "request_payload_digest": None,
        }
    if (
        str(evidence_packet.get("retrieval_mode", "")) != expected_retrieval_mode
        or tuple(evidence_packet.get("allowed_lanes", [])) != expected_lanes
    ):
        raise ValueError("evidence packet retrieval mode does not match the governed route")
    disallowed_lane_counts = {
        "candidate": candidate_count,
        "external_public": public_count,
    }
    unexpected_material = {
        lane: count
        for lane, count in disallowed_lane_counts.items()
        if lane not in expected_lanes and count
    }
    if unexpected_material:
        raise ValueError(
            "evidence packet contains material from a lane disallowed by the governed route: "
            f"{unexpected_material}"
        )
    materiality = str(plan.get("materiality", determine_materiality(question, query_type)))
    if materiality not in {"low", "medium", "high"}:
        raise ValueError("query plan contains invalid materiality")
    assurance_trigger_reasons = ["DESTINATION_POLICY"] if assurance_profile else []
    if assurance_profile == "enhanced":
        if materiality == "high":
            assurance_trigger_reasons.append("HIGH_MATERIALITY")
        if conflict_count:
            assurance_trigger_reasons.append("MATERIAL_CONFLICT_PRESENT")
        if plan.get("delivery_profile") in {
            "client_technical",
            "client_executive",
            "commercial",
        }:
            assurance_trigger_reasons.append("EXTERNAL_DELIVERY")
        if plan.get("artifact_type") in {"architecture_deck", "commercial_case"}:
            assurance_trigger_reasons.append("GOVERNED_ARTIFACT")

    required = False
    selected_reason: L2ReasonCode
    if policy is L2Policy.FORBIDDEN:
        selected_reason = L2ReasonCode.EXACT_LOOKUP_DETERMINISTIC
    elif approved_count + candidate_count + public_count == 0:
        selected_reason = L2ReasonCode.NO_EVIDENCE_L2_NOT_USEFUL
    elif query_type in {"exact_lookup", "schema_validation", "structural_check"} and not intent.semantic_synthesis_requested:
        # L2D Deterministic Complexity Boundary: Block model selection for structurally simple deterministic queries
        selected_reason = L2ReasonCode.EXACT_LOOKUP_DETERMINISTIC
    else:
        required = True
        selected_reason = _intent_reason(intent) or _ROUTE_REASON[query_type]

    offline = external_provider_offline(environment)
    provider_egress_allowed = bool(
        config.provider_egress_allowed and plan.get("provider_egress_allowed", True) and not offline
    )
    if required and not provider_egress_allowed:
        selected_reason = (
            L2ReasonCode.OFFLINE_EXTERNAL_PROVIDER_FORBIDDEN
            if offline
            else L2ReasonCode.PROVIDER_EGRESS_NOT_AUTHORIZED
        )

    model_policy: ModelPolicy | None = None
    selected_model: str | None = None
    if required:
        model_policy = query_model_policy_for_assurance(config, str(assurance_profile))
        selected_model = resolve_openai_model_id(model_policy)
        if prompt is not None:
            selected_request = L2ProviderRequest(
                purpose=CallPurpose.PRIMARY,
                question=question,
                plan=plan,
                evidence_packet=evidence_packet,
                prompt=prompt,
            )
            request_metrics = measure_responses_request(
                build_l2_provider_body(
                    config=config,
                    model_policy=model_policy,
                    request=selected_request,
                )
            )
            estimated_prompt_size_chars = int(
                request_metrics["request_serialized_chars"]
            )
            budget_verdict = provider_request_budget_verdict(
                request_metrics=request_metrics,
                maximum_provider_request_chars=(
                    config.request_budget.maximum_provider_request_chars
                ),
            )

    if model_policy is None:
        primary_cap = repair_cap = escalation_cap = 0
    else:
        primary_cap = min(
            int(plan.get("maximum_primary_calls", model_policy.maximum_primary_calls_per_run)),
            model_policy.maximum_primary_calls_per_run,
        )
        repair_cap = min(
            int(plan.get("maximum_repair_calls", model_policy.maximum_repair_calls_per_run)),
            model_policy.maximum_repair_calls_per_run,
        )
        escalation_cap = min(
            int(
                plan.get(
                    "maximum_escalation_calls",
                    model_policy.maximum_escalation_calls_per_run,
                )
            ),
            model_policy.maximum_escalation_calls_per_run,
        )
        if min(primary_cap, repair_cap, escalation_cap) < 0:
            raise ValueError("L2 call caps cannot be negative")

    bindings = {
        "run_id": str(plan["run_id"]),
        "trace_id": str(plan["trace_id"]),
        "validated_request_digest": digest(validated_request),
        "query_plan_digest": _artifact_digest(plan),
        "route_decision_digest": _artifact_digest(route),
        "evidence_packet_hash": _verified_evidence_packet_hash(evidence_packet),
        "policy_config_digest": config.config_digest,
    }
    decision_payload = bindings | {
        "required": required,
        "policy": policy.value,
        "reason_code": selected_reason.value,
        "query_type": query_type,
        "route": str(route.get("query_route", route.get("route", ""))),
        "materiality": materiality,
        "assurance_profile": assurance_profile,
        "assurance_trigger_reasons": tuple(assurance_trigger_reasons),
        "selected_model_profile": assurance_profile if required else None,
        "selected_model": selected_model,
        "approved_evidence_count": approved_count,
        "candidate_evidence_count": candidate_count,
        "external_public_evidence_count": public_count,
        "conflict_count": conflict_count,
        "gap_count": gap_count,
        "graph_path_count": graph_path_count,
        "estimated_prompt_size_chars": estimated_prompt_size_chars,
        "prompt_size_source": prompt_size_source,
        "request_metrics": request_metrics,
        "budget_verdict": budget_verdict,
        "structured_output_required": config.output.structured_output_required,
        "provider_egress_allowed": provider_egress_allowed,
        "maximum_primary_calls": primary_cap,
        "maximum_repair_calls": repair_cap,
        "maximum_escalation_calls": escalation_cap,
    }
    decision_id = stable_id("l2-execution-decision", decision_payload, length=28)
    if required:
        from dual_pipeline.l2_providers_core.sentinel import authorize_provider_instantiation
        authorize_provider_instantiation()
    return L2ExecutionDecision(decision_id=decision_id, **decision_payload)
