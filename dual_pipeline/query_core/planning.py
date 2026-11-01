"""Query plan construction and route decision compilation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact, create_versioned_artifact
from dual_pipeline.core import normalize_space, stable_id, utc_now
from dual_pipeline.l2 import (
    L2Config,
    analyze_request_intent,
    canonical_query_type,
    determine_materiality,
    query_retrieval_policy,
    query_type_policy,
)
from dual_pipeline.query_router import QueryDestination

from .constants import GRAPH_HOPS_BY_QUERY_TYPE, _entities

_CANONICAL_CONNECTED_PREDICATES: list[str] = [
    "AFFILIATED_WITH",
    "SUPPORTED_BY",
    "ROUTES_TO",
    "CONTRIBUTES_TO",
    "HAS_ROLE_IN",
    "CONTEXT_FOR",
    "HAS_CLAIM_TEXT",
    "USES_PATTERN",
    "OWNS_OR_LEADS",
    "REPORTS_TO",
    "CONTAINS",
    "DEPENDS_ON",
    "HAS_RISK",
    "CANDIDATE_FOR",
    "GOVERNS",
    "DELIVERS",
    "TRANSITIONS_TO",
    "CONSTRAINS",
    "VALIDATES",
    "RELATED_TO",
]

_QUERY_TYPE_BLUEPRINTS: dict[str, tuple[list[str], list[str], str, str]] = {
    "opportunity_nba": (
        [
            "Which PDLC-related initiatives and capabilities exist?",
            "Which documented pain points and risks exist?",
            "Which opportunities address which pain points?",
            "What approved evidence supports each relationship?",
            "Which approval and evidence limitations affect prioritization?",
            "What assumptions or missing information affect prioritization?",
        ],
        [
            "ADDRESSES",
            "DEPENDS_ON",
            "SUPPORTED_BY",
            "HAS_OPPORTUNITY",
            *[p for p in _CANONICAL_CONNECTED_PREDICATES if p not in {"DEPENDS_ON", "SUPPORTED_BY"}],
        ],
        "opportunity_nba_v1",
        "nee-opportunity-nba-answer-1",
    ),
    "gap_risk_inconsistency": (
        [
            "Which governed gaps or risks match the request?",
            "What evidence supports or contradicts each item?",
            "What is unresolved and what would resolve it?",
        ],
        [
            "HAS_RISK",
            "CONSTRAINS",
            "CONTRADICTS",
            "DEPENDS_ON",
            *[
                p
                for p in _CANONICAL_CONNECTED_PREDICATES
                if p not in {"HAS_RISK", "CONSTRAINS", "DEPENDS_ON"}
            ],
        ],
        "gap_risk_v1",
        "nee-gap-risk-answer-1",
    ),
    "relationship_explanation": (
        [
            "Which canonical entities are involved?",
            "Which governed paths connect them?",
            "What evidence supports the relationship?",
        ],
        [
            "RELATED_TO",
            "SUPPORTED_BY",
            "DEPENDS_ON",
            *[
                p
                for p in _CANONICAL_CONNECTED_PREDICATES
                if p not in {"RELATED_TO", "SUPPORTED_BY", "DEPENDS_ON"}
            ],
        ],
        "relationship_explanation_v1",
        "nee-relationship-answer-1",
    ),
    "cross_workstream_synthesis": (
        [
            "Which workstreams match the request?",
            "What patterns recur across them?",
            "What material differences and gaps remain?",
        ],
        [
            "CONTRIBUTES_TO",
            "USES_PATTERN",
            "SUPPORTED_BY",
            "DEPENDS_ON",
            "HAS_RISK",
            "CONTAINS",
            *[
                p
                for p in _CANONICAL_CONNECTED_PREDICATES
                if p
                not in {
                    "CONTRIBUTES_TO",
                    "USES_PATTERN",
                    "SUPPORTED_BY",
                    "DEPENDS_ON",
                    "HAS_RISK",
                    "CONTAINS",
                }
            ],
        ],
        "cross_workstream_v1",
        "nee-synthesis-answer-1",
    ),
    "current_state": (
        [
            "What is the latest governed current state?",
            "Which historical or candidate records are excluded?",
            "What evidence and currentness controls support the answer?",
        ],
        [
            "SUPPORTED_BY",
            "SUPERSEDES",
            "DEPENDS_ON",
            *[p for p in _CANONICAL_CONNECTED_PREDICATES if p not in {"SUPPORTED_BY", "DEPENDS_ON"}],
        ],
        "current_state_v1",
        "nee-current-state-answer-1",
    ),
    "current_state_corroboration": (
        [
            "What is the latest governed current state?",
            "Which historical or candidate records are excluded?",
            "What evidence and currentness controls support the answer?",
        ],
        [
            "SUPPORTED_BY",
            "SUPERSEDES",
            "DEPENDS_ON",
            *[p for p in _CANONICAL_CONNECTED_PREDICATES if p not in {"SUPPORTED_BY", "DEPENDS_ON"}],
        ],
        "current_state_v1",
        "nee-current-state-answer-1",
    ),
    "project_status_current": (
        [
            "Which dated project-status snapshot is latest for the named project or program?",
            "What exact source evidence supports that snapshot and its as-of date?",
        ],
        ["SUPPORTED_BY", *[p for p in _CANONICAL_CONNECTED_PREDICATES if p != "SUPPORTED_BY"]],
        "project_status_current_v1",
        "nee-project-status-snapshot-answer-1",
    ),
    "project_status_history": (
        [
            "Which dated project-status snapshots match the requested historical period?",
            "What exact source evidence supports each snapshot and its as-of date?",
        ],
        ["SUPPORTED_BY", *[p for p in _CANONICAL_CONNECTED_PREDICATES if p != "SUPPORTED_BY"]],
        "project_status_history_v1",
        "nee-project-status-snapshot-answer-1",
    ),
    "candidate_promotion_review": (
        [
            "Which candidate assertions are within the bounded review request?",
            "Which approved records corroborate or contradict each candidate?",
            "Which proposal-only disposition and HITL obligations remain?",
        ],
        [
            "SUPPORTED_BY",
            "CONTRADICTS",
            "RELATED_TO",
            *[p for p in _CANONICAL_CONNECTED_PREDICATES if p not in {"SUPPORTED_BY", "RELATED_TO"}],
        ],
        "candidate_promotion_review_v1",
        "nee-candidate-promotion-review-answer-1",
    ),
}

_DEFAULT_BLUEPRINT: tuple[list[str], list[str], str, str] = (
    [
        "Which exact governed records match the request?",
        "What evidence and source locators support them?",
    ],
    [
        "SUPPORTED_BY",
        "RELATED_TO",
        *[p for p in _CANONICAL_CONNECTED_PREDICATES if p not in {"SUPPORTED_BY", "RELATED_TO"}],
    ],
    "exact_lookup_v1",
    "nee-exact-answer-1",
)


def _resolve_query_type_blueprint(query_type: str) -> tuple[list[str], list[str], str, str]:
    blueprint = _QUERY_TYPE_BLUEPRINTS.get(query_type, _DEFAULT_BLUEPRINT)
    return list(blueprint[0]), list(blueprint[1]), blueprint[2], blueprint[3]


def _resolve_call_limits(
    graph_utility: bool,
    assurance_profile: str | None,
    l2_config: L2Config,
) -> tuple[int, int, int, int]:
    if graph_utility:
        if assurance_profile is not None:
            raise ValueError("graph evidence utility cannot select an assurance profile")
        return 0, 0, 0, 0

    if assurance_profile not in {"standard", "enhanced"}:
        raise ValueError("NEE decision intelligence requires an assurance profile")

    model_policy = (
        l2_config.standard
        if assurance_profile == "standard"
        else l2_config.enhanced
    )
    is_enhanced = assurance_profile == "enhanced"
    return (
        model_policy.maximum_primary_calls_per_run,
        1 if is_enhanced else 0,
        1 if is_enhanced else 0,
        2 if is_enhanced else 1,
    )


def _resolve_evidence_requirements(query_type: str, retrieval_mode: str) -> list[str]:
    if query_type in {"project_status_current", "project_status_history"}:
        return [
            "Dated project-status source snapshots with an explicit as-of date",
            "Citation-only source evidence; no project status is promoted to a durable fact",
        ]

    mode_requirement = {
        "candidate_review": "Explicit candidate labels and proposal-only handling for unverified material",
        "strategic": (
            "Role-separated intent, principles, options, constraints, outcomes, "
            "candidate hypotheses, and external analogies without factual-status leakage"
        ),
        "guidance": "Approved guidance must be explicitly labeled and cannot support a factual claim",
    }.get(
        retrieval_mode,
        "Candidate material is excluded unless an explicit candidate review route is selected",
    )

    return [
        "Approved internal facts for current-state statements",
        mode_requirement,
        "Resolvable evidence and source locators for material claims",
    ]


def _resolve_time_horizon(query_type: str) -> str:
    if query_type == "project_status_current":
        return "latest dated status snapshot only"
    if query_type == "project_status_history":
        return "explicitly requested dated status history"
    return "current governed state"


def _split_multi_subject_clauses(question: str) -> list[str]:
    """Split compound questions with coordinating conjunctions into independent clauses."""
    raw_clauses = re.split(
        r"(?:\s+and\s+|\s*;\s*|\s+vs\.?\s+|\s+versus\s+|\s+while\s+)",
        question,
        flags=re.IGNORECASE,
    )
    clauses = [normalize_space(c) for c in raw_clauses if len(normalize_space(c)) >= 3]
    return clauses if len(clauses) > 1 else []


def _build_plan_extensions(
    *,
    query_type: str,
    relationships: list[str],
    subquestions: list[str],
    card_scope: dict[str, Any] | None,
    decision_frame: dict[str, Any] | None,
    materiality: str,
    evidence_requirements: list[str],
) -> dict[str, Any]:
    graph_hops = GRAPH_HOPS_BY_QUERY_TYPE[query_type]
    extensions: dict[str, Any] = {
        "com.partner_advisory.nee.navigator": {
            "enabled": bool(graph_hops > 0),
            "max_steps": min(3, max(1, graph_hops)) if graph_hops > 0 else 1,
            "max_tool_calls": 6,
            "bridge_predicates": list(relationships),
            "subquestion_count": len(subquestions),
        }
    }
    if card_scope is not None:
        extensions["com.partner_advisory.nee.card-scope"] = {
            "card_id": card_scope["card_id"],
            "card_view_id": card_scope["card_view_id"],
            "view_version": card_scope["view_version"],
            "selector_digest": card_scope["selector_digest"],
            "membership_digest": card_scope["membership_digest"],
        }
    if decision_frame is not None:
        from dual_pipeline.l0.decision_support_contracts import (
            compile_frame_evidence_requirements,
        )

        frame_requirements = compile_frame_evidence_requirements(
            decision_frame, default_materiality=materiality
        )
        for freq in frame_requirements:
            f_label = freq.get("requirement_label")
            if f_label and f_label not in evidence_requirements:
                evidence_requirements.append(f_label)
        extensions["com.partner_advisory.nee.decision-frame"] = {
            "frame_digest": decision_frame.get("frame_digest"),
            "requirements": frame_requirements,
        }
    return extensions


def _merge_unique_entity_names(
    dynamic_matches: list[dict[str, Any]], static_names: list[str]
) -> list[str]:
    combined: list[str] = []
    seen_cf: set[str] = set()
    raw_names = [m.get("canonical_name") for m in dynamic_matches] + static_names
    for name in raw_names:
        if name and name.casefold() not in seen_cf:
            seen_cf.add(name.casefold())
            combined.append(name)
    return combined


def _resolve_plan_primary_entities(question: str, graph_path: Path | str | None) -> list[str]:
    if graph_path:
        try:
            from dual_pipeline.core import connect_sqlite
            from .dynamic_entities import resolve_dynamic_entity_matches

            conn = connect_sqlite(Path(graph_path), readonly=True)
            try:
                dynamic_matches = resolve_dynamic_entity_matches(conn, question, authorized_nodes=None)
            finally:
                conn.close()
            merged = _merge_unique_entity_names(
                dynamic_matches, _entities(question, default_to_nee=False)
            )
            if merged:
                return merged
        except Exception:
            pass
    return _entities(question)


def build_query_plan(
    *,
    question: str,
    query_type: str,
    request_id: str,
    run_id: str,
    current_trace_id: str,
    l2_config: L2Config,
    provider_egress_allowed: bool,
    destination_decision: dict[str, Any],
    assurance_profile: str | None,
    requested_materiality: str | None = None,
    card_scope: dict[str, Any] | None = None,
    decision_frame: dict[str, Any] | None = None,
    graph_path: Path | str | None = None,
) -> dict[str, Any]:
    query_type = canonical_query_type(query_type)
    if query_type == "exact_lookup" and analyze_request_intent(question).semantic_synthesis_requested:
        raise ValueError("exact_lookup is incompatible with a request for semantic synthesis or rationale")
    query_type_policy(query_type)
    retrieval_mode, allowed_lanes = query_retrieval_policy(query_type)
    materiality = determine_materiality(question, query_type, requested_materiality=requested_materiality)
    subquestions, relationships, prompt, output_schema = _resolve_query_type_blueprint(query_type)
    normalized_q = question.strip()
    if normalized_q and normalized_q not in subquestions:
        subquestions = [normalized_q] + subquestions
    for clause in reversed(_split_multi_subject_clauses(question)):
        if clause not in subquestions:
            subquestions.insert(1, clause)

    destination = str(destination_decision["destination"])
    graph_utility = destination == QueryDestination.GRAPH_EVIDENCE_UTILITY.value
    maximum_primary_calls, maximum_repair_calls, maximum_challenger_calls, maximum_judge_calls = _resolve_call_limits(
        graph_utility, assurance_profile, l2_config
    )
    evidence_requirements = _resolve_evidence_requirements(query_type, retrieval_mode)
    extensions = _build_plan_extensions(
        query_type=query_type, relationships=relationships, subquestions=subquestions,
        card_scope=card_scope, decision_frame=decision_frame, materiality=materiality,
        evidence_requirements=evidence_requirements,
    )
    primary_entities = _resolve_plan_primary_entities(question, graph_path)
    artifact_id = stable_id("query-plan", run_id, length=26)
    return create_versioned_artifact(
        "query-plan",
        "2.0.0",
        artifact_id,
        plan_id=artifact_id,
        request_id=request_id,
        run_id=run_id,
        trace_id=current_trace_id,
        destination_decision_digest=destination_decision["canonical_digest"]["value"],
        destination=destination,
        query_type=query_type,
        materiality=materiality,
        assurance_profile=assurance_profile,
        delivery_profile=destination_decision["delivery_profile"],
        artifact_type=destination_decision["artifact_type"],
        l2_policy="forbidden" if graph_utility else "required",
        l2_policy_reason=(
            "Bounded graph evidence is provider-free."
            if graph_utility
            else "A material graph-dependent decision requires governed synthesis."
        ),
        allowed_provider_class=None if graph_utility else "openai",
        maximum_primary_calls=maximum_primary_calls,
        maximum_repair_calls=maximum_repair_calls,
        maximum_challenger_calls=maximum_challenger_calls,
        maximum_judge_calls=maximum_judge_calls,
        required_output_schema="nee-governed-answer-1.0.0",
        provider_egress_allowed=False if graph_utility else provider_egress_allowed,
        primary_entities=primary_entities,
        time_horizon=_resolve_time_horizon(query_type),
        subquestions=subquestions,
        relationships_to_inspect=relationships,
        evidence_requirements=evidence_requirements,
        retrieval_mode=retrieval_mode,
        allowed_lanes=list(allowed_lanes),
        retrieval={
            "exact_anchors": True,
            "semantic": True,
            "graph_hops": GRAPH_HOPS_BY_QUERY_TYPE[query_type],
            "lane_blending": False,
            "bm25": True,
            "typed_path_expansion": (
                GRAPH_HOPS_BY_QUERY_TYPE[query_type] > 0
            ),
            "connected_evidence_selection": True,
        },
        external_research="denied",
        prompt_blueprint=prompt,
        output_schema=output_schema,
        exit_criteria=[
            "Requirement-specific retrieval coverage is complete",
            "Runtime X1 judge passes when required",
            "Delivery-profile release gates pass",
        ],
        extensions=extensions,
    )


def build_query_route(plan: dict[str, Any]) -> dict[str, Any]:
    route = {
        "exact_lookup": "EXACT_LOOKUP",
        "current_state": "CURRENT_STATE_CORROBORATION",
        "current_state_corroboration": "CURRENT_STATE_CORROBORATION",
        "project_status_current": "PROJECT_STATUS_CURRENT",
        "project_status_history": "PROJECT_STATUS_HISTORY",
        "relationship_explanation": "RELATIONSHIP_EXPLANATION",
        "cross_workstream_synthesis": "CROSS_WORKSTREAM_SYNTHESIS",
        "gap_risk_inconsistency": "GAP_RISK_INCONSISTENCY",
        "opportunity_nba": "OPPORTUNITY_NBA",
        "candidate_promotion_review": "CANDIDATE_PROMOTION_REVIEW",
    }[plan["query_type"]]
    artifact_id = stable_id("route-decision", plan["run_id"], route, length=26)
    return create_artifact(
        "route-decision",
        artifact_id,
        decision_id=artifact_id,
        run_id=plan["run_id"],
        trace_id=plan["trace_id"],
        use_case="query",
        subject_id=plan["request_id"],
        intake_route=None,
        query_route=route,
        allowed_lanes=plan["allowed_lanes"],
        materiality=plan["materiality"],
        rule_id=f"query-route-{plan['query_type'].replace('_', '-')}",
        rule_version="1.0.0",
        reason_codes=[f"explicit deterministic {plan['query_type']} intent classification"],
        model_recommendation=None,
        decided_at=utc_now(),
        extensions={},
    )


