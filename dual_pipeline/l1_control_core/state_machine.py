"""Pure state machine transitions and invariants for governed L1 controller."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from dual_pipeline.core import digest

from .constants import (
    LANES,
    OUTCOMES,
    GuardStatus,
    L1ControlError,
    ProgressStatus,
    _positive_int,
    _require_digest,
)
from .models import (
    BudgetState,
    GuardResult,
    L1Action,
    ProgressObservation,
    TerminalDecision,
    create_action,
)
from .policy import L1ControlPolicy


def guard_action(policy: L1ControlPolicy, state: BudgetState, action: L1Action) -> GuardResult:
    """Perform every closed pre-action check before retrieval initialization."""
    if action.action_type not in policy.action_allowlist:
        return GuardResult(GuardStatus.BLOCK, "L1_POLICY_BLOCKED", action.action_id)
    if action.action_id in state.action_ids or action.attempt_id in state.attempt_ids:
        return GuardResult(GuardStatus.BLOCK, "L1_INTERNAL_INVALID", action.action_id)
    if state.actions_used + 1 > policy.max_actions:
        return GuardResult(GuardStatus.BLOCK, "L1_BUDGET_EXHAUSTED", action.action_id)
    expected_ordinal = 0 if state.actions_used == 0 else state.additional_attempts_used + 1
    if action.attempt_ordinal != expected_ordinal:
        return GuardResult(GuardStatus.BLOCK, "L1_INTERNAL_INVALID", action.action_id)
    if action.attempt_ordinal > policy.max_additional_attempts:
        return GuardResult(GuardStatus.BLOCK, "L1_BUDGET_EXHAUSTED", action.action_id)
    if state.unique_records + action.estimated_records > policy.max_unique_records:
        return GuardResult(GuardStatus.BLOCK, "L1_BUDGET_EXHAUSTED", action.action_id)
    if state.result_bytes + action.estimated_result_bytes > policy.max_result_bytes:
        return GuardResult(GuardStatus.BLOCK, "L1_BUDGET_EXHAUSTED", action.action_id)
    if state.elapsed_ms + action.estimated_elapsed_ms > policy.max_elapsed_ms:
        return GuardResult(GuardStatus.BLOCK, "L1_BUDGET_EXHAUSTED", action.action_id)
    return GuardResult(GuardStatus.ALLOW, None, action.action_id)


def apply_action_result(
    state: BudgetState,
    action: L1Action,
    policy: L1ControlPolicy,
    *,
    record_ids_by_lane: Mapping[str, Sequence[str]],
    result_bytes: int,
    elapsed_ms: int,
    graph_sha256_before: str,
    graph_sha256_after: str,
    provider_initialization_count: int = 0,
    provider_call_count: int = 0,
) -> BudgetState:
    """Account a completed action without accepting hidden work."""
    if action.action_id in state.action_ids or action.attempt_id in state.attempt_ids:
        raise L1ControlError("L1_INTERNAL_INVALID", "duplicate action or attempt")
    if graph_sha256_before != graph_sha256_after:
        raise L1ControlError("L1_INTERNAL_INVALID", "graph hash changed during L1 action")
    _require_digest(graph_sha256_before, "graph_sha256_before")
    if provider_initialization_count != 0 or provider_call_count != 0:
        raise L1ControlError("L1_POLICY_BLOCKED", "pre-L2 action cannot initialize or call a provider")
    _positive_int(result_bytes, "result_bytes", allow_zero=True)
    _positive_int(elapsed_ms, "elapsed_ms", allow_zero=True)
    normalized: dict[str, tuple[str, ...]] = {}
    for lane, values in record_ids_by_lane.items():
        if lane not in LANES:
            raise L1ControlError("L1_POLICY_BLOCKED", f"unknown evidence lane: {lane}")
        ids = tuple(sorted(set(values)))
        if len(ids) != len(tuple(values)):
            raise L1ControlError("L1_INTERNAL_INVALID", "record IDs must be unique per lane")
        normalized[lane] = ids
    previous = state.record_ids_by_lane
    merged = {lane: set(previous.get(lane, ())) for lane in LANES}
    for lane, ids in normalized.items():
        merged[lane].update(ids)
    records = tuple((lane, tuple(sorted(ids))) for lane, ids in sorted(merged.items()) if ids)
    new_records = len({record_id for _, ids in records for record_id in ids})
    if state.actions_used + 1 > policy.max_actions:
        raise L1ControlError("L1_BUDGET_EXHAUSTED", "action budget exceeded after result accounting")
    if action.attempt_ordinal > policy.max_additional_attempts:
        raise L1ControlError("L1_BUDGET_EXHAUSTED", "attempt budget exceeded after result accounting")
    if new_records > policy.max_unique_records:
        raise L1ControlError("L1_BUDGET_EXHAUSTED", "unique-record budget exceeded after result accounting")
    if state.result_bytes + result_bytes > policy.max_result_bytes:
        raise L1ControlError("L1_BUDGET_EXHAUSTED", "result-byte budget exceeded after result accounting")
    if state.elapsed_ms + elapsed_ms > policy.max_elapsed_ms:
        raise L1ControlError("L1_BUDGET_EXHAUSTED", "elapsed-time budget exceeded after result accounting")
    return BudgetState(
        additional_attempts_used=max(state.additional_attempts_used, action.attempt_ordinal),
        actions_used=state.actions_used + 1,
        records_by_lane=records,
        result_bytes=state.result_bytes + result_bytes,
        elapsed_ms=state.elapsed_ms + elapsed_ms,
        action_ids=state.action_ids + (action.action_id,),
        attempt_ids=state.attempt_ids + (action.attempt_id,),
    )


def observe_progress(
    previous_outcomes: Mapping[str, str],
    current_outcomes: Mapping[str, str],
) -> ProgressObservation:
    """Compare two complete requirement maps using only material coverage."""
    if set(previous_outcomes) != set(current_outcomes):
        raise L1ControlError("L1_INTERNAL_INVALID", "coverage requirement set changed across attempts")
    for mapping in (previous_outcomes, current_outcomes):
        for requirement_id, outcome in mapping.items():
            if not requirement_id or outcome not in OUTCOMES:
                raise L1ControlError("L1_INTERNAL_INVALID", f"invalid requirement outcome: {requirement_id}/{outcome}")
    for requirement_id, previous in previous_outcomes.items():
        if previous in {"covered", "excluded"} and current_outcomes[requirement_id] != previous:
            raise L1ControlError("L1_INTERNAL_INVALID", "coverage outcomes cannot regress across attempts")
    newly_covered = tuple(sorted(
        requirement_id
        for requirement_id in current_outcomes
        if current_outcomes[requirement_id] == "covered"
        and previous_outcomes[requirement_id] not in {"covered", "excluded"}
    ))
    unresolved = tuple(sorted(
        requirement_id
        for requirement_id, outcome in current_outcomes.items()
        if outcome not in {"covered", "excluded"}
    ))
    if not unresolved:
        status = ProgressStatus.COMPLETE
    elif newly_covered:
        status = ProgressStatus.PROGRESS
    else:
        status = ProgressStatus.NO_PROGRESS
    return ProgressObservation(
        status=status,
        newly_covered_requirement_ids=newly_covered,
        unresolved_requirement_ids=unresolved,
        outcomes=tuple(sorted(current_outcomes.items())),
    )


def reduce_terminal(
    observation: ProgressObservation,
    *,
    state: BudgetState,
    policy: L1ControlPolicy,
    refinable_requirement_ids: Sequence[str] = (),
    policy_blocked: bool = False,
    non_refinable: bool = False,
    internal_invalid: bool = False,
) -> TerminalDecision:
    """Reduce one observation and budget state to one closed terminal reason."""
    if internal_invalid:
        return TerminalDecision("L1_INTERNAL_INVALID", "NONE", False)
    if policy_blocked:
        return TerminalDecision("L1_POLICY_BLOCKED", "NONE", False)
    if observation.status is ProgressStatus.COMPLETE:
        return TerminalDecision("L1_COMPLETE", "L2D", True)
    outcomes = dict(observation.outcomes)
    if any(value == "unauthorized" for value in outcomes.values()):
        return TerminalDecision("L1_UNAUTHORIZED", "HITL", False)
    if any(value == "conflict" for value in outcomes.values()):
        return TerminalDecision("L1_CONFLICT", "HITL", False)
    if observation.status is ProgressStatus.NO_PROGRESS:
        return TerminalDecision("L1_NO_PROGRESS", "NONE", False)
    if state.actions_used >= policy.max_actions or state.additional_attempts_used > policy.max_additional_attempts:
        return TerminalDecision("L1_BUDGET_EXHAUSTED", "NONE", False)
    if non_refinable:
        return TerminalDecision("L1_NON_REFINABLE", "NONE", False)
    if not tuple(refinable_requirement_ids):
        return TerminalDecision("L1_EXPLICIT_GAP", "NONE", False)
    return TerminalDecision("L1_BUDGET_EXHAUSTED", "NONE", False)


def validate_action_chain(
    actions: Sequence[L1Action],
    *,
    parent_plan_digest: str,
    coverage_manifest_digest: str,
) -> str:
    """Validate ordered, contiguous, same-run actions and return chain digest."""
    if not actions:
        raise L1ControlError("L1_INTERNAL_INVALID", "attempt chain cannot be empty")
    seen_actions: set[str] = set()
    seen_attempts: set[str] = set()
    expected: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if action.attempt_ordinal != index:
            raise L1ControlError("L1_INTERNAL_INVALID", "attempt ordinals must be contiguous and ordered")
        if action.action_id in seen_actions or action.attempt_id in seen_attempts:
            raise L1ControlError("L1_INTERNAL_INVALID", "attempt chain contains duplicate IDs")
        if action.parent_plan_digest != parent_plan_digest or action.coverage_manifest_digest != coverage_manifest_digest:
            raise L1ControlError("L1_INTERNAL_INVALID", "attempt chain crosses plan or manifest bindings")
        expected_action = create_action(
            parent_plan_digest=action.parent_plan_digest,
            coverage_manifest_digest=action.coverage_manifest_digest,
            authorization_scope_digest=action.authorization_scope_digest,
            attempt_ordinal=action.attempt_ordinal,
            action_type=action.action_type,
            requirement_ids=action.requirement_ids,
            input_digest=action.input_digest,
            estimated_records=action.estimated_records,
            estimated_result_bytes=action.estimated_result_bytes,
            estimated_elapsed_ms=action.estimated_elapsed_ms,
        )
        if action.action_id != expected_action.action_id or action.attempt_id != expected_action.attempt_id:
            raise L1ControlError("L1_INTERNAL_INVALID", "action identity does not match its bound inputs")
        seen_actions.add(action.action_id)
        seen_attempts.add(action.attempt_id)
        expected.append(action.to_dict())
    return digest(expected)
