"""Adversarial and boundary stress tests for DeferredArtifactWriter."""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from dual_pipeline.core import read_json
from dual_pipeline.query_core.artifact_writer import DeferredArtifactWriter


class TestDeferredArtifactWriterAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp_dir.name) / "run_adversarial"
        self.writer = DeferredArtifactWriter(self.run_dir)

    def tearDown(self) -> None:
        # Restore permissions if any were made read-only
        with contextlib.suppress(Exception):
            os.chmod(self.run_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        self.temp_dir.cleanup()

    def test_concurrent_multithreaded_staging_and_flushing(self) -> None:
        """High-concurrency race condition test across 20 workers staging and flushing."""
        num_workers = 20
        items_per_worker = 15
        barrier = threading.Barrier(num_workers)

        def worker_task(worker_id: int) -> None:
            barrier.wait()
            for i in range(items_per_worker):
                filename = f"worker_{worker_id}_artifact_{i}.json"
                self.writer.stage(filename, {"worker": worker_id, "item": i})
                # Periodic readback check during concurrent writes
                val = self.writer.get(filename)
                self.assertIsNotNone(val)
                self.assertEqual(val["worker"], worker_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, wid) for wid in range(num_workers)]
            concurrent.futures.wait(futures)
            for f in futures:
                f.result()

        total_staged = num_workers * items_per_worker
        self.assertEqual(self.writer.pending_count, total_staged)

        flushed = self.writer.flush(parallel=True, max_workers=8)
        self.assertEqual(flushed, total_staged)
        self.assertTrue(self.writer.is_flushed)

        # Verify all files written to disk accurately
        for wid in range(num_workers):
            for i in range(items_per_worker):
                p = self.run_dir / f"worker_{wid}_artifact_{i}.json"
                self.assertTrue(p.is_file())
                data = read_json(p)
                self.assertEqual(data["worker"], wid)
                self.assertEqual(data["item"], i)

    def test_overwrite_before_flush_reflects_latest_state(self) -> None:
        """Staging multiple updates to the same artifact must maintain latest version."""
        self.writer.stage("dynamic.json", {"version": 1, "state": "initial"})
        self.assertEqual(self.writer.get("dynamic.json")["version"], 1)

        self.writer.stage("dynamic.json", {"version": 2, "state": "updated"})
        self.assertEqual(self.writer.get("dynamic.json")["version"], 2)

        self.writer.stage("dynamic.json", {"version": 3, "state": "final"})
        self.assertEqual(self.writer.pending_count, 1)

        self.writer.flush()
        data = read_json(self.run_dir / "dynamic.json")
        self.assertEqual(data["version"], 3)
        self.assertEqual(data["state"], "final")

    def test_type_conflict_staged_json_vs_text_replacement(self) -> None:
        """Staging a text artifact over an earlier staged JSON replaces cleanly."""
        self.writer.stage("conflict.out", {"type": "json"})
        self.assertEqual(self.writer.pending_count, 1)
        self.writer.stage_text("conflict.out", "raw text content")
        self.assertEqual(self.writer.pending_count, 1)
        self.assertEqual(self.writer.get("conflict.out"), "raw text content")

        self.writer.flush()
        self.assertEqual((self.run_dir / "conflict.out").read_text(encoding="utf-8"), "raw text content")

    def test_deep_nested_directory_auto_creation(self) -> None:
        """Staging artifacts in deeply nested uncreated directory trees."""
        deep_path = "a/b/c/d/e/deep_artifact.json"
        self.writer.stage(deep_path, {"depth": 5})
        self.assertFalse((self.run_dir / deep_path).exists())

        self.writer.flush()
        self.assertTrue((self.run_dir / deep_path).is_file())
        self.assertEqual(read_json(self.run_dir / deep_path), {"depth": 5})

    def test_large_volume_batch_flush_boundary(self) -> None:
        """Staging 250 artifacts in memory and batch flushing with zero drops."""
        count = 250
        for i in range(count):
            self.writer.stage(f"batch_{i:04d}.json", {"seq": i, "padding": "x" * 64})

        self.assertEqual(self.writer.pending_count, count)
        flushed = self.writer.flush()
        self.assertEqual(flushed, count)
        self.assertEqual(self.writer.written_count, count)
        self.assertTrue(self.writer.is_flushed)

        for i in range(0, count, 25):
            self.assertEqual(read_json(self.run_dir / f"batch_{i:04d}.json")["seq"], i)

    def test_immediate_write_clears_staged_item(self) -> None:
        """write_now on an already staged path immediately removes it from staged queue."""
        self.writer.stage("mixed.json", {"staged": True})
        self.assertEqual(self.writer.pending_count, 1)

        self.writer.write_now("mixed.json", {"staged": False, "immediate": True})
        self.assertEqual(self.writer.pending_count, 0)
        self.assertEqual(self.writer.written_count, 1)
        self.assertEqual(read_json(self.run_dir / "mixed.json")["immediate"], True)


if __name__ == "__main__":
    unittest.main()
