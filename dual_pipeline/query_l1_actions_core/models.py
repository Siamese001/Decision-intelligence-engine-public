"""Data models and execution limits for query L1 actions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..l1_control import BudgetState, L1Action, L1ControlError, L1ControlPolicy
from .constants import LANE_ORDER


class L1ActionExecutionError(L1ControlError):
    """Closed execution failure that must terminate the single action."""


@dataclass(frozen=True)
class ActionLimits:
    """Per-action limits derived from the checked-in aggregate policy.

    The aggregate policy remains the authority.  Dividing each aggregate by
    the maximum action count prevents a later action from spending the whole
    run budget and dividing the record allowance by the authorized lane count
    prevents a silent top-k multiplication across lanes.
    """

    max_candidates_per_lane: int
    max_selected_per_lane: int
    max_records_per_action: int
    max_result_bytes_per_action: int
    max_elapsed_ms_per_action: int

    @classmethod
    def from_policy(
        cls, policy: L1ControlPolicy, lane_count: int
    ) -> "ActionLimits":
        if isinstance(lane_count, bool) or not isinstance(lane_count, int) or not 1 <= lane_count <= len(LANE_ORDER):
            raise L1ActionExecutionError("L1_INTERNAL_INVALID", "lane_count is outside the closed lane set")
        action_count = max(1, policy.max_actions)
        records = max(1, policy.max_unique_records // action_count)
        selected = max(1, records // lane_count)
        # Candidate work is no greater than the separately admitted per-action
        # record budget.  This keeps dense/sparse/ColBERT work bounded before
        # the encoder or retriever is initialized.
        candidates = selected
        return cls(
            max_candidates_per_lane=candidates,
            max_selected_per_lane=selected,
            max_records_per_action=records,
            max_result_bytes_per_action=max(1, policy.max_result_bytes // action_count),
            max_elapsed_ms_per_action=max(1, policy.max_elapsed_ms // action_count),
        )


@dataclass(frozen=True)
class ActionExecutionResult:
    """One terminal, single-attempt execution outcome."""

    status: str
    reason_code: str
    action: L1Action
    attempt_artifact: dict[str, Any]
    retrieval_artifact: dict[str, Any] | None
    budget_before: BudgetState
    budget_after: BudgetState
    encoding_count: int
