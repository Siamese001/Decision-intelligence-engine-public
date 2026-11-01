"""Unit tests for Wave 9: HTTP Concurrency & Throughput Scaling."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import write_json
from dual_pipeline.decision_api import runtime_readiness
from dual_pipeline.decision_service_core.request_queue import (
    BoundedRequestQueue,
    resolve_max_concurrency,
    resolve_max_queue_size,
)


class TestHttpConcurrencyUnit(unittest.TestCase):
    def test_resolve_max_concurrency(self) -> None:
        """Verify concurrency resolution from env var, cpu count, or explicit default."""
        cpu = os.cpu_count() or 4
        expected_default = min(max(cpu, 1), 8)

        # 1. Unset env var -> default bounded to min(cpu, 8)
        with patch.dict(os.environ, {}, clear=True):
            val = resolve_max_concurrency()
            self.assertEqual(val, expected_default)

        # 2. Configured env var -> overrides default
        with patch.dict(os.environ, {"NEE_QUERY_MAX_WORKERS": "6"}):
            self.assertEqual(resolve_max_concurrency(), 6)

        # 3. Invalid env var -> falls back to default
        with patch.dict(os.environ, {"NEE_QUERY_MAX_WORKERS": "invalid"}):
            self.assertEqual(resolve_max_concurrency(), expected_default)

        # 4. Explicit default parameter
        self.assertEqual(resolve_max_concurrency(default=3), 3)

    def test_resolve_max_queue_size(self) -> None:
        """Verify queue size resolution defaults to 2x concurrency or env var."""
        # 1. Default -> 2 * max_concurrency
        self.assertEqual(resolve_max_queue_size(4), 8)
        self.assertEqual(resolve_max_queue_size(8), 16)

        # 2. Configured env var -> overrides default
        with patch.dict(os.environ, {"NEE_QUERY_MAX_QUEUE_SIZE": "20"}):
            self.assertEqual(resolve_max_queue_size(4), 20)

        # 3. Explicit default parameter
        self.assertEqual(resolve_max_queue_size(4, default=12), 12)

    def test_bounded_request_queue_execution_and_stats(self) -> None:
        """Verify basic execution, queue counters, and stats transitions."""
        queue = BoundedRequestQueue(max_workers=2, max_queue_size=2)
        try:
            stats = queue.stats()
            self.assertEqual(stats["max_workers"], 2)
            self.assertEqual(stats["max_queue_size"], 2)
            self.assertEqual(stats["max_capacity"], 4)
            self.assertEqual(stats["active_workers"], 0)
            self.assertEqual(stats["queued_requests"], 0)

            fut = queue.submit(lambda: 42)
            self.assertIsNotNone(fut)
            self.assertEqual(fut.result(timeout=1.0), 42)

            post_stats = queue.stats()
            self.assertEqual(post_stats["completed_total"], 1)
            self.assertEqual(post_stats["rejected_total"], 0)
        finally:
            queue.shutdown(wait=True)

    def test_bounded_request_queue_burst_absorption_and_rejection(self) -> None:
        """Verify queue absorbs requests up to max_capacity and rejects excess with None."""
        # 2 workers + 2 queue slots = capacity 4
        queue = BoundedRequestQueue(max_workers=2, max_queue_size=2)
        try:
            gate = threading.Event()

            def slow_task() -> str:
                gate.wait(timeout=2.0)
                return "done"

            # Fill workers (2) + queue (2) = 4 admitted
            futures = [queue.submit(slow_task) for _ in range(4)]
            self.assertTrue(all(f is not None for f in futures))

            # 5th submission must be rejected (returns None -> HTTP 429)
            rejected = queue.submit(slow_task)
            self.assertIsNone(rejected)

            stats = queue.stats()
            self.assertEqual(stats["in_flight"], 4)
            self.assertEqual(stats["rejected_total"], 1)

            # Release workers
            gate.set()
            results = [f.result(timeout=2.0) for f in futures]
            self.assertEqual(results, ["done", "done", "done", "done"])

            drain_stats = queue.stats()
            self.assertEqual(drain_stats["active_workers"], 0)
            self.assertEqual(drain_stats["queued_requests"], 0)
            self.assertEqual(drain_stats["completed_total"], 4)
        finally:
            queue.shutdown(wait=True)

    def test_bounded_request_queue_cancellation_releases_capacity(self) -> None:
        """Verify cancelled queued futures properly release capacity without leakage."""
        queue = BoundedRequestQueue(max_workers=1, max_queue_size=2)
        try:
            gate = threading.Event()
            f1 = queue.submit(lambda: gate.wait(timeout=2.0))
            self.assertIsNotNone(f1)

            # f2 is queued
            f2 = queue.submit(lambda: "queued_task")
            self.assertIsNotNone(f2)
            self.assertEqual(queue.stats()["queued_requests"], 1)

            # Cancel f2 before f1 finishes
            cancelled = f2.cancel()
            self.assertTrue(cancelled)

            # Release f1
            gate.set()
            f1.result(timeout=1.0)

            # Slot should be available again
            f3 = queue.submit(lambda: "new_task")
            self.assertIsNotNone(f3)
            self.assertEqual(f3.result(timeout=1.0), "new_task")
        finally:
            queue.shutdown(wait=True)

    def test_readiness_probe_caching_latency(self) -> None:
        """Verify readiness probe hashes graph and sidecar once and subsequent calls are < 10ms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            graph_file = root / "graph.db"
            sidecar_file = root / "sidecar.db"
            graph_file.write_bytes(b"dummy_graph_data" * 1000)
            sidecar_file.write_bytes(b"dummy_sidecar_data" * 1000)

            from dual_pipeline.hash_cache import cached_sha256_file

            graph_sha = cached_sha256_file(graph_file)
            sidecar_sha = cached_sha256_file(sidecar_file)

            pair_payload = {
                "graph_path": "graph.db",
                "sidecar_path": "sidecar.db",
                "graph_sha256": graph_sha,
                "sidecar_sha256": sidecar_sha,
                "embedding_backend": "bge-m3-dev",
                "model_id": "BAAI/bge-m3",
                "model_revision": "main",
                "generation_id": "gen-test-1",
            }
            write_json(root / "active_pair.json", pair_payload)

            # First probe call populates / verifies cache
            report1 = runtime_readiness(root)
            self.assertEqual(report1["status"], "READY")

            # Subsequent probe calls must be fast (< 10ms)
            start_time = time.monotonic()
            for _ in range(10):
                report = runtime_readiness(root)
                self.assertEqual(report["status"], "READY")
            elapsed_ms = (time.monotonic() - start_time) * 1000 / 10

            self.assertLess(elapsed_ms, 10.0, f"Average readiness probe latency was {elapsed_ms:.2f}ms >= 10ms")


if __name__ == "__main__":
    unittest.main()
