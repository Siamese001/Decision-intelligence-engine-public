"""Unit tests for Dataproc Serverless Batch Controller Client.

Tests batch submission, status polling, timeout handling, and cancellation.
"""
from __future__ import annotations

import unittest

from dual_pipeline.spark.client import BatchStatusResult, DataprocBatchControllerClient
from dual_pipeline.spark.session import ManagedSparkSessionFactory, SparkSessionConfig


class TestSparkClientUnit(unittest.TestCase):
    """Unit tests for DataprocBatchControllerClient."""

    def test_submit_batch_simulation(self):
        """Verifies simulated batch submission in local environment."""
        client = DataprocBatchControllerClient(project_id="test-proj", location="us-central1")
        payload = {"test": "payload"}
        res = client.submit_batch(batch_id="batch-unit-001", batch_payload=payload)

        self.assertEqual(res["status"], "SUBMITTED")
        self.assertEqual(res["batch_id"], "batch-unit-001")
        self.assertTrue(res["simulated"])

    def test_submit_batch_empty_id_rejected(self):
        """Verifies empty batch ID raises ValueError."""
        client = DataprocBatchControllerClient()
        with self.assertRaises(ValueError):
            client.submit_batch(batch_id="", batch_payload={})

    def test_get_batch_status_mock(self):
        """Verifies query of batch status in simulated mode."""
        client = DataprocBatchControllerClient()
        client.submit_batch(batch_id="batch-status-001", batch_payload={})

        status = client.get_batch_status("batch-status-001")
        self.assertIsInstance(status, BatchStatusResult)
        self.assertEqual(status.batch_id, "batch-status-001")
        self.assertEqual(status.state, "SUCCEEDED")
        self.assertTrue(status.is_successful)

    def test_wait_for_completion_simulation(self):
        """Verifies polling waits and completes cleanly."""
        client = DataprocBatchControllerClient()
        client.submit_batch(batch_id="batch-wait-001", batch_payload={})

        status = client.wait_for_completion(batch_id="batch-wait-001", timeout_seconds=5.0)
        self.assertTrue(status.is_terminal)
        self.assertTrue(status.is_successful)

    def test_cancel_batch_simulation(self):
        """Verifies cancellation flips state in simulated mode."""
        client = DataprocBatchControllerClient()
        client.submit_batch(batch_id="batch-cancel-001", batch_payload={})

        cancel_res = client.cancel_batch("batch-cancel-001")
        self.assertEqual(cancel_res["status"], "CANCELLED")

    def test_session_factory_controller_integration(self):
        """Verifies ManagedSparkSessionFactory generates configured controller."""
        config = SparkSessionConfig(project_id="di-apex_global", location="us-east1")
        factory = ManagedSparkSessionFactory(default_config=config)
        controller = factory.get_batch_controller()

        self.assertIsInstance(controller, DataprocBatchControllerClient)
        self.assertEqual(controller.project_id, "di-apex_global")
        self.assertEqual(controller.location, "us-east1")


if __name__ == "__main__":
    unittest.main()
