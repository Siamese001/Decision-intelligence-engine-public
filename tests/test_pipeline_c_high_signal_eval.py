"""High-Signal Test Suite for Wave 4: Pipeline C Vector Cache, Retrieval SLA & Law 6.

Governed under AGENTS.md, .agents/rules/runtime-evidence.md, and
governance/decision_intelligence/PIPELINES_AD_HIGH_SIGNAL_METRIC_SPEC_V1.md.

Validates:
1. Metric C.1: Generational VectorCache Invalidation Precision (I_cache == True).
2. Metric C.2: Steady-State Hybrid Retrieval Latency SLA (T_retrieval <= 7500.0ms across 500 queries).
3. Metric C.3: Law 6 Quarantine Policy Enforcement (P_law6 == True, zero leakage of quarantined nodes).
4. Law 2: Namespace Isolation between CANONICAL and INTERNAL_ADVISORY enclaves.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.evals.pipelines.models import PipelineCEvalScorecard
from dual_pipeline.evals.pipelines.pipeline_c_evaluator import PipelineCEvaluator
from dual_pipeline.index_core.vector_cache import VectorCache


class TestPipelineCHighSignalEval(unittest.TestCase):
    """High-signal adversarial evaluation tests for Pipeline C."""

    def setUp(self) -> None:
        self.vector_cache = VectorCache()
        self.evaluator = PipelineCEvaluator(vector_cache=self.vector_cache)

    def test_generational_vector_cache_invalidation(self) -> None:
        """Verify VectorCache invalidates on generation bump and reuses cache on same generation."""
        res = self.evaluator.evaluate_vector_cache_invalidation()
        self.assertTrue(res["passed"])
        self.assertTrue(res["invalidation_verified"])
        self.assertEqual(res["cached_count"], 5)
        self.assertEqual(res["generation_transition"], "gen_001 -> gen_002")

    def test_steady_state_retrieval_sla_with_database_sidecar(self) -> None:
        """Verify 500 queries against SQLite sidecar loaded via VectorCache execute under 7500ms."""
        res = self.evaluator.evaluate_steady_state_retrieval_sla(query_count=500)
        self.assertTrue(res["passed"])
        self.assertTrue(res["sla_passed"])
        self.assertEqual(res["query_count"], 500)
        self.assertLess(res["total_duration_ms"], 7500.0)
        self.assertLess(res["avg_query_ms"], 15.0)
        self.assertEqual(res["vector_dimension"], 1024)
        self.assertEqual(res["cached_records"], 100)

    def test_law6_quarantine_enforcement(self) -> None:
        """Verify quarantined entities (is_quarantined = 1) are never returned in public search."""
        res = self.evaluator.evaluate_law6_quarantine_enforcement()
        self.assertTrue(res["passed"])
        self.assertTrue(res["quarantine_enforced"])
        self.assertEqual(res["public_results_count"], 1)

    def test_law2_namespace_isolation(self) -> None:
        """Verify INTERNAL_ADVISORY entities are segregated from CANONICAL graph nodes."""
        res = self.evaluator.evaluate_law2_namespace_isolation()
        self.assertTrue(res["passed"])
        self.assertTrue(res["namespace_isolated"])
        self.assertEqual(res["canonical_node_count"], 1)

    def test_pipeline_c_evaluator_scorecard_rfc8259_compliance(self) -> None:
        """Verify PipelineCEvalScorecard compiles into strict RFC-8259 compliant JSON."""
        scorecard: PipelineCEvalScorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertTrue(scorecard.vector_cache_invalidation_verified)
        self.assertTrue(scorecard.latency_sla_passed)
        self.assertLess(scorecard.steady_state_latency_ms, 7500.0)
        self.assertEqual(scorecard.queries_evaluated, 500)
        self.assertTrue(scorecard.law6_quarantine_enforced)
        self.assertTrue(scorecard.law2_namespace_isolation_enforced)

        # Strict RFC-8259 JSON serialization
        payload = scorecard.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertIsInstance(serialized, str)
        self.assertIn("Pipeline C Evaluation: PASS", serialized)


if __name__ == "__main__":
    unittest.main()
