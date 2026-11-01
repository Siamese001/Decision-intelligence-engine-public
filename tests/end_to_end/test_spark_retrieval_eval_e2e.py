"""End-to-End test suite for Spark-distributed 11-gate retrieval release evaluation.

Exercises full lifecycle:
1. Multi-partition benchmark QRELS query distribution
2. Parallel partition scoring and metric reduction
3. 11-gate non-compensating verification
4. Receipt generation and JSON disk persistence
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.spark.jobs.run_distributed_eval import execute_evaluation_job
from dual_pipeline.spark.retrieval_eval_spark import (
    RETRIEVAL_11_GATES,
    SparkRetrievalReleaseEvaluator,
)


class TestSparkRetrievalEvalE2E(unittest.TestCase):
    """End-to-End test suite for distributed 11-gate retrieval release evaluator."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_eval_e2e_")
        self.evaluator = SparkRetrievalReleaseEvaluator(mock_mode=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_11_gate_retrieval_eval_lifecycle(self):
        """Exercises distributed 11-gate evaluation across 4 partitions."""
        queries = [
            {"query_id": f"q_{i}", "golden_rank": 1 if i % 3 != 0 else 2, "latency_ms": 115.0}
            for i in range(16)
        ]

        res = self.evaluator.evaluate_queries_distributed(
            queries=queries,
            eval_id="e2e-11gate-eval-001",
            num_partitions=4,
        )

        self.assertTrue(res.is_passing)
        self.assertEqual(res.passed_gate_count, len(RETRIEVAL_11_GATES))
        self.assertEqual(res.evaluated_query_count, 16)
        self.assertIn("mean_reciprocal_rank", res.metrics)
        self.assertIn("ndcg_at_10", res.metrics)
        self.assertIn("mean_latency_ms", res.metrics)
        self.assertLess(res.metrics["mean_latency_ms"], 7500.0)

    def test_distributed_eval_job_receipt_generation(self):
        """Verifies execute_evaluation_job runs and writes valid sealed JSON receipt."""
        receipt_path = Path(self.temp_dir) / "retrieval_eval_receipt.json"
        exit_code = execute_evaluation_job(
            queries_table="graph_db.qrels_queries",
            output_receipt_path=str(receipt_path),
            eval_id="job-receipt-eval-001",
            num_partitions=2,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(receipt_path.exists())

        receipt_data = json.loads(receipt_path.read_text())
        self.assertTrue(receipt_data["is_passing"])
        self.assertEqual(receipt_data["passed_gate_count"], len(RETRIEVAL_11_GATES))
        self.assertEqual(receipt_data["total_gate_count"], len(RETRIEVAL_11_GATES))


if __name__ == "__main__":
    unittest.main()
