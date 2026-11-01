"""Pure deterministic state machine for governed L1 controller work.

Thin backward-compatibility facade delegating strictly to dual_pipeline.l1_control_core.
"""
from __future__ import annotations

from dual_pipeline.l1_control_core import (
    ACTION_TYPES,
    LANES,
    OUTCOMES,
    TERMINAL_REASONS,
    BudgetState,
    GuardResult,
    GuardStatus,
    L1Action,
    L1ControlError,
    L1ControlPolicy,
    ProgressObservation,
    ProgressStatus,
    TerminalDecision,
    apply_action_result,
    create_action,
    guard_action,
    load_policy,
    observe_progress,
    reduce_terminal,
    replay_digest,
    validate_action_chain,
)

__all__ = [
    "ACTION_TYPES",
    "TERMINAL_REASONS",
    "LANES",
    "OUTCOMES",
    "BudgetState",
    "GuardResult",
    "GuardStatus",
    "L1Action",
    "L1ControlError",
    "L1ControlPolicy",
    "ProgressObservation",
    "ProgressStatus",
    "TerminalDecision",
    "apply_action_result",
    "create_action",
    "guard_action",
    "load_policy",
    "observe_progress",
    "reduce_terminal",
    "replay_digest",
    "validate_action_chain",
]
