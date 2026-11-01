"""Receipt compilation, telemetry validation, and persistence for query L1 shadow execution."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core import write_json
from ..l1_control import (
    BudgetState,
    L1ControlError,
    L1ControlPolicy,
    apply_action_result,
    reduce_terminal,
    validate_action_chain,
)
from ..query_planning import compile_query_plan_candidate
from .builders import (
    build_attempt_zero,
    build_observation_zero,
    build_off_shadow_comparison,
    build_shadow_action,
    build_shadow_terminal,
)
from .constants import TELEMETRY_KEYS
from .helpers import (
    _canonical,
    _digest,
    _require_mode,
    _selected_records,
)
from .models import ShadowReceiptError, ShadowReceiptSet


def shadow_telemetry_payload(
    receipts: ShadowReceiptSet,
    *,
    runtime_terminal_outcome: str,
    runtime_next_stage: str,
    result_bytes: int = 0,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Return a content-safe L1 event payload for ``RunRecorder``."""

    if receipts.mode != "shadow" or receipts.planning is None:
        return {"event_type": "l1_shadow", "mode": "off", "executed": False, "reason_code": "OFF"}
    selected_count = 0
    unresolved_count = 0
    if receipts.attempt_zero is not None:
        selected_count = sum(
            len(ids)
            for ids in receipts.attempt_zero["result"]["record_ids_by_lane"].values()
        )
    if receipts.observation_zero is not None:
        unresolved_count = len(receipts.observation_zero["unresolved_requirement_ids"])
    payload = {
        "event_type": "l1_shadow",
        "mode": "shadow",
        "plan_digest": receipts.planning.plan["canonical_digest"]["value"],
        "coverage_manifest_digest": receipts.planning.plan["controller"]["coverage_manifest_digest"],
        "attempt_zero_digest": receipts.attempt_zero["canonical_digest"]["value"] if receipts.attempt_zero else None,
        "observation_zero_digest": receipts.observation_zero["canonical_digest"]["value"] if receipts.observation_zero else None,
        "shadow_action_digest": receipts.shadow_action["canonical_digest"]["value"] if receipts.shadow_action else None,
        "terminal_digest": receipts.terminal["canonical_digest"]["value"] if receipts.terminal else None,
        "comparison_digest": receipts.comparison.get("comparison_digest") if receipts.comparison else None,
        "action_count": len(receipts.planning.actions),
        "attempt_count": int(receipts.attempt_zero is not None) + int(receipts.shadow_action is not None),
        "selected_record_count": selected_count,
        "unresolved_requirement_count": unresolved_count,
        "provider_call_count": 0,
        "graph_mutation_count": 0,
        "elapsed_ms": elapsed_ms,
        "result_bytes": result_bytes,
        "controller_terminal_reason": receipts.controller_terminal_reason,
        "runtime_terminal_outcome": runtime_terminal_outcome,
        "runtime_next_stage": runtime_next_stage,
        "executed": False,
        "reason_code": "SHADOW_ONLY",
        "budget": receipts.budget.to_dict(),
    }
    validate_shadow_telemetry(payload)
    return payload


def validate_shadow_telemetry(payload: Mapping[str, Any]) -> None:
    forbidden_names = ("question", "label", "prompt", "response", "evidence", "excerpt", "raw")
    for key in payload:
        if any(token in key.casefold() for token in forbidden_names):
            raise ShadowReceiptError("L1_POLICY_BLOCKED", f"telemetry key is content-bearing: {key}")
    unknown = set(payload) - TELEMETRY_KEYS
    if unknown:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", f"telemetry contains unsupported keys: {sorted(unknown)}")


