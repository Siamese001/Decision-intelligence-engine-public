#!/usr/bin/env python3
"""Unit test suite for Sub-Agent Dynamic Budget Exhaustion & Escalation Protocols.

Fulfills 2026+ Agentic Verification Standards (Dimension 8):
1. Verifies that exhausting sub-agent tool call budgets triggers immediate halt and upstream ESCALATED return.
2. Verifies that elapsed execution timeout exhaustion prevents infinite loops and yields a structured return.
3. Verifies that sub-agents cannot execute mutating actions when budget is depleted.
4. Verifies 10-field delegation contract validation and upstream return contract adherence.
5. Verifies A2A Task Ledger state progression, event logging, and idempotency guarantees.
"""

from __future__ import annotations

import time
import unittest

try:
    import pytest

    unit_marker = pytest.mark.unit
except ImportError:  # pragma: no cover

    def unit_marker(cls_or_fn):
        return cls_or_fn


from dual_pipeline.a2a.protocol import (
    A2ATask,
    A2ATaskStatus,
    DelegationBudget,
)
from dual_pipeline.a2a.task_ledger import A2ATaskLedger
from dual_pipeline.unified_agent_architecture import (
    REQUIRED_DELEGATION_CONTRACT_FIELDS,
    validate_delegation_contract,
    validate_subagent_return,
)


class MockSubAgentExecutor:
    """Deterministic mock sub-agent simulating tool execution bounded by DelegationBudget."""

    def __init__(self, contract: dict, current_time_fn=None):
        self.contract = contract
        self.budget = contract.get("budget", {})
        self.max_tool_calls = self.budget.get("max_tool_calls", 10)
        self.timeout_seconds = self.budget.get("timeout_seconds", 30.0)
        self.tool_calls_used = 0
        self.simulated_elapsed = 0.0
        self._time_fn = current_time_fn or time.time
        self._start_time = self._time_fn()
        self.executed_tools: list[str] = []

    def execute_tool(self, tool_name: str, payload: dict | None = None) -> dict:
        """Execute a tool call with strict budget enforcement."""
        # 1. Check tool call budget exhaustion
        if self.tool_calls_used >= self.max_tool_calls:
            raise BudgetExceededError(
                f"BUDGET_TOOL_CALLS_EXHAUSTED: Cap of {self.max_tool_calls} reached."
            )

        # 2. Check timeout budget exhaustion
        elapsed = self.simulated_elapsed or (self._time_fn() - self._start_time)
        if elapsed >= self.timeout_seconds:
            raise BudgetTimeoutError(
                f"BUDGET_TIMEOUT_EXHAUSTED: Timeout of {self.timeout_seconds}s exceeded (elapsed {elapsed:.2f}s)."
            )

        # 3. Check allowed tools whitelist
        allowed_tools = self.contract.get("allowed_tools", [])
        if tool_name not in allowed_tools:
            raise PermissionError(
                f"PERMISSION_DENIED_TOOL_NOT_ALLOWED: Tool '{tool_name}' not in allowed {allowed_tools}."
            )

        # 4. Check sub-agent mutation prohibition
        if (
            "mutate" in tool_name.lower()
            or "write" in tool_name.lower()
            or "delete" in tool_name.lower()
        ):
            raise PermissionError(
                f"PERMISSION_DENIED_SUBAGENT_MUTATION_PROHIBITED: Sub-agents cannot execute mutating tool '{tool_name}' directly."
            )

        self.tool_calls_used += 1
        self.executed_tools.append(tool_name)
        return {"status": "ok", "tool": tool_name, "call_index": self.tool_calls_used}

    def run_subtask_loop(self, planned_tools: list[str]) -> dict:
        """Simulate subtask execution loop with safe upstream escalation on budget breach."""
        partial_results: list[dict] = []
        try:
            for tool in planned_tools:
                res = self.execute_tool(tool)
                partial_results.append(res)

            # Normal completion
            return {
                "task_id": self.contract["task_id"],
                "status": "COMPLETE",
                "deliverable": {"results": partial_results},
                "evidence_refs": ["urn:sha256:mock_evidence_complete"],
                "metrics": {
                    "tool_calls_used": self.tool_calls_used,
                    "elapsed_seconds": self.simulated_elapsed
                    or (self._time_fn() - self._start_time),
                },
            }
        except (BudgetExceededError, BudgetTimeoutError) as budget_err:
            # Safe upstream escalation return
            return {
                "task_id": self.contract["task_id"],
                "status": "ESCALATED",
                "escalation_reason": str(budget_err),
                "deliverable": {
                    "partial_results": partial_results,
                    "escalation_reason": str(budget_err),
                    "escalation_rule": self.contract.get("escalation_rule", ""),
                },
                "evidence_refs": ["urn:sha256:mock_evidence_partial"],
                "metrics": {
                    "tool_calls_used": self.tool_calls_used,
                    "elapsed_seconds": self.simulated_elapsed
                    or (self._time_fn() - self._start_time),
                    "budget_exhausted": True,
                },
            }


