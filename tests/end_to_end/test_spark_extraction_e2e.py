"""End-to-End test suite for Spark-distributed Pipeline B extraction.

Exercises full pipeline lifecycle:
1. Multi-turn meeting transcript partitioning
2. Distributed multi-agent extraction across workers
3. Output 03 canonical signal projection persistence
4. Batch receipt generation and validation
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pipeline_b_extraction.agents.spark_extraction_coordinator import (
    SparkExtractionCoordinator,
)


class TestSparkExtractionE2E(unittest.TestCase):
    """End-to-End test for distributed multi-agent extraction pipeline."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_ext_e2e_")
        self.coordinator = SparkExtractionCoordinator(mock_mode=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_corpus_extraction_lifecycle(self):
        """Simulates distributed meeting corpus extraction across 3 partitions."""
        transcript_turns = [
            {"id": "t1", "speaker": "Alex", "text": "We need to scale the knowledge graph to 50M assertions using Dataproc Serverless."},
            {"id": "t2", "speaker": "Jordan", "text": "I commit to delivering the BGE-M3 embedding batch job by next Friday."},
            {"id": "t3", "speaker": "Alex", "text": "We have friction with existing BigQuery streaming quotas."},
            {"id": "t4", "speaker": "Taylor", "text": "The stakeholder alignment between PartnerAdvisory and Apex Global is solidifying."},
            {"id": "t5", "speaker": "Jordan", "text": "Let's enforce driver memory safety and AST limits on all queries."},
            {"id": "t6", "speaker": "Alex", "text": "Final decision: deploy the SparkAgentPartitionWorker to production."},
        ]

        receipt = self.coordinator.run_distributed_extraction(
            records=transcript_turns,
            sink_base_uri=self.temp_dir,
            batch_id="batch-e2e-corpus-001",
            num_partitions=3,
        )

        # 1. Verify receipt statistics
        self.assertEqual(receipt.batch_id, "batch-e2e-corpus-001")
        self.assertEqual(receipt.total_records, 6)
        self.assertEqual(receipt.successful_records, 6)
        self.assertEqual(receipt.failed_records, 0)
        self.assertTrue(receipt.is_safe)
        self.assertGreater(receipt.runtime_seconds, 0.0)

        # 2. Inspect persisted partition files
        sink_files = list(Path(self.temp_dir).glob("part-*.jsonl"))
        self.assertGreaterEqual(len(sink_files), 1)

        total_extracted_records = 0
        for s_file in sink_files:
            lines = [json.loads(line) for line in s_file.read_text().splitlines() if line.strip()]
            for item in lines:
                payload = item["payload"]
                self.assertIn("canonical_nodes", payload)
                self.assertIn("evidence_id", payload)
                total_extracted_records += 1

        self.assertEqual(total_extracted_records, 6)


if __name__ == "__main__":
    unittest.main()
