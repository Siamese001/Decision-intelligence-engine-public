"""Query execution state and QueryEngine lifecycle coordinator."""

from __future__ import annotations

import shutil
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from config.credentials import CredentialManager
from dual_pipeline.assurance import (
    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    INTERNAL_OPERATIONAL,
    load_internal_operational_policy,
    pair_assurance_tier,
    require_internal_operational_use,
)
from dual_pipeline.assurance import (
    policy_digest as internal_operational_policy_digest,
)
from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.card_views import resolve_authorized_card_scope
from dual_pipeline.core import (
    assert_safe_mutable_path,
    connect_sqlite,
    digest,
    is_link_or_reparse,
    read_json,
    sha256_bytes,
    stable_id,
    trace_id,
    utc_now,
    write_json,
)
from dual_pipeline.drift_guard import (
    DriftError,
    assert_replay_binding,
    assert_runtime_binding_unchanged,
    capture_runtime_binding,
)
from dual_pipeline.evidence_contract import create_final_evidence_contract
from dual_pipeline.exceptions import RetrievalCoverageInsufficientError
from dual_pipeline.graph_retrieval import expand_connected_retrieval
from dual_pipeline.hash_cache import cached_sha256_file
from dual_pipeline.index import (
    HYBRID_RETRIEVAL_POLICY_DIGEST,
    EmbeddingBackend,
    get_global_retriever_cache,
)
from dual_pipeline.l1_control import (
    BudgetState,
    L1ControlPolicy,
    create_action,
    load_policy,
)
from dual_pipeline.l1_deterministic_checker import run_l1_deterministic_checks
from dual_pipeline.l1_feedback_loop import L1FeedbackStateMachine, LoopState
from dual_pipeline.l1_review_contracts import (
    DeterministicReasonCode,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.l1_semantic_evaluator import L1SemanticSufficiencyEvaluator
from dual_pipeline.l2 import (
    DECISION_OUTPUT_CONTRACT_DIGEST,
    CallPurpose,
    L2Config,
    L2Disposition,
    ReasoningProviderFactory,
    canonical_query_type,
    decide_l2_execution,
    determine_materiality,
    execute_conditional_l2,
    execute_content_repair,
    external_provider_offline,
    load_l2_config,
    query_retrieval_policy,
    query_type_policy,
)
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.prompt_assembly import compile_prompt_artifact
from dual_pipeline.query_assurance import (
    create_l2_decision_artifact,
    select_assurance_profile,
)
from dual_pipeline.query_eligibility import assess_query_graph_fit
from dual_pipeline.query_l1_actions import (
    execute_l1_action,
    write_controlled_retrieval_artifact,
)
from dual_pipeline.query_l1_controller import (
    bounded_telemetry_payload,
    build_bounded_terminal,
    build_complete_first_pass_terminal,
    build_observation_one,
    feedback_eligible,
    merge_retrieval_results,
)
from dual_pipeline.query_l1_shadow import (
    build_attempt_zero,
    build_observation_zero,
)
from dual_pipeline.query_planning import (
    compile_query_plan_candidate,
    compile_requirement_query,
)
from dual_pipeline.query_result import QueryResult
from dual_pipeline.query_router import (
    DestinationDecision,
    QueryDestination,
    QueryRoutingPolicy,
    load_query_routing_policy,
    route_query,
)
from dual_pipeline.query_u0 import QueryU0Validator
from dual_pipeline.release import ReleaseManager
from dual_pipeline.sealed_l2 import seal_l2_output
from dual_pipeline.semantic_cache import SemanticCache, SemanticCacheLookup
from dual_pipeline.strategic_reasoning import (
    build_strategic_channels,
    create_strategic_evidence_artifact,
    create_strategic_reasoning_output,
)
from dual_pipeline.telemetry import RunRecorder
from dual_pipeline.x1_quality import (
    QualityProviderFactory,
    QualityReviewStatus,
    QualityRole,
    QualityVerdict,
    X1QualityConfig,
    X1QualityMode,
    load_x1_quality_config,
    run_quality_review,
)

from .artifact_writer import (
    DeferredArtifactWriter,
    mirror_artifacts_to_output_dir,
)
from .cache import (
    _cached_query_output,
    _correlation,
    _delivery_evidence_ready,
    _emit_l1_shadow_receipts,
    _query_cache_context,
    _rebind_cached_retrieval_artifacts,
)
from .constants import (
    _claim_new_directory,
)
from .coverage import (
    build_claim_evidence_map,
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    coverage_complete,
    coverage_supplemental_retrieval,
    filter_packet_for_l2,
    render_retrieval_evidence_markdown,
)
from .hydration import (
    build_evidence_packet,
    exact_retrieval,
    hydrate_results,
)
from .planning import (
    build_query_plan,
    build_query_route,
)
from .quality_review_stage import (
    build_and_evaluate_deterministic_x1,
    execute_challenger_and_concurrent_judge,
    resolve_initial_judge_review,
)
from .reasoning import (
    assemble_prompt,
    build_x1_gate,
    evidence_only_over_packet,
    reason_over_packet,
    render_markdown,
)
from .routed_request import _finish_routed_request


def _finish_pre_l2_blocked(
    *,
    manager: ReleaseManager,
    backend: EmbeddingBackend,
    decision: DestinationDecision,
    decision_artifact: dict[str, Any],
    graph_fit_decision: dict[str, Any],
    plan: dict[str, Any],
    retrieval: dict[str, Any],
    graph_retrieval: dict[str, Any],
    route_decision: dict[str, Any],
    coverage_manifest: dict[str, Any],
    retrieval_evidence_report: dict[str, Any],
    packet: dict[str, Any],
    connected_evidence_map: dict[str, Any],
    semantic_cache: SemanticCache,
    cache_binding: dict[str, Any],
    cache_lookup: SemanticCacheLookup,
    question: str,
    quality_config: X1QualityConfig,
    assurance_profile: str | None,
    recorder: RunRecorder,
    request_id: str,
    run_id: str,
    current_trace_id: str,
    active: dict[str, Any],
    graph_path: Path,
    graph_hash_before: str,
    runtime_binding: dict[str, Any],
    runtime_policy_digests: Mapping[str, str | None],
    summary_path: Path,
    run_dir: Path,
    output_dir: Path | None,
    l1_controller_mode: str = "off",
    l1_control_policy: L1ControlPolicy | None = None,
    l1_terminal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed after retrieval when graph fit or material coverage is insufficient."""

    is_graph_fit_failure = graph_fit_decision["status"] == "FAIL"
    reason = (
        "RETRIEVAL_GRAPH_FIT_INSUFFICIENT"
        if is_graph_fit_failure
        else "RETRIEVAL_COVERAGE_INSUFFICIENT"
    )
    correlation = _correlation(request_id)
    route = route_decision["query_route"]
    recorder.record(
        stage="C0.3",
        event_type="pipeline_event",
        payload=graph_fit_decision,
        correlation=correlation,
        route=route,
        status="BLOCKED",
    )
    recorder.record(
        stage="L2D",
        event_type="l2_execution_decision",
        payload={
            "required": False,
            "reason_code": reason,
            "graph_fit_decision_digest": graph_fit_decision["canonical_digest"]["value"],
            "provider_call_count": 0,
        },
        correlation=correlation,
        route=route,
        status="BLOCKED",
    )
    x1 = {
        "status": "FAIL",
        "coverage_complete": coverage_complete(retrieval_evidence_report),
        "graph_fit_status": graph_fit_decision["status"],
        "reason_codes": (
            list(graph_fit_decision["reason_codes"])
            if is_graph_fit_failure
            else ["RETRIEVAL_COVERAGE_INSUFFICIENT"]
        ),
    }
    x2 = {
        "status": "FAIL",
        "decision_ready": False,
        "evidence_ready": False,
        "l2_required": False,
        "external_l2_call_count": 0,
        "materiality_score": 0.0,
        "epistemic_score": 0.0,
        "epistemic_tier": "EPISTEMICALLY_INSUFFICIENT",
        "scoring_receipt": {
            "materiality_weight": 0.0,
            "coverage_factor": 0.0,
            "approved_ratio": 0.0,
            "contradiction_penalty": 0.0,
        },
    }
    x3 = {
        "status": "BLOCKED",
        "outcome": "X3B_ESCALATE_HITL",
        "reason": (
            "The governed graph did not contain connected evidence sufficient for NEE reasoning."
            if is_graph_fit_failure
            else "One or more material evidence requirements lacked relevant authorized support."
        ),
    }
    blocked_quality_controls = {
        "retrieval_evidence_report_digest": retrieval_evidence_report[
            "canonical_digest"
        ]["value"],
        "coverage_manifest_digest": coverage_manifest["canonical_digest"]["value"],
        "graph_fit_decision_digest": graph_fit_decision["canonical_digest"]["value"],
        "terminal_reason": reason,
        "answer_absent": True,
    }
    challenger_review = run_quality_review(
        role=QualityRole.CHALLENGER,
        config=quality_config,
        assurance_profile=assurance_profile or "standard",
        question=question,
        answer=None,
        evidence_packet=packet,
        deterministic_controls=blocked_quality_controls,
        prerequisite_passed=False,
    )
    challenger_review_artifact = challenger_review.to_dict()
    write_json(run_dir / "challenger_review.json", challenger_review_artifact)
    initial_judge_review = run_quality_review(
        role=QualityRole.JUDGE,
        config=quality_config,
        assurance_profile=assurance_profile or "standard",
        question=question,
        answer=None,
        evidence_packet=packet,
        deterministic_controls=blocked_quality_controls,
        challenger_review=challenger_review_artifact,
        prerequisite_passed=False,
    )
    initial_judge_review_artifact = initial_judge_review.to_dict()
    write_json(run_dir / "judge_review_initial.json", initial_judge_review_artifact)
    content_repair_artifact = {
        "status": "SKIPPED",
        "reason": "RETRIEVAL_COVERAGE_BLOCKED",
        "call_count": 0,
        "answer_digest": None,
    }
    write_json(run_dir / "content_repair.json", content_repair_artifact)
    judge_review_artifact = initial_judge_review_artifact
    write_json(run_dir / "judge_review.json", judge_review_artifact)
    x1["runtime_quality"] = {
        "mode": quality_config.mode.value,
        "policy_digest": quality_config.config_digest,
        "challenger_review_digest": challenger_review_artifact["review_digest"],
        "initial_judge_review_digest": initial_judge_review_artifact["review_digest"],
        "judge_review_digest": judge_review_artifact["review_digest"],
        "content_repair_digest": digest(content_repair_artifact),
        "content_repair_status": content_repair_artifact["status"],
        "judge_required_for_release": False,
        "judge_release_passed": False,
        "judge_verdict": initial_judge_review.verdict.value,
        "judge_failure_code": initial_judge_review.failure_code,
    }
    for stage, payload, status in (
        ("X1", x1, "FAIL"),
        ("X2", x2, "FAIL"),
        ("X3", x3, "BLOCKED"),
    ):
        recorder.record(
            stage=stage,
            event_type="exit_gate_event",
            payload=payload,
            correlation=correlation,
            route=route,
            status=status,
        )
    graph_hash_after = cached_sha256_file(graph_path)
    if graph_hash_after != graph_hash_before:
        raise RuntimeError("query pipeline mutated graph truth")
    terminal_binding = assert_runtime_binding_unchanged(
        manager=manager,
        expected_binding=runtime_binding,
        backend=backend,
        policy_digests=runtime_policy_digests,
        stage="pre-l2-blocked-terminal",
    )
    shadow_projection = {
        "route_digest": route_decision["canonical_digest"]["value"],
        "retrieval_digest": digest(
            {
                "retrieval": retrieval.get("canonical_digest", {}).get("value"),
                "graph_retrieval": graph_retrieval.get("canonical_digest", {}).get("value"),
            }
        ),
        "selected_records_digest": digest(retrieval_evidence_report["selected_record_ids_by_lane"]),
        "evidence_packet_digest": packet["packet_digest"],
        "l2d_digest": digest({"reason_code": reason, "provider_call_count": 0}),
        "answer_digest": digest({}),
        "x_gates_digest": digest({"X1": x1, "X2": x2, "X3": x3}),
        "cache_behavior_digest": digest({"lookup_reason": cache_lookup.reason, "writeback": "SKIPPED_NO_TERMINAL_OUTPUT"}),
        "provider_call_count": 0,
        "graph_sha256_before": graph_hash_before,
        "graph_sha256_after": graph_hash_after,
        "runtime_binding": terminal_binding,
        "terminal_outcome": reason,
        "caller_output_digest": digest({"status": "BLOCKED", "x3_outcome": x3["outcome"]}),
    }
    if l1_controller_mode == "shadow":
        _emit_l1_shadow_receipts(
            mode=l1_controller_mode,
            policy=l1_control_policy,
            plan=plan,
            coverage_manifest=coverage_manifest,
            evidence_report=retrieval_evidence_report,
            active=active,
            run_dir=run_dir,
            output_dir=output_dir,
            recorder=recorder,
            request_id=request_id,
            runtime_terminal_outcome=x3["outcome"],
            runtime_next_stage="HITL",
            graph_sha256_before=graph_hash_before,
            graph_sha256_after=graph_hash_after,
            comparison_projection=shadow_projection,
        )
    recorder.record(
        stage="L4",
        event_type="pipeline_event",
        payload={
            "terminal_reason": reason,
            "provider_call_count": 0,
            "semantic_cache": {
                "mode": semantic_cache.mode,
                "lookup_reason": cache_lookup.reason,
                "embedding_binding": cache_binding,
                "writeback": "SKIPPED_NO_TERMINAL_OUTPUT",
            },
        },
        correlation=correlation,
        route=route,
        status="BLOCKED",
    )
    required_stages = [
        "U0", "L1", "L0", "C0.1", "C0.2", "C0.3", "L2D", "X1", "X2", "X3", "L4"
    ]
    seal = recorder.seal(required_stages, correlation, status="BLOCKED")
    graph_hash_after = cached_sha256_file(graph_path)
    if graph_hash_after != graph_hash_before:
        raise RuntimeError("query pipeline mutated graph truth")
    selected_counts = {
        lane: len(rows)
        for lane, rows in retrieval_evidence_report["selected_record_ids_by_lane"].items()
    }
    report_supported_counts = {
        lane: len(rows)
        for lane, rows in retrieval_evidence_report[
            "l2_input_record_ids_by_lane"
        ].items()
    }
    l2_visible_counts = {lane: 0 for lane in ("approved", "candidate", "external_public")}
    evidence_root = output_dir if output_dir is not None else run_dir
    final = {
        "status": "BLOCKED",
        "request_id": request_id,
        "run_id": run_id,
        "trace_id": current_trace_id,
        "destination": decision.destination.value,
        "eligibility_status": "REVOKED_AFTER_RETRIEVAL",
        "eligibility_checks": dict(decision.eligibility_checks) | {
            "post_retrieval_graph_fit": not is_graph_fit_failure,
            "material_evidence_coverage": coverage_complete(retrieval_evidence_report),
        },
        "destination_reason_codes": list(decision.reason_codes),
        "query_destination_decision_digest": decision_artifact["canonical_digest"]["value"],
        "query_graph_fit_decision_digest": graph_fit_decision["canonical_digest"]["value"],
        "graph_fit_reason_codes": list(graph_fit_decision["reason_codes"]),
        "delivery_profile": decision.delivery_profile,
        "artifact_type": decision.artifact_type,
        "assurance_profile": assurance_profile,
        "query_type": plan["query_type"],
        "terminal_outcome": reason,
        "x3_outcome": x3["outcome"],
        "graph_snapshot": active["graph_snapshot"],
        "generation_id": active["generation_id"],
        "graph_sha256_before": graph_hash_before,
        "graph_sha256_after": graph_hash_after,
        "embedding_backend": active["embedding_backend"],
        "runtime_binding": terminal_binding,
        "model_id": active["model_id"],
        "model_revision": active.get("model_revision"),
        "production_qualified": active["production_qualified"],
        "approved_record_count": len(packet.get("approved", [])),
        "candidate_record_count": len(packet.get("candidate", [])),
        "selected_record_counts_by_lane": selected_counts,
        "l2_report_supported_record_counts_by_lane": report_supported_counts,
        "l2_visible_record_counts_by_lane": l2_visible_counts,
        "graph_path_count": graph_fit_decision["graph_path_count"],
        "dense_executed": True,
        "sparse_executed": True,
        "bm25_executed": bool(retrieval.get("bm25_executed")),
        "colbert_executed": True,
        "graph_traversal_executed": bool(
            graph_retrieval["graph_traversal_executed"]
        ),
        "retrieval_evidence": {
            "report_id": retrieval_evidence_report["report_id"],
            "report_digest": retrieval_evidence_report["canonical_digest"]["value"],
            "report_json_path": str((evidence_root / "retrieval_evidence_report.json").resolve()),
            "report_markdown_path": str((evidence_root / "retrieval_evidence.md").resolve()),
            "report_supported_record_count": sum(report_supported_counts.values()),
            "l2_visible_record_count": 0,
        },
        "embedding_call_count": 1,
        "provider_initialization_count": 0,
        "external_l2_call_count": 0,
        "judge_call_count": 0,
        "challenger_call_count": 0,
        "total_external_model_call_count": 0,
        "graph_mutation_count": 0,
        "l1_controller": {
            "mode": l1_controller_mode,
            "policy_digest": (
                l1_control_policy.policy_digest if l1_control_policy is not None else None
            ),
            "terminal_digest": (
                l1_terminal["canonical_digest"]["value"]
                if l1_terminal is not None
                else None
            ),
            "terminal_reason": (
                l1_terminal["terminal_reason"] if l1_terminal is not None else None
            ),
            "final_evidence_report_digest": retrieval_evidence_report[
                "canonical_digest"
            ]["value"],
        },
        "learning_event_count": seal["event_count"],
        "learning_event_chain_digest": seal["event_chain_digest"],
        "required_stages": required_stages,
        "content_repair_call_count": 0,
        "x1_quality": {
            "mode": quality_config.mode.value,
            "policy_digest": quality_config.config_digest,
            "challenger_review_digest": challenger_review_artifact["review_digest"],
            "initial_judge_review_digest": initial_judge_review_artifact[
                "review_digest"
            ],
            "judge_review_digest": judge_review_artifact["review_digest"],
            "content_repair_digest": digest(content_repair_artifact),
            "challenger_call_count": 0,
            "judge_call_count": 0,
            "content_repair_call_count": 0,
            "judge_release_passed": False,
        },
        "semantic_cache": {
            "mode": semantic_cache.mode,
            "config_source": semantic_cache.config_source,
            "policy_digest": semantic_cache.policy_digest,
            "embedding_binding": cache_binding,
            "lookup_hit": False,
            "lookup_reason": cache_lookup.reason,
            "lookup_entry_id": cache_lookup.entry_id,
            "lookup_similarity": cache_lookup.similarity,
            "replayed_output": False,
            "source_run_id": cache_lookup.source_run_id,
            "source_trace_id": cache_lookup.source_trace_id,
            "source_output_digest": cache_lookup.source_output_digest,
            "source_answer_digest": None,
            "delivered_answer_digest": None,
            "writeback": {
                "status": "SKIPPED",
                "reason": "CACHE_NO_TERMINAL_OUTPUT",
                "entry_id": None,
                "replay_eligible": False,
            },
        },
        "answer": None,
        "idempotent_replay": False,
    }
    write_json(run_dir / "x_gates.json", {"X1": x1, "X2": x2, "X3": x3})
    write_json(summary_path, final)
    (run_dir / "answer.md").write_text(render_markdown(None, x3["outcome"]), encoding="utf-8")
    if output_dir is not None:
        artifacts = {
            "runtime_binding.json": terminal_binding,
            "answer.json": final,
            "query_destination_decision.json": decision_artifact,
            "query_plan.json": plan,
            "query_coverage_manifest.json": coverage_manifest,
            "query_graph_fit_decision.json": graph_fit_decision,
            "retrieval_evidence_report.json": retrieval_evidence_report,
            "connected_evidence_map.json": connected_evidence_map,
            "evidence_packet.json": packet,
        }
        if l1_terminal is not None:
            artifacts["query_plan_terminal.json"] = dict(l1_terminal)
        text_artifacts = {
            "retrieval_evidence.md": render_retrieval_evidence_markdown(
                coverage_manifest, retrieval_evidence_report
            ),
            "answer.md": render_markdown(None, x3["outcome"]),
        }
        mirror_artifacts_to_output_dir(output_dir, artifacts, text_artifacts=text_artifacts)
    return QueryResult(**final)


@dataclass
class _QueryRunState:
    action: Any = None
    action_result: Any = None
    activation: Any = None
    active: Any = None
    answer: Any = None
    artifact_type: Any = None
    artifact_writer: DeferredArtifactWriter | None = None
    assurance: Any = None
    assurance_profile: Any = None
    attempt_zero: Any = None
    attempt_zero_action: Any = None
    audience: Any = None
    audiences: Any = None
    aug_connected_evidence_map: Any = None
    aug_graph_retrieval: Any = None
    aug_hydrated: Any = None
    aug_packet: Any = None
    aug_report: Any = None
    authorization_context: Any = None
    authorization_scope_digest: Any = None
    bounded_telemetry: Any = None
    budget_before_action: Any = None
    cache_answer_replayed: Any = None
    cache_binding: Any = None
    cache_context: Any = None
    cache_lookup: Any = None
    cache_writeback: Any = None
    cached_output: Any = None
    call: Any = None
    call_payload: Any = None
    card_assertion_scope: Any = None
    card_candidate_review: Any = None
    card_id: Any = None
    card_view_version: Any = None
    challenger_controls: Any = None
    challenger_review: Any = None
    challenger_review_artifact: Any = None
    claim_evidence_map: Any = None
    classification: Any = None
    comparison_mode: Any = False
    compiled_prompt: Any = None
    connected_evidence_map: Any = None
    connection: Any = None
    concurrent_quality_reviews: Any = None
    content_repair: Any = None
    content_repair_artifact: Any = None
    content_repair_call_count: Any = None
    controlled: Any = None
    controller: Any = None
    controller_plan: Any = None
    coverage_augmentation_count: Any = None
    coverage_disposition: Any = None
    coverage_manifest: Any = None
    decision_frame: Any = None
    current_trace_id: Any = None
    decision_otel: Any = None
    deficiency: Any = None
    delivery_profile: Any = None
    delivery_quality_decision: Any = None
    dense: Any = None
    destination: Any = None
    destination_artifact: Any = None
    destination_decision: Any = None
    deterministic_x1_passed: Any = None
    effective_assertion_scope: Any = None
    effective_provider_egress: Any = None
    effective_quality_egress: Any = None
    encoded_intent: Any = None
    event_by_purpose: Any = None
    evidence_only: Any = None
    evidence_output_root: Any = None
    evidence_ready: Any = None
    exact: Any = None
    external_l2_offline: Any = None
    final: Any = None
    forbidden: Any = None
    governed_lanes: Any = None
    graph_fit_decision: Any = None
    graph_hash_after: Any = None
    graph_hash_after_reasoning: Any = None
    graph_hash_before: Any = None
    graph_hash_shadow: Any = None
    graph_path: Any = None
    graph_retrieval: Any = None
    hydrated: Any = None
    ids: Any = None
    initial_judge_review: Any = None
    initial_judge_review_artifact: Any = None
    intent_digest: Any = None
    intent_vector: Any = None
    judge_enforcement_failed: Any = None
    judge_release_passed: Any = None
    judge_required_for_release: Any = None
    judge_review: Any = None
    judge_review_artifact: Any = None
    key: Any = None
    l1_feedback_sm: Any = None
    l1_post_l2_review: Any = None
    l1_post_l2_review_contract: Any = None
    l1_review_history: Any = None
    l1_terminal: Any = None
    l2_decision: Any = None
    l2_decision_artifact: Any = None
    l2_execution: Any = None
    l2_ledger: Any = None
    l2_packet: Any = None
    l2_ready: Any = None
    l2_visible_counts: Any = None
    lane: Any = None
    lane_data: Any = None
    markdown: Any = None
    materiality: Any = None
    model_path_value: Any = None
    name: Any = None
    observation_one: Any = None
    observation_zero: Any = None
    observations: Any = None
    outcome: Any = None
    outcome_counts: Any = None
    output_dir: Any = None
    packet: Any = None
    pending_cache_entry: Any = None
    plan: Any = None
    portfolio: Any = None
    prior_report: Any = None
    prompt: Any = None
    provider_egress_allowed: Any = None
    quality_config: Any = None
    query_type: Any = None
    question: Any = None
    raw_output: Any = None
    record_id: Any = None
    recorder: Any = None
    rejudge_call_count: Any = None
    repair_eligible: Any = None
    report_supported_counts: Any = None
    request_id: Any = None
    request_identity: Any = None
    request_context: Any = None
    required_material: Any = None
    required_stages: Any = None
    requirement: Any = None
    requirement_by_id: Any = None
    requirement_id: Any = None
    resolved_card_scope: Any = None
    resolved_output: Any = None
    result: Any = None
    retrieval: Any = None
    retrieval_evidence_report: Any = None
    retrieval_mode: Any = None
    root: Any = None
    route: Any = None
    routing_policy_digest: Any = None
    row: Any = None
    rows: Any = None
    run_dir: Any = None
    run_id: Any = None
    runtime_binding: Any = None
    runtime_policy_digests: Any = None
    seal: Any = None
    sealed_queries: Any = None
    selected_before: Any = None
    selected_counts: Any = None
    selected_type: Any = None
    selection_stage: Any = None
    selection_status: Any = None
    semantic_cache: Any = None
    shadow_projection: Any = None
    sidecar_path: Any = None
    source_action: Any = None
    status_snapshot_scope: Any = None
    strategic_evidence_artifact: Any = None
    strategic_reasoning_artifact: Any = None
    summary_path: Any = None
    terminal_binding: Any = None
    total_judge_call_count: Any = None
    total_l2_provider_call_count: Any = None
    u0_validation: Any = None
    user_id: Any = None
    validated_request: Any = None
    validated_request_digest: Any = None
    value: Any = None
    values: Any = None
    x1: Any = None
    x2: Any = None
    x3: Any = None
    x3_outcome: Any = None

    def write_artifact(self, path: Path, payload: Any, *, critical: bool = False) -> None:
        if self.artifact_writer is not None and not critical:
            self.artifact_writer.stage(path, payload)
        else:
            write_json(path, payload)

    def write_text_artifact(self, path: Path, text: str, *, critical: bool = False) -> None:
        if self.artifact_writer is not None and not critical:
            self.artifact_writer.stage_text(path, text)
        else:
            path.write_text(text, encoding="utf-8")

def _can_dispatch_l1_replan(next_state: Any, payload: Any, l2_decision: Any, l2_execution: Any) -> bool:
    if next_state != LoopState.REPLANNING or payload is None or not getattr(l2_decision, "required", False):
        return False
    return l2_execution is None or l2_execution.disposition is not L2Disposition.FAILED


class QueryEngine:
    def __init__(
        self,
        runtime_root: Path,
        seed_graph: Path,
        backend: EmbeddingBackend,
        *,
        provider_factory: ReasoningProviderFactory | None = None,
        l2_config: L2Config | None = None,
        x1_quality_config: X1QualityConfig | None = None,
        quality_provider_factory: QualityProviderFactory | None = None,
        semantic_cache_mode: str | None = None,
        routing_policy: QueryRoutingPolicy | None = None,
        l1_controller_mode: str = "off",
        l1_control_policy: L1ControlPolicy | None = None,
        defer_artifacts: bool = True,
    ) -> None:
        self.defer_artifacts = defer_artifacts
        self.manager = ReleaseManager(runtime_root, seed_graph)
        self.runtime_root = self.manager.runtime_root
        self.backend = backend
        self._l2_config_is_default = l2_config is None
        self._x1_quality_config_is_default = x1_quality_config is None
        self._routing_policy_is_default = routing_policy is None
        self.l2_config = l2_config or load_l2_config()
        self.x1_quality_config = x1_quality_config or load_x1_quality_config()
        self.provider_factory = provider_factory
        self.quality_provider_factory = quality_provider_factory
        self.semantic_cache_mode = semantic_cache_mode
        self.routing_policy = routing_policy or load_query_routing_policy()
        if l1_controller_mode not in {"off", "shadow", "bounded"}:
            raise ValueError("l1_controller_mode must be off, shadow, or bounded")
        self.l1_controller_mode = l1_controller_mode
        if l1_controller_mode == "shadow" and l1_control_policy is None:
            policy_path = Path(__file__).resolve().parents[1] / "requirements" / "query-l1-control-policy.json"
            l1_control_policy = load_policy(read_json(policy_path))
        if l1_controller_mode == "bounded":
            if l1_control_policy is None or l1_control_policy.mode != "bounded":
                raise ValueError(
                    "bounded L1 controller mode requires an explicit bounded candidate policy"
                )
            if backend.production_qualified:
                raise PermissionError("L1_BOUNDED_PRODUCTION_APPROVAL_REQUIRED")
        self.l1_control_policy = l1_control_policy

    def _assert_default_policy_sources_current(self) -> None:
        """Do not execute with a policy object that predates its source file."""

        checks = (
            (
                "routing",
                self._routing_policy_is_default,
                self.routing_policy.policy_digest,
                load_query_routing_policy().policy_digest,
            ),
            (
                "l2",
                self._l2_config_is_default,
                self.l2_config.config_digest,
                load_l2_config().config_digest,
            ),
            (
                "x1",
                self._x1_quality_config_is_default,
                self.x1_quality_config.config_digest,
                load_x1_quality_config().config_digest,
            ),
        )
        for name, enabled, expected, observed in checks:
            if enabled and expected != observed:
                raise DriftError(
                    "POLICY_DRIFT",
                    f"{name} policy changed after QueryEngine initialization",
                    expected=expected,
                    observed=observed,
                )

    def _runtime_policy_digests(
        self,
        active: Mapping[str, Any],
        quality_config: X1QualityConfig,
    ) -> dict[str, str | None]:
        """Return policy bindings, refreshing repository-default sources."""

        self._assert_default_policy_sources_current()
        values: dict[str, str | None] = {
            "routing": self.routing_policy.policy_digest,
            "l2": self.l2_config.config_digest,
            "x1": quality_config.config_digest,
            "hybrid_retrieval": HYBRID_RETRIEVAL_POLICY_DIGEST,
        }
        if self.l1_control_policy is not None:
            values["l1_controller"] = self.l1_control_policy.policy_digest
        if pair_assurance_tier(dict(active)) == INTERNAL_OPERATIONAL:
            policy_path = Path(
                getattr(
                    self.backend,
                    "internal_operational_policy_path",
                    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
                )
            )
            try:
                values["internal_operational"] = internal_operational_policy_digest(
                    load_internal_operational_policy(policy_path)
                )
            except Exception as exc:
                raise DriftError(
                    "POLICY_DRIFT",
                    "internal operational policy could not be revalidated",
                ) from exc
        return values

    def ask(
        self,
        question: str,
        *,
        query_type: str | None = None,
        output_dir: Path | None = None,
        user_id: str = "local-authorized-operator",
        classification: str = "internal",
        audience: str = "nee-engineering",
        authorization_context: AuthorizationContext | None = None,
        materiality: str | None = None,
        provider_egress_allowed: bool = True,
        card_id: str | None = None,
        card_view_version: int | None = None,
        card_candidate_review: bool = False,
        destination: str | None = None,
        delivery_profile: str | None = None,
        artifact_type: str | None = None,
        decision_frame: dict[str, Any] | None = None,
        comparison_mode: bool = False,
        request_context: Any | None = None,
    ) -> dict[str, Any]:
        state = _QueryRunState(
            artifact_type=artifact_type,
            audience=audience,
            authorization_context=authorization_context,
            card_candidate_review=card_candidate_review,
            card_id=card_id,
            card_view_version=card_view_version,
            classification=classification,
            comparison_mode=comparison_mode,
            decision_frame=decision_frame,
            delivery_profile=delivery_profile,
            destination=destination,
            materiality=materiality,
            output_dir=output_dir,
            provider_egress_allowed=provider_egress_allowed,
            query_type=query_type,
            question=question,
            request_context=request_context,
            user_id=user_id,
        )
        try:
            for stage_fn in (
                self._stage_validate_and_normalize, self._stage_resolve_routing,
                self._stage_initialize_run, self._stage_determine_mode,
                self._stage_check_cache, self._stage_baseline_retrieval,
                self._stage_coverage_augmentation, self._stage_prepare_l2,
                self._stage_execute_l2, self._stage_l1_post_l2_review,
                self._stage_challenger_review, self._stage_l2d_and_repair,
                self._stage_finalize_gates, self._stage_build_receipt,
            ):
                stage_fn(state)
                if state.final is not None:
                    return state.final
            return state.final
        finally:
            if state.artifact_writer is not None:
                state.artifact_writer.flush()
            if state.recorder is not None:
                state.recorder.close()
    def _stage_validate_and_normalize(self, state: _QueryRunState) -> None:
        self.manager._assert_mutable_layout()
        normalized_cls = "public" if state.classification == "external_approved" else state.classification
        validator = QueryU0Validator(
            production_qualified=self.backend.production_qualified,
            model_manifest_digest=getattr(self.backend, "model_manifest_digest", None),
        )
        validated = validator.validate(
            state.question,
            user_id=state.user_id,
            classification=normalized_cls,
            audience=state.audience or "nee-engineering",
            materiality=state.materiality,
            delivery_profile=state.delivery_profile,
            artifact_type=state.artifact_type,
            card_id=state.card_id,
            card_view_version=state.card_view_version,
            card_candidate_review=bool(state.card_candidate_review),
            provider_egress_allowed=True if state.provider_egress_allowed is None else bool(state.provider_egress_allowed),
            authorization_context=state.authorization_context,
        )
        state.question = validated.question
        state.user_id = validated.user_id
        state.classification = validated.classification
        state.audiences = validated.audiences
        state.audience = ",".join(validated.audiences)
        state.validated_request = validated
        state.u0_validation = validated

    def _stage_resolve_routing(self, state: _QueryRunState) -> None:
        active = state.active
        artifact_type = state.artifact_type
        audiences = state.audiences
        authorization_context = state.authorization_context
        card_candidate_review = state.card_candidate_review
        card_id = state.card_id
        card_view_version = state.card_view_version
        classification = state.classification
        connection = state.connection
        delivery_profile = state.delivery_profile
        destination = state.destination
        destination_decision = state.destination_decision
        effective_provider_egress = state.effective_provider_egress
        effective_quality_egress = state.effective_quality_egress
        external_l2_offline = state.external_l2_offline
        forbidden = state.forbidden
        graph_path = state.graph_path
        materiality = state.materiality
        model_path_value = state.model_path_value
        name = state.name
        output_dir = state.output_dir
        provider_egress_allowed = state.provider_egress_allowed
        quality_config = state.quality_config
        query_type = state.query_type
        question = state.question
        raw_output = state.raw_output
        request_identity = state.request_identity
        resolved_card_scope = state.resolved_card_scope
        resolved_output = state.resolved_output
        root = state.root
        routing_policy_digest = state.routing_policy_digest
        runtime_policy_digests = state.runtime_policy_digests
        user_id = state.user_id
        self._assert_default_policy_sources_current()
        destination_decision = route_query(
            question,
            query_type_override=query_type,
            destination_override=destination,
            requested_materiality=materiality,
            delivery_profile=delivery_profile,
            artifact_type=artifact_type,
            authorized=True,
            policy=self.routing_policy,
        )
        active = self.manager.active_pair()
        if active["embedding_backend"] != self.backend.backend_id:
            raise RuntimeError("query backend does not match the active pair")
        if self.backend.production_qualified and (
            active.get("model_manifest_digest") != self.backend.model_manifest_digest
            or not active.get("production_qualified")
        ):
            raise RuntimeError("production query model binding does not match the active pair")
        assurance = require_internal_operational_use(
            self.runtime_root,
            active,
            policy_path=Path(
                getattr(
                    self.backend,
                    "internal_operational_policy_path",
                    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
                )
            ),
            classification=classification,
            audiences=audiences,
        )
        if pair_assurance_tier(active) == INTERNAL_OPERATIONAL:
            # Internal operational query egress is closed unless a future
            # separately governed policy and authorization contract is added.
            provider_egress_allowed = False
        if output_dir is not None:
            raw_output = Path(output_dir).expanduser().absolute()
            if is_link_or_reparse(raw_output):
                raise RuntimeError("query output directory cannot be a link or reparse point")
            resolved_output = raw_output.resolve()
            forbidden = [
                self.runtime_root / name
                for name in ("generations", "releases", "runs", "ledger", "telemetry")
            ]
            model_path_value = getattr(self.backend, "model_path", None)
            if model_path_value:
                forbidden.append(Path(str(model_path_value)).resolve())
            if any(
                resolved_output == root.resolve()
                or root.resolve() in resolved_output.parents
                for root in forbidden
            ):
                raise RuntimeError(
                    "query output directory overlaps protected runtime or model content"
                )
            output_dir = resolved_output
        ReleaseManager._verify_backend_snapshot(self.backend)
        graph_path = self.manager._resolve(active["graph_path"])
        sidecar_path = self.manager._resolve(active["sidecar_path"])
        graph_hash_before = cached_sha256_file(graph_path)
        resolved_card_scope: dict[str, Any] | None = None
        if card_id is not None:
            connection = connect_sqlite(graph_path, readonly=True)
            try:
                resolved_card_scope = resolve_authorized_card_scope(
                    connection,
                    card_id=card_id,
                    view_version=card_view_version,
                    authorization_context={
                        "classification_clearance": classification,
                        "audiences": audiences,
                        "candidate_review": card_candidate_review,
                    },
                )
            finally:
                connection.close()
            if authorization_context is not None:
                authorization_context.require_resources(
                    {
                        "card_view_scope": sha256_bytes(
                            resolved_card_scope["membership_digest"].encode("utf-8")
                        )
                    }
                )
        external_l2_offline = external_provider_offline()
        effective_provider_egress = bool(
            provider_egress_allowed
            and self.l2_config.provider_egress_allowed
            and not external_l2_offline
        )
        effective_quality_egress = bool(
            provider_egress_allowed
            and self.x1_quality_config.provider_egress_allowed
            and not external_l2_offline
        )
        quality_config = replace(
            self.x1_quality_config,
            provider_egress_allowed=effective_quality_egress,
        )
        routing_policy_digest = self.routing_policy.policy_digest
        runtime_policy_digests = self._runtime_policy_digests(active, quality_config)
        runtime_binding = capture_runtime_binding(
            manager=self.manager,
            active_pair=active,
            backend=self.backend,
            policy_digests=runtime_policy_digests,
        )
        request_identity: list[Any] = [
            question,
            query_type,
            destination_decision.destination.value,
            destination_decision.query_type,
            destination_decision.delivery_profile,
            destination_decision.artifact_type,
            materiality,
            effective_provider_egress,
            external_l2_offline,
            routing_policy_digest,
            self.l2_config.config_digest,
            DECISION_OUTPUT_CONTRACT_DIGEST,
            quality_config.config_digest,
            quality_config.mode.value,
            effective_quality_egress,
            active["generation_id"],
            user_id,
            classification,
            audiences,
        ]
        if resolved_card_scope is not None:
            request_identity.append(
                {
                    "card_id": resolved_card_scope["card_id"],
                    "card_view_id": resolved_card_scope["card_view_id"],
                    "view_version": resolved_card_scope["view_version"],
                    "selector_digest": resolved_card_scope["selector_digest"],
                    "membership_digest": resolved_card_scope["membership_digest"],
                }
            )
        state.active = active
        state.assurance = assurance
        state.connection = connection
        state.destination_decision = destination_decision
        state.effective_provider_egress = effective_provider_egress
        state.effective_quality_egress = effective_quality_egress
        state.external_l2_offline = external_l2_offline
        state.forbidden = forbidden
        state.graph_hash_before = graph_hash_before
        state.graph_path = graph_path
        state.model_path_value = model_path_value
        state.name = name
        state.output_dir = output_dir
        state.provider_egress_allowed = provider_egress_allowed
        state.quality_config = quality_config
        state.raw_output = raw_output
        state.request_identity = request_identity
        state.resolved_card_scope = resolved_card_scope
        state.resolved_output = resolved_output
        state.root = root
        state.routing_policy_digest = routing_policy_digest
        state.runtime_binding = runtime_binding
        state.runtime_policy_digests = runtime_policy_digests
        state.sidecar_path = sidecar_path

    def _stage_initialize_run(self, state: _QueryRunState) -> None:
        active, audiences, authorization_context, classification = state.active, state.audiences, state.authorization_context, state.classification
        destination, destination_decision, materiality = state.destination, state.destination_decision, state.materiality
        effective_provider_egress, effective_quality_egress = state.effective_provider_egress, state.effective_quality_egress
        external_l2_offline, output_dir, quality_config = state.external_l2_offline, state.output_dir, state.quality_config
        question, recorder, resolved_card_scope = state.question, state.recorder, state.resolved_card_scope
        routing_policy_digest, user_id = state.routing_policy_digest, state.user_id
        request_identity = list(state.request_identity)
        if state.decision_frame is not None:
            request_identity.append(
                {"decision_frame_digest": state.decision_frame.get("frame_digest")}
            )
        state.request_identity = request_identity
        result = state.result
        runtime_binding = state.runtime_binding
        u0_validation = state.u0_validation
        request_id = stable_id("request", *request_identity, length=26)
        run_id = stable_id("run-query", request_id, length=26)
        current_trace_id = trace_id(run_id, request_id, active["graph_snapshot"])
        run_dir = self.runtime_root / "runs" / run_id
        assert_safe_mutable_path(
            self.runtime_root, run_dir, label="query run directory"
        )
        summary_path = run_dir / "query_summary.json"
        if summary_path.is_file():
            result = read_json(summary_path)
            assert_replay_binding(result, runtime_binding)
            result["idempotent_replay"] = True
            state.final = QueryResult(**result)
            return
        if run_dir.exists():
            shutil.rmtree(run_dir)
        if output_dir is not None and output_dir.exists():
            raise FileExistsError(
                f"query output directory must be a new directory: {output_dir}"
            )
        _claim_new_directory(run_dir, label="query run directory")
        if output_dir is not None:
            _claim_new_directory(output_dir, label="query output directory")
        if self.defer_artifacts:
            state.artifact_writer = DeferredArtifactWriter(run_dir)
        state.write_artifact(run_dir / "runtime_binding.json", runtime_binding)
        recorder = RunRecorder(
            self.runtime_root,
            run_id=run_id,
            trace_id=current_trace_id,
            use_case="query",
            graph_snapshot=active["graph_snapshot"],
            model_or_index_generation=active["generation_id"],
        )
        u0_validation = {
            "request_id": request_id,
            "run_id": run_id,
            "trace_id": current_trace_id,
            "user_id": user_id,
            "classification": classification,
            "audiences": audiences,
            "normalized_question": question,
            "external_reasoning_allowed": effective_provider_egress,
            "external_provider_offline": external_l2_offline,
            "x1_quality_mode": quality_config.mode.value,
            "x1_quality_policy_digest": quality_config.config_digest,
            "x1_quality_egress_allowed": effective_quality_egress,
            "requested_materiality": materiality,
            "requested_destination": destination,
            "destination": destination_decision.destination.value,
            "delivery_profile": destination_decision.delivery_profile,
            "artifact_type": destination_decision.artifact_type,
            "routing_policy_digest": routing_policy_digest,
            "runtime_binding_digest": runtime_binding["binding_digest"],
            "authorization_context_digest": (
                authorization_context.context_digest
                if authorization_context
                else None
            ),
            "authorization_token_id": (
                authorization_context.token_id if authorization_context else None
            ),
            "status": "PASS",
        }
        if resolved_card_scope is not None:
            u0_validation["card_scope"] = {
                "card_id": resolved_card_scope["card_id"],
                "card_view_id": resolved_card_scope["card_view_id"],
                "view_version": resolved_card_scope["view_version"],
                "selector_digest": resolved_card_scope["selector_digest"],
                "membership_digest": resolved_card_scope["membership_digest"],
                "assertion_count": len(resolved_card_scope["assertion_ids"]),
            }
        state.write_artifact(run_dir / "u0_validation.json", u0_validation)
        recorder.record(
            stage="U0",
            event_type="pipeline_event",
            payload=u0_validation,
            correlation=_correlation(request_id),
            input_digest=digest(question),
        )
        destination_artifact = destination_decision.to_artifact(
            request_id=request_id,
            run_id=run_id,
            trace_id=current_trace_id,
            decided_at=utc_now(),
        )
        state.write_artifact(run_dir / "query_destination_decision.json", destination_artifact)
        state.current_trace_id = current_trace_id
        state.destination_artifact = destination_artifact
        state.recorder = recorder
        state.request_id = request_id
        state.result = result
        state.run_dir = run_dir
        state.run_id = run_id
        state.summary_path = summary_path
        state.u0_validation = u0_validation

    def _stage_determine_mode(self, state: _QueryRunState) -> None:
        active = state.active
        assurance_profile = state.assurance_profile
        audiences = state.audiences
        authorization_context = state.authorization_context
        authorization_scope_digest = state.authorization_scope_digest
        classification = state.classification
        coverage_manifest = state.coverage_manifest
        current_trace_id = state.current_trace_id
        destination_artifact = state.destination_artifact
        destination_decision = state.destination_decision
        effective_provider_egress = state.effective_provider_egress
        external_l2_offline = state.external_l2_offline
        governed_lanes = state.governed_lanes
        graph_hash_before = state.graph_hash_before
        materiality = state.materiality
        output_dir = state.output_dir
        plan = state.plan
        quality_config = state.quality_config
        question = state.question
        recorder = state.recorder
        request_id = state.request_id
        resolved_card_scope = state.resolved_card_scope
        retrieval_mode = state.retrieval_mode
        route = state.route
        routing_policy_digest = state.routing_policy_digest
        run_dir = state.run_dir
        run_id = state.run_id
        runtime_binding = state.runtime_binding
        selected_type = state.selected_type
        summary_path = state.summary_path
        u0_validation = state.u0_validation
        user_id = state.user_id
        validated_request = state.validated_request
        validated_request_digest = state.validated_request_digest
        if not destination_decision.enters_query_runtime:
            state.final = _finish_routed_request(
                manager=self.manager,
                backend=self.backend,
                decision=destination_decision,
                decision_artifact=destination_artifact,
                recorder=recorder,
                request_id=request_id,
                run_id=run_id,
                current_trace_id=current_trace_id,
                active=active,
                graph_hash_before=graph_hash_before,
                runtime_binding=runtime_binding,
                runtime_policy_digests=self._runtime_policy_digests(active, quality_config),
                summary_path=summary_path,
                output_dir=output_dir,
            )
            return
        recorder.record(
            stage="L1",
            event_type="pipeline_event",
            payload=destination_artifact,
            correlation=_correlation(request_id),
        )
        if destination_decision.query_type is None:
            raise RuntimeError("an eligible query destination must bind a query type")
        selected_type = canonical_query_type(destination_decision.query_type)
        query_type_policy(selected_type)
        retrieval_mode, governed_lanes = query_retrieval_policy(selected_type)
        effective_materiality = determine_materiality(
            question,
            selected_type,
            requested_materiality=materiality or destination_decision.materiality,
        )
        assurance_profile = select_assurance_profile(
            destination=destination_decision.destination.value,
            query_type=selected_type,
            materiality=effective_materiality,
            delivery_profile=destination_decision.delivery_profile,
            artifact_type=destination_decision.artifact_type,
            question=question,
        )
        validated_request = {
            "request_id": request_id,
            "run_id": run_id,
            "trace_id": current_trace_id,
            "user_id": user_id,
            "classification": classification,
            "audiences": audiences,
            "normalized_question": question,
            "permitted_capabilities": [
                "exact_lookup",
                "bm25_retrieval",
                "hybrid_retrieval",
                "typed_multi_hop_graph_retrieval",
                "connected_evidence_selection",
                "graph_hydration",
            ],
            "web_research": "denied",
            "embedding_allowed": True,
            "external_reasoning_allowed": effective_provider_egress,
            "external_provider_offline": external_l2_offline,
            "requested_materiality": materiality,
            "destination": destination_decision.destination.value,
            "destination_decision_digest": destination_artifact[
                "canonical_digest"
            ]["value"],
            "delivery_profile": destination_decision.delivery_profile,
            "artifact_type": destination_decision.artifact_type,
            "routed_materiality": destination_decision.materiality,
            "assurance_profile": assurance_profile,
            "routing_policy_digest": routing_policy_digest,
            "retrieval_mode": retrieval_mode,
            "allowed_lanes": list(governed_lanes),
            "l2_policy_config_digest": self.l2_config.config_digest,
            "l2_output_contract_digest": DECISION_OUTPUT_CONTRACT_DIGEST,
            "authorization_context_digest": (
                authorization_context.context_digest
                if authorization_context
                else None
            ),
            "authorization_token_id": (
                authorization_context.token_id if authorization_context else None
            ),
        }
        if resolved_card_scope is not None:
            validated_request["card_scope"] = u0_validation["card_scope"]
        state.write_artifact(run_dir / "validated_request.json", validated_request)
        plan = build_query_plan(
            question=question,
            query_type=selected_type,
            request_id=request_id,
            run_id=run_id,
            current_trace_id=current_trace_id,
            l2_config=self.l2_config,
            provider_egress_allowed=effective_provider_egress,
            destination_decision=destination_artifact,
            assurance_profile=assurance_profile,
            requested_materiality=materiality or destination_decision.materiality,
            card_scope=resolved_card_scope,
            decision_frame=state.decision_frame,
            graph_path=str(state.graph_path) if state.graph_path else (active.get("graph_path") if active else None),
        )
        if (
            plan["retrieval_mode"] != retrieval_mode
            or plan["allowed_lanes"] != list(governed_lanes)
        ):
            raise RuntimeError("query plan retrieval policy does not match the governed route")
        validated_request_digest = digest(validated_request)
        authorization_scope_digest = (
            authorization_context.context_digest
            if authorization_context is not None
            else digest(
                {
                    "classification": classification,
                    "audiences": sorted(audiences),
                    "allowed_lanes": list(governed_lanes),
                    "user_id": user_id,
                }
            )
        )
        coverage_manifest = compile_query_coverage_manifest(
            question=question,
            validated_request_digest=validated_request_digest,
            plan=plan,
            active=active,
            authorization_scope_digest=authorization_scope_digest,
        )
        route = build_query_route(plan)
        state.write_artifact(run_dir / "query_plan.json", plan)
        state.write_artifact(run_dir / "query_coverage_manifest.json", coverage_manifest)
        state.write_artifact(run_dir / "route_decision.json", route)
        recorder.record(
            stage="L1",
            event_type="query_plan",
            payload=plan,
            correlation=_correlation(request_id),
        )
        recorder.record(
            stage="L0",
            event_type="route_decision",
            payload=route,
            correlation=_correlation(request_id),
            route=route["query_route"],
        )

        state.assurance_profile = assurance_profile
        state.authorization_scope_digest = authorization_scope_digest
        state.coverage_manifest = coverage_manifest
        state.governed_lanes = governed_lanes
        state.plan = plan
        state.retrieval_mode = retrieval_mode
        state.route = route
        state.selected_type = selected_type
        state.validated_request = validated_request
        state.validated_request_digest = validated_request_digest

    def _stage_check_cache(self, state: _QueryRunState) -> None:
        active = state.active
        audiences = state.audiences
        authorization_context = state.authorization_context
        cache_binding = state.cache_binding
        cache_context = state.cache_context
        cache_lookup = state.cache_lookup
        cached_output = state.cached_output
        classification = state.classification
        coverage_manifest = state.coverage_manifest
        dense = state.dense
        effective_provider_egress = state.effective_provider_egress
        encoded_intent = state.encoded_intent
        intent_vector = state.intent_vector
        key = state.key
        plan = state.plan
        quality_config = state.quality_config
        question = state.question
        recorder = state.recorder
        request_id = state.request_id
        requirement = state.requirement
        resolved_card_scope = state.resolved_card_scope
        route = state.route
        routing_policy_digest = state.routing_policy_digest
        semantic_cache = state.semantic_cache
        user_id = state.user_id
        value = state.value
        semantic_cache = SemanticCache(self.runtime_root)
        if (
            self.semantic_cache_mode is not None
            and self.semantic_cache_mode != semantic_cache.mode
        ):
            raise ValueError(
                "SEMANTIC_CACHE_MODE_MISMATCH: requested "
                f"{self.semantic_cache_mode}, runtime is {semantic_cache.mode}; "
                "use semantic-cache-config before the query"
            )
        cache_binding = semantic_cache.bind_embedding_backend(self.backend)
        cache_context = _query_cache_context(
            active=active,
            route=route,
            plan=plan,
            classification=classification,
            audiences=audiences,
            user_id=user_id,
            authorization_context=authorization_context,
            l2_config=self.l2_config,
            destination_policy_digest=routing_policy_digest,
            x1_policy_digest=quality_config.config_digest,
            provider_egress_allowed=effective_provider_egress,
            decision_frame_digest=(
                state.decision_frame.get("frame_digest")
                if state.decision_frame is not None
                else None
            ),
            coverage_requirements_digest=(
                digest(
                    [
                        {
                            key: value
                            for key, value in requirement.items()
                            if key != "requirement_id"
                        }
                        for requirement in coverage_manifest["requirements"]
                    ]
                )
                if coverage_manifest is not None
                else ""
            ),
            card_scope=resolved_card_scope,
            l1_controller_mode=self.l1_controller_mode,
            l1_control_policy=self.l1_control_policy,
        )
        intent_digest = digest(question)
        encoded_intent: dict[str, Any] | None = None
        intent_vector: list[float] | None = None
        cache_lookup = SemanticCacheLookup(
            mode=semantic_cache.mode,
            hit=False,
            reason=semantic_cache.health_reason,
        )
        cached_output: dict[str, Any] | None = None
        if (
            self.l1_controller_mode == "bounded"
            and semantic_cache.mode != "off"
        ):
            # A bounded replay must carry a run-specific terminal/evidence
            # chain.  Wave 7 isolates it until replay can rebind that complete
            # chain rather than replay a partial or cross-mode result.
            cache_lookup = SemanticCacheLookup(
                mode=semantic_cache.mode,
                hit=False,
                reason="CACHE_BOUNDED_REPLAY_DEFERRED",
            )
        elif semantic_cache.mode != "off" and semantic_cache.health_reason == "CACHE_READY":
            if not semantic_cache.can_read:
                cache_lookup = SemanticCacheLookup(
                    mode=semantic_cache.mode,
                    hit=False,
                    reason=semantic_cache._disabled_reason("READ"),
                )
            else:
                try:
                    encoded_intent = self.backend.encode(question)
                    dense = encoded_intent.get("dense")
                    if not isinstance(dense, list):
                        raise ValueError("embedding backend returned no dense intent vector")
                    intent_vector = [float(value) for value in dense]
                    dense = intent_vector
                    cache_lookup = semantic_cache.lookup(
                        context=cache_context,
                        intent_vector=intent_vector,
                    )
                    cached_output = _cached_query_output(
                        cache_lookup,
                        active=active,
                        plan=plan,
                        l1_controller_mode=self.l1_controller_mode,
                        l1_control_policy=self.l1_control_policy,
                    )
                    if cache_lookup.hit and cached_output is None:
                        cache_lookup = SemanticCacheLookup(
                            mode=semantic_cache.mode,
                            hit=False,
                            reason="CACHE_MISS_OUTPUT_BINDING_INVALID",
                            entry_id=cache_lookup.entry_id,
                            similarity=cache_lookup.similarity,
                        )
                except (OSError, TypeError, ValueError) as exc:
                    cache_lookup = SemanticCacheLookup(
                        mode=semantic_cache.mode,
                        hit=False,
                        reason=f"CACHE_EMBEDDING_ERROR:{type(exc).__name__}",
                    )
        recorder.record(
            stage="L0",
            event_type="route_decision",
            payload={
                "semantic_cache": {
                    "mode": semantic_cache.mode,
                    "hit": cached_output is not None,
                    "reason": cache_lookup.reason,
                    "entry_id": cache_lookup.entry_id,
                    "similarity": cache_lookup.similarity,
                    "context_digest": digest(semantic_cache.compatibility(cache_context)),
                    "policy_digest": semantic_cache.policy_digest,
                    "embedding_binding": cache_binding,
                }
            },
            correlation=_correlation(request_id),
            route=route["query_route"],
        )

        state.cache_binding = cache_binding
        state.cache_context = cache_context
        state.cache_lookup = cache_lookup
        state.cached_output = cached_output
        state.dense = dense
        state.encoded_intent = encoded_intent
        state.intent_digest = intent_digest
        state.intent_vector = intent_vector
        state.key = key
        state.requirement = requirement
        state.semantic_cache = semantic_cache
        state.value = value

    def _stage_baseline_retrieval(self, state: _QueryRunState) -> None:
        active = state.active
        audiences = state.audiences
        cache_lookup = state.cache_lookup
        cached_output = state.cached_output
        card_assertion_scope = state.card_assertion_scope
        classification = state.classification
        connected_evidence_map = state.connected_evidence_map
        effective_assertion_scope = state.effective_assertion_scope
        encoded_intent = state.encoded_intent
        exact = state.exact
        graph_path = state.graph_path
        graph_retrieval = state.graph_retrieval
        hydrated = state.hydrated
        key = state.key
        lane = state.lane
        packet = state.packet
        plan = state.plan
        question = state.question
        recorder = state.recorder
        request_id = state.request_id
        resolved_card_scope = state.resolved_card_scope
        result = state.result
        retrieval = state.retrieval
        route = state.route
        row = state.row
        run_dir = state.run_dir
        run_id = state.run_id
        sidecar_path = state.sidecar_path
        status_snapshot_scope = state.status_snapshot_scope
        value = state.value
        exact = (
            deepcopy(cached_output["exact_retrieval"])
            if cached_output is not None
            else exact_retrieval(
                question,
                graph_path,
                classification=classification,
                audiences=audiences,
                retrieval_mode=plan["retrieval_mode"],
                allowed_assertion_ids=(
                    None
                    if resolved_card_scope is None
                    else resolved_card_scope["assertion_ids"]
                ),
            )
        )
        write_json(run_dir / "exact_retrieval.json", exact)
        recorder.record(
            stage="C0.1",
            event_type="retrieval_event",
            payload=exact | {"semantic_cache_replay": cached_output is not None},
            correlation=_correlation(request_id),
            route=route["query_route"],
            record_count=exact["anchor_count"],
        )
        status_snapshot_scope = exact.get("status_snapshot_assertion_ids")
        if status_snapshot_scope is not None and not isinstance(status_snapshot_scope, list):
            raise RuntimeError("status snapshot retrieval scope is invalid")
        card_assertion_scope = (
            None if resolved_card_scope is None else resolved_card_scope["assertion_ids"]
        )
        if status_snapshot_scope is None:
            effective_assertion_scope = card_assertion_scope
        elif card_assertion_scope is None:
            effective_assertion_scope = status_snapshot_scope
        else:
            effective_assertion_scope = sorted(set(status_snapshot_scope) & set(card_assertion_scope))
        if cached_output is None:
            if encoded_intent is None:
                encoded_intent = self.backend.encode(question)
                state.encoded_intent = encoded_intent
                dense = encoded_intent.get("dense")
                if dense is not None:
                    state.dense = dense
                    state.intent_vector = [float(value) for value in dense]
            else:
                state.encoded_intent = encoded_intent
                if state.dense is None and dense is not None:
                    state.dense = dense
                    state.intent_vector = [float(value) for value in dense]
        retrieval = (
            deepcopy(cached_output["hybrid_retrieval"])
            if cached_output is not None
            else get_global_retriever_cache().get(
                sidecar_path, self.backend, active.get("generation_id")
            ).retrieve(
                question,
                plan["allowed_lanes"],
                classification=classification,
                audiences=audiences,
                retrieval_mode=plan["retrieval_mode"],
                encoded_query=encoded_intent,
                allowed_assertion_ids=(
                    effective_assertion_scope
                ),
                bm25_assertion_ranks_by_lane=(
                    exact["bm25_ranks_by_lane"]
                    if exact["bm25_executed"]
                    else None
                ),
                # Exact-anchor injection is deliberately restricted to
                # explicit candidate-review routes.  It makes newly ingested
                # candidate material discoverable without perturbing the
                # ranking or graph expansion of approved/strategic answers.
                exact_anchor_assertion_ids_by_lane=(
                    {
                        "candidate": list(
                            dict.fromkeys(
                                row["assertion_id"]
                                for row in exact["exact_anchors"]
                                if row["lane"] == "candidate"
                            )
                        )
                    }
                    if (
                        plan["retrieval_mode"]
                        in {
                            "candidate_review",
                            "candidate_evaluation",
                            "candidate_guidance",
                            "status_snapshot_current",
                            "status_snapshot_history",
                        }
                        and "candidate" in plan["allowed_lanes"]
                    )
                    else None
                ),
            )
        )
        ReleaseManager._verify_backend_snapshot(self.backend)
        write_json(run_dir / "hybrid_retrieval.json", retrieval)
        recorder.record(
            stage="C0.2",
            event_type="retrieval_event",
            payload=retrieval | {"semantic_cache_replay": cached_output is not None},
            correlation=_correlation(request_id),
            route=route["query_route"],
            model_id=active["model_id"],
            generation_id=active["generation_id"],
            record_count=retrieval["final_record_count"],
        )
        for lane in plan["allowed_lanes"]:
            for result in retrieval["lanes"][lane]["results"]:
                recorder.record(
                    stage="C0.2",
                    event_type="retrieved_record",
                    payload={key: value for key, value in result.items() if key != "record"}
                    | {
                        "record_digest": result["record"]["canonical_digest"]["value"],
                        "namespace": result["record"]["namespace"],
                    },
                    correlation=_correlation(request_id),
                    route=route["query_route"],
                    lane=lane,
                    model_id=active["model_id"],
                    generation_id=active["generation_id"],
                )
        if cached_output is not None:
            (
                graph_retrieval,
                connected_evidence_map,
                hydrated,
                packet,
            ) = _rebind_cached_retrieval_artifacts(
                cached_output,
                run_id=run_id,
                plan=plan,
                lookup=cache_lookup,
            )
        else:
            graph_retrieval, connected_evidence_map = expand_connected_retrieval(
                retrieval=retrieval,
                question=question,
                graph_path=graph_path,
                sidecar_path=sidecar_path,
                run_id=run_id,
                graph_snapshot=active["graph_snapshot"],
                query_plan_digest=plan["canonical_digest"]["value"],
                requested_hops=plan["retrieval"]["graph_hops"],
                relationships_to_inspect=plan["relationships_to_inspect"],
                classification=classification,
                audiences=audiences,
                retrieval_mode=plan["retrieval_mode"],
                allowed_assertion_ids=(
                    effective_assertion_scope
                ),
            )
            hydrated = hydrate_results(
                graph_retrieval,
                graph_path,
                allowed_assertion_ids=(
                    effective_assertion_scope
                ),
                connected_evidence_map=connected_evidence_map,
            )
            packet = build_evidence_packet(
                hydrated=hydrated,
                graph_snapshot=active["graph_snapshot"],
                exact=exact,
                retrieval_mode=plan["retrieval_mode"],
                card_scope=resolved_card_scope,
                connected_evidence_map=connected_evidence_map,
            )
        state.write_artifact(run_dir / "graph_retrieval.json", graph_retrieval)
        state.write_artifact(run_dir / "connected_evidence_map.json", connected_evidence_map)
        state.write_artifact(run_dir / "graph_hydration.json", hydrated)
        state.write_artifact(run_dir / "exact_retrieval.json", exact)
        state.write_artifact(run_dir / "hybrid_retrieval.json", retrieval)
        state.write_artifact(run_dir / "evidence_packet.json", packet)
        strategic_evidence_artifact: dict[str, Any] | None = None
        state.card_assertion_scope = card_assertion_scope
        state.connected_evidence_map = connected_evidence_map
        state.effective_assertion_scope = effective_assertion_scope
        state.exact = exact
        state.graph_retrieval = graph_retrieval
        state.hydrated = hydrated
        state.key = key
        state.lane = lane
        state.packet = packet
        state.result = result
        state.retrieval = retrieval
        state.row = row
        state.status_snapshot_scope = status_snapshot_scope
        state.strategic_evidence_artifact = strategic_evidence_artifact
        state.value = value

    def _stage_coverage_augmentation(self, state: _QueryRunState) -> None:
        active, audiences, classification = state.active, state.audiences, state.classification
        aug_connected_evidence_map, aug_graph_retrieval = state.aug_connected_evidence_map, state.aug_graph_retrieval
        aug_hydrated, aug_packet, aug_report = state.aug_hydrated, state.aug_packet, state.aug_report
        authorization_scope_digest, cached_output = state.authorization_scope_digest, state.cached_output
        connected_evidence_map, coverage_manifest = state.connected_evidence_map, state.coverage_manifest
        effective_assertion_scope, exact = state.effective_assertion_scope, state.exact
        graph_path, graph_retrieval, hydrated = state.graph_path, state.graph_retrieval, state.hydrated
        key, packet, plan, portfolio = state.key, state.packet, state.plan, state.portfolio
        question, recorder, request_id = state.question, state.recorder, state.request_id
        resolved_card_scope, retrieval, retrieval_evidence_report = state.resolved_card_scope, state.retrieval, state.retrieval_evidence_report
        route, run_dir, run_id = state.route, state.run_dir, state.run_id
        selected_type, sidecar_path = state.selected_type, state.sidecar_path
        strategic_evidence_artifact, value = state.strategic_evidence_artifact, state.value
        if selected_type == "opportunity_nba":
            portfolio = packet.get("strategic_context")
            if not isinstance(portfolio, dict):
                portfolio = build_strategic_channels(packet)
                packet["strategic_context"] = portfolio
                packet["packet_digest"] = digest(
                    {key: value for key, value in packet.items() if key != "packet_digest"}
                )
                write_json(run_dir / "evidence_packet.json", packet)
            strategic_evidence_artifact = create_strategic_evidence_artifact(
                query_run_id=run_id,
                graph_snapshot=active["graph_snapshot"],
                source_packet_digest=packet["packet_digest"],
                portfolio=portfolio,
            )
            write_json(
                run_dir / "strategic_evidence_packet.json",
                strategic_evidence_artifact,
            )
        retrieval_evidence_report = build_retrieval_evidence_report(
            coverage_manifest=coverage_manifest,
            packet=packet,
            authorization_scope_digest=authorization_scope_digest,
            graph_sha256=active["graph_sha256"],
            view_authorized=True,
        )
        state.write_artifact(run_dir / "retrieval_evidence_report.json", retrieval_evidence_report)
        state.write_text_artifact(
            run_dir / "retrieval_evidence.md",
            render_retrieval_evidence_markdown(
                coverage_manifest, retrieval_evidence_report
            ),
        )
        # ── Coverage-driven supplemental retrieval ──────────────────────
        # When the L1 controller is off and initial coverage has gaps,
        # run targeted BM25 queries from uncovered requirement labels to
        # pull missing risk/gap/dependency/opportunity records.
        coverage_augmentation_count = 0
        if (
            self.l1_controller_mode == "off"
            and not coverage_complete(retrieval_evidence_report)
            and cached_output is None
        ):
            (
                aug_report,
                aug_packet,
                aug_graph_retrieval,
                aug_connected_evidence_map,
                aug_hydrated,
                coverage_augmentation_count,
            ) = coverage_supplemental_retrieval(
                retrieval_evidence_report=retrieval_evidence_report,
                coverage_manifest=coverage_manifest,
                retriever=get_global_retriever_cache().get(
                    sidecar_path, self.backend, active.get("generation_id")
                ),
                retrieval=retrieval,
                question=question,
                graph_path=graph_path,
                sidecar_path=sidecar_path,
                run_id=run_id,
                graph_snapshot=active["graph_snapshot"],
                query_plan_digest=plan["canonical_digest"]["value"],
                requested_hops=plan["retrieval"]["graph_hops"],
                relationships_to_inspect=plan["relationships_to_inspect"],
                classification=classification,
                audiences=audiences,
                retrieval_mode=plan["retrieval_mode"],
                allowed_assertion_ids=effective_assertion_scope,
                exact=exact,
                card_scope=resolved_card_scope,
                active=active,
                authorization_scope_digest=authorization_scope_digest,
                max_supplemental_queries=1 if state.decision_frame is not None else 5,
                comparison_mode=state.comparison_mode,
                request_context=state.request_context,
            )
            if aug_report is not None:
                retrieval_evidence_report = aug_report
                packet = aug_packet
                graph_retrieval = aug_graph_retrieval
                connected_evidence_map = aug_connected_evidence_map
                hydrated = aug_hydrated
                # Persist the augmented artifacts
                write_json(run_dir / "graph_retrieval.json", graph_retrieval)
                write_json(run_dir / "connected_evidence_map.json", connected_evidence_map)
                write_json(run_dir / "graph_hydration.json", hydrated)
                write_json(run_dir / "evidence_packet.json", packet)
                write_json(run_dir / "retrieval_evidence_report.json", retrieval_evidence_report)
                (run_dir / "retrieval_evidence.md").write_text(
                    render_retrieval_evidence_markdown(
                        coverage_manifest, retrieval_evidence_report
                    ),
                    encoding="utf-8",
                )
                recorder.record(
                    stage="C0.3",
                    event_type="pipeline_event",
                    payload={
                        "pipeline_event_kind": "coverage_supplemental_retrieval",
                        "supplemental_query_count": coverage_augmentation_count,
                        "coverage_improved": True,
                    },
                    correlation=_correlation(request_id),
                    route=route["query_route"],
                )
        state.aug_connected_evidence_map = aug_connected_evidence_map
        state.aug_graph_retrieval = aug_graph_retrieval
        state.aug_hydrated = aug_hydrated
        state.aug_packet = aug_packet
        state.aug_report = aug_report
        state.connected_evidence_map = connected_evidence_map
        state.coverage_augmentation_count = coverage_augmentation_count
        state.graph_retrieval = graph_retrieval
        state.hydrated = hydrated
        state.key = key
        state.packet = packet
        state.portfolio = portfolio
        state.retrieval_evidence_report = retrieval_evidence_report
        state.strategic_evidence_artifact = strategic_evidence_artifact
        state.value = value

    def _stage_prepare_l2(self, state: _QueryRunState) -> None:
        action = state.action
        action_result = state.action_result
        active = state.active
        assurance_profile = state.assurance_profile
        attempt_zero = state.attempt_zero
        attempt_zero_action = state.attempt_zero_action
        audiences = state.audiences
        authorization_scope_digest = state.authorization_scope_digest
        bounded_telemetry = state.bounded_telemetry
        budget_before_action = state.budget_before_action
        cache_binding = state.cache_binding
        cache_lookup = state.cache_lookup
        classification = state.classification
        compiled_prompt = state.compiled_prompt
        connected_evidence_map = state.connected_evidence_map
        controlled = state.controlled
        controller = state.controller
        controller_plan = state.controller_plan
        coverage_manifest = state.coverage_manifest
        current_trace_id = state.current_trace_id
        destination_artifact = state.destination_artifact
        destination_decision = state.destination_decision
        effective_assertion_scope = state.effective_assertion_scope
        exact = state.exact
        external_l2_offline = state.external_l2_offline
        graph_fit_decision = state.graph_fit_decision
        graph_hash_before = state.graph_hash_before
        graph_path = state.graph_path
        graph_retrieval = state.graph_retrieval
        hydrated = state.hydrated
        ids = state.ids
        key = state.key
        l1_terminal = state.l1_terminal
        l2_decision = state.l2_decision
        l2_decision_artifact = state.l2_decision_artifact
        l2_packet = state.l2_packet
        lane = state.lane
        lane_data = state.lane_data
        observation_one = state.observation_one
        observation_zero = state.observation_zero
        observations = state.observations
        output_dir = state.output_dir
        packet = state.packet
        plan = state.plan
        prior_report = state.prior_report
        quality_config = state.quality_config
        question = state.question
        recorder = state.recorder
        request_id = state.request_id
        requirement_by_id = state.requirement_by_id
        requirement_id = state.requirement_id
        resolved_card_scope = state.resolved_card_scope
        retrieval = state.retrieval
        retrieval_evidence_report = state.retrieval_evidence_report
        route = state.route
        row = state.row
        run_dir = state.run_dir
        run_id = state.run_id
        runtime_binding = state.runtime_binding
        sealed_queries = state.sealed_queries
        selected_before = state.selected_before
        selected_type = state.selected_type
        semantic_cache = state.semantic_cache
        sidecar_path = state.sidecar_path
        source_action = state.source_action
        summary_path = state.summary_path
        validated_request = state.validated_request
        value = state.value
        values = state.values
        graph_fit_decision = assess_query_graph_fit(
            destination_decision=destination_artifact,
            plan=plan,
            connected_evidence_map=connected_evidence_map,
            evidence_packet=packet,
            active_pair=active,
        )
        state.write_artifact(run_dir / "query_graph_fit_decision.json", graph_fit_decision)
        l1_terminal: dict[str, Any] | None = None
        if (
            self.l1_controller_mode == "bounded"
            and self.l1_control_policy is not None
            and feedback_eligible(
                mode="bounded",
                destination=destination_decision.destination.value,
                graph_fit_decision=graph_fit_decision,
                evidence_report=retrieval_evidence_report,
            )
        ):
            observations = {
                row["requirement_id"]: {
                    "outcome": row["outcome"],
                    "reason_code": row.get("reason_code") or "RETRIEVAL_COVERAGE_MISSING",
                }
                for row in retrieval_evidence_report["requirement_outcomes"]
            }
            controller = compile_query_plan_candidate(
                plan,
                coverage_manifest,
                self.l1_control_policy,
                mode="bounded",
                observations=observations,
            )
            if controller.actions:
                controller_plan = controller.plan
                attempt_zero, attempt_zero_action = build_attempt_zero(
                    plan=plan,
                    coverage_manifest=coverage_manifest,
                    evidence_report=retrieval_evidence_report,
                    active_pair=active,
                    graph_sha256_before=active["graph_sha256"],
                    graph_sha256_after=active["graph_sha256"],
                )
                observation_zero, _ = build_observation_zero(
                    plan=plan,
                    coverage_manifest=coverage_manifest,
                    evidence_report=retrieval_evidence_report,
                    attempt_zero=attempt_zero,
                    graph_sha256_before=active["graph_sha256"],
                    graph_sha256_after=active["graph_sha256"],
                )
                selected_before = retrieval_evidence_report["selected_record_ids_by_lane"]
                budget_before_action = BudgetState(
                    actions_used=1,
                    records_by_lane=tuple(
                        (lane, tuple(values))
                        for lane, values in selected_before.items()
                        if values
                    ),
                    action_ids=(attempt_zero_action.action_id,),
                    attempt_ids=(attempt_zero_action.attempt_id,),
                )
                source_action = controller.actions[0]
                action = create_action(
                    parent_plan_digest=controller_plan["canonical_digest"]["value"],
                    coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"],
                    authorization_scope_digest=authorization_scope_digest,
                    attempt_ordinal=1,
                    action_type=source_action.action_type,
                    requirement_ids=source_action.requirement_ids,
                    input_digest=source_action.input_digest,
                )
                requirement_by_id = {
                    row["requirement_id"]: row for row in coverage_manifest["requirements"]
                }
                sealed_queries = {
                    requirement_id: compile_requirement_query(
                        requirement_by_id[requirement_id], controller_plan
                    )
                    for requirement_id in action.requirement_ids
                }
                action_result = execute_l1_action(
                    action,
                    plan=controller_plan,
                    coverage_manifest=coverage_manifest,
                    requirement_queries=sealed_queries,
                    active_pair=active,
                    graph_path=graph_path,
                    sidecar_path=sidecar_path,
                    backend=self.backend,
                    policy=self.l1_control_policy,
                    budget=budget_before_action,
                    classification=classification,
                    audiences=audiences,
                    allowed_lanes=plan["allowed_lanes"],
                    retrieval_mode=plan["retrieval_mode"],
                    authorization_scope_digest=authorization_scope_digest,
                    allowed_assertion_ids=effective_assertion_scope,
                    card_scope=resolved_card_scope,
                    bm25_assertion_ranks_by_lane=(
                        exact["bm25_ranks_by_lane"] if exact["bm25_executed"] else None
                    ),
                )
                write_json(run_dir / "query_plan_v3.json", controller_plan)
                write_json(run_dir / "query_retrieval_attempt_zero.json", attempt_zero)
                write_json(run_dir / "query_plan_observation_zero.json", observation_zero)
                write_json(run_dir / "query_retrieval_attempt_one.json", action_result.attempt_artifact)
                if action_result.retrieval_artifact is not None:
                    write_controlled_retrieval_artifact(
                        run_dir / "query_retrieval_controlled_one.json", action_result
                    )
                if action_result.status == "completed" and action_result.retrieval_artifact is not None:
                    controlled = deepcopy(action_result.retrieval_artifact)
                    for lane_data in controlled["lanes"].values():
                        for row in lane_data["results"]:
                            row["l1_target_requirement_count"] = len(action.requirement_ids)
                    retrieval = merge_retrieval_results(
                        initial_retrieval=retrieval,
                        action_retrievals=[controlled],
                        allowed_lanes=plan["allowed_lanes"],
                        max_records=self.l1_control_policy.max_unique_records,
                    )
                    graph_retrieval, connected_evidence_map = expand_connected_retrieval(
                        retrieval=retrieval,
                        question=question,
                        graph_path=graph_path,
                        sidecar_path=sidecar_path,
                        run_id=run_id,
                        graph_snapshot=active["graph_snapshot"],
                        query_plan_digest=plan["canonical_digest"]["value"],
                        requested_hops=plan["retrieval"]["graph_hops"],
                        relationships_to_inspect=plan["relationships_to_inspect"],
                        classification=classification,
                        audiences=audiences,
                        retrieval_mode=plan["retrieval_mode"],
                        allowed_assertion_ids=effective_assertion_scope,
                    )
                    hydrated = hydrate_results(
                        graph_retrieval,
                        graph_path,
                        allowed_assertion_ids=effective_assertion_scope,
                        connected_evidence_map=connected_evidence_map,
                    )
                    packet = build_evidence_packet(
                        hydrated=hydrated,
                        graph_snapshot=active["graph_snapshot"],
                        exact=exact,
                        retrieval_mode=plan["retrieval_mode"],
                        card_scope=resolved_card_scope,
                        connected_evidence_map=connected_evidence_map,
                    )
                    if selected_type == "opportunity_nba":
                        packet["strategic_context"] = build_strategic_channels(packet)
                        packet["packet_digest"] = digest(
                            {key: value for key, value in packet.items() if key != "packet_digest"}
                        )
                    prior_report = retrieval_evidence_report
                    retrieval_evidence_report = build_retrieval_evidence_report(
                        coverage_manifest=coverage_manifest,
                        packet=packet,
                        authorization_scope_digest=authorization_scope_digest,
                        graph_sha256=active["graph_sha256"],
                        view_authorized=True,
                    )
                    graph_fit_decision = assess_query_graph_fit(
                        destination_decision=destination_artifact,
                        plan=plan,
                        connected_evidence_map=connected_evidence_map,
                        evidence_packet=packet,
                        active_pair=active,
                    )
                else:
                    prior_report = retrieval_evidence_report
                observation_one = build_observation_one(
                    plan=controller_plan,
                    coverage_manifest=coverage_manifest,
                    evidence_report=retrieval_evidence_report,
                    action_attempt=action_result.attempt_artifact,
                    prior_report=prior_report,
                    graph_sha256=active["graph_sha256"],
                )
                l1_terminal = build_bounded_terminal(
                    plan=controller_plan,
                    coverage_manifest=coverage_manifest,
                    final_evidence_report=retrieval_evidence_report,
                    attempts=[attempt_zero, action_result.attempt_artifact],
                    observation_one=observation_one,
                    budget=action_result.budget_after,
                    graph_sha256=active["graph_sha256"],
                )
                write_json(run_dir / "query_plan_observation_one.json", observation_one)
                write_json(run_dir / "query_plan_terminal.json", l1_terminal)
                write_json(run_dir / "hybrid_retrieval.json", retrieval)
                write_json(run_dir / "graph_retrieval.json", graph_retrieval)
                write_json(run_dir / "connected_evidence_map.json", connected_evidence_map)
                write_json(run_dir / "graph_hydration.json", hydrated)
                write_json(run_dir / "evidence_packet.json", packet)
                write_json(run_dir / "retrieval_evidence_report.json", retrieval_evidence_report)
                write_json(run_dir / "query_graph_fit_decision.json", graph_fit_decision)
                bounded_telemetry = bounded_telemetry_payload(
                    plan_digest=controller_plan["canonical_digest"]["value"],
                    coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"],
                    terminal=l1_terminal,
                    budget=action_result.budget_after,
                    target_count=len(action.requirement_ids),
                    newly_covered_count=len(observation_one["newly_covered_requirement_ids"]),
                )
                recorder.record(
                    stage="L1",
                    event_type="l1_bounded",
                    payload=bounded_telemetry,
                    correlation=_correlation(request_id),
                    record_count=sum(len(ids) for ids in retrieval_evidence_report["selected_record_ids_by_lane"].values()),
                )
        if (
            self.l1_controller_mode == "bounded"
            and self.l1_control_policy is not None
            and l1_terminal is None
            and coverage_complete(retrieval_evidence_report)
        ):
            observations = {
                row["requirement_id"]: {
                    "outcome": row["outcome"],
                    "reason_code": row.get("reason_code") or "RETRIEVAL_COVERAGE_MISSING",
                }
                for row in retrieval_evidence_report["requirement_outcomes"]
            }
            controller = compile_query_plan_candidate(
                plan,
                coverage_manifest,
                self.l1_control_policy,
                mode="bounded",
                observations=observations,
            )
            attempt_zero, _ = build_attempt_zero(
                plan=plan,
                coverage_manifest=coverage_manifest,
                evidence_report=retrieval_evidence_report,
                active_pair=active,
                graph_sha256_before=active["graph_sha256"],
                graph_sha256_after=active["graph_sha256"],
            )
            observation_zero, _ = build_observation_zero(
                plan=plan,
                coverage_manifest=coverage_manifest,
                evidence_report=retrieval_evidence_report,
                attempt_zero=attempt_zero,
                graph_sha256_before=active["graph_sha256"],
                graph_sha256_after=active["graph_sha256"],
            )
            l1_terminal = build_complete_first_pass_terminal(
                controller_plan=controller.plan,
                coverage_manifest=coverage_manifest,
                evidence_report=retrieval_evidence_report,
                attempt_zero=attempt_zero,
                graph_sha256=active["graph_sha256"],
            )
            write_json(run_dir / "query_plan_v3.json", controller.plan)
            write_json(run_dir / "query_retrieval_attempt_zero.json", attempt_zero)
            write_json(run_dir / "query_plan_observation_zero.json", observation_zero)
            write_json(run_dir / "query_plan_terminal.json", l1_terminal)
            bounded_telemetry = bounded_telemetry_payload(
                plan_digest=controller.plan["canonical_digest"]["value"],
                coverage_manifest_digest=coverage_manifest["canonical_digest"]["value"],
                terminal=l1_terminal,
                budget=BudgetState(
                    actions_used=1,
                    records_by_lane=tuple(
                        (lane, tuple(values))
                        for lane, values in retrieval_evidence_report[
                            "selected_record_ids_by_lane"
                        ].items()
                        if values
                    ),
                ),
                target_count=0,
                newly_covered_count=0,
            )
            recorder.record(
                stage="L1",
                event_type="l1_bounded",
                payload=bounded_telemetry,
                correlation=_correlation(request_id),
                record_count=sum(len(ids) for ids in retrieval_evidence_report["selected_record_ids_by_lane"].values()),
            )
        recorder.record(
            stage="C0.3",
            event_type="graph_path",
            payload={
                "connected_evidence_map_id": connected_evidence_map["map_id"],
                "connected_evidence_map_digest": connected_evidence_map[
                    "canonical_digest"
                ]["value"],
                "requested_hops": connected_evidence_map["requested_hops"],
                "max_hop_reached": connected_evidence_map["max_hop_reached"],
                "metrics": connected_evidence_map["metrics"],
                "graph_paths": hydrated["graph_paths"],
            },
            correlation=_correlation(request_id),
            route=route["query_route"],
            record_count=len(hydrated["graph_paths"]),
        )
        recorder.record(
            stage="C0.3",
            event_type="evidence_packet",
            payload=packet,
            correlation=_correlation(request_id),
            route=route["query_route"],
            record_count=sum(len(packet[lane]) for lane in plan["allowed_lanes"]),
        )

        if (
            destination_decision.destination is QueryDestination.NEE_DECISION_INTELLIGENCE
            and (
                not graph_fit_decision["l2_allowed"]
                or not coverage_complete(retrieval_evidence_report)
            )
        ):
            state.final = _finish_pre_l2_blocked(
                manager=self.manager,
                backend=self.backend,
                decision=destination_decision,
                decision_artifact=destination_artifact,
                graph_fit_decision=graph_fit_decision,
                plan=plan,
                retrieval=retrieval,
                graph_retrieval=graph_retrieval,
                route_decision=route,
                coverage_manifest=coverage_manifest,
                retrieval_evidence_report=retrieval_evidence_report,
                packet=packet,
                connected_evidence_map=connected_evidence_map,
                semantic_cache=semantic_cache,
                cache_binding=cache_binding,
                cache_lookup=cache_lookup,
                question=question,
                quality_config=quality_config,
                assurance_profile=assurance_profile,
                recorder=recorder,
                request_id=request_id,
                run_id=run_id,
                current_trace_id=current_trace_id,
                active=active,
                graph_path=graph_path,
                graph_hash_before=graph_hash_before,
                runtime_binding=runtime_binding,
                runtime_policy_digests=self._runtime_policy_digests(active, quality_config),
                summary_path=summary_path,
                run_dir=run_dir,
                output_dir=output_dir,
                l1_controller_mode=self.l1_controller_mode,
                l1_control_policy=self.l1_control_policy,
                l1_terminal=l1_terminal,
            )
            return

        l2_packet = filter_packet_for_l2(packet, retrieval_evidence_report)
        state.write_artifact(run_dir / "l2_evidence_packet.json", l2_packet)

        # --- Phase 3: FinalEvidenceContract (C0 -> PA boundary) ---
        final_evidence_contract = create_final_evidence_contract(
            query_id=request_id or "unknown-query",
            run_id=run_id or "unknown-run",
            graph_generation_id=active.get("generation_id", "gen-default") if isinstance(active, Mapping) else "gen-default",
            graph_sha256=graph_hash_before if isinstance(graph_hash_before, str) and len(graph_hash_before) == 64 else ("0" * 64),
            sidecar_sha256=(
                active.get("sidecar_sha256")
                or active.get("index_sha256")
                or ("0" * 64)
            ) if isinstance(active, Mapping) else ("0" * 64),
            packet=l2_packet,
            retrieval_evidence_report=retrieval_evidence_report,
            coverage_manifest=coverage_manifest,
            classification=classification,
            audiences=audiences,
            retrieval_metadata={
                "retrieval_mode": plan.get("retrieval_mode", "unknown"),
                "total_candidates_considered": sum(len(packet.get(lane, [])) for lane in plan.get("allowed_lanes", [])),
                "total_records_selected": sum(len(l2_packet.get(lane, [])) for lane in plan.get("allowed_lanes", [])),
            },
        )
        state.write_artifact(run_dir / "final_evidence_contract.json", final_evidence_contract)

        compiled_prompt = assemble_prompt(question, plan, route, l2_packet)
        l2_decision = decide_l2_execution(
            validated_request=validated_request,
            plan=plan,
            route=route,
            evidence_packet=l2_packet,
            config=self.l2_config,
            prompt=compiled_prompt,
            environment=(CredentialManager.get_process_environment() | {"NEE_EXTERNAL_L2_OFFLINE": "1"} if external_l2_offline else None),
        )
        l2_decision_artifact = create_l2_decision_artifact(
            decision=l2_decision,
            destination_decision=destination_artifact,
            query_plan=plan,
            route_decision=route,
            coverage_manifest=coverage_manifest,
            evidence_report=retrieval_evidence_report,
            l1_terminal=l1_terminal,
        )
        state.write_artifact(run_dir / "l2_execution_decision.json", l2_decision_artifact)

        # --- Phase 3: CompiledPromptArtifact (PA -> L2 boundary) ---
        compiled_prompt_artifact = compile_prompt_artifact(
            query_id=request_id or "unknown-query",
            run_id=run_id or "unknown-run",
            evidence_contract=final_evidence_contract,
            l2_execution_decision_id=l2_decision_artifact.get("artifact_id", "l2-exec-unknown"),
            compiled_prompt=compiled_prompt,
            question=question,
            plan=plan,
            route=route,
            target_model=l2_decision.selected_model or resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING),
            reasoning_effort="high",
            materiality=plan.get("materiality"),
            assurance_profile=assurance_profile,
            classification=classification,
            audiences=audiences,
        )
        state.write_artifact(run_dir / "compiled_prompt_artifact.json", compiled_prompt_artifact)
        decision_otel = {
            "query_type": l2_decision.query_type,
            "materiality": l2_decision.materiality,
            "assurance_profile": assurance_profile,
            "l2_policy": l2_decision.policy,
            "l2_required": l2_decision.required,
            "l2_reason_code": l2_decision.reason_code,
            "selected_model_profile": l2_decision.selected_model_profile,
            "selected_model": l2_decision.selected_model,
            "provider_egress_allowed": l2_decision.provider_egress_allowed,
            "approved_evidence_count": l2_decision.approved_evidence_count,
            "candidate_evidence_count": l2_decision.candidate_evidence_count,
            "external_public_evidence_count": l2_decision.external_public_evidence_count,
            "conflict_count": l2_decision.conflict_count,
            "gap_count": l2_decision.gap_count,
            "graph_path_count": l2_decision.graph_path_count,
            "request_input_chars": l2_decision.request_metrics[
                "request_input_chars"
            ],
            "request_schema_chars": l2_decision.request_metrics[
                "request_schema_chars"
            ],
            "request_serialized_chars": l2_decision.request_metrics[
                "request_serialized_chars"
            ],
            "request_payload_digest": l2_decision.request_metrics[
                "request_payload_digest"
            ],
            "budget_status": l2_decision.budget_verdict["status"],
            "budget_reason_code": l2_decision.budget_verdict["reason_code"],
            "judge_call_count": 0,
            "challenger_call_count": 0,
        }
        state.action = action
        state.action_result = action_result
        state.attempt_zero = attempt_zero
        state.attempt_zero_action = attempt_zero_action
        state.bounded_telemetry = bounded_telemetry
        state.budget_before_action = budget_before_action
        state.compiled_prompt = compiled_prompt
        state.connected_evidence_map = connected_evidence_map
        state.controlled = controlled
        state.controller = controller
        state.controller_plan = controller_plan
        state.decision_otel = decision_otel
        state.graph_fit_decision = graph_fit_decision
        state.graph_retrieval = graph_retrieval
        state.hydrated = hydrated
        state.ids = ids
        state.key = key
        state.l1_terminal = l1_terminal
        state.l2_decision = l2_decision
        state.l2_decision_artifact = l2_decision_artifact
        state.l2_packet = l2_packet
        state.lane = lane
        state.lane_data = lane_data
        state.observation_one = observation_one
        state.observation_zero = observation_zero
        state.observations = observations
        state.packet = packet
        state.prior_report = prior_report
        state.requirement_by_id = requirement_by_id
        state.requirement_id = requirement_id
        state.retrieval = retrieval
        state.retrieval_evidence_report = retrieval_evidence_report
        state.row = row
        state.sealed_queries = sealed_queries
        state.selected_before = selected_before
        state.source_action = source_action
        state.value = value
        state.values = values

    def _stage_execute_l2(self, state: _QueryRunState) -> None:
        cached_output = state.cached_output
        compiled_prompt = state.compiled_prompt
        decision_otel = state.decision_otel
        l2_decision = state.l2_decision
        l2_decision_artifact = state.l2_decision_artifact
        l2_execution = state.l2_execution
        l2_packet = state.l2_packet
        plan = state.plan
        prompt = state.prompt
        question = state.question
        recorder = state.recorder
        request_id = state.request_id
        route = state.route
        run_dir = state.run_dir
        validated_request = state.validated_request
        recorder.record(
            stage="L2D",
            event_type="l2_execution_decision",
            payload=l2_decision_artifact,
            correlation=_correlation(request_id),
            route=route["query_route"],
            model_id=l2_decision.selected_model,
            otel_attributes=decision_otel,
        )

        prompt: dict[str, Any] | None = None
        if l2_decision.required:
            prompt = compiled_prompt
            write_json(run_dir / "prompt_assembly.json", prompt)
            recorder.record(
                stage="PA",
                event_type="prompt_event",
                payload={
                    "template_version": prompt["template_version"],
                    "slot_digests": prompt["slot_digests"],
                    "evidence_packet_hash": prompt["evidence_packet_hash"],
                    "prompt_hash": prompt["prompt_hash"],
                    "request_metrics": dict(l2_decision.request_metrics),
                    "budget_verdict": dict(l2_decision.budget_verdict),
                },
                correlation=_correlation(request_id),
                route=route["query_route"],
            )

        l2_execution = execute_conditional_l2(
            decision=l2_decision,
            config=self.l2_config,
            validated_request=validated_request,
            route=route,
            question=question,
            plan=plan,
            evidence_packet=l2_packet,
            prompt=prompt,
            decision_prompt=compiled_prompt,
            provider_factory=self.provider_factory,
        )
        evidence_only = bool(
            l2_decision.required
            and not l2_decision.provider_egress_allowed
            and l2_execution.disposition is L2Disposition.FAILED
            and l2_execution.provider_failure_code == "PROVIDER_EGRESS_NOT_AUTHORIZED"
        )
        cache_answer_replayed = bool(
            cached_output is not None and not l2_decision.required
        )
        state.cache_answer_replayed = cache_answer_replayed
        state.evidence_only = evidence_only
        state.l2_execution = l2_execution
        state.prompt = prompt

        # --- Phase 4: Seal L2 output for tamper-evident X gate entry ---
        sealed = seal_l2_output(
            run_id=state.run_id or "unknown",
            query_id=request_id or "unknown",
            l2_decision_artifact_id=l2_decision_artifact.get("artifact_id", "unknown"),
            l2_disposition=l2_execution.disposition.value,
            l2_model=l2_decision.selected_model,
            l2_answer=l2_execution.answer,
            l2_provider_call_count=l2_execution.provider_call_count,
            l2_token_usage=getattr(l2_execution, "token_usage", None),
        )
        write_json(run_dir / "sealed_l2_artifact.json", sealed)
        state.seal = sealed

    def _stage_l1_post_l2_review(self, state: _QueryRunState) -> None:  # noqa: C901
        if getattr(state, "l1_feedback_sm", None) is None:
            max_iters = getattr(self, "l1_max_feedback_iterations", 2)
            state.l1_feedback_sm = L1FeedbackStateMachine(max_iterations=max_iters)
            state.l1_feedback_sm.start_cycle()
            state.l1_review_history = []

        sm: L1FeedbackStateMachine = state.l1_feedback_sm
        if sm.state == LoopState.PLANNING:
            sm.mark_executed()
        if sm.state == LoopState.EXECUTING:
            sm.start_review()

        plan = state.plan or {}
        sealed_l2 = state.seal or {}
        cached_output = state.cached_output
        cache_answer_replayed = state.cache_answer_replayed
        l2_decision = state.l2_decision
        l2_execution = state.l2_execution
        evidence_only = state.evidence_only
        packet = state.packet
        l2_packet = state.l2_packet or packet
        question = state.question or ""
        run_dir = state.run_dir
        run_id = state.run_id or "unknown"
        request_id = state.request_id or "unknown"
        recorder = state.recorder
        route = state.route
        retrieval_evidence_report = state.retrieval_evidence_report
        coverage_manifest = state.coverage_manifest

        # Resolve current answer candidate
        if cache_answer_replayed:
            answer = dict(cached_output["answer"])
        elif l2_decision and l2_decision.required:
            answer = (
                dict(l2_execution.answer)
                if l2_execution and l2_execution.answer is not None
                else (
                    evidence_only_over_packet(
                        plan,
                        l2_packet,
                        reason_code=l2_decision.reason_code,
                    )
                    if evidence_only
                    else None
                )
            )
        elif l2_decision and getattr(l2_decision, "evidence_only", False):
            answer = evidence_only_over_packet(
                plan,
                packet,
                reason_code=l2_decision.reason_code,
            )
        else:
            answer = reason_over_packet(plan, packet) if (plan and packet) else None
        state.answer = answer

        # Construct claim evidence map if needed for deterministic evaluation
        if (
            state.claim_evidence_map is None
            and answer is not None
            and retrieval_evidence_report is not None
            and coverage_manifest is not None
        ):
            claim_evidence_map = build_claim_evidence_map(
                answer=answer,
                report=retrieval_evidence_report,
                coverage_manifest=coverage_manifest,
            )
            write_json(run_dir / "claim_evidence_map.json", claim_evidence_map)
            state.claim_evidence_map = claim_evidence_map

        # Phase 1: Run deterministic checks
        deterministic_passed, deterministic_checks = run_l1_deterministic_checks(
            plan=plan,
            sealed_l2=sealed_l2,
            answer=answer,
            evidence_packet=l2_packet or packet,
            claim_evidence_map=state.claim_evidence_map,
            graph_hash_before=state.graph_hash_before,
            graph_hash_after=state.graph_hash_after,
        )

        # Phase 2: Run semantic sufficiency evaluator
        mock_eval = getattr(self, "mock_mode", False) or not bool(
            CredentialManager.get_credential("OPENAI_API_KEY")
        )
        evaluator = L1SemanticSufficiencyEvaluator(mock_mode=mock_eval)
        semantic_result = evaluator.evaluate_sufficiency(
            question=question,
            plan=plan,
            answer=answer,
            sealed_l2=sealed_l2,
            evidence_packet=l2_packet or packet,
        )

        # Phase 3: Synthesize verdict
        if not deterministic_passed:
            fatal_codes = {
                DeterministicReasonCode.GRAPH_MUTATION_VIOLATION.value,
                DeterministicReasonCode.CANONICAL_DIGEST_MISMATCH.value,
            }
            if any(c.reason_code in fatal_codes for c in deterministic_checks if not c.passed):
                verdict = L1ReviewVerdict.FATAL_BLOCK
            elif any(
                c.reason_code == DeterministicReasonCode.L2_EXECUTION_FAILED.value
                for c in deterministic_checks
                if not c.passed
            ):
                verdict = L1ReviewVerdict.INSUFFICIENT_RETRY
            else:
                verdict = L1ReviewVerdict.INSUFFICIENT_REPLAN
        elif not semantic_result.is_sufficient:
            verdict = L1ReviewVerdict.INSUFFICIENT_REPLAN
        else:
            verdict = L1ReviewVerdict.SUFFICIENT

        # Phase 4: Create review contract & write artifacts
        semantic_payload = {
            "subgoal_coverage_score": semantic_result.subgoal_coverage_score,
            "unanswered_subquestions": list(semantic_result.unanswered_subquestions),
            "missing_aspects": list(semantic_result.missing_aspects),
            "rationale": semantic_result.rationale,
            "iteration": sm.current_iteration,
            "max_iterations": sm.max_iterations,
        }
        review_contract = L1PostL2ReviewContract.create(
            run_id=run_id,
            query_id=request_id,
            plan_id=plan.get("plan_id", "unknown"),
            sealed_artifact_id=sealed_l2.get("sealed_artifact_id", "unknown"),
            verdict=verdict,
            deterministic_passed=deterministic_passed,
            deterministic_checks=deterministic_checks,
            semantic_passed=semantic_result.is_sufficient,
            semantic_reasoning=semantic_payload,
            replan_directive=semantic_result.replan_directive,
        )
        contract_dict = review_contract.to_dict()
        write_json(run_dir / f"l1_post_l2_review_iter_{sm.current_iteration}.json", contract_dict)
        write_json(run_dir / "l1_post_l2_review.json", contract_dict)
        state.l1_post_l2_review = contract_dict
        state.l1_post_l2_review_contract = review_contract
        if state.l1_review_history is not None:
            state.l1_review_history.append(contract_dict)

        if recorder is not None:
            recorder.record(
                stage="L1",
                event_type="pipeline_event",
                payload={
                    "l1_post_l2_review": {
                        "verdict": verdict.value,
                        "iteration": sm.current_iteration,
                        "max_iterations": sm.max_iterations,
                        "deterministic_passed": deterministic_passed,
                        "semantic_sufficient": semantic_result.is_sufficient,
                        "subgoal_coverage_score": semantic_result.subgoal_coverage_score,
                        "review_digest": contract_dict["canonical_digest"]["value"],
                    }
                },
                correlation=_correlation(request_id),
                route=route.get("query_route") if isinstance(route, Mapping) else None,
                status="PASS" if verdict is L1ReviewVerdict.SUFFICIENT else "FAIL",
            )

        # Phase 5: Feedback state machine transition & re-plan dispatch
        next_state, payload = sm.process_review_verdict(review_contract)

        if _can_dispatch_l1_replan(next_state, payload, l2_decision, l2_execution):
            new_plan = L1FeedbackStateMachine.apply_replan_to_plan(plan, payload)
            write_json(run_dir / f"replan_plan_iter_{sm.current_iteration}.json", {**new_plan, "replan_iteration": sm.current_iteration})
            valid_plan = dict(new_plan)
            exts = dict(valid_plan.get("extensions") or {})
            exts["nee.l1.replan"] = {
                "iteration": valid_plan.pop("replan_iteration", None),
                "history": valid_plan.pop("replan_history", None),
                "focus_subquestions": valid_plan.pop("focus_subquestions", None),
            }
            valid_plan["extensions"] = exts
            from dual_pipeline.core import seal_artifact
            state.plan = valid_plan = seal_artifact(valid_plan)
            write_json(run_dir / "query_plan.json", valid_plan)
            sm.start_cycle()
            self._stage_coverage_augmentation(state)
            self._stage_prepare_l2(state)
            self._stage_execute_l2(state)
            self._stage_l1_post_l2_review(state)
        elif next_state == LoopState.BLOCKED:
            state.deficiency = f"L1 Post-L2 Review fatal block: {verdict.value}"
        elif next_state == LoopState.EXHAUSTED:
            state.deficiency = f"L1 Feedback Loop budget exhausted after {sm.current_iteration} iterations"

    def _stage_challenger_review(self, state: _QueryRunState) -> None:
        answer = state.answer
        assurance_profile = state.assurance_profile
        cache_answer_replayed = state.cache_answer_replayed
        cached_output = state.cached_output
        call = state.call
        call_payload = state.call_payload
        challenger_controls = state.challenger_controls
        challenger_review = state.challenger_review
        challenger_review_artifact = state.challenger_review_artifact
        claim_evidence_map = state.claim_evidence_map
        coverage_manifest = state.coverage_manifest
        decision_otel = state.decision_otel
        deficiency = state.deficiency
        event_by_purpose = state.event_by_purpose
        evidence_only = state.evidence_only
        l2_decision = state.l2_decision
        l2_decision_artifact = state.l2_decision_artifact
        l2_execution = state.l2_execution
        l2_ledger = state.l2_ledger
        l2_packet = state.l2_packet
        packet = state.packet
        plan = state.plan
        quality_config = state.quality_config
        question = state.question
        record_id = state.record_id
        recorder = state.recorder
        request_id = state.request_id
        required_material = state.required_material
        retrieval_evidence_report = state.retrieval_evidence_report
        route = state.route
        row = state.row
        run_dir = state.run_dir
        run_id = state.run_id
        selected_type = state.selected_type
        strategic_reasoning_artifact = state.strategic_reasoning_artifact
        if cache_answer_replayed:
            answer = dict(cached_output["answer"])
        elif l2_decision.required:
            answer = (
                dict(l2_execution.answer)
                if l2_execution.answer is not None
                else (
                    evidence_only_over_packet(
                        plan,
                        l2_packet,
                        reason_code=l2_decision.reason_code,
                    )
                    if evidence_only
                    else None
                )
            )
        else:
            answer = reason_over_packet(plan, packet)
        if cache_answer_replayed:
            answer = dict(cached_output["answer"])
        claim_evidence_map = build_claim_evidence_map(
            answer=answer,
            report=retrieval_evidence_report,
            coverage_manifest=coverage_manifest,
        )
        write_json(run_dir / "claim_evidence_map.json", claim_evidence_map)
        challenger_controls = {
            "l2_decision_digest": l2_decision_artifact["canonical_digest"]["value"],
            "l2_disposition": l2_execution.disposition.value,
            "claim_evidence_map_digest": claim_evidence_map["canonical_digest"]["value"],
            "claim_evidence_map_valid": all(
                row["status"] == "grounded" for row in claim_evidence_map["claims"]
            ),
            "retrieval_evidence_report_digest": retrieval_evidence_report[
                "canonical_digest"
            ]["value"],
        }
        if state.l1_post_l2_review is not None:
            challenger_controls["l1_post_l2_review_digest"] = state.l1_post_l2_review.get(
                "canonical_digest", {}
            ).get("value")
            challenger_controls["l1_review_verdict"] = state.l1_post_l2_review.get("verdict")
            challenger_controls["l1_review_passed"] = (
                state.l1_post_l2_review.get("verdict") == L1ReviewVerdict.SUFFICIENT.value
            )
        (
            challenger_review,
            initial_judge_review,
            x1,
            delivery_quality_decision,
            deterministic_x1_passed,
            graph_hash_after_reasoning,
            concurrent_reviews,
        ) = execute_challenger_and_concurrent_judge(
            quality_config=quality_config,
            assurance_profile=assurance_profile or "standard",
            question=question,
            answer=answer,
            evidence_packet=packet,
            challenger_controls=challenger_controls,
            quality_provider_factory=self.quality_provider_factory,
            packet=packet,
            retrieval_evidence_report=retrieval_evidence_report,
            claim_evidence_map=claim_evidence_map,
            graph_hash_before=state.graph_hash_before,
            graph_path=state.graph_path,
            prompt=state.prompt,
            l2_decision_artifact=l2_decision_artifact,
            cached_output=state.cached_output,
            cache_lookup=state.cache_lookup,
            coverage_manifest=coverage_manifest,
            connected_evidence_map=state.connected_evidence_map,
            run_id=run_id,
            provider_failure_code=l2_execution.provider_failure_code,
            destination_decision=state.destination_decision,
            run_dir=run_dir,
        )
        if concurrent_reviews:
            state.x1 = x1
            state.delivery_quality_decision = delivery_quality_decision
            state.deterministic_x1_passed = deterministic_x1_passed
            state.graph_hash_after_reasoning = graph_hash_after_reasoning
            state.initial_judge_review = initial_judge_review
            state.concurrent_quality_reviews = True

        challenger_review_artifact = challenger_review.to_dict()
        write_json(run_dir / "challenger_review.json", challenger_review_artifact)
        recorder.record(
            stage="L2",
            event_type="pipeline_event",
            payload={"x1_quality_challenger": challenger_review_artifact},
            correlation=_correlation(request_id),
            route=route["query_route"],
            status=(
                "FAIL"
                if challenger_review.status is QualityReviewStatus.FAIL
                else "PASS"
            ),
            model_id=challenger_review.model,
            otel_attributes=decision_otel
            | {
                "challenger_call_count": challenger_review.call_count,
                "judge_call_count": 0 if not concurrent_reviews else initial_judge_review.call_count,
            },
        )
        strategic_reasoning_artifact: dict[str, Any] | None = None
        if answer is not None:
            write_json(run_dir / "selected_answer.json", answer)
            if selected_type == "opportunity_nba":
                strategic_reasoning_artifact = create_strategic_reasoning_output(
                    query_run_id=run_id,
                    packet=packet,
                    answer=answer,
                )
                write_json(
                    run_dir / "strategic_reasoning_output.json",
                    strategic_reasoning_artifact,
                )

        l2_ledger = l2_execution.to_ledger_payload()
        l2_ledger["concurrent_quality_reviews"] = concurrent_reviews
        required_material = l2_packet.get(
            "required_material_record_ids_by_lane",
            {"approved": [], "candidate": [], "external_public": []},
        )
        l2_ledger["required_material_record_ids_by_lane"] = required_material
        l2_ledger["missing_required_material_record_ids"] = sorted(
            {
                record_id
                for deficiency in l2_execution.quality_deficiencies
                for record_id in deficiency.missing_record_ids
            }
        )
        if cache_answer_replayed and answer is not None:
            l2_ledger["selected_output_digest"] = digest(answer)
            l2_ledger["output_source"] = "semantic_cache"
        elif evidence_only and answer is not None:
            l2_ledger["selected_output_digest"] = digest(answer)
            l2_ledger["output_source"] = "evidence_only"
        elif not l2_decision.required and answer is not None:
            l2_ledger["selected_output_digest"] = digest(answer)
            l2_ledger["output_source"] = "deterministic"
        elif answer is not None:
            l2_ledger["selected_output_digest"] = digest(answer)
            l2_ledger["output_source"] = "external_l2"
        else:
            l2_ledger["selected_output_digest"] = None
            l2_ledger["output_source"] = "external_l2"
        write_json(run_dir / "l2_execution.json", l2_ledger)

        event_by_purpose = {
            CallPurpose.PRIMARY.value: "l2_primary_call",
            CallPurpose.SCHEMA_REPAIR.value: "l2_schema_repair",
            CallPurpose.QUALITY_ESCALATION.value: "l2_quality_escalation",
        }
        for call in l2_execution.call_records:
            call_payload = call.to_dict()
            recorder.record(
                stage="L2",
                event_type=event_by_purpose[call.call_purpose],
                payload=call_payload,
                correlation=_correlation(request_id),
                route=route["query_route"],
                status="PASS" if call.status == "PASS" else "FAIL",
                model_id=call.model,
                otel_attributes=decision_otel
                | {
                    "selected_model_profile": call.model_profile,
                    "selected_model": call.model,
                    "call_purpose": call.call_purpose,
                    "call_ordinal": call.call_ordinal,
                    "input_tokens": call.input_tokens,
                    "cached_input_tokens": call.cached_input_tokens,
                    "uncached_input_tokens": (
                        call.input_tokens - call.cached_input_tokens
                    ),
                    "output_tokens": call.output_tokens,
                    "total_tokens": call.total_tokens,
                    "input_output_ratio": round(
                        call.input_tokens / max(call.output_tokens, 1), 6
                    ),
                    "cached_input_share": round(
                        call.cached_input_tokens / max(call.input_tokens, 1), 6
                    ),
                    "estimated_input_cost": call.estimated_input_cost,
                    "estimated_output_cost": call.estimated_output_cost,
                    "estimated_total_cost": call.estimated_total_cost,
                    "latency_ms": call.latency_ms,
                    "model_call_count": call.call_ordinal,
                    "repair_call_count": int(
                        call.call_purpose == CallPurpose.SCHEMA_REPAIR.value
                    ),
                    "escalation_call_count": int(
                        call.call_purpose == CallPurpose.QUALITY_ESCALATION.value
                    ),
                    "response_digest": call.response_digest,
                },
            )

        state.answer = answer
        state.call = call
        state.call_payload = call_payload
        state.challenger_controls = challenger_controls
        state.challenger_review = challenger_review
        state.challenger_review_artifact = challenger_review_artifact
        state.claim_evidence_map = claim_evidence_map
        state.concurrent_quality_reviews = concurrent_reviews
        state.deficiency = deficiency
        state.event_by_purpose = event_by_purpose
        state.l2_ledger = l2_ledger
        state.record_id = record_id
        state.required_material = required_material
        state.row = row
        state.strategic_reasoning_artifact = strategic_reasoning_artifact

    def _stage_l2d_and_repair(self, state: _QueryRunState) -> None:
        answer = state.answer
        assurance_profile = state.assurance_profile
        cache_lookup = state.cache_lookup
        cached_output = state.cached_output
        challenger_review = state.challenger_review
        challenger_review_artifact = state.challenger_review_artifact
        claim_evidence_map = state.claim_evidence_map
        connected_evidence_map = state.connected_evidence_map
        content_repair = state.content_repair
        content_repair_artifact = state.content_repair_artifact
        content_repair_call_count = state.content_repair_call_count
        coverage_manifest = state.coverage_manifest
        decision_otel = state.decision_otel
        delivery_quality_decision = state.delivery_quality_decision
        destination_decision = state.destination_decision
        deterministic_x1_passed = state.deterministic_x1_passed
        evidence_only = state.evidence_only
        graph_hash_after_reasoning = state.graph_hash_after_reasoning
        graph_hash_before = state.graph_hash_before
        graph_path = state.graph_path
        initial_judge_review = state.initial_judge_review
        initial_judge_review_artifact = state.initial_judge_review_artifact
        judge_enforcement_failed = state.judge_enforcement_failed
        judge_release_passed = state.judge_release_passed
        judge_required_for_release = state.judge_required_for_release
        judge_review = state.judge_review
        judge_review_artifact = state.judge_review_artifact
        l2_decision = state.l2_decision
        l2_decision_artifact = state.l2_decision_artifact
        l2_execution = state.l2_execution
        l2_ledger = state.l2_ledger
        packet = state.packet
        plan = state.plan
        prompt = state.prompt
        quality_config = state.quality_config
        question = state.question
        recorder = state.recorder
        rejudge_call_count = state.rejudge_call_count
        repair_eligible = state.repair_eligible
        request_id = state.request_id
        retrieval_evidence_report = state.retrieval_evidence_report
        route = state.route
        run_dir = state.run_dir
        run_id = state.run_id
        selected_type = state.selected_type
        selection_stage = state.selection_stage
        selection_status = state.selection_status
        strategic_reasoning_artifact = state.strategic_reasoning_artifact
        total_judge_call_count = state.total_judge_call_count
        total_l2_provider_call_count = state.total_l2_provider_call_count
        x1 = state.x1
        selection_stage = "L2" if l2_decision.required else "L2D"
        selection_status = (
            "BLOCKED"
            if l2_execution.disposition is L2Disposition.FAILED and not evidence_only
            else "PASS"
        )
        if l2_execution.disposition is L2Disposition.FAILED:
            recorder.record(
                stage="L2",
                event_type="l2_provider_failure",
                payload=l2_ledger,
                correlation=_correlation(request_id),
                route=route["query_route"],
                status="BLOCKED",
                model_id=l2_decision.selected_model,
                otel_attributes=decision_otel
                | {
                    "disposition": l2_execution.disposition.value,
                    "model_call_count": l2_execution.model_call_count,
                    "repair_call_count": l2_execution.schema_repair_call_count,
                    "escalation_call_count": l2_execution.quality_escalation_call_count,
                    "input_tokens": l2_ledger["input_tokens"],
                    "cached_input_tokens": l2_ledger["cached_input_tokens"],
                    "uncached_input_tokens": l2_ledger["uncached_input_tokens"],
                    "output_tokens": l2_ledger["output_tokens"],
                    "total_tokens": l2_ledger["total_tokens"],
                    "input_output_ratio": l2_ledger["input_output_ratio"],
                    "cached_input_share": l2_ledger["cached_input_share"],
                    "estimated_input_cost": l2_ledger["estimated_input_cost"],
                    "estimated_output_cost": l2_ledger["estimated_output_cost"],
                    "estimated_total_cost": l2_ledger["estimated_total_cost"],
                    "latency_ms": l2_ledger["latency_ms"],
                },
            )
        recorder.record(
            stage=selection_stage,
            event_type="l2_output_selection",
            payload={
                "l2_disposition": l2_execution.disposition.value,
                "selected_output_digest": l2_ledger.get("selected_output_digest"),
                "output_source": l2_ledger["output_source"],
                "delivered_model": l2_execution.delivered_model,
                "delivered_model_profile": l2_execution.delivered_model_profile,
                "model_call_count": l2_execution.model_call_count,
                "schema_repair_call_count": l2_execution.schema_repair_call_count,
                "quality_escalation_call_count": l2_execution.quality_escalation_call_count,
                "judge_call_count": 0,
                "challenger_call_count": 0,
            },
            correlation=_correlation(request_id),
            route=route["query_route"],
            status=selection_status,
            model_id=l2_execution.delivered_model,
            otel_attributes=decision_otel
            | {
                "disposition": l2_execution.disposition.value,
                "delivered_model": l2_execution.delivered_model,
                "delivered_model_profile": l2_execution.delivered_model_profile,
                "model_call_count": l2_execution.model_call_count,
                "repair_call_count": l2_execution.schema_repair_call_count,
                "escalation_call_count": l2_execution.quality_escalation_call_count,
                "input_tokens": l2_ledger["input_tokens"],
                "cached_input_tokens": l2_ledger["cached_input_tokens"],
                "uncached_input_tokens": l2_ledger["uncached_input_tokens"],
                "output_tokens": l2_ledger["output_tokens"],
                "total_tokens": l2_ledger["total_tokens"],
                "input_output_ratio": l2_ledger["input_output_ratio"],
                "cached_input_share": l2_ledger["cached_input_share"],
            },
        )

        if state.initial_judge_review is not None:
            graph_hash_after_reasoning = state.graph_hash_after_reasoning
            x1 = state.x1
            delivery_quality_decision = state.delivery_quality_decision
            deterministic_x1_passed = state.deterministic_x1_passed
            initial_judge_review = state.initial_judge_review
        else:
            (
                x1,
                delivery_quality_decision,
                deterministic_x1_passed,
                graph_hash_after_reasoning,
            ) = build_and_evaluate_deterministic_x1(
                answer=answer,
                packet=packet,
                retrieval_evidence_report=retrieval_evidence_report,
                claim_evidence_map=claim_evidence_map,
                graph_hash_before=graph_hash_before,
                graph_path=graph_path,
                prompt=prompt,
                l2_decision_artifact=l2_decision_artifact,
                cached_output=cached_output,
                cache_lookup=cache_lookup,
                coverage_manifest=coverage_manifest,
                connected_evidence_map=connected_evidence_map,
                run_id=run_id,
                provider_failure_code=l2_execution.provider_failure_code,
                destination_decision=destination_decision,
                run_dir=run_dir,
            )
            initial_judge_review = resolve_initial_judge_review(
                initial_judge_review=None,
                quality_config=quality_config,
                assurance_profile=assurance_profile or "standard",
                question=question,
                answer=answer,
                evidence_packet=packet,
                deterministic_controls=x1,
                challenger_review_artifact=challenger_review_artifact,
                quality_provider_factory=self.quality_provider_factory,
                deterministic_x1_passed=deterministic_x1_passed,
                l1_post_l2_review=state.l1_post_l2_review,
                claim_evidence_map=claim_evidence_map,
            )
        initial_judge_review_artifact = initial_judge_review.to_dict()
        write_json(
            run_dir / "judge_review_initial.json",
            initial_judge_review_artifact,
        )
        content_repair_artifact: dict[str, Any] = {
            "status": "SKIPPED",
            "reason": "JUDGE_VERDICT_NOT_REPAIRABLE",
            "call_count": 0,
            "answer_digest": None,
        }
        content_repair_call_count = 0
        repair_eligible = bool(
            quality_config.mode is X1QualityMode.ENFORCED
            and deterministic_x1_passed
            and assurance_profile
            in quality_config.content_repair.eligible_assurance_profiles
            and initial_judge_review.status is QualityReviewStatus.PASS
            and initial_judge_review.verdict is QualityVerdict.REPAIRABLE_FAIL
            and l2_execution.disposition is L2Disposition.PRIMARY
            and l2_execution.model_call_count == 1
            and l2_execution.schema_repair_call_count == 0
            and l2_execution.quality_escalation_call_count == 0
        )
        if not repair_eligible:
            if quality_config.mode is not X1QualityMode.ENFORCED:
                content_repair_artifact["reason"] = "QUALITY_MODE_NOT_ENFORCED"
            elif assurance_profile not in quality_config.content_repair.eligible_assurance_profiles:
                content_repair_artifact["reason"] = "ASSURANCE_NOT_ELIGIBLE"
            elif not deterministic_x1_passed:
                content_repair_artifact["reason"] = "DETERMINISTIC_X1_NOT_PASSED"
            elif (
                initial_judge_review.status is not QualityReviewStatus.PASS
                or initial_judge_review.verdict is not QualityVerdict.REPAIRABLE_FAIL
            ):
                content_repair_artifact["reason"] = "JUDGE_VERDICT_NOT_REPAIRABLE"
            else:
                content_repair_artifact["reason"] = "ORIGINAL_L2_NOT_DIRECT"
        judge_review = initial_judge_review
        if repair_eligible:
            content_repair = execute_content_repair(
                decision=l2_decision,
                config=self.l2_config,
                question=question,
                plan=plan,
                evidence_packet=packet,
                prompt=prompt,
                original_answer=answer or {},
                defect_codes=initial_judge_review.defect_codes,
                provider_factory=self.provider_factory,
                prior_model_calls=l2_execution.model_call_count,
            )
            content_repair_artifact = content_repair.to_dict() | {
                "reason": "REPAIRABLE_X1_JUDGE_VERDICT",
                "trigger_judge_review_digest": initial_judge_review_artifact[
                    "review_digest"
                ],
            }
            content_repair_call_count = content_repair.call_count
            write_json(run_dir / "content_repair.json", content_repair_artifact)
            recorder.record(
                stage="L2",
                event_type="pipeline_event",
                payload={"x1_quality_content_repair": content_repair_artifact},
                correlation=_correlation(request_id),
                route=route["query_route"],
                status=content_repair_artifact["status"],
                model_id=(
                    content_repair.call_record.model
                    if content_repair.call_record is not None
                    else None
                ),
                otel_attributes=decision_otel
                | {
                    "challenger_call_count": challenger_review.call_count,
                    "judge_call_count": initial_judge_review.call_count,
                },
            )
            if content_repair.succeeded:
                assert content_repair.answer is not None
                answer = dict(content_repair.answer)
                claim_evidence_map = build_claim_evidence_map(
                    answer=answer,
                    report=retrieval_evidence_report,
                    coverage_manifest=coverage_manifest,
                )
                write_json(run_dir / "claim_evidence_map.json", claim_evidence_map)
                write_json(run_dir / "selected_answer.json", answer)
                if selected_type == "opportunity_nba":
                    strategic_reasoning_artifact = create_strategic_reasoning_output(
                        query_run_id=run_id,
                        packet=packet,
                        answer=answer,
                    )
                    write_json(
                        run_dir / "strategic_reasoning_output.json",
                        strategic_reasoning_artifact,
                    )
                x1 = build_x1_gate(
                    answer=answer,
                    packet=packet,
                    retrieval_evidence_report=retrieval_evidence_report,
                    claim_evidence_map=claim_evidence_map,
                    graph_hash_before=graph_hash_before,
                    graph_hash_after_reasoning=cached_sha256_file(graph_path),
                    prompt=prompt,
                    l2_decision_digest=l2_decision_artifact["canonical_digest"][
                        "value"
                    ],
                    cached_output=cached_output,
                    cache_lookup=cache_lookup,
                    coverage_manifest=coverage_manifest,
                    connected_evidence_map=connected_evidence_map,
                    run_id=run_id,
                    provider_failure_code=content_repair.provider_failure_code,
                )
                judge_review = run_quality_review(
                    role=QualityRole.JUDGE,
                    config=quality_config,
                    assurance_profile=assurance_profile or "standard",
                    question=question,
                    answer=answer,
                    evidence_packet=packet,
                    deterministic_controls=x1,
                    challenger_review=challenger_review_artifact,
                    provider_factory=self.quality_provider_factory,
                    prerequisite_passed=x1["status"] == "PASS",
                    prior_calls_for_role=initial_judge_review.call_count,
                    repair_attempt=1,
                )
        else:
            write_json(run_dir / "content_repair.json", content_repair_artifact)
        judge_review_artifact = judge_review.to_dict()
        write_json(run_dir / "judge_review.json", judge_review_artifact)
        rejudge_call_count = (
            judge_review.call_count
            if judge_review is not initial_judge_review
            else 0
        )
        total_judge_call_count = initial_judge_review.call_count + rejudge_call_count
        total_l2_provider_call_count = (
            l2_execution.model_call_count + content_repair_call_count
        )
        l2_ledger["content_repair"] = content_repair_artifact
        l2_ledger["content_repair_call_count"] = content_repair_call_count
        l2_ledger["total_l2_provider_call_count"] = total_l2_provider_call_count
        if content_repair_artifact["status"] == "PASS" and answer is not None:
            l2_ledger["selected_output_digest"] = digest(answer)
            l2_ledger["output_source"] = "external_l2_content_repair"
        write_json(run_dir / "l2_execution.json", l2_ledger)
        judge_required_for_release = bool(
            quality_config.mode is X1QualityMode.ENFORCED
            and judge_review.eligible
            and x1["status"] == "PASS"
        )
        judge_release_passed = bool(
            not judge_required_for_release
            or (
                judge_review.status is QualityReviewStatus.PASS
                and judge_review.verdict is QualityVerdict.PASS
            )
        )
        judge_enforcement_failed = bool(
            judge_required_for_release and not judge_release_passed
        )
        x1["runtime_quality"] = {
            "mode": quality_config.mode.value,
            "policy_digest": quality_config.config_digest,
            "challenger_review_digest": challenger_review_artifact["review_digest"],
            "initial_judge_review_digest": initial_judge_review_artifact[
                "review_digest"
            ],
            "judge_review_digest": judge_review_artifact["review_digest"],
            "content_repair_digest": digest(content_repair_artifact),
            "content_repair_status": content_repair_artifact["status"],
            "judge_required_for_release": judge_required_for_release,
            "judge_release_passed": judge_release_passed,
            "judge_verdict": judge_review.verdict.value,
            "judge_failure_code": judge_review.failure_code,
        }
        if judge_enforcement_failed:
            x1["status"] = "FAIL"
        recorder.record(
            stage="X1",
            event_type="exit_gate_event",
            payload=x1,
            correlation=_correlation(request_id),
            route=route["query_route"],
            status="PASS" if x1["status"] == "PASS" else "FAIL",
            model_id=judge_review.model,
            otel_attributes=decision_otel
            | {
                "challenger_call_count": challenger_review.call_count,
                "judge_call_count": total_judge_call_count,
            },
        )
        state.answer = answer
        state.claim_evidence_map = claim_evidence_map
        state.content_repair = content_repair
        state.content_repair_artifact = content_repair_artifact
        state.content_repair_call_count = content_repair_call_count
        state.delivery_quality_decision = delivery_quality_decision
        state.deterministic_x1_passed = deterministic_x1_passed
        state.graph_hash_after_reasoning = graph_hash_after_reasoning
        state.initial_judge_review = initial_judge_review
        state.initial_judge_review_artifact = initial_judge_review_artifact
        state.judge_enforcement_failed = judge_enforcement_failed
        state.judge_release_passed = judge_release_passed
        state.judge_required_for_release = judge_required_for_release
        state.judge_review = judge_review
        state.judge_review_artifact = judge_review_artifact
        state.rejudge_call_count = rejudge_call_count
        state.repair_eligible = repair_eligible
        state.selection_stage = selection_stage
        state.selection_status = selection_status
        state.strategic_reasoning_artifact = strategic_reasoning_artifact
        state.total_judge_call_count = total_judge_call_count
        state.total_l2_provider_call_count = total_l2_provider_call_count
        state.x1 = x1

    def _stage_finalize_gates(self, state: _QueryRunState) -> None:
        activation = state.activation
        active = state.active
        answer = state.answer
        cache_binding = state.cache_binding
        cache_context = state.cache_context
        cache_writeback = state.cache_writeback
        challenger_review = state.challenger_review
        claim_evidence_map = state.claim_evidence_map
        connected_evidence_map = state.connected_evidence_map
        content_repair_call_count = state.content_repair_call_count
        coverage_manifest = state.coverage_manifest
        current_trace_id = state.current_trace_id
        evidence_only = state.evidence_only
        evidence_ready = state.evidence_ready
        exact = state.exact
        graph_hash_after = state.graph_hash_after
        graph_hash_before = state.graph_hash_before
        graph_hash_shadow = state.graph_hash_shadow
        graph_path = state.graph_path
        graph_retrieval = state.graph_retrieval
        hydrated = state.hydrated
        intent_digest = state.intent_digest
        intent_vector = state.intent_vector
        judge_enforcement_failed = state.judge_enforcement_failed
        judge_release_passed = state.judge_release_passed
        judge_required_for_release = state.judge_required_for_release
        judge_review = state.judge_review
        l1_terminal = state.l1_terminal
        l2_decision = state.l2_decision
        l2_decision_artifact = state.l2_decision_artifact
        l2_execution = state.l2_execution
        l2_ready = state.l2_ready
        output_dir = state.output_dir
        packet = state.packet
        pending_cache_entry = state.pending_cache_entry
        plan = state.plan
        recorder = state.recorder
        request_id = state.request_id
        required_stages = state.required_stages
        retrieval = state.retrieval
        retrieval_evidence_report = state.retrieval_evidence_report
        route = state.route
        run_dir = state.run_dir
        run_id = state.run_id
        seal = state.seal
        semantic_cache = state.semantic_cache
        shadow_projection = state.shadow_projection
        total_judge_call_count = state.total_judge_call_count
        total_l2_provider_call_count = state.total_l2_provider_call_count
        x1 = state.x1
        x2 = state.x2
        x3 = state.x3
        x3_outcome = state.x3_outcome
        l2_ready = not l2_decision.required or l2_execution.disposition in {
            L2Disposition.PRIMARY,
            L2Disposition.REPAIRED,
            L2Disposition.ESCALATED,
        }
        evidence_ready = _delivery_evidence_ready(plan, packet)

        # --- Phase 6: Materiality & Epistemic scoring for X2 gate ---
        approved_count = len(packet.get("approved", []))
        candidate_count = len(packet.get("candidate", []))
        external_count = len(packet.get("external_public", []))
        contradiction_count = len(packet.get("counterevidence", []))
        total_evidence = approved_count + candidate_count + external_count

        if total_evidence > 0:
            approved_ratio = approved_count / total_evidence
            contradiction_penalty = min(0.5, 0.25 * contradiction_count)
            epistemic_score = max(0.0, min(1.0, approved_ratio - contradiction_penalty))
        else:
            epistemic_score = 1.0 if evidence_ready else 0.0
            approved_ratio = 1.0
            contradiction_penalty = 0.0

        if contradiction_count > 0:
            epistemic_tier = "EPISTEMICALLY_DISPUTED"
        elif candidate_count > 0:
            epistemic_tier = "QUALIFIED_HYPOTHESIS"
        else:
            epistemic_tier = "VERIFIED_FACTUAL"

        mat_level = plan.get("materiality", "low")
        mat_weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(mat_level, 0.3)
        coverage_factor = 1.0 if x1.get("coverage_complete") else 0.5
        materiality_score = round(mat_weight * coverage_factor * epistemic_score, 4)

        x2 = {
            "status": "PASS"
            if x1["status"] == "PASS"
            and evidence_ready
            and (l2_ready or evidence_only)
            and x1["coverage_complete"]
            else "FAIL",
            "materiality": plan["materiality"],
            "approved_evidence_count": approved_count,
            "candidate_evidence_count": candidate_count,
            "materiality_score": materiality_score,
            "epistemic_score": round(epistemic_score, 4),
            "epistemic_tier": epistemic_tier,
            "scoring_receipt": {
                "materiality_weight": mat_weight,
                "coverage_factor": coverage_factor,
                "approved_ratio": round(approved_ratio, 4),
                "contradiction_penalty": contradiction_penalty,
            },
            "l2_required": l2_decision.required,
            "l2_disposition": l2_execution.disposition.value,
            "evidence_only": evidence_only,
            "deterministic_quality_checks_passed": x1["status"] == "PASS",
            "judge_call_count": total_judge_call_count,
            "challenger_call_count": challenger_review.call_count,
            "content_repair_call_count": content_repair_call_count,
            "judge_required_for_release": judge_required_for_release,
            "judge_release_passed": judge_release_passed,
            "judge_verdict": judge_review.verdict.value,
            "decision_ready": evidence_ready and l2_ready,
            "evidence_ready": evidence_ready,
            "coverage_complete": x1["coverage_complete"],
        }
        recorder.record(
            stage="X2",
            event_type="exit_gate_event",
            payload=x2,
            correlation=_correlation(request_id),
            route=route["query_route"],
            status="PASS" if x2["status"] == "PASS" else "FAIL",
        )
        if x1["status"] == "PASS" and x2["status"] == "PASS":
            x3_outcome = (
                "X3E_ALLOW_EVIDENCE_ONLY" if evidence_only else "X3D_ALLOW_FINISH"
            )
        elif judge_enforcement_failed or (l2_decision.required and not l2_ready) or not x1["coverage_complete"]:
            x3_outcome = "X3B_ESCALATE_HITL"
        else:
            x3_outcome = "X3F_BLOCK"
        x3 = {
            "status": (
                "PASS"
                if x3_outcome in {"X3D_ALLOW_FINISH", "X3E_ALLOW_EVIDENCE_ONLY"}
                else "BLOCKED"
            ),
            "outcome": x3_outcome,
            "reason": (
                "All deterministic evidence, lane, citation, replay, and L2 policy "
                "gates passed."
                if x3_outcome == "X3D_ALLOW_FINISH"
                else (
                    "External synthesis was unavailable, so the run delivered only a "
                    "sealed, citation-complete evidence projection."
                    if x3_outcome == "X3E_ALLOW_EVIDENCE_ONLY"
                    else (
                        "The enforced runtime X1 judge did not approve the sealed "
                        "answer and requires governed HITL escalation."
                        if judge_enforcement_failed
                        else "Required external L2 failed closed and requires governed HITL "
                        "escalation."
                        if x3_outcome == "X3B_ESCALATE_HITL"
                        else "One or more deterministic decision-readiness gates failed."
                    )
                )
            ),
            "judge_call_count": total_judge_call_count,
            "challenger_call_count": challenger_review.call_count,
            "content_repair_call_count": content_repair_call_count,
            "judge_verdict": judge_review.verdict.value,
            "judge_enforcement_failed": judge_enforcement_failed,
        }
        write_json(run_dir / "x_gates.json", {"X1": x1, "X2": x2, "X3": x3})
        recorder.record(
            stage="X3",
            event_type="exit_gate_event",
            payload=x3,
            correlation=_correlation(request_id),
            route=route["query_route"],
            status=x3["status"],
        )
        pending_cache_entry = None
        cache_writeback = {
            "status": "SKIPPED",
            "reason": "CACHE_NO_TERMINAL_OUTPUT",
            "entry_id": None,
            "replay_eligible": False,
        }
        if self.l1_controller_mode == "bounded":
            cache_writeback = {
                "status": "SKIPPED",
                "reason": "CACHE_BOUNDED_REPLAY_DEFERRED",
                "entry_id": None,
                "replay_eligible": False,
            }
        elif answer is not None and intent_vector is not None:
            write_result = semantic_cache.stage(
                context=cache_context,
                intent_digest=intent_digest,
                intent_vector=intent_vector,
                output={
                    "exact_retrieval": exact,
                    "hybrid_retrieval": retrieval,
                    "graph_retrieval": graph_retrieval,
                    "connected_evidence_map": connected_evidence_map,
                    "graph_hydration": hydrated,
                    "evidence_packet": packet,
                    "answer": answer,
                    "query_coverage_manifest": coverage_manifest,
                    "retrieval_evidence_report": retrieval_evidence_report,
                    "claim_evidence_map": claim_evidence_map,
                    "l1_controller": {
                        "mode": self.l1_controller_mode,
                        "plan_version": plan["schema_version"],
                        "policy_digest": (
                            self.l1_control_policy.policy_digest
                            if self.l1_control_policy is not None
                            else None
                        ),
                        "action_policy_digest": (
                            digest(self.l1_control_policy.action_allowlist)
                            if self.l1_control_policy is not None
                            else None
                        ),
                        "budget_profile": cache_context["l1_budget_profile"],
                        "terminal_digest": (
                            l1_terminal["canonical_digest"]["value"]
                            if l1_terminal is not None
                            else None
                        ),
                        "final_evidence_report_digest": retrieval_evidence_report[
                            "canonical_digest"
                        ]["value"],
                    },
                },
                outcome=x3_outcome,
                source_run_id=run_id,
                source_trace_id=current_trace_id,
                replay_eligible=bool(
                    total_l2_provider_call_count == 0
                    or (
                        semantic_cache.policy is not None
                        and semantic_cache.policy.allow_external_l2_replay
                    )
                ),
            )
            pending_cache_entry = write_result.entry
            cache_writeback = {
                "status": write_result.status,
                "reason": write_result.reason,
                "entry_id": (
                    write_result.entry.entry_id if write_result.entry else None
                ),
                "replay_eligible": (
                    write_result.entry.replay_eligible if write_result.entry else False
                ),
            }
        graph_hash_shadow = cached_sha256_file(graph_path)
        if graph_hash_shadow != graph_hash_before:
            raise RuntimeError("query pipeline mutated graph truth")
        shadow_projection = {
            "route_digest": route["canonical_digest"]["value"],
            "retrieval_digest": digest(
                {
                    "exact": exact.get("canonical_digest", {}).get("value"),
                    "hybrid": retrieval.get("canonical_digest", {}).get("value"),
                    "graph": graph_retrieval.get("canonical_digest", {}).get("value"),
                }
            ),
            "selected_records_digest": digest(retrieval_evidence_report["selected_record_ids_by_lane"]),
            "evidence_packet_digest": packet["packet_digest"],
            "l2d_digest": l2_decision_artifact["canonical_digest"]["value"],
            "answer_digest": digest(answer or {}),
            "x_gates_digest": digest({"X1": x1, "X2": x2, "X3": x3}),
            "cache_behavior_digest": digest(cache_writeback),
            "provider_call_count": total_l2_provider_call_count,
            "graph_sha256_before": graph_hash_before,
            "graph_sha256_after": graph_hash_shadow,
            "terminal_outcome": x3_outcome,
            "caller_output_digest": digest({"x3_outcome": x3_outcome, "answer_digest": digest(answer or {})}),
        }
        if self.l1_controller_mode == "shadow":
            _emit_l1_shadow_receipts(
                mode=self.l1_controller_mode,
                policy=self.l1_control_policy,
                plan=plan,
                coverage_manifest=coverage_manifest,
                evidence_report=retrieval_evidence_report,
                active=active,
                run_dir=run_dir,
                output_dir=output_dir,
                recorder=recorder,
                request_id=request_id,
                runtime_terminal_outcome=x3_outcome,
                runtime_next_stage="NONE",
                graph_sha256_before=graph_hash_before,
                graph_sha256_after=graph_hash_shadow,
                comparison_projection=shadow_projection,
            )
        recorder.record(
            stage="L4",
            event_type="pipeline_event",
            payload={
                "semantic_cache_writeback": cache_writeback,
                "context_digest": digest(semantic_cache.compatibility(cache_context)),
                "intent_digest": intent_digest,
                "policy_digest": semantic_cache.policy_digest,
                "embedding_binding": cache_binding,
            },
            correlation=_correlation(request_id),
            route=route["query_route"],
            status="PASS",
        )
        required_stages = [
            "U0",
            "L1",
            "L0",
            "C0.1",
            "C0.2",
            "C0.3",
            "L2D",
            *( ["PA", "L2"] if l2_decision.required else [] ),
            "X1",
            "X2",
            "X3",
            "L4",
        ]
        seal = recorder.seal(
            required_stages,
            _correlation(request_id),
            status=(
                "PASS"
                if x3_outcome in {"X3D_ALLOW_FINISH", "X3E_ALLOW_EVIDENCE_ONLY"}
                else "BLOCKED"
            ),
        )
        if pending_cache_entry is not None:
            activation = semantic_cache.activate(
                pending_cache_entry,
                source_event_count=seal["event_count"],
                source_event_chain_digest=seal["event_chain_digest"],
            )
            cache_writeback["activation_status"] = activation.status
            cache_writeback["activation_reason"] = activation.reason
        else:
            cache_writeback["activation_status"] = "SKIPPED"
            cache_writeback["activation_reason"] = cache_writeback["reason"]
        graph_hash_after = cached_sha256_file(graph_path)
        if graph_hash_after != graph_hash_before:
            raise RuntimeError("query pipeline mutated graph truth")
        state.activation = activation
        state.cache_writeback = cache_writeback
        state.evidence_ready = evidence_ready
        state.graph_hash_after = graph_hash_after
        state.graph_hash_shadow = graph_hash_shadow
        state.l2_ready = l2_ready
        state.pending_cache_entry = pending_cache_entry
        state.required_stages = required_stages
        state.seal = seal
        state.shadow_projection = shadow_projection
        state.x2 = x2
        state.x3 = x3
        state.x3_outcome = x3_outcome

    def _stage_build_receipt(self, state: _QueryRunState) -> None:
        active = state.active
        answer = state.answer
        assurance = state.assurance
        assurance_profile = state.assurance_profile
        cache_answer_replayed = state.cache_answer_replayed
        cache_binding = state.cache_binding
        cache_lookup = state.cache_lookup
        cache_writeback = state.cache_writeback
        cached_output = state.cached_output
        challenger_review = state.challenger_review
        challenger_review_artifact = state.challenger_review_artifact
        claim_evidence_map = state.claim_evidence_map
        connected_evidence_map = state.connected_evidence_map
        content_repair_artifact = state.content_repair_artifact
        content_repair_call_count = state.content_repair_call_count
        coverage_disposition = state.coverage_disposition
        coverage_manifest = state.coverage_manifest
        current_trace_id = state.current_trace_id
        delivery_quality_decision = state.delivery_quality_decision
        destination_artifact = state.destination_artifact
        destination_decision = state.destination_decision
        evidence_only = state.evidence_only
        evidence_output_root = state.evidence_output_root
        exact = state.exact
        final = state.final
        graph_fit_decision = state.graph_fit_decision
        graph_hash_after = state.graph_hash_after
        graph_hash_before = state.graph_hash_before
        graph_retrieval = state.graph_retrieval
        initial_judge_review_artifact = state.initial_judge_review_artifact
        judge_release_passed = state.judge_release_passed
        judge_required_for_release = state.judge_required_for_release
        judge_review_artifact = state.judge_review_artifact
        l1_terminal = state.l1_terminal
        l2_decision = state.l2_decision
        l2_decision_artifact = state.l2_decision_artifact
        l2_execution = state.l2_execution
        l2_ledger = state.l2_ledger
        l2_packet = state.l2_packet
        l2_visible_counts = state.l2_visible_counts
        lane = state.lane
        markdown = state.markdown
        name = state.name
        outcome = state.outcome
        outcome_counts = state.outcome_counts
        output_dir = state.output_dir
        packet = state.packet
        plan = state.plan
        prompt = state.prompt
        quality_config = state.quality_config
        report_supported_counts = state.report_supported_counts
        request_id = state.request_id
        required_stages = state.required_stages
        resolved_card_scope = state.resolved_card_scope
        retrieval = state.retrieval
        retrieval_evidence_report = state.retrieval_evidence_report
        route = state.route
        routing_policy_digest = state.routing_policy_digest
        row = state.row
        rows = state.rows
        run_dir = state.run_dir
        run_id = state.run_id
        runtime_binding = state.runtime_binding
        seal = state.seal
        selected_counts = state.selected_counts
        selected_type = state.selected_type
        semantic_cache = state.semantic_cache
        strategic_evidence_artifact = state.strategic_evidence_artifact
        strategic_reasoning_artifact = state.strategic_reasoning_artifact
        summary_path = state.summary_path
        terminal_binding = state.terminal_binding
        total_judge_call_count = state.total_judge_call_count
        total_l2_provider_call_count = state.total_l2_provider_call_count
        u0_validation = state.u0_validation
        x1 = state.x1
        x2 = state.x2
        x3 = state.x3
        x3_outcome = state.x3_outcome
        terminal_binding = assert_runtime_binding_unchanged(
            manager=self.manager,
            expected_binding=runtime_binding,
            backend=self.backend,
            policy_digests=self._runtime_policy_digests(active, quality_config),
            stage="query-terminal",
        )
        outcome_counts = {
            name: sum(
                row["outcome"] == outcome
                for row in retrieval_evidence_report["requirement_outcomes"]
            )
            for name, outcome in (
                ("supported", "covered"),
                ("excluded", "excluded"),
                ("missing", "explicit_gap"),
                ("unauthorized", "unauthorized"),
                ("conflicted", "conflict"),
            )
        }
        outcome_counts["partial"] = 0
        coverage_disposition = (
            "NOT_AUTHORIZED"
            if outcome_counts["unauthorized"]
            else "CONFLICTED"
            if outcome_counts["conflicted"]
            else "MISSING"
            if outcome_counts["missing"]
            else "COMPLETE"
        )
        evidence_output_root = output_dir if output_dir is not None else run_dir
        selected_counts = {
            lane: len(rows)
            for lane, rows in retrieval_evidence_report[
                "selected_record_ids_by_lane"
            ].items()
        }
        report_supported_counts = {
            lane: len(rows)
            for lane, rows in retrieval_evidence_report[
                "l2_input_record_ids_by_lane"
            ].items()
        }
        l2_visible_counts = {
            lane: len(l2_packet.get(lane, []))
            for lane in ("approved", "candidate", "external_public")
        }
        final = {
            "status": (
                "COMPLETED"
                if x3_outcome in {"X3D_ALLOW_FINISH", "X3E_ALLOW_EVIDENCE_ONLY"}
                else "BLOCKED"
            ),
            "request_id": request_id,
            "run_id": run_id,
            "trace_id": current_trace_id,
            "destination": destination_decision.destination.value,
            "eligibility_status": destination_decision.eligibility_status,
            "eligibility_checks": dict(destination_decision.eligibility_checks),
            "destination_reason_codes": list(destination_decision.reason_codes),
            "query_destination_decision_digest": destination_artifact[
                "canonical_digest"
            ]["value"],
            "query_graph_fit_decision_digest": graph_fit_decision[
                "canonical_digest"
            ]["value"],
            "graph_fit_status": graph_fit_decision["status"],
            "graph_fit_reason_codes": list(graph_fit_decision["reason_codes"]),
            "delivery_profile": destination_decision.delivery_profile,
            "artifact_type": destination_decision.artifact_type,
            "query_type": selected_type,
            "assurance_profile": assurance_profile,
            "routed_materiality": destination_decision.materiality,
            "routing_policy_digest": routing_policy_digest,
            "route": route["query_route"],
            "retrieval_mode": plan["retrieval_mode"],
            "x3_outcome": x3_outcome,
            "terminal_outcome": x3_outcome,
            "graph_snapshot": active["graph_snapshot"],
            "generation_id": active["generation_id"],
            "embedding_backend": active["embedding_backend"],
            "production_qualified": active["production_qualified"],
            "assurance_tier": pair_assurance_tier(active),
            "operational_status": assurance["operational_status"],
            "assurance_status": assurance["assurance_status"],
            "qualification_id": assurance.get("qualification_id"),
            "qualification_digest": assurance.get("qualification_digest"),
            "approved_record_count": len(packet["approved"]),
            "candidate_record_count": len(packet["candidate"]),
            "selected_record_counts_by_lane": selected_counts,
            "l2_report_supported_record_counts_by_lane": report_supported_counts,
            "l2_visible_record_counts_by_lane": l2_visible_counts,
            "dense_executed": True,
            "sparse_executed": True,
            "bm25_executed": bool(
                exact.get("bm25_executed")
                and retrieval.get("bm25_executed")
            ),
            "colbert_executed": True,
            "graph_traversal_executed": graph_retrieval[
                "graph_traversal_executed"
            ],
            "graph_requested_hops": connected_evidence_map["requested_hops"],
            "graph_max_hop_reached": connected_evidence_map["max_hop_reached"],
            "graph_expanded_record_count": connected_evidence_map["metrics"][
                "expanded_record_count"
            ],
            "connected_evidence_map_id": connected_evidence_map["map_id"],
            "connected_evidence_map_digest": connected_evidence_map[
                "canonical_digest"
            ]["value"],
            "counterevidence_count": connected_evidence_map["metrics"][
                "counterevidence_count"
            ],
            "opportunity_context_count": connected_evidence_map["metrics"][
                "opportunity_context_count"
            ],
            "lane_blending": False,
            "l2_policy": l2_decision.policy,
            "l2_required": l2_decision.required,
            "l2_reason_code": l2_decision.reason_code,
            "l2_decision_id": l2_decision.decision_id,
            "l2_decision_digest": l2_decision_artifact["canonical_digest"]["value"],
            "selected_model_profile": l2_decision.selected_model_profile,
            "selected_model": l2_decision.selected_model,
            "estimated_prompt_size_chars": (
                l2_decision.estimated_prompt_size_chars
            ),
            "routing_rationale": {
                "destination_reason_codes": list(destination_decision.reason_codes),
                "assurance_profile": assurance_profile,
                "l2_reason_code": l2_decision.reason_code,
            },
            "delivered_model_profile": l2_execution.delivered_model_profile,
            "delivered_model": l2_execution.delivered_model,
            "provider_egress_allowed": l2_decision.provider_egress_allowed,
            "l2_disposition": l2_execution.disposition.value,
            "answer_delivery": (
                "evidence_only"
                if evidence_only
                else (
                    "grounded_synthesis"
                    if l2_decision.required
                    else "direct_graph_answer"
                )
            ),
            "synthesis_status": (
                "unavailable"
                if evidence_only
                else ("completed" if l2_decision.required else "not_requested")
            ),
            "evidence_only_reason": l2_decision.reason_code if evidence_only else None,
            "provider_initialization_count": l2_execution.provider_initialization_count,
            "model_call_count": l2_execution.model_call_count,
            "external_l2_call_count": total_l2_provider_call_count,
            "content_repair_call_count": content_repair_call_count,
            "total_external_model_call_count": (
                total_l2_provider_call_count
                + challenger_review.call_count
                + total_judge_call_count
            ),
            "primary_call_count": l2_execution.primary_call_count,
            "schema_repair_call_count": l2_execution.schema_repair_call_count,
            "quality_escalation_call_count": l2_execution.quality_escalation_call_count,
            "judge_call_count": total_judge_call_count,
            "challenger_call_count": challenger_review.call_count,
            "quality_provider_call_count": (
                challenger_review.call_count + total_judge_call_count
            ),
            "x1_quality": {
                "mode": quality_config.mode.value,
                "policy_digest": quality_config.config_digest,
                "provider_egress_allowed": quality_config.provider_egress_allowed,
                "challenger": challenger_review_artifact,
                "initial_judge": initial_judge_review_artifact,
                "judge": judge_review_artifact,
                "content_repair": content_repair_artifact,
                "judge_required_for_release": judge_required_for_release,
                "judge_release_passed": judge_release_passed,
                "delivery_quality": delivery_quality_decision,
            },
            "provider_failure_code": l2_execution.provider_failure_code,
            "input_tokens": l2_ledger["input_tokens"],
            "cached_input_tokens": l2_ledger["cached_input_tokens"],
            "output_tokens": l2_ledger["output_tokens"],
            "total_tokens": l2_ledger["total_tokens"],
            "estimated_input_cost": l2_ledger["estimated_input_cost"],
            "estimated_output_cost": l2_ledger["estimated_output_cost"],
            "estimated_total_cost": l2_ledger["estimated_total_cost"],
            "latency_ms": l2_ledger["latency_ms"],
            "prompt_hash": prompt["prompt_hash"] if prompt is not None else None,
            "query_coverage_manifest_digest": coverage_manifest[
                "canonical_digest"
            ]["value"],
            "query_coverage_requirement_count": len(
                coverage_manifest["requirements"]
            ),
            "retrieval_evidence_report_digest": retrieval_evidence_report[
                "canonical_digest"
            ]["value"],
            "claim_evidence_map_digest": claim_evidence_map["canonical_digest"]["value"],
            "retrieval_evidence": {
                "report_id": retrieval_evidence_report["report_id"],
                "report_digest": retrieval_evidence_report["canonical_digest"]["value"],
                "report_json_path": str(
                    (evidence_output_root / "retrieval_evidence_report.json").resolve()
                ),
                "report_markdown_path": str(
                    (evidence_output_root / "retrieval_evidence.md").resolve()
                ),
                "coverage_counts": outcome_counts,
                "selected_record_counts_by_lane": selected_counts,
                "report_supported_record_count": sum(report_supported_counts.values()),
                "l2_visible_record_counts_by_lane": l2_visible_counts,
                "l2_visible_record_count": sum(l2_visible_counts.values()),
                "terminal_coverage_disposition": coverage_disposition,
            },
            "evidence_packet_hash": packet["packet_digest"],
            "strategic_evidence_packet_digest": (
                strategic_evidence_artifact["canonical_digest"]["value"]
                if strategic_evidence_artifact is not None
                else None
            ),
            "strategic_reasoning_output_digest": (
                strategic_reasoning_artifact["canonical_digest"]["value"]
                if strategic_reasoning_artifact is not None
                else None
            ),
            "graph_sha256_before": graph_hash_before,
            "graph_sha256_after": graph_hash_after,
            "runtime_binding": terminal_binding,
            "graph_mutation_count": 0,
            "l1_controller": {
                "mode": self.l1_controller_mode,
                "policy_digest": (
                    self.l1_control_policy.policy_digest
                    if self.l1_control_policy is not None
                    else None
                ),
                "terminal_digest": (
                    l1_terminal["canonical_digest"]["value"]
                    if l1_terminal is not None
                    else None
                ),
                "terminal_reason": (
                    l1_terminal["terminal_reason"] if l1_terminal is not None else None
                ),
                "final_evidence_report_digest": retrieval_evidence_report[
                    "canonical_digest"
                ]["value"],
            },
            "learning_event_count": seal["event_count"],
            "learning_event_chain_digest": seal["event_chain_digest"],
            "required_stages": required_stages,
            "semantic_cache": {
                "mode": semantic_cache.mode,
                "config_source": semantic_cache.config_source,
                "policy_digest": semantic_cache.policy_digest,
                "embedding_binding": cache_binding,
                "lookup_hit": cached_output is not None,
                "lookup_reason": cache_lookup.reason,
                "lookup_entry_id": cache_lookup.entry_id,
                "lookup_similarity": cache_lookup.similarity,
                "replayed_output": cache_answer_replayed,
                "source_run_id": cache_lookup.source_run_id,
                "source_trace_id": cache_lookup.source_trace_id,
                "source_output_digest": cache_lookup.source_output_digest,
                "source_answer_digest": (
                    digest(cached_output["answer"])
                    if cached_output is not None
                    else None
                ),
                "delivered_answer_digest": (
                    digest(answer) if answer is not None else None
                ),
                "writeback": cache_writeback,
            },
            "answer": answer,
            "idempotent_replay": False,
        }
        if resolved_card_scope is not None:
            final["card_scope"] = u0_validation["card_scope"]
        write_json(summary_path, final)
        markdown = render_markdown(answer, x3_outcome)
        (run_dir / "answer.md").write_text(markdown, encoding="utf-8")
        if output_dir:
            artifacts = {
                "runtime_binding.json": terminal_binding,
                "answer.json": final,
                "query_plan.json": plan,
                "query_coverage_manifest.json": coverage_manifest,
                "retrieval_evidence_report.json": retrieval_evidence_report,
                "l2_evidence_packet.json": l2_packet,
                "claim_evidence_map.json": claim_evidence_map,
                "query_destination_decision.json": destination_artifact,
                "query_graph_fit_decision.json": graph_fit_decision,
                "l2_execution_decision.json": l2_decision_artifact,
                "l2_execution.json": l2_ledger,
                "challenger_review.json": challenger_review_artifact,
                "judge_review_initial.json": initial_judge_review_artifact,
                "judge_review.json": judge_review_artifact,
                "content_repair.json": content_repair_artifact,
                "delivery_quality_decision.json": delivery_quality_decision,
                "x_gates.json": {"X1": x1, "X2": x2, "X3": x3},
                "evidence_packet.json": packet,
                "connected_evidence_map.json": connected_evidence_map,
            }
            if strategic_evidence_artifact is not None:
                artifacts["strategic_evidence_packet.json"] = strategic_evidence_artifact
            if strategic_reasoning_artifact is not None:
                artifacts["strategic_reasoning_output.json"] = strategic_reasoning_artifact
            text_artifacts = {
                "answer.md": markdown,
                "retrieval_evidence.md": render_retrieval_evidence_markdown(coverage_manifest, retrieval_evidence_report),
            }
            mirror_artifacts_to_output_dir(output_dir, artifacts, text_artifacts=text_artifacts, writer=state.artifact_writer)
        if state.artifact_writer is not None:
            state.artifact_writer.flush()
        state.coverage_disposition = coverage_disposition
        state.evidence_output_root = evidence_output_root
        state.final = QueryResult(**final)
        state.l2_visible_counts = l2_visible_counts
        state.lane = lane
        state.markdown = markdown
        state.name = name
        state.outcome = outcome
        state.outcome_counts = outcome_counts
        state.report_supported_counts = report_supported_counts
        state.row = row
        state.rows = rows
        state.selected_counts = selected_counts
        state.terminal_binding = terminal_binding