class BudgetExceededError(Exception):
    """Raised when a sub-agent exhausts its max_tool_calls cap."""

    pass


class BudgetTimeoutError(Exception):
    """Raised when a sub-agent execution exceeds timeout_seconds."""

    pass


@unit_marker
class MultiAgentBudgetEscalationTests(unittest.TestCase):
    """Formal verification of sub-agent dynamic budget exhaustion, escalation, and task ledgers."""

    def setUp(self) -> None:
        self.valid_delegation_contract = {
            "task_id": "task-sec-inspect-402",
            "parent_task_id": "agent-orchestrator-main",
            "objective": "Inspect AST dependencies for cycle detection",
            "scope_in": ["dual_pipeline/query_core/"],
            "scope_out": ["storage/database.py", "external_apis"],
            "allowed_tools": ["ast_scanner", "cycle_detector", "metric_collector"],
            "deliverable": "cycle_audit_report.json",
            "definition_of_done": "All modules scanned with Tarjan's SCC and zero cycles detected",
            "budget": {
                "max_tool_calls": 3,
                "timeout_seconds": 15.0,
            },
            "escalation_rule": "Escalate to parent agent immediately if budget exhausted or cycle detected",
        }
        self.ledger = A2ATaskLedger(db_path=":memory:")

    # --- 1. Delegation Contract Validation ---

    def test_delegation_contract_contains_all_10_required_fields(self) -> None:
        """Every delegation contract must strictly provide all 10 mandatory governance fields."""
        is_valid, errors = validate_delegation_contract(self.valid_delegation_contract)
        self.assertTrue(is_valid, f"Expected valid contract, errors: {errors}")
        self.assertEqual(errors, [])

        for required_field in REQUIRED_DELEGATION_CONTRACT_FIELDS:
            corrupt_contract = dict(self.valid_delegation_contract)
            del corrupt_contract[required_field]
            is_valid_bad, errors_bad = validate_delegation_contract(corrupt_contract)
            self.assertFalse(is_valid_bad)
            self.assertTrue(any(f"'{required_field}' is required" in e for e in errors_bad))

    def test_delegation_contract_rejects_unbounded_budget(self) -> None:
        """Delegation contracts with missing or unbounded budgets must fail closed."""
        unbounded_contract = dict(self.valid_delegation_contract)
        unbounded_contract["budget"] = {"unbounded_flag": True}
        is_valid, errors = validate_delegation_contract(unbounded_contract)
        self.assertFalse(is_valid)
        self.assertTrue(any("DELEGATION_CONTRACT_UNBOUNDED_BUDGET" in e for e in errors))

    # --- 2. Dynamic Tool Call Budget Exhaustion & Escalation ---

    def test_subagent_halts_and_escalates_on_tool_call_cap_exhaustion(self) -> None:
        """Sub-agent must halt execution and return ESCALATED status when max_tool_calls is hit."""
        # Contract budget allows max 3 tool calls
        executor = MockSubAgentExecutor(self.valid_delegation_contract)

        # Attempt to run 5 tools (exceeding budget of 3)
        planned = [
            "ast_scanner",
            "cycle_detector",
            "metric_collector",
            "ast_scanner",
            "cycle_detector",
        ]
        return_payload = executor.run_subtask_loop(planned)

        # Verify safe upstream escalation return contract
        is_valid, errors = validate_subagent_return(return_payload)
        self.assertTrue(is_valid, f"Return contract must be valid: {errors}")
        self.assertEqual(return_payload["status"], "ESCALATED")
        self.assertEqual(return_payload["metrics"]["tool_calls_used"], 3)
        self.assertTrue(return_payload["metrics"]["budget_exhausted"])
        self.assertIn(
            "BUDGET_TOOL_CALLS_EXHAUSTED", return_payload["deliverable"]["escalation_reason"]
        )
        self.assertEqual(len(executor.executed_tools), 3)

    def test_depleted_budget_strictly_blocks_subsequent_tool_calls(self) -> None:
        """Once budget is depleted, any further tool call attempt raises BudgetExceededError."""
        executor = MockSubAgentExecutor(self.valid_delegation_contract)
        executor.execute_tool("ast_scanner")
        executor.execute_tool("cycle_detector")
        executor.execute_tool("metric_collector")

        # 4th call must fail closed
        with self.assertRaises(BudgetExceededError) as ctx:
            executor.execute_tool("ast_scanner")
        self.assertIn("BUDGET_TOOL_CALLS_EXHAUSTED", str(ctx.exception))

    # --- 3. Dynamic Execution Timeout Budget Exhaustion ---

    def test_subagent_halts_and_escalates_on_timeout_exhaustion(self) -> None:
        """Sub-agent must halt execution and return ESCALATED when timeout_seconds is exceeded."""
        executor = MockSubAgentExecutor(self.valid_delegation_contract)
        # Simulate elapsed time of 16.0s (exceeding 15.0s budget)
        executor.simulated_elapsed = 16.5

        planned = ["ast_scanner", "cycle_detector"]
        return_payload = executor.run_subtask_loop(planned)

        is_valid, errors = validate_subagent_return(return_payload)
        self.assertTrue(is_valid, f"Return contract must be valid: {errors}")
        self.assertEqual(return_payload["status"], "ESCALATED")
        self.assertEqual(return_payload["metrics"]["tool_calls_used"], 0)
        self.assertIn(
            "BUDGET_TIMEOUT_EXHAUSTED", return_payload["deliverable"]["escalation_reason"]
        )

    # --- 4. Sub-Agent Mutation Prohibition Invariant ---

    def test_subagent_direct_graph_mutation_attempt_is_rejected(self) -> None:
        """Sub-agents are strictly forbidden from executing direct mutating actions."""
        bad_contract = dict(self.valid_delegation_contract)
        bad_contract["allowed_tools"] = ["graph_mutate_node", "ast_scanner"]
        executor = MockSubAgentExecutor(bad_contract)

        with self.assertRaises(PermissionError) as ctx:
            executor.execute_tool("graph_mutate_node")
        self.assertIn("PERMISSION_DENIED_SUBAGENT_MUTATION_PROHIBITED", str(ctx.exception))

    def test_subagent_unauthorized_tool_attempt_is_rejected(self) -> None:
        """Attempting to invoke a tool outside allowed_tools raises PermissionError."""
        executor = MockSubAgentExecutor(self.valid_delegation_contract)
        with self.assertRaises(PermissionError) as ctx:
            executor.execute_tool("unauthorized_shell_tool")
        self.assertIn("PERMISSION_DENIED_TOOL_NOT_ALLOWED", str(ctx.exception))

    # --- 5. A2A Task Ledger & State Machine Progression ---

    def test_a2a_task_ledger_state_progression_and_idempotency(self) -> None:
        """Verify task persistence, state transitions, and idempotency in A2ATaskLedger."""
        budget = DelegationBudget(max_tool_calls=5, timeout_seconds=60.0)
        task = A2ATask(
            task_id="task-delegation-ledger-001",
            parent_task_id="agent-parent-01",
            sender_agent_id="agent-parent",
            recipient_agent_id="subagent-auditor",
            objective="Audit security scope",
            status=A2ATaskStatus.SUBMITTED,
            budget=budget,
            scope_in=["dual_pipeline/"],
            allowed_tools=["ast_scanner"],
            idempotency_key="idem-key-sec-001",
        )

        # 1. Record submission
        recorded = self.ledger.record_task_submission(task)
        self.assertEqual(recorded.task_id, "task-delegation-ledger-001")
        self.assertEqual(recorded.status, A2ATaskStatus.SUBMITTED)

        # 2. Idempotent re-submission returns existing task
        re_recorded = self.ledger.record_task_submission(task)
        self.assertEqual(re_recorded.task_id, recorded.task_id)

        # 3. State transition: SUBMITTED -> ACCEPTED -> WORKING
        updated_accepted = self.ledger.update_task_status(
            "task-delegation-ledger-001",
            A2ATaskStatus.ACCEPTED,
        )
        self.assertEqual(updated_accepted.status, A2ATaskStatus.ACCEPTED)

        updated_working = self.ledger.update_task_status(
            "task-delegation-ledger-001",
            A2ATaskStatus.WORKING,
        )
        self.assertEqual(updated_working.status, A2ATaskStatus.WORKING)

        # 4. Completion transition
        completed = self.ledger.update_task_status(
            "task-delegation-ledger-001",
            A2ATaskStatus.COMPLETED,
            output_payload={"findings": "clean"},
        )
        self.assertEqual(completed.status, A2ATaskStatus.COMPLETED)
        self.assertEqual(completed.output_payload, {"findings": "clean"})

        # 5. Verify events recorded in event ledger
        events = self.ledger.get_task_history("task-delegation-ledger-001")
        self.assertGreaterEqual(len(events), 3)
        statuses = [e["to_status"] for e in events]
        self.assertIn("ACCEPTED", statuses)
        self.assertIn("WORKING", statuses)
        self.assertIn("COMPLETED", statuses)


if __name__ == "__main__":
    unittest.main()
