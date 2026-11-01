"""Unit tests for safety policies and lifecycle hooks in Antigravity SDK.

Validates:
- NeeSecurityPolicy accurately routes tool names to APPROVE, ASK_USER, or DENY
- Built policies conform to Policy interface
- NeeLifecycleHooks intercept pre-tool, post-tool, and stop events correctly
"""
from __future__ import annotations

import asyncio
import unittest

from google.antigravity import types
from google.antigravity.hooks.policy import Decision
from google.antigravity.types import StopArgs, StopDecision

from dual_pipeline.agent_sdk import (
    HITL_REQUIRED_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    STRICTLY_DENIED_TOOLS,
    NeeLifecycleHooks,
    NeeSecurityPolicy,
)


class TestAgentPolicies(unittest.TestCase):
    """Unit test suite for safety policies and lifecycle hooks."""

    def test_static_policy_decisions_for_categories(self):
        """Validates static policy routing across the three tool categories."""
        # 1. Read-only tools must be approved autonomously
        for safe_tool in READ_ONLY_SAFE_TOOLS:
            decision = NeeSecurityPolicy.evaluate_static_decision(safe_tool)
            self.assertEqual(
                decision,
                Decision.APPROVE,
                f"Safe tool '{safe_tool}' was not approved autonomously.",
            )

        # 2. Expensive / state-mutating tools must ask operator
        for hitl_tool in HITL_REQUIRED_TOOLS:
            decision = NeeSecurityPolicy.evaluate_static_decision(hitl_tool)
            self.assertEqual(
                decision,
                Decision.ASK_USER,
                f"Expensive/mutating tool '{hitl_tool}' did not require ASK_USER.",
            )

        # 3. Forbidden destructive tools must be hard denied
        for denied_tool in STRICTLY_DENIED_TOOLS:
            decision = NeeSecurityPolicy.evaluate_static_decision(denied_tool)
            self.assertEqual(
                decision,
                Decision.DENY,
                f"Destructive tool '{denied_tool}' was not hard-denied.",
            )

    def test_build_governed_policies_returns_policy_list(self):
        """Constructs prioritized policy list containing denies, asks, and allows."""
        policies = NeeSecurityPolicy.build_governed_policies()
        self.assertGreater(len(policies), 10)

    def test_lifecycle_hooks_pre_tool_call_decide(self):
        """Pre-tool hook allows safe tools and denies forbidden tools."""
        hooks = NeeLifecycleHooks(run_id="test_run_001")
        pre_tool_fn = hooks.build_pre_tool_call_decide()

        # Mock safe tool call
        safe_call = types.ToolCall(name="query_knowledge_graph", args={"query_text": "nexus"})
        res_safe = asyncio.run(pre_tool_fn(safe_call))
        self.assertTrue(res_safe.allow)

        # Mock denied tool call
        denied_call = types.ToolCall(name="run_command", args={"command": "rm -rf /"})
        res_denied = asyncio.run(pre_tool_fn(denied_call))
        self.assertFalse(res_denied.allow)

        # Check telemetry history recorded
        self.assertEqual(len(hooks.tool_call_history), 2)
        self.assertEqual(hooks.tool_call_history[0]["name"], "query_knowledge_graph")
        self.assertEqual(hooks.tool_call_history[1]["name"], "run_command")

    def test_lifecycle_hooks_stop_hook_validation(self):
        """Stop hook requires execution evidence before allowing agent termination."""
        hooks = NeeLifecycleHooks(run_id="test_run_002")
        stop_fn = hooks.build_stop_hook(enforce_receipt_verification=True)

        # 1. Idle without any tool calls returns CONTINUE
        args_empty = StopArgs(response_text="Done without doing anything.", continuation_count=0)
        res_empty = asyncio.run(stop_fn(args_empty))
        self.assertEqual(res_empty.decision, StopDecision.CONTINUE)
        self.assertIn("without executing required domain tools", res_empty.reason)

        # 2. Simulate tool execution
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})
        res_with_tools = asyncio.run(stop_fn(args_empty))
        self.assertEqual(res_with_tools.decision, StopDecision.ALLOW_STOP)


if __name__ == "__main__":
    unittest.main()
