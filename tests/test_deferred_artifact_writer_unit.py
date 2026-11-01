"""Unit tests for DeferredArtifactWriter and batched I/O."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import read_json
from dual_pipeline.query_core.artifact_writer import (
    DeferredArtifactWriter,
    mirror_artifacts_to_output_dir,
)


class TestDeferredArtifactWriterUnit(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp_dir.name) / "run_test"
        self.writer = DeferredArtifactWriter(self.run_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initial_state(self) -> None:
        self.assertEqual(self.writer.pending_count, 0)
        self.assertEqual(self.writer.written_count, 0)
        self.assertTrue(self.writer.is_flushed)
        self.assertEqual(self.writer.staged_paths, [])

    def test_stage_and_transparent_readback(self) -> None:
        payload = {"step": 1, "status": "PASS"}
        target = self.writer.stage("u0_validation.json", payload)
        self.assertEqual(target, (self.run_dir / "u0_validation.json").resolve())
        self.assertEqual(self.writer.pending_count, 1)
        self.assertFalse(self.writer.is_flushed)

        # File is in memory, not yet on disk
        self.assertFalse(target.exists())
        self.assertTrue(self.writer.has("u0_validation.json"))
        self.assertEqual(self.writer.get("u0_validation.json"), payload)

    def test_stage_text_and_transparent_readback(self) -> None:
        text = "# Executive Summary\nAll requirements satisfied."
        target = self.writer.stage_text("summary.md", text)
        self.assertEqual(target, (self.run_dir / "summary.md").resolve())
        self.assertEqual(self.writer.pending_count, 1)
        self.assertFalse(target.exists())
        self.assertTrue(self.writer.has("summary.md"))
        self.assertEqual(self.writer.get("summary.md"), text)

    def test_write_now_immediate_persistence(self) -> None:
        payload = {"x_gates": {"X1": "PASS", "X2": "PASS", "X3": "PASS"}}
        target = self.writer.write_now("x_gates.json", payload)
        self.assertTrue(target.is_file())
        self.assertEqual(read_json(target), payload)
        self.assertEqual(self.writer.pending_count, 0)
        self.assertEqual(self.writer.written_count, 1)
        self.assertTrue(self.writer.has("x_gates.json"))
        self.assertEqual(self.writer.get("x_gates.json"), payload)

    def test_write_text_now_immediate_persistence(self) -> None:
        text = "Immediate report text."
        target = self.writer.write_text_now("report.txt", text)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), text)
        self.assertEqual(self.writer.pending_count, 0)
        self.assertEqual(self.writer.written_count, 1)

    def test_flush_persists_all_staged_artifacts(self) -> None:
        self.writer.stage("a.json", {"a": 1})
        self.writer.stage("sub/b.json", {"b": 2})
        self.writer.stage_text("c.txt", "content c")
        self.assertEqual(self.writer.pending_count, 3)

        flushed = self.writer.flush()
        self.assertEqual(flushed, 3)
        self.assertEqual(self.writer.pending_count, 0)
        self.assertTrue(self.writer.is_flushed)
        self.assertEqual(self.writer.written_count, 3)

        # Check disk contents
        self.assertEqual(read_json(self.run_dir / "a.json"), {"a": 1})
        self.assertEqual(read_json(self.run_dir / "sub" / "b.json"), {"b": 2})
        self.assertEqual((self.run_dir / "c.txt").read_text(encoding="utf-8"), "content c")

        # Double flush is idempotent
        self.assertEqual(self.writer.flush(), 0)

    def test_flush_parallel(self) -> None:
        for i in range(10):
            self.writer.stage(f"artifact_{i}.json", {"index": i})
        self.assertEqual(self.writer.pending_count, 10)

        flushed = self.writer.flush(parallel=True, max_workers=4)
        self.assertEqual(flushed, 10)
        self.assertEqual(self.writer.pending_count, 0)
        for i in range(10):
            self.assertEqual(read_json(self.run_dir / f"artifact_{i}.json"), {"index": i})

    def test_flush_async(self) -> None:
        for i in range(5):
            self.writer.stage(f"async_{i}.json", {"val": i})
        future = self.writer.flush_async()
        flushed = future.result(timeout=5.0)
        self.assertEqual(flushed, 5)
        self.assertTrue(self.writer.is_flushed)
        for i in range(5):
            self.assertEqual(read_json(self.run_dir / f"async_{i}.json"), {"val": i})

    def test_mirror_artifacts_with_writer(self) -> None:
        output_dir = Path(self.temp_dir.name) / "output_mirror"
        artifacts = {
            "query_summary.json": {"answer": "verified"},
            "query_plan.json": {"stages": ["U0", "L1"]},
        }
        text_artifacts = {
            "answer.md": "# Answer",
        }
        mirror_artifacts_to_output_dir(
            output_dir,
            artifacts,
            text_artifacts=text_artifacts,
            writer=self.writer,
        )

        # Staged in writer, not yet on disk
        self.assertEqual(self.writer.pending_count, 3)
        self.assertFalse((output_dir / "query_summary.json").exists())

        # Flush persists to output_dir
        self.writer.flush()
        self.assertTrue((output_dir / "query_summary.json").is_file())
        self.assertEqual(read_json(output_dir / "query_summary.json"), {"answer": "verified"})
        self.assertEqual((output_dir / "answer.md").read_text(encoding="utf-8"), "# Answer")

    def test_mirror_artifacts_without_writer(self) -> None:
        output_dir = Path(self.temp_dir.name) / "direct_output"
        artifacts = {
            "result.json": {"ok": True},
        }
        text_artifacts = {
            "note.txt": "direct write",
        }
        mirror_artifacts_to_output_dir(
            output_dir,
            artifacts,
            text_artifacts=text_artifacts,
            writer=None,
        )
        self.assertTrue((output_dir / "result.json").is_file())
        self.assertEqual(read_json(output_dir / "result.json"), {"ok": True})
        self.assertEqual((output_dir / "note.txt").read_text(encoding="utf-8"), "direct write")


if __name__ == "__main__":
    unittest.main()
