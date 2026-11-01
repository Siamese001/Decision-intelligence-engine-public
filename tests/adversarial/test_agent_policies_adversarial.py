"""Adversarial stress test suite for multi-agent policies and lifecycle hooks.

Covers:
1. Invariant bypass attempts (denied tool execution, synthetic stop hook evasion)
2. Delegation contract forgery (missing fields, rogue tool injections)
3. Recursion ceiling evasion (attempting to turn leaf specialists into subagent spawners)
4. Boundary stress testing across lifecycle hooks
"""
from __future__ import annotations

import asyncio
import unittest

from google.antigravity import types
from google.antigravity.hooks.policy import Decision
from google.antigravity.types import StopArgs, StopDecision

from dual_pipeline.agent_sdk import (
    NeeAgentHierarchy,
    NeeLifecycleHooks,
)
from dual_pipeline.unified_agent_architecture import validate_delegation_contract


class TestAgentPoliciesAdversarial(unittest.TestCase):
    """Adversarial test suite stress testing policies, hooks, and delegation contracts."""

    def test_strictly_denied_tools_interception(self):
        """Invariant bypass attempt: Forbidden tools must be intercepted with allow=False."""
        hooks = NeeLifecycleHooks(run_id="adv_test_run")
        pre_tool_fn = hooks.build_pre_tool_call_decide()

        prohibited_tools = [
            "run_command",
            "drop_graph_database",
            "purge_audit_ledger",
        ]
        for tool_name in prohibited_tools:
            tool_call = types.ToolCall(name=tool_name, args={"cmd": "bypass"})
            res = asyncio.run(pre_tool_fn(tool_call))
            self.assertFalse(res.allow, f"Tool '{tool_name}' was not denied!")
            # Telemetry record must indicate DENY
            last_record = hooks.tool_call_history[-1]
            self.assertEqual(last_record["decision"], Decision.DENY.value)

    def test_synthetic_stop_hook_premature_completion_rejection(self):
        """Invariant bypass: Premature stop without tool execution must be rejected."""
        hooks = NeeLifecycleHooks(run_id="adv_stop_run")
        stop_fn = hooks.build_stop_hook(enforce_receipt_verification=True)

        # Agent did not execute any tools, but attempts to stop
        rogue_stop = StopArgs(response_text="Everything is fine, I did nothing.", continuation_count=0)
        verdict = asyncio.run(stop_fn(rogue_stop))

        self.assertEqual(verdict.decision, StopDecision.CONTINUE)
        self.assertIn("Trajectory reached idle without executing", verdict.reason)

    def test_forged_delegation_contract_rejected(self):
        """Malformed input: Contracts missing mandatory fields fail validation."""
        forged_contracts = [
            # Missing escalation_rule
            {
                "task_id": "forged_01",
                "parent_task_id": "p0",
                "objective": "test",
                "scope_in": ["query_knowledge_graph"],
                "scope_out": [],
                "allowed_tools": ["query_knowledge_graph"],
                "deliverable": "receipt",
                "definition_of_done": "done",
                "budget": {"max_model_calls": 5},
            },
            # Missing budget
            {
                "task_id": "forged_02",
                "parent_task_id": "p0",
                "objective": "test",
                "scope_in": ["query_knowledge_graph"],
                "scope_out": [],
                "allowed_tools": ["query_knowledge_graph"],
                "deliverable": "receipt",
                "definition_of_done": "done",
                "escalation_rule": "escalate",
            },
            # Missing definition_of_done
            {
                "task_id": "forged_03",
                "parent_task_id": "p0",
                "objective": "test",
                "scope_in": ["query_knowledge_graph"],
                "scope_out": [],
                "allowed_tools": ["query_knowledge_graph"],
                "deliverable": "receipt",
                "budget": {"max_model_calls": 5},
                "escalation_rule": "escalate",
            },
        ]

        for contract in forged_contracts:
            is_valid, errors = validate_delegation_contract(contract)
            self.assertFalse(is_valid)
            self.assertGreater(len(errors), 0)

    def test_leaf_specialists_subagent_boundary_invariant(self):
        """Invariant: Tier 1 specialists cannot spawn subagents or exceed recursion depth."""
        configs = NeeAgentHierarchy.build_subagent_configs()
        for cfg in configs:
            self.assertEqual(
                cfg.capabilities.allowed_subagents,
                [],
                f"Specialist '{cfg.name}' cannot have allowed subagents!",
            )


if __name__ == "__main__":
    unittest.main()
