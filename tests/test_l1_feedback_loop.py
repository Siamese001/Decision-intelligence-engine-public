"""Unit tests for L1 Semantic Sufficiency Evaluator & Feedback State Machine."""
from __future__ import annotations

import unittest

from dual_pipeline.l1_feedback_loop import (
    L1FeedbackStateMachine,
    LoopState,
)
from dual_pipeline.l1_review_contracts import (
    DeterministicCheckItem,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.l1_semantic_evaluator import (
    L1SemanticSufficiencyEvaluator,
)


class TestL1SemanticEvaluator(unittest.TestCase):
    """Verifies semantic sufficiency evaluation logic and re-plan generation."""

    def setUp(self) -> None:
        self.evaluator = L1SemanticSufficiencyEvaluator(mock_mode=True)
        self.plan = {
            "plan_id": "plan-001",
            "query_type": "opportunity_nba",
            "subquestions": [
                "Which PDLC initiatives and capabilities exist?",
                "Which documented pain points and risks exist?",
                "Which opportunities address which pain points?",
            ],
        }

    def test_complete_answer_is_sufficient(self) -> None:
        answer = {
            "query_type": "opportunity_nba",
            "opportunities": [
                {
                    "opportunity": "Modernize PDLC architecture and initiatives",
                    "why_prioritize": "Directly resolves high documented pain points and delivery risks",
                    "citations": ["rec-001"],
                }
            ],
        }
        res = self.evaluator.evaluate_sufficiency(
            question="What PDLC opportunities exist?",
            plan=self.plan,
            answer=answer,
            sealed_l2={"l2_disposition": "primary"},
        )
        self.assertTrue(res.is_sufficient)
        self.assertGreaterEqual(res.subgoal_coverage_score, 0.75)
        self.assertIsNone(res.replan_directive)

    def test_partial_answer_generates_replan_directive(self) -> None:
        # Answer only mentions pain points, misses capabilities and opportunities addressing them
        answer = {
            "query_type": "opportunity_nba",
            "findings": "We only have pain points in legacy billing.",
        }
        res = self.evaluator.evaluate_sufficiency(
            question="What PDLC opportunities exist?",
            plan=self.plan,
            answer=answer,
            sealed_l2={"l2_disposition": "primary"},
        )
        self.assertFalse(res.is_sufficient)
        self.assertLess(res.subgoal_coverage_score, 0.75)
        self.assertIsNotNone(res.replan_directive)
        self.assertIn("FOCUSED_SUBQUESTION_REPLAN", res.replan_directive["directive_type"])
        self.assertGreater(len(res.unanswered_subquestions), 0)

    def test_null_answer_is_insufficient(self) -> None:
        res = self.evaluator.evaluate_sufficiency(
            question="What opportunities exist?",
            plan=self.plan,
            answer=None,
            sealed_l2={"l2_disposition": "failed"},
        )
        self.assertFalse(res.is_sufficient)
        self.assertEqual(res.subgoal_coverage_score, 0.0)
        self.assertEqual(res.replan_directive["directive_type"], "PRIMARY_EXECUTION_REQUIRED")


class TestL1FeedbackStateMachine(unittest.TestCase):
    """Verifies state machine transitions, bounded loops, and re-planning."""

    def test_single_pass_success_flow(self) -> None:
        sm = L1FeedbackStateMachine(max_iterations=2)
        self.assertEqual(sm.state, LoopState.IDLE)

        iter_num = sm.start_cycle()
        self.assertEqual(iter_num, 0)
        self.assertEqual(sm.state, LoopState.PLANNING)

        sm.mark_executed()
        self.assertEqual(sm.state, LoopState.EXECUTING)

        sm.start_review()
        self.assertEqual(sm.state, LoopState.REVIEWING)

        contract = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=True,
        )
        next_state, directive = sm.process_review_verdict(contract)
        self.assertEqual(next_state, LoopState.SUFFICIENT)
        self.assertIsNone(directive)
        self.assertTrue(sm.is_terminal())
        self.assertTrue(sm.can_proceed_to_exit_gates())

    def test_replan_and_recovery_flow(self) -> None:
        sm = L1FeedbackStateMachine(max_iterations=2)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        # Cycle 0: Insufficient
        replan_dir = {
            "directive_type": "FOCUSED_SUBQUESTION_REPLAN",
            "unanswered_subquestions": ["Which capabilities exist?"],
            "suggested_query_expansion": ["Evidence for capabilities"],
        }
        contract_0 = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=False,
            replan_directive=replan_dir,
        )
        state_0, dir_0 = sm.process_review_verdict(contract_0)
        self.assertEqual(state_0, LoopState.REPLANNING)
        self.assertEqual(sm.current_iteration, 1)
        self.assertEqual(dir_0, replan_dir)
        self.assertFalse(sm.can_proceed_to_exit_gates())

        # Cycle 1: Adapted planning and successful execution
        iter_1 = sm.start_cycle()
        self.assertEqual(iter_1, 1)
        self.assertEqual(sm.state, LoopState.PLANNING)
        sm.mark_executed()
        sm.start_review()

        contract_1 = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-002",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=True,
        )
        state_1, dir_1 = sm.process_review_verdict(contract_1)
        self.assertEqual(state_1, LoopState.SUFFICIENT)
        self.assertTrue(sm.can_proceed_to_exit_gates())

    def test_loop_exhaustion_at_max_iterations(self) -> None:
        sm = L1FeedbackStateMachine(max_iterations=1)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        # Cycle 0 fails
        contract_0 = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=False,
        )
        state_0, dir_0 = sm.process_review_verdict(contract_0)
        self.assertEqual(state_0, LoopState.REPLANNING)
        self.assertEqual(sm.current_iteration, 1)

        # Cycle 1 starts
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        # Cycle 1 fails -> budget exhausted
        contract_1 = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-002",
            verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=False,
        )
        state_1, payload_1 = sm.process_review_verdict(contract_1)
        self.assertEqual(state_1, LoopState.EXHAUSTED)
        self.assertTrue(sm.is_terminal())
        self.assertFalse(sm.can_proceed_to_exit_gates())
        self.assertEqual(payload_1["reason"], "L1_LOOP_BUDGET_EXHAUSTED")

    def test_fatal_block_halts_immediately(self) -> None:
        sm = L1FeedbackStateMachine(max_iterations=2)
        sm.start_cycle()
        sm.mark_executed()
        sm.start_review()

        contract = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="q-001",
            plan_id="p-001",
            sealed_artifact_id="s-001",
            verdict=L1ReviewVerdict.FATAL_BLOCK,
            deterministic_passed=False,
            deterministic_checks=[DeterministicCheckItem("graph_immutability", False, "mutated", "GRAPH_MUTATION_VIOLATION")],
            semantic_passed=False,
        )
        state, payload = sm.process_review_verdict(contract)
        self.assertEqual(state, LoopState.BLOCKED)
        self.assertTrue(sm.is_terminal())
        self.assertFalse(sm.can_proceed_to_exit_gates())
        self.assertEqual(payload["reason"], "FATAL_BLOCK")

    def test_apply_replan_to_plan(self) -> None:
        original_plan = {
            "plan_id": "plan-orig",
            "query_type": "opportunity_nba",
            "evidence_requirements": ["Base requirement"],
        }
        directive = {
            "directive_type": "FOCUSED_SUBQUESTION_REPLAN",
            "unanswered_subquestions": ["Subquestion 1"],
            "suggested_query_expansion": ["Expanded requirement A"],
        }
        adapted_plan = L1FeedbackStateMachine.apply_replan_to_plan(original_plan, directive)
        self.assertEqual(adapted_plan["replan_iteration"], 1)
        self.assertEqual(adapted_plan["focus_subquestions"], ["Subquestion 1"])
        self.assertIn("Expanded requirement A", adapted_plan["evidence_requirements"])
        self.assertIn("Base requirement", adapted_plan["evidence_requirements"])
        # Ensure original was not mutated
        self.assertNotIn("replan_iteration", original_plan)


if __name__ == "__main__":
    unittest.main()