def write_shadow_receipts(
    run_dir: Path,
    receipts: ShadowReceiptSet,
    *,
    output_dir: Path | None = None,
) -> dict[str, str]:
    """Persist shadow artifacts only; return the written paths by artifact name."""

    if receipts.mode != "shadow":
        return {}
    run_dir.mkdir(parents=True, exist_ok=True)
    targets: dict[str, dict[str, Any] | None] = {
        "query_plan_v3": receipts.planning.plan if receipts.planning else None,
        "query_retrieval_attempt_zero": receipts.attempt_zero,
        "query_plan_observation_zero": receipts.observation_zero,
        "query_retrieval_attempt_shadow": receipts.shadow_action,
        "query_plan_terminal_shadow": receipts.terminal,
        "l1_shadow_comparison": receipts.comparison,
    }
    names = {
        "query_plan_v3": "query_plan_v3.json",
        "query_retrieval_attempt_zero": "query_retrieval_attempt_zero.json",
        "query_plan_observation_zero": "query_plan_observation_zero.json",
        "query_retrieval_attempt_shadow": "query_retrieval_attempt_shadow.json",
        "query_plan_terminal_shadow": "query_plan_terminal_shadow.json",
        "l1_shadow_comparison": "l1_shadow_comparison.json",
    }
    written: dict[str, str] = {}
    for key, artifact in targets.items():
        if artifact is None:
            continue
        path = run_dir / names[key]
        write_json(path, artifact)
        written[key] = str(path)
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            write_json(output_dir / names[key], artifact)
    return written


def _extract_observations(outcomes: Any) -> Mapping[str, Any]:
    if not isinstance(outcomes, Sequence) or isinstance(outcomes, (str, bytes)):
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "evidence report outcomes are missing")
    return {
        row["requirement_id"]: {
            "outcome": row["outcome"],
            "reason_code": row.get("reason_code") or "RETRIEVAL_COVERAGE_MISSING",
        }
        for row in outcomes
        if isinstance(row, Mapping)
    }

def _apply_budget_safe(
    attempt_action: Any, policy: Any, selected: Any, result_bytes: int, elapsed_ms: int, graph_before: Any, graph_after: Any
) -> Any:
    try:
        return apply_action_result(
            BudgetState.initial(),
            attempt_action,
            policy,
            record_ids_by_lane=selected,
            result_bytes=result_bytes,
            elapsed_ms=elapsed_ms,
            graph_sha256_before=graph_before,
            graph_sha256_after=graph_after,
        )
    except L1ControlError as exc:
        if exc.reason != "L1_BUDGET_EXHAUSTED":
            raise
        return BudgetState(
            additional_attempts_used=0,
            actions_used=policy.max_actions,
            records_by_lane=tuple((lane, ids) for lane, ids in selected.items() if ids),
            result_bytes=result_bytes,
            elapsed_ms=elapsed_ms,
            action_ids=(attempt_action.action_id,),
            attempt_ids=(attempt_action.attempt_id,),
        )

def _get_refinable_requirements(outcomes: Any) -> tuple:
    return tuple(
        requirement_id
        for row in outcomes
        if isinstance(row, Mapping)
        and row.get("outcome") == "explicit_gap"
        and (row.get("reason_code") in {None, "RETRIEVAL_COVERAGE_MISSING"})
        for requirement_id in (row["requirement_id"],)
    )

