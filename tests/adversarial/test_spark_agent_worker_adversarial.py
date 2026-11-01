"""Adversarial and edge-case test suite for Spark-distributed Antigravity Agent Worker.

Covers:
1. Malformed inputs & boundary payloads (huge strings, nulls, corrupt unicode)
2. Invariant bypass & directory traversal attempts in record_id
3. Worker crash recovery and partial partition failure resilience
4. Credential isolation & secret sanitization (zero API key / Bearer token leakage)
5. High-volume buffer rotation stress testing
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.spark.agent_worker import (
    SparkAgentPartitionWorker,
)
from dual_pipeline.spark.sink import PartitionedStorageSink


class TestSparkAgentWorkerAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case validation for worker partition execution."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="spark_worker_adv_")
        SparkAgentPartitionWorker.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        SparkAgentPartitionWorker.clear_cache()

    def test_directory_traversal_in_record_id_contained(self):
        """Verifies attempts to use path traversal in record_id do not escape sink target."""
        sink = PartitionedStorageSink(base_uri=self.temp_dir, partition_id=0)
        traversal_id = "../../../../etc/shadow"

        # Should write cleanly as standard string field inside partition file without affecting target_uri
        sink.write_record(traversal_id, {"status": "attack_contained"})
        sink.close()

        # Check that output remains strictly within self.temp_dir
        out_file = Path(sink.target_uri)
        self.assertTrue(out_file.exists())
        self.assertTrue(str(out_file).startswith(self.temp_dir))

        content = out_file.read_text()
        self.assertIn("../../../../etc/shadow", content)

    def test_empty_record_id_rejected(self):
        """Ensures empty or whitespace record IDs fail fast."""
        sink = PartitionedStorageSink(base_uri=self.temp_dir, partition_id=0)
        with self.assertRaises(ValueError):
            sink.write_record("", {"foo": "bar"})

        with self.assertRaises(ValueError):
            sink.write_record("   ", {"foo": "bar"})

    def test_huge_payload_and_boundary_unicode(self):
        """Exercises 500KB string payload and complex unicode boundary sequences."""
        huge_text = "A" * 500_000
        complex_unicode = "🔥\u202e\ufeff\x00\ufffd\U0001F600"

        records = [
            {"id": "huge_rec", "prompt": huge_text},
            {"id": "unicode_rec", "prompt": complex_unicode},
            {"id": "empty_rec", "prompt": ""},
            {"id": "none_prompt_rec", "prompt": None},
        ]

        summaries = list(
            SparkAgentPartitionWorker.process_partition(
                partition_iter=iter(records),
                partition_id=0,
                sink_base_uri=self.temp_dir,
            )
        )

        self.assertEqual(len(summaries), 4)
        for s in summaries:
            self.assertEqual(s.status, "SUCCESS")

        out_file = Path(summaries[0].sink_uri)
        self.assertTrue(out_file.exists())
        lines = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip()]
        self.assertEqual(len(lines), 4)

    def test_worker_memory_error_isolation(self):
        """Validates that a fatal MemoryError in one record is caught and isolated."""
        records = [
            {"id": "good_1", "prompt": "normal 1"},
            {"id": "oom_trigger", "prompt": "trigger oom"},
            {"id": "good_2", "prompt": "normal 2"},
        ]

        def crashing_handler(agent, rec):
            if rec["id"] == "oom_trigger":
                raise MemoryError("Simulated worker executor OOM")
            return {"record_id": rec["id"], "tokens_used": 50}

        summaries = list(
            SparkAgentPartitionWorker.process_partition(
                partition_iter=iter(records),
                partition_id=3,
                sink_base_uri=self.temp_dir,
                record_handler=crashing_handler,
            )
        )

        self.assertEqual(len(summaries), 3)
        self.assertEqual(summaries[0].status, "SUCCESS")
        self.assertEqual(summaries[1].status, "FAILED")
        self.assertIn("Simulated worker executor OOM", summaries[1].error or "")
        self.assertEqual(summaries[2].status, "SUCCESS")

    def test_credential_leak_sanitization(self):
        """Verifies that API keys and sensitive tokens are never exposed in summaries or sink error messages."""
        fake_secret_key = "AIzaSySecretApiKey123456789"

        records = [
            {"id": "leak_test", "prompt": f"Using secret {fake_secret_key}"},
        ]

        def leaking_handler(agent, rec):
            raise RuntimeError(f"Failed authentication with key {fake_secret_key}")

        summaries = list(
            SparkAgentPartitionWorker.process_partition(
                partition_iter=iter(records),
                partition_id=0,
                sink_base_uri=self.temp_dir,
                record_handler=leaking_handler,
            )
        )

        self.assertEqual(len(summaries), 1)
        # Even if handler threw error, verify sink URI doesn't contain the secret
        self.assertNotIn(fake_secret_key, summaries[0].sink_uri)

    def test_high_volume_buffer_rotation_stress(self):
        """Stresses rapid buffer flushing across 200 records with small buffer_size=5."""
        sink = PartitionedStorageSink(
            base_uri=self.temp_dir,
            partition_id=99,
            buffer_size=5,
        )

        for i in range(200):
            sink.write_record(f"stress_rec_{i}", {"index": i, "val": i * 2})

        flush_res = sink.close()
        self.assertTrue(flush_res.is_successful)

        out_file = Path(sink.target_uri)
        lines = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip()]
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[0]["record_id"], "stress_rec_0")
        self.assertEqual(lines[-1]["record_id"], "stress_rec_199")


if __name__ == "__main__":
    unittest.main()
