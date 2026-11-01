"""End-to-End test suite for Spark-distributed Antigravity Agent Worker Engine.

Exercises the full multi-partition distributed agent workflow:
1. Multi-partition transcript chunk distribution
2. Partition-affinity Antigravity agent initialization
3. Direct worker output persistence to partition sink
4. Driver aggregation of token metrics and execution summaries
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.spark.agent_worker import (
    SparkAgentPartitionWorker,
    WorkerTokenAccumulator,
)


class TestSparkAgentWorkerE2E(unittest.TestCase):
    """End-to-end lifecycle test for distributed agent execution across partitions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_worker_e2e_")
        SparkAgentPartitionWorker.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        SparkAgentPartitionWorker.clear_cache()

    def test_full_distributed_multi_partition_agent_lifecycle(self):
        """Simulates distributed multi-partition execution with real Antigravity agent initialization."""
        # 1. Prepare simulated 3-partition dataset (15 transcript turns total)
        partitions_data = [
            [
                {"id": f"p0_turn_{i}", "prompt": f"Extract tech architecture from turn {i}"}
                for i in range(5)
            ],
            [
                {"id": f"p1_turn_{i}", "prompt": f"Extract strategic commitments from turn {i}"}
                for i in range(5)
            ],
            [
                {"id": f"p2_turn_{i}", "prompt": f"Extract friction blockers from turn {i}"}
                for i in range(5)
            ],
        ]

        # 2. Configure AgentConfigFactory with governed offline/test settings
        factory_kwargs = {
            "env": {"GEMINI_API_KEY": "dummy-spark-worker-key"},
        }

        token_accumulator = WorkerTokenAccumulator()
        driver_summaries = []

        # 3. Simulate Spark RDD mapPartitionsWithIndex across all 3 partitions
        for part_idx, part_records in enumerate(partitions_data):
            part_summaries = list(
                SparkAgentPartitionWorker.process_partition(
                    partition_iter=iter(part_records),
                    partition_id=part_idx,
                    sink_base_uri=self.temp_dir,
                    profile_name="extraction_specialist",
                    factory_kwargs=factory_kwargs,
                )
            )

            for s in part_summaries:
                driver_summaries.append(s)
                token_accumulator.add(
                    prompt=100,
                    completion=s.tokens_used,
                    latency=s.latency_ms,
                    is_error=(s.status != "SUCCESS"),
                )

        # 4. Verify Driver received all 15 summaries with zero data loss
        self.assertEqual(len(driver_summaries), 15)
        for s in driver_summaries:
            self.assertEqual(s.status, "SUCCESS")
            self.assertGreater(s.tokens_used, 0)
            self.assertGreater(s.latency_ms, 0.0)

        # 5. Verify Token Accumulator metrics on driver
        metrics = token_accumulator.value
        self.assertEqual(metrics.record_count, 15)
        self.assertEqual(metrics.error_count, 0)
        self.assertEqual(metrics.prompt_tokens, 1500)
        self.assertGreater(metrics.total_tokens, 1500)

        # 6. Verify partition sink output files written directly by workers
        output_files = list(Path(self.temp_dir).glob("part-*.jsonl"))
        self.assertEqual(len(output_files), 3)

        total_persisted_records = 0
        for out_file in output_files:
            lines = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip()]
            self.assertEqual(len(lines), 5)
            total_persisted_records += len(lines)

        self.assertEqual(total_persisted_records, 15)


if __name__ == "__main__":
    unittest.main()
