"""Policy loading and validation for governed L1 controller."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from dual_pipeline.core import digest

from .constants import (
    ACTION_TYPES,
    L1ControlError,
    _positive_int,
    _strict_keys,
)


@dataclass(frozen=True)
class L1ControlPolicy:
    policy_id: str
    policy_version: str
    mode: str
    action_allowlist: tuple[str, ...]
    max_additional_attempts: int
    max_actions: int
    max_unique_records: int
    max_result_bytes: int
    max_elapsed_ms: int
    progress_rule: str
    terminal_reasons: tuple[str, ...]
    provider_calls_before_l2d: int
    graph_mutations_before_l2d: int
    runtime_default: str
    policy_digest: str


def load_policy(value: Mapping[str, Any]) -> L1ControlPolicy:
    """Strictly parse a caller-supplied policy mapping without I/O."""

    expected = {
        "policy_id",
        "schema_version",
        "mode",
        "action_allowlist",
        "budget",
        "progress_rule",
        "terminal_reasons",
        "provider_calls_before_l2d",
        "graph_mutations_before_l2d",
        "runtime_default",
        "extensions",
    }
    _strict_keys(value, expected, "policy")
    if value["schema_version"] != "1.0.0":
        raise L1ControlError("L1_INTERNAL_INVALID", "policy schema_version must be 1.0.0")
    mode = value["mode"]
    if mode not in {"off", "shadow", "bounded"}:
        raise L1ControlError("L1_INTERNAL_INVALID", "policy mode is not closed")
    action_allowlist = value["action_allowlist"]
    if action_allowlist != list(ACTION_TYPES):
        raise L1ControlError(
            "L1_POLICY_BLOCKED",
            "policy must allow exactly requirement_hybrid_retrieval and stop",
        )
    budget = value["budget"]
    if not isinstance(budget, Mapping):
        raise L1ControlError("L1_INTERNAL_INVALID", "budget must be an object")
    _strict_keys(
        budget,
        {
            "max_additional_attempts",
            "max_actions",
            "max_unique_records",
            "max_result_bytes",
            "max_elapsed_ms",
        },
        "policy.budget",
    )
    terminal_reasons = value["terminal_reasons"]
    if tuple(terminal_reasons) != (
        "COMPLETED",
        "EXPLICIT_GAP",
        "NO_PROGRESS",
        "BUDGET_EXHAUSTED",
        "UNAUTHORIZED",
        "CONFLICT",
        "HITL_REQUIRED",
        "SHADOW",
    ):
        raise L1ControlError("L1_POLICY_BLOCKED", "policy terminal predicates are not the frozen set")
    if value["progress_rule"] != "newly_covered_material_requirements":
        raise L1ControlError("L1_POLICY_BLOCKED", "unsupported progress rule")
    provider_calls = _positive_int(value["provider_calls_before_l2d"], "provider_calls_before_l2d", allow_zero=True)
    graph_mutations = _positive_int(value["graph_mutations_before_l2d"], "graph_mutations_before_l2d", allow_zero=True)
    if provider_calls != 0 or graph_mutations != 0:
        raise L1ControlError("L1_POLICY_BLOCKED", "pre-L2 provider and graph budgets must be zero")
    if value["runtime_default"] != "off":
        raise L1ControlError("L1_POLICY_BLOCKED", "runtime default must remain off")
    return L1ControlPolicy(
        policy_id=str(value["policy_id"]),
        policy_version="1.0.0",
        mode=mode,
        action_allowlist=tuple(ACTION_TYPES),
        max_additional_attempts=_positive_int(budget["max_additional_attempts"], "max_additional_attempts", allow_zero=True),
        max_actions=_positive_int(budget["max_actions"], "max_actions"),
        max_unique_records=_positive_int(budget["max_unique_records"], "max_unique_records"),
        max_result_bytes=_positive_int(budget["max_result_bytes"], "max_result_bytes"),
        max_elapsed_ms=_positive_int(budget["max_elapsed_ms"], "max_elapsed_ms"),
        progress_rule="newly_covered_material_requirements",
        terminal_reasons=tuple(terminal_reasons),
        provider_calls_before_l2d=provider_calls,
        graph_mutations_before_l2d=graph_mutations,
        runtime_default="off",
        policy_digest=digest(dict(value)),
    )


__all__ = [
    "L1ControlPolicy",
    "load_policy",
]
