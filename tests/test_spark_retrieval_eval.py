"""Unit tests for SparkRetrievalReleaseEvaluator.

Tests partition query scoring, MRR/NDCG metric computation, and 11-gate audit logic.
"""
from __future__ import annotations

import unittest

from dual_pipeline.spark.retrieval_eval_spark import (
    RETRIEVAL_11_GATES,
    SparkRetrievalEvalResult,
    SparkRetrievalReleaseEvaluator,
)


class TestSparkRetrievalEvalUnit(unittest.TestCase):
    """Unit tests for distributed 11-gate retrieval release evaluator."""

    def setUp(self):
        self.evaluator = SparkRetrievalReleaseEvaluator(mock_mode=True)

    def test_score_partition_queries(self):
        """Validates partition query score accumulation."""
        queries = [
            {"golden_rank": 1, "latency_ms": 100.0},
            {"golden_rank": 2, "latency_ms": 150.0},
        ]
        res = self.evaluator.score_partition_queries(queries, mock_mode=True)

        self.assertEqual(res["query_count"], 2)
        # MRR sum: 1.0 + 0.5 = 1.5
        self.assertEqual(res["mrr_sum"], 1.5)
        self.assertEqual(res["latency_sum_ms"], 250.0)

    def test_evaluate_queries_distributed_empty_fails_closed(self):
        """Verifies empty queries input fails closed with all gates False."""
        res = self.evaluator.evaluate_queries_distributed(queries=[], eval_id="empty-eval")

        self.assertIsInstance(res, SparkRetrievalEvalResult)
        self.assertFalse(res.is_passing)
        self.assertEqual(res.passed_gate_count, 0)
        self.assertEqual(res.total_gate_count, len(RETRIEVAL_11_GATES))

    def test_evaluate_queries_distributed_passing(self):
        """Verifies high-quality query set passes all 11 gates."""
        queries = [
            {"golden_rank": 1, "latency_ms": 80.0},
            {"golden_rank": 1, "latency_ms": 90.0},
            {"golden_rank": 2, "latency_ms": 110.0},
            {"golden_rank": 1, "latency_ms": 95.0},
        ]
        res = self.evaluator.evaluate_queries_distributed(queries=queries, eval_id="passing-eval")

        self.assertTrue(res.is_passing)
        self.assertEqual(res.passed_gate_count, len(RETRIEVAL_11_GATES))
        self.assertGreaterEqual(res.metrics["mean_reciprocal_rank"], 0.70)
        self.assertGreater(res.runtime_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
