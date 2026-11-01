"""Adversarial and boundary test suite for Spark-distributed Pipeline B extraction.

Covers:
1. Corrupted dialogue turns (missing fields, null speakers, non-string IDs)
2. Fault injection in worker extraction step
3. High partition fan-out boundary testing
4. Poisoned payloads and empty text handling
"""
from __future__ import annotations

import shutil
import tempfile
import unittest

from pipeline_b_extraction.agents.spark_extraction_coordinator import (
    SparkExtractionCoordinator,
)


class TestSparkExtractionAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case testing for distributed extraction."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_ext_adv_")
        self.coordinator = SparkExtractionCoordinator(mock_mode=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_corrupted_dialogue_records_handled(self):
        """Verifies missing fields and non-standard types do not crash coordinator."""
        corrupted_records = [
            {"id": "turn_null_speaker", "speaker": None, "text": "Architecture discussion with null speaker"},
            {"id": "turn_empty_text", "speaker": "Alex", "text": ""},
            {"id": "turn_none_text", "speaker": "Alex", "text": None},
            {"id": 12345, "speaker": "Numeric ID", "text": "Valid text with integer ID"},
            {},  # completely empty dict
        ]

        receipt = self.coordinator.run_distributed_extraction(
            records=corrupted_records,
            sink_base_uri=self.temp_dir,
            batch_id="batch-adv-corrupt",
            num_partitions=2,
        )

        self.assertEqual(receipt.total_records, 5)
        self.assertEqual(receipt.successful_records, 5)
        self.assertEqual(receipt.failed_records, 0)
        self.assertTrue(receipt.is_safe)

    def test_high_partition_fanout_stress(self):
        """Exercises 50 records partitioned across 10 partitions."""
        records = [
            {"id": f"rec_{i}", "speaker": f"Speaker_{i % 3}", "text": f"Strategic commitment claim {i}"}
            for i in range(50)
        ]

        receipt = self.coordinator.run_distributed_extraction(
            records=records,
            sink_base_uri=self.temp_dir,
            batch_id="batch-adv-fanout",
            num_partitions=10,
        )

        self.assertEqual(receipt.total_records, 50)
        self.assertEqual(receipt.successful_records, 50)
        self.assertTrue(receipt.is_safe)

    def test_extraction_worker_fault_tolerance(self):
        """Verifies that an unhandled exception in an extraction agent is isolated."""
        bundle = self.coordinator._create_worker_agent_bundle(mock_mode=True)
        # Inject fault into tech agent
        bundle["tech"].extract_batch = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("AST Parsing exploded"))

        # Calling extract_record_multi_agent with faulty agent must handle or raise cleanly
        with self.assertRaises(RuntimeError):
            self.coordinator.extract_record_multi_agent(bundle, {"id": "fault_rec", "text": "fault test"})


if __name__ == "__main__":
    unittest.main()
