"""Pure deterministic models and data structures for governed L1 controller work."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from dual_pipeline.core import digest, stable_id

from .constants import (
    ACTION_TYPES,
    LANES,
    OUTCOMES,
    TERMINAL_REASONS,
    GuardStatus,
    L1ControlError,
    ProgressStatus,
    _positive_int,
    _require_digest,
)


@dataclass(frozen=True)
class BudgetState:
    additional_attempts_used: int = 0
    actions_used: int = 0
    records_by_lane: tuple[tuple[str, tuple[str, ...]], ...] = ()
    result_bytes: int = 0
    elapsed_ms: int = 0
    action_ids: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()

    @property
    def record_ids_by_lane(self) -> dict[str, tuple[str, ...]]:
        return {lane: tuple(ids) for lane, ids in self.records_by_lane}

    @property
    def unique_record_ids(self) -> frozenset[str]:
        return frozenset(record_id for _, ids in self.records_by_lane for record_id in ids)

    @property
    def unique_records(self) -> int:
        return len(self.unique_record_ids)

    @classmethod
    def initial(cls) -> "BudgetState":
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "additional_attempts_used": self.additional_attempts_used,
            "actions_used": self.actions_used,
            "records_by_lane": {lane: list(ids) for lane, ids in self.records_by_lane},
            "result_bytes": self.result_bytes,
            "elapsed_ms": self.elapsed_ms,
            "action_ids": list(self.action_ids),
            "attempt_ids": list(self.attempt_ids),
        }


@dataclass(frozen=True)
class L1Action:
    action_id: str
    attempt_id: str
    parent_plan_digest: str
    coverage_manifest_digest: str
    authorization_scope_digest: str
    attempt_ordinal: int
    action_type: str
    requirement_ids: tuple[str, ...]
    input_digest: str
    estimated_records: int = 0
    estimated_result_bytes: int = 0
    estimated_elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "attempt_id": self.attempt_id,
            "parent_plan_digest": self.parent_plan_digest,
            "coverage_manifest_digest": self.coverage_manifest_digest,
            "authorization_scope_digest": self.authorization_scope_digest,
            "attempt_ordinal": self.attempt_ordinal,
            "action_type": self.action_type,
            "requirement_ids": list(self.requirement_ids),
            "input_digest": self.input_digest,
            "estimated_records": self.estimated_records,
            "estimated_result_bytes": self.estimated_result_bytes,
            "estimated_elapsed_ms": self.estimated_elapsed_ms,
        }


def create_action(
    *,
    parent_plan_digest: str,
    coverage_manifest_digest: str,
    authorization_scope_digest: str,
    attempt_ordinal: int,
    action_type: str,
    requirement_ids: Sequence[str],
    input_digest: str,
    estimated_records: int = 0,
    estimated_result_bytes: int = 0,
    estimated_elapsed_ms: int = 0,
) -> L1Action:
    if isinstance(requirement_ids, (str, bytes)):
        raise L1ControlError("L1_INTERNAL_INVALID", "action requirements must be a sequence of IDs")
    requirements = tuple(requirement_ids)
    if not requirements or len(set(requirements)) != len(requirements):
        raise L1ControlError("L1_INTERNAL_INVALID", "action requirements must be nonempty and unique")
    if action_type not in ACTION_TYPES:
        raise L1ControlError("L1_POLICY_BLOCKED", f"action type is not allowed: {action_type}")
    if attempt_ordinal < 0:
        raise L1ControlError("L1_INTERNAL_INVALID", "attempt ordinal cannot be negative")
    for label, value in (
        ("estimated_records", estimated_records),
        ("estimated_result_bytes", estimated_result_bytes),
        ("estimated_elapsed_ms", estimated_elapsed_ms),
    ):
        _positive_int(value, label, allow_zero=True)
    _require_digest(parent_plan_digest, "parent_plan_digest")
    _require_digest(coverage_manifest_digest, "coverage_manifest_digest")
    _require_digest(authorization_scope_digest, "authorization_scope_digest")
    _require_digest(input_digest, "input_digest")
    identity = [
        parent_plan_digest,
        coverage_manifest_digest,
        authorization_scope_digest,
        attempt_ordinal,
        action_type,
        list(requirements),
        input_digest,
    ]
    attempt_id = stable_id("l1-attempt", *identity, length=26)
    action_id = stable_id("l1-action", *identity, length=26)
    return L1Action(
        action_id=action_id,
        attempt_id=attempt_id,
        parent_plan_digest=parent_plan_digest,
        coverage_manifest_digest=coverage_manifest_digest,
        authorization_scope_digest=authorization_scope_digest,
        attempt_ordinal=attempt_ordinal,
        action_type=action_type,
        requirement_ids=requirements,
        input_digest=input_digest,
        estimated_records=estimated_records,
        estimated_result_bytes=estimated_result_bytes,
        estimated_elapsed_ms=estimated_elapsed_ms,
    )


@dataclass(frozen=True)
class GuardResult:
    status: GuardStatus
    reason: str | None
    action_id: str


@dataclass(frozen=True)
class ProgressObservation:
    status: ProgressStatus
    newly_covered_requirement_ids: tuple[str, ...]
    unresolved_requirement_ids: tuple[str, ...]
    outcomes: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "progress": self.status.value,
            "newly_covered_requirement_ids": list(self.newly_covered_requirement_ids),
            "unresolved_requirement_ids": list(self.unresolved_requirement_ids),
            "outcomes": {key: value for key, value in self.outcomes},
        }


@dataclass(frozen=True)
class TerminalDecision:
    reason: str
    permitted_next_stage: str
    coverage_complete: bool


def replay_digest(state: BudgetState) -> str:
    """Return a deterministic digest for comparison of pure controller state."""
    return digest(state.to_dict())


__all__ = [
    "ACTION_TYPES",
    "LANES",
    "OUTCOMES",
    "TERMINAL_REASONS",
    "BudgetState",
    "GuardResult",
    "GuardStatus",
    "L1Action",
    "L1ControlError",
    "ProgressObservation",
    "ProgressStatus",
    "TerminalDecision",
    "create_action",
    "replay_digest",
]
