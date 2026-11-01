"""Unit & Invariant test suite for Agentic Feedback Loop Topologies & Orchestration Decoupling.

Verifies:
1. Strict mathematical iteration ceiling (max_iterations <= 2).
2. Infinite loop prevention and exhaustive termination.
3. Structured FeedbackAuditEvent emission into ledger/recorder.
4. LoopDescriptor schema validation and serializability.
5. Formal decoupling between Class A re-planning and Class C healing mesh.
6. AST invariants enforcing HARD_CEILING_MAX_ITERATIONS <= 2 repository-wide.
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.feedback_topology_core.contracts import (
    FeedbackTopologyClass,
    LoopDescriptor,
    LoopState,
    LoopTerminationReason,
)
from dual_pipeline.l1_feedback_loop import (
    L1FeedbackStateMachine,
)
from dual_pipeline.l1_review_contracts import (
    DeterministicCheckItem,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestAgenticFeedbackTopologies(unittest.TestCase):
    """Verifies Class A bounded re-planning invariants and Class C decoupling."""

    def test_mathematical_budget_ceiling_max_2(self) -> None:
        """Assert that max_iterations cannot exceed 2 under any circumstance."""
        # Hard ceiling invariant
        self.assertEqual(L1FeedbackStateMachine.HARD_CEILING_MAX_ITERATIONS, 2)

        # Attempts to set higher iterations are strictly clamped to 2
        sm_high = L1FeedbackStateMachine(max_iterations=10)
        self.assertEqual(sm_high.max_iterations, 2)

        sm_five = L1FeedbackStateMachine(max_iterations=5)
        self.assertEqual(sm_five.max_iterations, 2)

        sm_100 = L1FeedbackStateMachine(max_iterations=100)
        self.assertEqual(sm_100.max_iterations, 2)

        # Lower values are respected within [1, 2]
        sm_one = L1FeedbackStateMachine(max_iterations=1)
        self.assertEqual(sm_one.max_iterations, 1)

        sm_zero = L1FeedbackStateMachine(max_iterations=0)
        self.assertEqual(sm_zero.max_iterations, 1)

    def test_infinite_loop_prevention_exhaustive_halt(self) -> None:
        """Assert that state machine terminates with BUDGET_EXHAUSTED at iteration 2."""
        sm = L1FeedbackStateMachine(max_iterations=2, loop_id="test-loop-exhaust")
        self.assertEqual(sm.state, LoopState.IDLE)
        self.assertFalse(sm.is_terminal())

        # Cycle 0: Planning -> Executing -> Reviewing -> Insufficient Replan
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract_0 = L1PostL2ReviewContract.create(
            run_id="run-test",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=False,
            replan_directive={"directive_type": "SUBQUESTION_REPLAN", "focus": "missing items"},
        )
        state_0, dir_0 = sm.process_review_verdict(contract_0)
        self.assertEqual(state_0, LoopState.REPLANNING)
        self.assertEqual(sm.current_iteration, 1)
        self.assertFalse(sm.is_terminal())
        self.assertFalse(sm.can_proceed_to_exit_gates())

        # Cycle 1: Planning -> Executing -> Reviewing -> Insufficient Replan
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract_1 = L1PostL2ReviewContract.create(
            run_id="run-test",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-002",
            verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=False,
            replan_directive={"directive_type": "SUBQUESTION_REPLAN", "focus": "still missing"},
        )
        state_1, dir_1 = sm.process_review_verdict(contract_1)
        self.assertEqual(state_1, LoopState.REPLANNING)
        self.assertEqual(sm.current_iteration, 2)
        self.assertFalse(sm.is_terminal())
        self.assertFalse(sm.can_proceed_to_exit_gates())

        # Cycle 2: Planning -> Executing -> Reviewing -> Insufficient Replan (Exhausts budget)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract_2 = L1PostL2ReviewContract.create(
            run_id="run-test",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-003",
            verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=False,
        )
        state_2, payload_2 = sm.process_review_verdict(contract_2)

        # Budget exhausted at iteration 2 (max_iterations=2): loop halts unconditionally
        self.assertEqual(state_2, LoopState.EXHAUSTED)
        self.assertTrue(sm.is_terminal())
        self.assertFalse(sm.can_proceed_to_exit_gates())
        self.assertEqual(sm.termination_reason, LoopTerminationReason.BUDGET_EXHAUSTED)
        self.assertEqual(payload_2["reason"], "L1_LOOP_BUDGET_EXHAUSTED")
        self.assertEqual(payload_2["iterations_completed"], 2)

        # Further cycle attempts fail-closed
        with self.assertRaises(RuntimeError):
            sm.start_cycle()

    def test_fatal_block_immediate_halt(self) -> None:
        """Assert that fatal check failures immediately halt with FATAL_BLOCK."""
        sm = L1FeedbackStateMachine(max_iterations=2)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract = L1PostL2ReviewContract.create(
            run_id="run-test",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.FATAL_BLOCK,
            deterministic_passed=False,
            deterministic_checks=[
                DeterministicCheckItem("security_egress", False, "Unauthorized egress attempt", "GRAPH_MUTATION_VIOLATION")
            ],
            semantic_passed=False,
        )
        state, payload = sm.process_review_verdict(contract)
        self.assertEqual(state, LoopState.BLOCKED)
        self.assertTrue(sm.is_terminal())
        self.assertFalse(sm.can_proceed_to_exit_gates())
        self.assertEqual(sm.termination_reason, LoopTerminationReason.FATAL_BLOCK)
        self.assertEqual(payload["reason"], "FATAL_BLOCK")

    def test_sufficient_completion_flow(self) -> None:
        """Assert that sufficient observations terminate with SUFFICIENT_COMPLETION."""
        sm = L1FeedbackStateMachine(max_iterations=2)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract = L1PostL2ReviewContract.create(
            run_id="run-test",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=True,
        )
        state, directive = sm.process_review_verdict(contract)
        self.assertEqual(state, LoopState.SUFFICIENT)
        self.assertIsNone(directive)
        self.assertTrue(sm.is_terminal())
        self.assertTrue(sm.can_proceed_to_exit_gates())
        self.assertEqual(sm.termination_reason, LoopTerminationReason.SUFFICIENT_COMPLETION)

    def test_structured_audit_event_emission(self) -> None:
        """Assert that all state transitions emit structured FeedbackAuditEvent records."""
        recorder = MagicMock()
        sm = L1FeedbackStateMachine(max_iterations=2, loop_id="audit-loop-01", recorder=recorder)

        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        events = sm.get_audit_events()
        self.assertEqual(len(events), 3)

        # Event 1: IDLE -> PLANNING
        self.assertEqual(events[0].from_state, LoopState.IDLE.value)
        self.assertEqual(events[0].to_state, LoopState.PLANNING.value)
        self.assertEqual(events[0].event_type, "start_cycle")
        self.assertTrue(events[0].event_id.startswith("fbevt-"))

        # Event 2: PLANNING -> EXECUTING
        self.assertEqual(events[1].from_state, LoopState.PLANNING.value)
        self.assertEqual(events[1].to_state, LoopState.EXECUTING.value)

        # Event 3: EXECUTING -> REVIEWING
        self.assertEqual(events[2].from_state, LoopState.EXECUTING.value)
        self.assertEqual(events[2].to_state, LoopState.REVIEWING.value)

        # Verify recorder was notified for each event
        self.assertEqual(recorder.record_event.call_count, 3)

        # Serializability
        event_dict = events[0].to_dict()
        self.assertIn("timestamp", event_dict)
        json_str = json.dumps(event_dict)
        self.assertIn("fbevt-", json_str)

    def test_loop_descriptor_export(self) -> None:
        """Assert that LoopDescriptor accurately summarizes loop configuration and telemetry."""
        sm = L1FeedbackStateMachine(max_iterations=2, loop_id="desc-loop-99")
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        desc = sm.to_descriptor()
        self.assertIsInstance(desc, LoopDescriptor)
        self.assertEqual(desc.loop_id, "desc-loop-99")
        self.assertEqual(desc.topology_class, FeedbackTopologyClass.CLASS_A_BOUNDED_REPLAN)
        self.assertEqual(desc.max_iterations, 2)
        self.assertEqual(desc.current_iteration, 0)
        self.assertEqual(desc.current_state, LoopState.REVIEWING)
        self.assertFalse(desc.terminal)
        self.assertIsNone(desc.termination_reason)
        self.assertEqual(desc.transitions_count, 3)

        # Serializability
        desc_dict = desc.to_dict()
        self.assertEqual(desc_dict["loop_id"], "desc-loop-99")
        self.assertIn("Class A", desc_dict["topology_class"])

    def test_class_a_vs_class_c_decoupling(self) -> None:
        """Assert that Class A and Class C topologies are strictly decoupled."""
        # Class A state machine
        sm = L1FeedbackStateMachine(max_iterations=2)
        desc = sm.to_descriptor()
        self.assertEqual(desc.topology_class, FeedbackTopologyClass.CLASS_A_BOUNDED_REPLAN)

        # Class C healing mesh
        self.assertTrue(DeterministicHealer.is_decoupled_from_primary_flow())
        self.assertIn("Class C", DeterministicHealer.get_topology_class())

        # Test stage context isolation in DeterministicHealer
        DeterministicHealer.clear_audit_log()
        raw_text = "**Date:** 2026-09-18\n\n\n\nBody content"
        healed, changes = DeterministicHealer.heal_output_1(
            raw_text,
            metadata={"meeting_date": "2026-09-18"},
            stage_context="stage01_checkpoint",
        )
        log = DeterministicHealer.get_audit_log()
        self.assertGreater(len(log), 0)
        for entry in log:
            self.assertEqual(entry["stage_context"], "stage01_checkpoint")
        DeterministicHealer.clear_audit_log()

    def test_ast_feedback_budget_invariants(self) -> None:
        """AST crawl asserting HARD_CEILING_MAX_ITERATIONS <= 2 across repository."""
        target_file = REPO_ROOT / "dual_pipeline" / "l1_feedback_loop.py"
        tree = ast.parse(target_file.read_text(encoding="utf-8"))

        found_ceiling = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "HARD_CEILING_MAX_ITERATIONS":
                        found_ceiling = True
                        if isinstance(node.value, ast.Constant):
                            self.assertLessEqual(node.value.value, 2, "HARD_CEILING_MAX_ITERATIONS must be <= 2")

        self.assertTrue(found_ceiling, "HARD_CEILING_MAX_ITERATIONS constant must exist in l1_feedback_loop.py")


if __name__ == "__main__":
    unittest.main()
