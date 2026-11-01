"""Acceptance Test Suite for Wave 3: Pipeline C Retrieval Evaluation Suitability.

Validates:
1. Generational VectorCache invalidation under generation_id bump.
2. 500 consecutive query executions within steady-state SLA (<7,500ms).
3. Law 6 policy-before-retrieval quarantine enforcement (is_quarantined = 1 dropped).
4. Law 2 namespace isolation (INTERNAL_ADVISORY strictly isolated from canonical graph).
5. Emits RFC-8259 compliant PipelineCEvalScorecard.
6. Adheres strictly to the 400-line module budget (Invariant 1).
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.pipelines.models import PipelineCEvalScorecard
from dual_pipeline.evals.pipelines.pipeline_c_evaluator import PipelineCEvaluator


class TestPipelineCEvalSuitability(unittest.TestCase):
    """Test suite for specialized Pipeline C adversarial evaluator."""

    def setUp(self) -> None:
        self.evaluator = PipelineCEvaluator()

    def test_vector_cache_invalidation(self) -> None:
        """Evaluator verifies VectorCache purges on generation bump."""
        res = self.evaluator.evaluate_vector_cache_invalidation()
        self.assertTrue(res["passed"])
        self.assertTrue(res["invalidation_verified"])
        self.assertEqual(res["cached_count"], 5)

    def test_steady_state_retrieval_sla(self) -> None:
        """Evaluator confirms 500 consecutive query executions execute within <7,500ms."""
        res = self.evaluator.evaluate_steady_state_retrieval_sla(query_count=500, max_duration_ms=7500.0)
        self.assertTrue(res["passed"])
        self.assertTrue(res["sla_passed"])
        self.assertEqual(res["query_count"], 500)
        self.assertLess(res["total_duration_ms"], 7500.0)
        self.assertLess(res["avg_query_ms"], 15.0)

    def test_law6_quarantine_enforcement(self) -> None:
        """Evaluator confirms quarantined nodes are excluded from public search."""
        res = self.evaluator.evaluate_law6_quarantine_enforcement()
        self.assertTrue(res["passed"])
        self.assertTrue(res["quarantine_enforced"])
        self.assertEqual(res["public_results_count"], 1)

    def test_law2_namespace_isolation(self) -> None:
        """Evaluator confirms advisory nodes are segregated from canonical graph."""
        res = self.evaluator.evaluate_law2_namespace_isolation()
        self.assertTrue(res["passed"])
        self.assertTrue(res["namespace_isolated"])
        self.assertEqual(res["canonical_node_count"], 1)

    def test_full_pipeline_c_evaluation_run(self) -> None:
        """Evaluator produces a passed, complete PipelineCEvalScorecard."""
        scorecard: PipelineCEvalScorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertTrue(scorecard.vector_cache_invalidation_verified)
        self.assertTrue(scorecard.latency_sla_passed)
        self.assertTrue(scorecard.law6_quarantine_enforced)
        self.assertTrue(scorecard.law2_namespace_isolation_enforced)
        self.assertIn("Pipeline C Evaluation: PASS", scorecard.summary)
        d = scorecard.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["passed"], True)


if __name__ == "__main__":
    unittest.main()
