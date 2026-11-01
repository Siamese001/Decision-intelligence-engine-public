#!/usr/bin/env python3
"""Unit tests for agent-subagent delegation contracts, upstream return validation,
and lifecycle tracking under dual_pipeline.unified_agent_architecture.
"""
from __future__ import annotations

import unittest

from dual_pipeline.unified_agent_architecture import (
    ExecutionLifecycleTracker,
    HookTiming,
    validate_delegation_contract,
    validate_hook_definition,
    validate_subagent_return,
)


class OrchestrationDelegationContractTests(unittest.TestCase):
    """Test suite ensuring strict contract enforcement for agent-to-subagent delegation."""

    def setUp(self) -> None:
        self.valid_contract = {
            "task_id": "task-sec-audit-001",
            "parent_task_id": "agent-orchestrator-main",
            "objective": "Perform static AST vulnerability audit on intake modules",
            "scope_in": ["dual_pipeline/intake.py", "dual_pipeline/normalization.py"],
            "scope_out": ["dual_pipeline/query.py", "external_apis"],
            "allowed_tools": ["ast_parser", "view_file"],
            "deliverable": "vulnerability_report.json",
            "definition_of_done": "All AST nodes inspected; report generated with zero high severity findings",
            "budget": {
                "max_tool_calls": 50,
                "timeout_seconds": 120,
            },
            "escalation_rule": "Escalate immediately if private key or credential pattern detected",
        }

    def test_valid_delegation_contract_passes(self) -> None:
        """A complete, bounded delegation contract must pass validation with 0 errors."""
        is_valid, errors = validate_delegation_contract(self.valid_contract)
        self.assertTrue(is_valid, f"Expected valid contract, got errors: {errors}")
        self.assertEqual(errors, [])

    def test_missing_required_contract_field_fails(self) -> None:
        """Omitting any required contract field must fail validation."""
        for field_name in [
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
        ]:
            bad_contract = dict(self.valid_contract)
            del bad_contract[field_name]
            is_valid, errors = validate_delegation_contract(bad_contract)
            self.assertFalse(is_valid)
            self.assertTrue(
                any(f"DELEGATION_CONTRACT_MISSING_FIELD: '{field_name}'" in e for e in errors),
                f"Expected missing field error for {field_name}, got: {errors}",
            )

    def test_unbounded_budget_fails(self) -> None:
        """A budget map without at least one bounded constraint must be rejected."""
        contract = dict(self.valid_contract)
        contract["budget"] = {"unrecognized_cap": 100}
        is_valid, errors = validate_delegation_contract(contract)
        self.assertFalse(is_valid)
        self.assertTrue(any("DELEGATION_CONTRACT_UNBOUNDED_BUDGET" in e for e in errors))

    def test_valid_subagent_return_passes(self) -> None:
        """A valid subagent return payload must pass validation."""
        valid_return = {
            "task_id": "task-sec-audit-001",
            "status": "COMPLETE",
            "deliverable": {"audit_findings": []},
            "evidence_refs": ["urn:sha256:abc123def456"],
            "metrics": {"elapsed_ms": 450, "tool_calls_used": 12},
        }
        is_valid, errors = validate_subagent_return(valid_return)
        self.assertTrue(is_valid, f"Expected valid return, got errors: {errors}")
        self.assertEqual(errors, [])

    def test_invalid_subagent_status_fails(self) -> None:
        """An unrecognized status in subagent return must fail validation."""
        invalid_return = {
            "task_id": "task-sec-audit-001",
            "status": "DONE_SUCCESS",  # Invalid; must be COMPLETE, BLOCKED, ESCALATED, FAILED
            "deliverable": {},
            "evidence_refs": [],
            "metrics": {},
        }
        is_valid, errors = validate_subagent_return(invalid_return)
        self.assertFalse(is_valid)
        self.assertTrue(any("SUBAGENT_RETURN_INVALID_STATUS" in e for e in errors))

    def test_intrusive_hook_fails(self) -> None:
        """A hook that claims to modify core logic must fail non-intrusive validation."""
        hook = {
            "id": "hook-malicious-bypass",
            "event": "pre_tool_call",
            "timing": HookTiming.PRE.value,
            "modifies_core_logic": True,
        }
        is_valid, errors = validate_hook_definition(hook)
        self.assertFalse(is_valid)
        self.assertTrue(any("HOOK_INTRUSIVE_VIOLATION" in e for e in errors))

    def test_7_step_lifecycle_tracker_sequence(self) -> None:
        """ExecutionLifecycleTracker must enforce the sequential 7-step lifecycle."""
        tracker = ExecutionLifecycleTracker()
        steps = [
            "1_goal_and_rule_verification",
            "2_planning_and_skill_selection",
            "3_sub_agent_delegation",
            "4_skill_execution",
            "5_hook_triggers",
            "6_continuous_rule_validation",
            "7_upstream_synthesis",
        ]
        for step in steps:
            tracker.record_step(step)
        self.assertEqual(len(tracker.history), 7)
        is_valid, errors = tracker.validate_lifecycle_progression()
        self.assertTrue(is_valid, f"Expected valid lifecycle progression, got errors: {errors}")


if __name__ == "__main__":
    unittest.main()
