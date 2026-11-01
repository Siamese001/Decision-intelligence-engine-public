"""Unit tests for Query Core modularization and decoupled stages (Wave 6)."""
from __future__ import annotations

import unittest

from dual_pipeline.l1_deterministic_checker import run_l1_deterministic_checks
from dual_pipeline.l1_feedback_loop import L1FeedbackStateMachine, LoopState
from dual_pipeline.l1_review_contracts import (
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.l1_semantic_evaluator import L1SemanticSufficiencyEvaluator
from dual_pipeline.query_core.constants import (
    GRAPH_HOPS_BY_QUERY_TYPE,
    QUERY_STAGES,
)
from dual_pipeline.sealed_l2 import seal_l2_output


class QueryCoreModularizationTests(unittest.TestCase):
    """Verifies that query stages are decoupled into dedicated modular components."""

    def test_constants_and_contracts_isolated(self) -> None:
        self.assertIn("L1", QUERY_STAGES)
        self.assertIn("L2", QUERY_STAGES)
        self.assertEqual(3, GRAPH_HOPS_BY_QUERY_TYPE["relationship_explanation"])

    def test_l1_deterministic_checker_is_pure_and_provider_free(self) -> None:
        packet = {
            "packet_digest": "digest123",
            "approved": [{"record": {"record_id": "rec-01", "title": "PDLC Offering"}}],
            "candidate": [],
            "external_public": [],
        }
        answer = {
            "query_type": "opportunity_nba",
            "opportunities": [
                {
                    "opportunity": "Modernize PDLC",
                    "why_prioritize": "Critical path",
                    "citations": ["rec-01"],
                }
            ],
        }
        plan = {
            "plan_id": "plan-001",
            "query_type": "opportunity_nba",
            "acceptance_criteria": {"min_citations": 1},
        }
        sealed = seal_l2_output(
            run_id="run-001",
            query_id="query-101",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-sol",
            l2_answer=answer,
            l2_provider_call_count=1,
        )
        passed, checks = run_l1_deterministic_checks(
            plan=plan,
            sealed_l2=sealed,
            answer=answer,
            evidence_packet=packet,
            graph_hash_before="hash_a",
            graph_hash_after="hash_a",
        )
        self.assertTrue(passed)
        self.assertTrue(all(c.passed for c in checks))

    def test_l1_semantic_evaluator_isolated_evaluation(self) -> None:
        evaluator = L1SemanticSufficiencyEvaluator(mock_mode=True)
        plan = {
            "plan_id": "plan-mod-1",
            "query_type": "relational_graph",
            "subquestions": ["What is system A?", "How does system A connect to system B?"],
        }
        answer = {
            "query_type": "relational_graph",
            "summary": "System A connects to System B via REST API.",
            "citations": ["rec-01"],
        }
        res = evaluator.evaluate_sufficiency(
            question="How does system A connect to system B?",
            plan=plan,
            answer=answer,
            sealed_l2={},
        )
        self.assertTrue(res.is_sufficient)
        self.assertIsNone(res.replan_directive)

    def test_l1_feedback_state_machine_isolated_transitions(self) -> None:
        sm = L1FeedbackStateMachine(max_iterations=2)
        self.assertEqual(LoopState.IDLE, sm.state)

        sm.start_cycle()
        self.assertEqual(LoopState.PLANNING, sm.state)

        sm.mark_executed()
        self.assertEqual(LoopState.EXECUTING, sm.state)

        sm.start_review()
        self.assertEqual(LoopState.REVIEWING, sm.state)

        contract = L1PostL2ReviewContract.create(
            run_id="run-mod",
            query_id="q-mod",
            plan_id="p-mod",
            sealed_artifact_id="s-mod",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=True,
        )
        next_state, _ = sm.process_review_verdict(contract)
        self.assertEqual(LoopState.SUFFICIENT, next_state)
        self.assertTrue(sm.can_proceed_to_exit_gates())


if __name__ == "__main__":
    unittest.main()
