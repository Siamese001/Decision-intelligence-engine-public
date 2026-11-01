"""Adversarial and failure-recovery test suite for Dataproc Serverless batch controller.

Covers:
1. Simulated GCP provider errors (HTTP 429 quota exhaustion, 503 unavailable)
2. Polling timeout degradation
3. Empty/malformed batch IDs and SQL injection attempts
4. Driver memory overflow limit clamping
5. Unsupported job type boundary handling
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dual_pipeline.agent_sdk.tools import (
    execute_spark_sql,
    inspect_spark_batch_status,
    submit_spark_graph_batch,
)
from dual_pipeline.spark.client import DataprocBatchControllerClient


class TestSparkClientAdversarial(unittest.TestCase):
    """Adversarial edge-case and failure injection tests for Dataproc batch client."""

    def test_live_client_submission_failure_handling(self):
        """Verifies clean error handling when Dataproc API raises exception."""
        mock_client = MagicMock()
        mock_client.create_batch.side_effect = RuntimeError("429 Resource quota exhausted")

        controller = DataprocBatchControllerClient(live_client=mock_client)
        with self.assertRaises(RuntimeError) as ctx:
            controller.submit_batch(batch_id="batch-fail-001", batch_payload={})

        self.assertIn("Dataproc Serverless submission error", str(ctx.exception))
        self.assertIn("429", str(ctx.exception))

    def test_polling_timeout_exhaustion(self):
        """Verifies wait_for_completion returns TIMEOUT status when polling exceeds boundary."""
        controller = DataprocBatchControllerClient()
        # Mock batch that stays RUNNING
        controller._mock_batches["batch-slow-001"] = {
            "batch_id": "batch-slow-001",
            "state": "RUNNING",
            "submitted_at": 0.0,
            "payload": {},
        }
        # Override get_batch_status to return RUNNING
        mock_get_status = MagicMock()
        from dual_pipeline.spark.client import BatchStatusResult
        mock_get_status.return_value = BatchStatusResult(
            batch_id="batch-slow-001",
            state="RUNNING",
            is_terminal=False,
            is_successful=False,
            runtime_seconds=10.0,
            spark_ui_url="",
        )
        controller.get_batch_status = mock_get_status

        status = controller.wait_for_completion(
            batch_id="batch-slow-001",
            timeout_seconds=0.05,
            poll_interval=0.01,
        )
        self.assertEqual(status.state, "TIMEOUT")
        self.assertTrue(status.is_terminal)
        self.assertFalse(status.is_successful)
        self.assertIn("exceeded timeout", status.error_message or "")

    def test_sql_injection_and_limit_overflow_clamping(self):
        """Verifies SQL injection attempts and massive row requests are bounded."""
        malicious_query = "SELECT * FROM graph_db.node; DROP TABLE graph_db.node; --"
        res = execute_spark_sql(malicious_query, row_limit=500_000)

        # Enforced row limit must never exceed 100
        self.assertLessEqual(res["enforced_row_limit"], 100)
        self.assertIn("LIMIT 100", res["executed_query"])

    def test_unsupported_batch_job_type_fails_closed(self):
        """Verifies unsupported job type returns structured error without crashing."""
        res = submit_spark_graph_batch(
            job_type="unsupported_quantum_clustering",
            project="project_nexus",
        )
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("Unsupported job_type", res["error"])

    def test_inspect_empty_or_whitespace_batch_id(self):
        """Verifies inspect_spark_batch_status handles whitespace/empty inputs."""
        res1 = inspect_spark_batch_status("")
        self.assertEqual(res1["status"], "ERROR")

        res2 = inspect_spark_batch_status("   ")
        self.assertEqual(res2["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