def build_shadow_receipts(
    *,
    plan_v2: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    active_pair: Mapping[str, Any],
    policy: L1ControlPolicy,
    mode: str = "shadow",
    runtime_terminal_outcome: str = "PENDING",
    runtime_next_stage: str = "NONE",
    graph_sha256_before: str | None = None,
    graph_sha256_after: str | None = None,
    result_bytes: int = 0,
    elapsed_ms: int = 0,
    off_projection: Mapping[str, Any] | None = None,
    shadow_projection: Mapping[str, Any] | None = None,
) -> ShadowReceiptSet:
    """Build one recoverable, ordered shadow chain around existing retrieval."""

    _require_mode(mode)
    if mode == "off":
        return ShadowReceiptSet("off", None, None, None, None, None, None, BudgetState.initial(), None)
    plan_digest = _canonical(plan_v2, "query plan")
    manifest_digest = _canonical(coverage_manifest, "coverage manifest")
    _canonical(evidence_report, "evidence report")
    graph_before = graph_sha256_before or active_pair.get("graph_sha256")
    graph_after = graph_sha256_after or active_pair.get("graph_sha256")
    if graph_before != graph_after:
        raise ShadowReceiptError("L1_INTERNAL_INVALID", "shadow instrumentation observed graph drift")
    _digest(graph_before, "graph hash before")
    _digest(graph_after, "graph hash after")
    outcomes = evidence_report.get("requirement_outcomes")
    observations = _extract_observations(outcomes)
    planning = compile_query_plan_candidate(
        plan_v2,
        coverage_manifest,
        policy,
        mode="shadow",
        observations=observations,
    )
    attempt_zero, attempt_action = build_attempt_zero(
        plan=plan_v2,
        coverage_manifest=coverage_manifest,
        evidence_report=evidence_report,
        active_pair=active_pair,
        graph_sha256_before=graph_before,
        graph_sha256_after=graph_after,
        result_bytes=result_bytes,
        elapsed_ms=elapsed_ms,
        status="completed",
    )
    selected = _selected_records(evidence_report)
    budget = _apply_budget_safe(
        attempt_action, policy, selected, result_bytes, elapsed_ms, graph_before, graph_after
    )
    observation_zero, progress = build_observation_zero(
        plan=plan_v2,
        coverage_manifest=coverage_manifest,
        evidence_report=evidence_report,
        attempt_zero=attempt_zero,
        graph_sha256_before=graph_before,
        graph_sha256_after=graph_after,
    )
    refinable = _get_refinable_requirements(outcomes)
    terminal_decision = reduce_terminal(
        progress,
        state=budget,
        policy=policy,
        refinable_requirement_ids=refinable,
        non_refinable=bool(progress.unresolved_requirement_ids) and not refinable,
    )
    shadow_action_artifact: dict[str, Any] | None = None
    chain_actions = [attempt_action]
    if planning.actions and terminal_decision.reason not in {"L1_COMPLETE", "L1_UNAUTHORIZED", "L1_CONFLICT"}:
        shadow_action_artifact, shadow_action = build_shadow_action(
            action=planning.actions[0],
            plan_digest=plan_digest,
            coverage_manifest_digest=manifest_digest,
            authorization_scope_digest=_digest(
                coverage_manifest.get("authorization_scope_digest"),
                "authorization scope digest",
            ),
            active_pair=active_pair,
            budget=budget,
            graph_sha256=graph_before,
        )
        chain_actions.append(shadow_action)
    validate_action_chain(
        chain_actions,
        parent_plan_digest=plan_digest,
        coverage_manifest_digest=manifest_digest,
    )
    attempt_chain = [attempt_zero]
    if shadow_action_artifact is not None:
        attempt_chain.append(shadow_action_artifact)
    terminal = build_shadow_terminal(
        plan=plan_v2,
        coverage_manifest=coverage_manifest,
        evidence_report=evidence_report,
        attempt_chain=attempt_chain,
        observation=observation_zero,
        budget=budget,
        controller_terminal_reason=terminal_decision.reason,
        runtime_terminal_outcome=runtime_terminal_outcome,
        runtime_next_stage=runtime_next_stage,
        graph_sha256_before=graph_before,
        graph_sha256_after=graph_after,
    )
    comparison = None
    if off_projection is not None and shadow_projection is not None:
        comparison = build_off_shadow_comparison(
            off_projection=off_projection,
            shadow_projection=shadow_projection,
        )
    return ShadowReceiptSet(
        mode="shadow",
        planning=planning,
        attempt_zero=attempt_zero,
        observation_zero=observation_zero,
        shadow_action=shadow_action_artifact,
        terminal=terminal,
        comparison=comparison,
        budget=budget,
        controller_terminal_reason=terminal_decision.reason,
    )
