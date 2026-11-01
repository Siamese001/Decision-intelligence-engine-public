"""Unit tests for SparkExtractionCoordinator in Pipeline B.

Tests multi-agent extraction bundling, partition chunking, and receipt generation.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest

from pipeline_b_extraction.agents.spark_extraction_coordinator import (
    SparkExtractionBatchReceipt,
    SparkExtractionCoordinator,
)


class TestSparkExtractionCoordinatorUnit(unittest.TestCase):
    """Unit tests for distributed multi-agent extraction coordinator."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_ext_unit_")
        self.coordinator = SparkExtractionCoordinator(mock_mode=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extract_record_multi_agent(self):
        """Verifies multi-agent extraction produces canonical and advisory nodes."""
        bundle = self.coordinator._create_worker_agent_bundle(mock_mode=True)
        rec = {
            "id": "turn_42",
            "text": "We decided to migrate our event loop to Kafka with 99.99% SLA.",
            "speaker": "Principal Architect",
        }
        res = self.coordinator.extract_record_multi_agent(bundle, rec)

        self.assertEqual(res["evidence_id"], "turn_42")
        self.assertEqual(res["speaker"], "Principal Architect")
        self.assertGreater(res["canonical_count"], 0)
        self.assertGreater(res["tokens_used"], 0)

    def test_run_distributed_extraction_empty_input(self):
        """Verifies zero records returns clean receipt with zero counts."""
        receipt = self.coordinator.run_distributed_extraction(
            records=[],
            sink_base_uri=self.temp_dir,
            batch_id="batch-empty",
        )
        self.assertEqual(receipt.total_records, 0)
        self.assertEqual(receipt.successful_records, 0)
        self.assertEqual(receipt.failed_records, 0)
        self.assertTrue(receipt.is_safe)

    def test_run_distributed_extraction_success(self):
        """Verifies multi-record extraction partitions and produces valid receipt."""
        records = [
            {"id": f"rec_{i}", "text": f"Architecture turn {i} discussion", "speaker": "Alex"}
            for i in range(6)
        ]

        receipt = self.coordinator.run_distributed_extraction(
            records=records,
            sink_base_uri=self.temp_dir,
            batch_id="batch-unit-6recs",
            num_partitions=2,
        )

        self.assertIsInstance(receipt, SparkExtractionBatchReceipt)
        self.assertEqual(receipt.total_records, 6)
        self.assertEqual(receipt.successful_records, 6)
        self.assertEqual(receipt.failed_records, 0)
        self.assertGreater(receipt.total_tokens_used, 0)
        self.assertTrue(receipt.is_safe)


if __name__ == "__main__":
    unittest.main()
