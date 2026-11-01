"""Unit tests for Spark-distributed Antigravity Agent Worker Engine.

Tests partition-affinity agent initialization, worker caching,
token accumulation, and partition sink persistence.
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
    WorkerTokenMetrics,
)
from dual_pipeline.spark.sink import (
    PartitionedStorageSink,
    RecordExecutionSummary,
)


class TestSparkAgentWorkerUnit(unittest.TestCase):
    """Unit tests for SparkAgentPartitionWorker and related classes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_worker_test_")
        SparkAgentPartitionWorker.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        SparkAgentPartitionWorker.clear_cache()

    def test_worker_token_metrics_add_and_merge(self):
        """Validates token metrics accumulation and merging."""
        m1 = WorkerTokenMetrics()
        m1.add(prompt=100, completion=50, latency=25.0)
        self.assertEqual(m1.prompt_tokens, 100)
        self.assertEqual(m1.completion_tokens, 50)
        self.assertEqual(m1.total_tokens, 150)
        self.assertEqual(m1.record_count, 1)
        self.assertEqual(m1.error_count, 0)

        m2 = WorkerTokenMetrics()
        m2.add(prompt=200, completion=80, latency=40.0, is_error=True)
        self.assertEqual(m2.error_count, 1)

        merged = m1.merge(m2)
        self.assertEqual(merged.prompt_tokens, 300)
        self.assertEqual(merged.completion_tokens, 130)
        self.assertEqual(merged.total_tokens, 430)
        self.assertEqual(merged.record_count, 2)
        self.assertEqual(merged.error_count, 1)

    def test_worker_token_accumulator(self):
        """Validates thread-safe token accumulator."""
        acc = WorkerTokenAccumulator()
        acc.add(prompt=50, completion=25, latency=10.0)
        acc.add(prompt=75, completion=35, latency=15.0)
        self.assertEqual(acc.value.total_tokens, 185)
        self.assertEqual(acc.value.record_count, 2)

        acc.reset()
        self.assertEqual(acc.value.total_tokens, 0)
        self.assertEqual(acc.value.record_count, 0)

    def test_partitioned_storage_sink(self):
        """Validates direct worker partition sink writes and flushing."""
        sink = PartitionedStorageSink(
            base_uri=self.temp_dir,
            partition_id=1,
            buffer_size=2,
        )
        uri1 = sink.write_record("rec-1", {"claim": "Antigravity on Spark"})
        self.assertIn("part-00001", uri1)

        # Buffer size is 2, writing second record triggers auto-flush
        sink.write_record("rec-2", {"claim": "Zero Driver OOM"})
        res = sink.close()
        self.assertTrue(res.is_successful)

        target_file = Path(sink.target_uri)
        self.assertTrue(target_file.exists())

        lines = [json.loads(line) for line in target_file.read_text().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["record_id"], "rec-1")
        self.assertEqual(lines[1]["payload"]["claim"], "Zero Driver OOM")

    def test_partition_affinity_agent_caching(self):
        """Verifies that an agent is initialized only once per worker partition/process."""
        init_calls = [0]

        def custom_init():
            init_calls[0] += 1
            return {"name": "MockSpecialist", "instance_id": init_calls[0]}

        # 1. First retrieval initializes agent
        agent1 = SparkAgentPartitionWorker.get_or_create_agent(
            profile_name="test_specialist",
            custom_initializer=custom_init,
        )
        self.assertEqual(init_calls[0], 1)
        self.assertEqual(agent1["instance_id"], 1)

        # 2. Second retrieval reuses cached agent (zero re-initialization)
        agent2 = SparkAgentPartitionWorker.get_or_create_agent(
            profile_name="test_specialist",
            custom_initializer=custom_init,
        )
        self.assertEqual(init_calls[0], 1)
        self.assertIs(agent1, agent2)
        self.assertEqual(SparkAgentPartitionWorker.get_init_count("test_specialist"), 1)

    def test_process_partition_execution(self):
        """Verifies processing partition stream with summary return and sink writing."""
        records = [
            {"id": "chunk_01", "prompt": "Extract tech architecture decisions"},
            {"id": "chunk_02", "prompt": "Extract strategic commitments"},
            {"id": "chunk_03", "prompt": "Extract friction blocker items"},
        ]

        def mock_handler(agent, rec):
            return {
                "record_id": rec["id"],
                "extracted_entities": ["system:spark_cluster", "policy:driver_safety"],
                "tokens_used": 145,
            }

        summaries = list(
            SparkAgentPartitionWorker.process_partition(
                partition_iter=iter(records),
                partition_id=0,
                sink_base_uri=self.temp_dir,
                profile_name="extraction_agent",
                record_handler=mock_handler,
            )
        )

        self.assertEqual(len(summaries), 3)
        for s in summaries:
            self.assertIsInstance(s, RecordExecutionSummary)
            self.assertEqual(s.status, "SUCCESS")
            self.assertEqual(s.tokens_used, 145)
            self.assertTrue(Path(s.sink_uri).exists())

    def test_process_partition_record_error_isolation(self):
        """Verifies single record failure does not crash partition iterator."""
        records = [
            {"id": "ok_chunk", "prompt": "valid input"},
            {"id": "bad_chunk", "prompt": "trigger exception"},
            {"id": "ok_chunk_2", "prompt": "another valid input"},
        ]

        def fault_handler(agent, rec):
            if rec["id"] == "bad_chunk":
                raise RuntimeError("Worker extraction failure")
            return {"record_id": rec["id"], "tokens_used": 100}

        summaries = list(
            SparkAgentPartitionWorker.process_partition(
                partition_iter=iter(records),
                partition_id=2,
                sink_base_uri=self.temp_dir,
                profile_name="fault_agent",
                record_handler=fault_handler,
            )
        )

        self.assertEqual(len(summaries), 3)
        self.assertEqual(summaries[0].status, "SUCCESS")
        self.assertEqual(summaries[1].status, "FAILED")
        self.assertIn("Worker extraction failure", summaries[1].error or "")
        self.assertEqual(summaries[2].status, "SUCCESS")


if __name__ == "__main__":
    unittest.main()
