"""Adversarial and non-compensating gate test suite for SparkRetrievalReleaseEvaluator.

Covers:
1. Latency threshold breach (>7,500ms floor failure)
2. Quality score drift (low MRR / NDCG failing core_thresholds)
3. Insufficient query coverage (<3 queries failing coverage_complete)
4. Non-compensating gate enforcement (single gate failure blocks release)
"""
from __future__ import annotations

import unittest

from dual_pipeline.spark.retrieval_eval_spark import SparkRetrievalReleaseEvaluator


class TestSparkRetrievalEvalAdversarial(unittest.TestCase):
    """Adversarial testing for distributed retrieval release evaluation."""

    def setUp(self):
        self.evaluator = SparkRetrievalReleaseEvaluator(mock_mode=True)

    def test_latency_floor_breach_fails_non_compensating(self):
        """Verifies latency exceeding 7,500ms threshold fails latency_threshold gate and entire eval."""
        queries = [
            {"query_id": "q1", "golden_rank": 1, "latency_ms": 8200.0},
            {"query_id": "q2", "golden_rank": 1, "latency_ms": 9500.0},
            {"query_id": "q3", "golden_rank": 1, "latency_ms": 7800.0},
        ]
        res = self.evaluator.evaluate_queries_distributed(queries=queries, eval_id="adv-latency-breach")

        self.assertFalse(res.is_passing)
        self.assertFalse(res.gate_results["latency_threshold"])
        # Even if quality is 1.0, single gate breach fails evaluation
        self.assertEqual(res.metrics["mean_reciprocal_rank"], 1.0)

    def test_mrr_quality_drift_fails_core_thresholds(self):
        """Verifies poor ranking causes core_thresholds and critical_cases gates to fail."""
        queries = [
            {"query_id": f"q_{i}", "golden_rank": 8, "latency_ms": 100.0}
            for i in range(5)
        ]
        res = self.evaluator.evaluate_queries_distributed(queries=queries, eval_id="adv-mrr-drift")

        self.assertFalse(res.is_passing)
        self.assertFalse(res.gate_results["core_thresholds"])
        self.assertFalse(res.gate_results["critical_cases"])

    def test_insufficient_query_coverage_fails(self):
        """Verifies fewer than 3 queries fails coverage_complete gate."""
        queries = [
            {"query_id": "single_q", "golden_rank": 1, "latency_ms": 100.0},
        ]
        res = self.evaluator.evaluate_queries_distributed(queries=queries, eval_id="adv-thin-coverage")

        self.assertFalse(res.is_passing)
        self.assertFalse(res.gate_results["coverage_complete"])


if __name__ == "__main__":
    unittest.main()
