"""Contracts and validators for agent delegation, returns, and hooks.

Enforces:
- Structural interaction contracts between Agents and Sub-Agents.
- Cycle prevention in task delegation hierarchies.
- Strict numeric budget boundaries (tool calls, timeouts, recursion depth).
- Upstream return protocol compliance and typed escalation handling.
- Non-intrusive hook extension semantics.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from dual_pipeline.agent_architecture_core.models import HookTiming

REQUIRED_UNIFIED_ARCHITECTURE_PHRASES: tuple[str, ...] = (
    "## Skills, agents, sub-agents, hooks, and rules unified architecture",
    "Five Core Architectural Pillars",
    "Skills (What work gets done)",
    "Agents (Who decides & orchestrates)",
    "Sub-Agents (Specialized executors)",
    "Hooks (When to extend)",
    "Rules (What must be true)",
    "Shared Foundations (Common to All)",
    "Structural Interactions & Delegation Contracts",
    "End-to-End Execution Flow (7-Step Lifecycle)",
    "Skills are the building blocks",
    "Agents orchestrate and decide",
    "Sub-Agents specialize and scale",
    "Hooks extend behavior",
    "Rules govern",
)

REQUIRED_DELEGATION_CONTRACT_FIELDS: tuple[str, ...] = (
    "task_id",
    "parent_task_id",
    "objective",
    "scope_in",
    "scope_out",
    "allowed_tools",
    "deliverable",
    "definition_of_done",
    "budget",
    "escalation_rule",
)

REQUIRED_SUBAGENT_RETURN_FIELDS: tuple[str, ...] = (
    "task_id",
    "status",
    "deliverable",
    "evidence_refs",
    "metrics",
)

VALID_SUBAGENT_STATUSES: tuple[str, ...] = (
    "COMPLETE",
    "BLOCKED",
    "ESCALATED",
    "FAILED",
)

BUDGET_LIMITS: Mapping[str, tuple[float, float]] = {
    "max_tool_calls": (1, 100),
    "timeout_seconds": (1, 300),
    "max_depth": (1, 4),
    "max_turns": (1, 50),
}


def _validate_contract_required_fields(contract: Mapping[str, Any]) -> list[str]:
    """Checks required fields are present and non-empty."""
    errors: list[str] = []
    for req_field in REQUIRED_DELEGATION_CONTRACT_FIELDS:
        val = contract.get(req_field)
        if val is None or (isinstance(val, (str, list, dict)) and len(val) == 0):
            errors.append(f"DELEGATION_CONTRACT_MISSING_FIELD: '{req_field}' is required and cannot be empty.")
    return errors


def _validate_contract_cycles(
    contract: Mapping[str, Any],
    ancestry_chain: Sequence[str] | None = None,
) -> list[str]:
    """Detects self-delegation or cyclical ancestry in delegation chains."""
    errors: list[str] = []
    task_id = str(contract.get("task_id", "")).strip()
    parent_task_id = str(contract.get("parent_task_id", "")).strip()

    if task_id and parent_task_id and task_id == parent_task_id:
        errors.append(f"DELEGATION_CONTRACT_CYCLE_DETECTED: task_id '{task_id}' cannot equal parent_task_id.")

    lineage = list(ancestry_chain or [])
    contract_lineage = contract.get("task_lineage") or contract.get("visited_task_ids")
    if isinstance(contract_lineage, (list, tuple)):
        lineage.extend(str(tid) for tid in contract_lineage)

    if task_id and task_id in lineage:
        errors.append(
            f"DELEGATION_CONTRACT_CYCLE_DETECTED: task_id '{task_id}' already present in delegation lineage: {lineage}."
        )
    return errors


def _validate_contract_budget(budget: Any) -> list[str]:
    """Validates budget mapping constraints and bounds."""
    errors: list[str] = []
    if not isinstance(budget, Mapping):
        if budget is not None:
            errors.append("DELEGATION_CONTRACT_INVALID_BUDGET: budget must be a mapping with explicit limits.")
        return errors

    recognized_keys = ("max_turns", "max_tool_calls", "timeout_seconds", "max_cost", "max_depth")
    if not any(k in budget for k in recognized_keys):
        errors.append("DELEGATION_CONTRACT_UNBOUNDED_BUDGET: budget must declare at least one bounded constraint.")

    for b_key, (min_v, max_v) in BUDGET_LIMITS.items():
        if b_key in budget:
            val = budget[b_key]
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val < min_v or val > max_v:
                errors.append(
                    f"DELEGATION_CONTRACT_BUDGET_OUT_OF_BOUNDS: '{b_key}' must be between {int(min_v)} and {int(max_v)}, got {val}."
                )
    return errors


def validate_delegation_contract(
    contract: Mapping[str, Any],
    ancestry_chain: Sequence[str] | None = None,
) -> tuple[bool, list[str]]:
    """Verify that a delegation contract from an Agent to a Sub-Agent is contract-bound."""
    errors: list[str] = []
    errors.extend(_validate_contract_required_fields(contract))
    errors.extend(_validate_contract_cycles(contract, ancestry_chain))
    errors.extend(_validate_contract_budget(contract.get("budget")))
    return len(errors) == 0, errors


def validate_subagent_return(return_payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Verify that results returned upstream by a Sub-Agent satisfy the return protocol."""
    errors: list[str] = []
    for req_field in REQUIRED_SUBAGENT_RETURN_FIELDS:
        if req_field not in return_payload:
            errors.append(f"SUBAGENT_RETURN_MISSING_FIELD: '{req_field}' is required.")

    status = return_payload.get("status")
    if status not in VALID_SUBAGENT_STATUSES:
        errors.append(
            f"SUBAGENT_RETURN_INVALID_STATUS: status '{status}' not in allowed {VALID_SUBAGENT_STATUSES}."
        )

    # Typed failure/escalation protocol validation
    if status in ("FAILED", "ESCALATED"):
        has_error_context = bool(
            return_payload.get("error_code")
            or return_payload.get("error_message")
            or return_payload.get("escalation_reason")
        )
        if not has_error_context:
            errors.append(
                f"SUBAGENT_RETURN_MISSING_ERROR_CONTEXT: status '{status}' requires 'error_code', 'error_message', or 'escalation_reason'."
            )

    evidence_refs = return_payload.get("evidence_refs")
    if evidence_refs is not None and not isinstance(evidence_refs, (list, tuple)):
        errors.append("SUBAGENT_RETURN_INVALID_EVIDENCE: evidence_refs must be a list of locators or digests.")

    return len(errors) == 0, errors


def validate_hook_definition(hook: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Validate that a hook declaration conforms to non-intrusive pre/post/around extension semantics."""
    errors: list[str] = []
    hook_id = hook.get("id", "")
    if not hook_id:
        errors.append("HOOK_MISSING_ID: Hook definition must have an 'id'.")

    timing_raw = str(hook.get("timing", "")).upper()
    event_raw = str(hook.get("event", "")).lower()

    if not timing_raw:
        if "pre" in event_raw:
            timing_raw = HookTiming.PRE.value
        elif "post" in event_raw:
            timing_raw = HookTiming.POST.value
        elif "around" in event_raw:
            timing_raw = HookTiming.AROUND.value

    valid_timings = {t.value for t in HookTiming}
    if timing_raw not in valid_timings:
        errors.append(f"HOOK_INVALID_TIMING: Hook '{hook_id}' has invalid timing '{timing_raw}'. Expected {valid_timings}.")

    if hook.get("modifies_core_logic") is True:
        errors.append(f"HOOK_INTRUSIVE_VIOLATION: Hook '{hook_id}' must remain non-intrusive.")

    return len(errors) == 0, errors
