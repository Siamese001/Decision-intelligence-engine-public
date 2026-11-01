"""Builders for attempt zero, observation zero, shadow action, and comparison artifacts."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import create_versioned_artifact
from ..core import digest, stable_id
from ..l1_control import (
    BudgetState,
    L1Action,
    ProgressObservation,
    ProgressStatus,
    create_action,
    observe_progress,
)
from .constants import ATTEMPT_CONTRACT, OBSERVATION_CONTRACT, TERMINAL_CONTRACT
from .helpers import (
    _attempt_action,
    _canonical,
    _digest,
    _outcome_map,
    _pair,
    _requirements,
    _selected_records,
    _terminal_reason,
)
from .models import ShadowReceiptError


def build_attempt_zero(
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    active_pair: Mapping[str, Any],
    graph_sha256_before: str,
    graph_sha256_after: str,
    budget_before: BudgetState | None = None,
    budget_after: BudgetState | None = None,
    result_bytes: int = 0,
    elapsed_ms: int = 0,
    status: str = "completed",
) -> tuple[dict[str, Any], L1Action]:
    """Seal the existing C0.1-C0.3 retrieval as attempt zero."""

    plan_digest = _canonical(plan, "query plan")
    manifest_digest = _canonical(coverage_manifest, "coverage manifest")
    report_digest = _canonical(evidence_report, "evidence report")
    if coverage_manifest.get("query_plan_digest") != plan_digest:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "coverage manifest is not bound to the plan")
    if evidence_report.get("query_plan_digest") != plan_digest:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report is not bound to the plan")
    if evidence_report.get("coverage_manifest_digest") != manifest_digest:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report is not bound to the manifest")
    _digest(graph_sha256_before, "graph hash before")
    _digest(graph_sha256_after, "graph hash after")
    authorization_digest = _digest(
        coverage_manifest.get("authorization_scope_digest"),
        "authorization scope digest",
    )
    requirements = _requirements(coverage_manifest)
    action = _attempt_action(
        plan_digest=plan_digest,
        manifest_digest=manifest_digest,
        authorization_digest=authorization_digest,
        requirement_ids=tuple(requirement["requirement_id"] for requirement in requirements),
        report_digest=report_digest,
    )
    before = budget_before or BudgetState.initial()
    after = budget_after or BudgetState(
        additional_attempts_used=0,
        actions_used=1,
        records_by_lane=tuple((lane, ids) for lane, ids in _selected_records(evidence_report).items() if ids),
        result_bytes=result_bytes,
        elapsed_ms=elapsed_ms,
        action_ids=(action.action_id,),
        attempt_ids=(action.attempt_id,),
    )
    record_ids = {lane: list(ids) for lane, ids in _selected_records(evidence_report).items() if ids}
    result_digest = digest(
        {
            "evidence_report_digest": report_digest,
            "record_ids_by_lane": record_ids,
            "status": status,
            "bytes": result_bytes,
            "elapsed_ms": elapsed_ms,
        }
    )
    artifact_id = stable_id("query-retrieval-attempt", action.attempt_id, report_digest, length=26)
    artifact = create_versioned_artifact(
        ATTEMPT_CONTRACT,
        "1.0.0",
        artifact_id,
        attempt_id=action.attempt_id,
        parent_plan_digest=plan_digest,
        coverage_manifest_digest=manifest_digest,
        attempt_ordinal=0,
        action_id=action.action_id,
        action_type=action.action_type,
        requirement_ids=list(action.requirement_ids),
        authorization_scope_digest=authorization_digest,
        active_pair=_pair(active_pair),
        input_digest=action.input_digest,
        budget_before={
            "actions_used": before.actions_used,
            "records_used": before.unique_records,
            "bytes_used": before.result_bytes,
            "elapsed_ms": before.elapsed_ms,
        },
        result={
            "status": status,
            "record_ids_by_lane": record_ids,
            "result_digest": result_digest,
            "elapsed_ms": elapsed_ms,
            "bytes": result_bytes,
            "provider_initialization_count": 0,
            "provider_call_count": 0,
        },
        budget_after={
            "actions_used": after.actions_used,
            "records_used": after.unique_records,
            "bytes_used": after.result_bytes,
            "elapsed_ms": after.elapsed_ms,
        },
        graph_sha256_before=graph_sha256_before,
        graph_sha256_after=graph_sha256_after,
        provider_initialization_count=0,
        provider_call_count=0,
        extensions={"wave": "W4", "execution": "existing_c0_retrieval"},
    )
    return artifact, action


def build_observation_zero(
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    attempt_zero: Mapping[str, Any],
    graph_sha256_before: str,
    graph_sha256_after: str,
) -> tuple[dict[str, Any], ProgressObservation]:
    """Seal coverage observation using only manifest and evidence report."""

    plan_digest = _canonical(plan, "query plan")
    manifest_digest = _canonical(coverage_manifest, "coverage manifest")
    report_digest = _canonical(evidence_report, "evidence report")
    attempt_digest = _canonical(attempt_zero, "attempt zero")
    if attempt_zero.get("parent_plan_digest") != plan_digest:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "attempt zero is not bound to the plan")
    if attempt_zero.get("coverage_manifest_digest") != manifest_digest:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "attempt zero is not bound to the manifest")
    outcomes = _outcome_map(evidence_report, coverage_manifest)
    previous = {requirement_id: "explicit_gap" for requirement_id in outcomes}
    progress = observe_progress(previous, outcomes)
    if any(outcome in {"unauthorized", "conflict"} for outcome in outcomes.values()):
        progress_status = "blocked"
        progress_reason = "unauthorized or conflicting evidence"
    elif progress.status is ProgressStatus.COMPLETE:
        progress_status = "complete"
        progress_reason = "coverage complete"
    elif progress.newly_covered_requirement_ids:
        progress_status = "progress"
        progress_reason = "newly covered material requirements"
    else:
        progress_status = "no_progress"
        progress_reason = "no newly covered material requirements"
    artifact_id = stable_id("query-plan-observation", attempt_digest, report_digest, length=26)
    artifact = create_versioned_artifact(
        OBSERVATION_CONTRACT,
        "1.0.0",
        artifact_id,
        observation_id=artifact_id,
        parent_plan_digest=plan_digest,
        attempt_digest=attempt_digest,
        coverage_manifest_digest=manifest_digest,
        evidence_report_digest=report_digest,
        requirement_outcomes=[
            {"requirement_id": requirement_id, "outcome": outcomes[requirement_id]}
            for requirement_id in outcomes
        ],
        newly_covered_requirement_ids=list(progress.newly_covered_requirement_ids),
        unresolved_requirement_ids=list(progress.unresolved_requirement_ids),
        progress=progress_status,
        progress_reason=progress_reason,
        graph_sha256_before=graph_sha256_before,
        graph_sha256_after=graph_sha256_after,
        provider_call_count=0,
        extensions={"wave": "W4", "evidence_source": "sealed_retrieval_evidence_report"},
    )
    return artifact, progress


def build_shadow_action(
    *,
    action: L1Action,
    plan_digest: str,
    coverage_manifest_digest: str,
    authorization_scope_digest: str,
    active_pair: Mapping[str, Any],
    budget: BudgetState,
    graph_sha256: str,
) -> tuple[dict[str, Any], L1Action]:
    """Seal one would-be next action as skipped and explicitly shadow-only."""

    next_action = create_action(
        parent_plan_digest=plan_digest,
        coverage_manifest_digest=coverage_manifest_digest,
        authorization_scope_digest=authorization_scope_digest,
        attempt_ordinal=1,
        action_type=action.action_type,
        requirement_ids=action.requirement_ids,
        input_digest=action.input_digest,
    )
    artifact_id = stable_id("query-retrieval-attempt-shadow", next_action.attempt_id, length=26)
    artifact = create_versioned_artifact(
        ATTEMPT_CONTRACT,
        "1.0.0",
        artifact_id,
        attempt_id=next_action.attempt_id,
        parent_plan_digest=plan_digest,
        coverage_manifest_digest=coverage_manifest_digest,
        attempt_ordinal=1,
        action_id=next_action.action_id,
        action_type=next_action.action_type,
        requirement_ids=list(next_action.requirement_ids),
        authorization_scope_digest=authorization_scope_digest,
        active_pair=_pair(active_pair),
        input_digest=next_action.input_digest,
        budget_before={
            "actions_used": budget.actions_used,
            "records_used": budget.unique_records,
            "bytes_used": budget.result_bytes,
            "elapsed_ms": budget.elapsed_ms,
        },
        result={
            "status": "skipped",
            "record_ids_by_lane": {},
            "result_digest": digest({"action_id": next_action.action_id, "reason_code": "SHADOW_ONLY"}),
            "elapsed_ms": 0,
            "bytes": 0,
            "provider_initialization_count": 0,
            "provider_call_count": 0,
        },
        budget_after={
            "actions_used": budget.actions_used,
            "records_used": budget.unique_records,
            "bytes_used": budget.result_bytes,
            "elapsed_ms": budget.elapsed_ms,
        },
        graph_sha256_before=graph_sha256,
        graph_sha256_after=graph_sha256,
        provider_initialization_count=0,
        provider_call_count=0,
        extensions={"wave": "W4", "executed": False, "reason_code": "SHADOW_ONLY"},
    )
    return artifact, next_action


def build_shadow_terminal(
    *,
    plan: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    attempt_chain: Sequence[Mapping[str, Any]],
    observation: Mapping[str, Any],
    budget: BudgetState,
    controller_terminal_reason: str,
    runtime_terminal_outcome: str,
    runtime_next_stage: str,
    graph_sha256_before: str,
    graph_sha256_after: str,
) -> dict[str, Any]:
    plan_digest = _canonical(plan, "query plan")
    manifest_digest = _canonical(coverage_manifest, "coverage manifest")
    report_digest = _canonical(evidence_report, "evidence report")
    observation_digest = _canonical(observation, "observation zero")
    if not attempt_chain:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "shadow terminal requires an attempt chain")
    attempt_digests = [_canonical(artifact, "attempt chain artifact") for artifact in attempt_chain]
    if observation.get("parent_plan_digest") != plan_digest:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "observation is not bound to the plan")
    terminal_id = stable_id("query-plan-terminal", plan_digest, observation_digest, runtime_terminal_outcome, length=26)
    artifact = create_versioned_artifact(
        TERMINAL_CONTRACT,
        "1.0.0",
        terminal_id,
        terminal_id=terminal_id,
        parent_plan_digest=plan_digest,
        coverage_manifest_digest=manifest_digest,
        attempt_chain=attempt_digests,
        final_evidence_report_digest=report_digest,
        total_work={
            "attempts": len(attempt_chain),
            "actions": max(1, budget.actions_used),
            "unique_records": budget.unique_records,
            "bytes": budget.result_bytes,
            "elapsed_ms": budget.elapsed_ms,
        },
        final_provider_call_count=0,
        graph_sha256_before=graph_sha256_before,
        graph_sha256_after=graph_sha256_after,
        terminal_reason="SHADOW",
        # The artifact is intentionally a blocked SHADOW terminal. Contract
        # semantics forbid a blocked terminal from claiming completion; the
        # would-be completion state remains bound in ``extensions``.
        coverage_complete=False,
        permitted_next_stage=runtime_next_stage,
        extensions={
            "wave": "W4",
            "executed": False,
            "reason_code": "SHADOW_ONLY",
            "would_be_terminal_reason": _terminal_reason(controller_terminal_reason),
            "controller_terminal_reason": controller_terminal_reason,
            "runtime_terminal_outcome": runtime_terminal_outcome,
            "runtime_next_stage": runtime_next_stage,
            "observation_digest": observation_digest,
        },
    )
    return artifact


def build_off_shadow_comparison(
    *,
    off_projection: Mapping[str, Any],
    shadow_projection: Mapping[str, Any],
    comparison_id: str | None = None,
) -> dict[str, Any]:
    """Compare only authority-relevant, content-safe off/shadow projections."""

    fields = (
        "route_digest",
        "retrieval_digest",
        "selected_records_digest",
        "evidence_packet_digest",
        "l2d_digest",
        "answer_digest",
        "x_gates_digest",
        "cache_behavior_digest",
        "provider_call_count",
        "graph_sha256_before",
        "graph_sha256_after",
        "terminal_outcome",
        "caller_output_digest",
    )
    comparisons: dict[str, dict[str, Any]] = {}
    for field in fields:
        left, right = off_projection.get(field), shadow_projection.get(field)
        comparisons[field] = {"off": left, "shadow": right, "equal": left == right}
    body = {
        "comparison_id": comparison_id or stable_id("l1-shadow-comparison", digest(comparisons), length=26),
        "mode_pair": "off_vs_shadow",
        "status": "PASS" if all(value["equal"] for value in comparisons.values()) else "FAIL",
        "fields": comparisons,
        "provider_call_count": 0,
        "graph_mutation_count": 0,
    }
    body["comparison_digest"] = digest(body)
    return body
