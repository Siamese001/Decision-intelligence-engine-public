"""tests/unit/test_worktree_staging_isolation.py

Unit test suite validating worktree chunk staging isolation:
1. Closed-chunk filtering (active writing head bypassed during live capture).
2. Deferral and temporary file cleanup on active chunk modification.
3. Drain behavior copying all chunks when is_final=True.
4. Non-fatal fault containment in recorder polling loop.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _copy_chunk_if_missing,
    migrate_staged_files,
)


@pytest.mark.unit
class TestWorktreeStagingIsolation(unittest.TestCase):
    """Validates that staging migration isolates closed chunks from active writing heads."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.src_dir = self.tmp_path / "src_chunks"
        self.dst_dir = self.tmp_path / "dst_chunks"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.dst_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_migrate_staged_files_excludes_active_chunk_during_recording(self) -> None:
        """During active capture (is_final=False), only closed chunks are migrated."""
        (self.src_dir / "chunk_00000.wav").write_bytes(b"RIFF" + b"\x01" * 1000)
        (self.src_dir / "chunk_00001.wav").write_bytes(b"RIFF" + b"\x02" * 1000)
        (self.src_dir / "chunk_00002.wav").write_bytes(b"RIFF" + b"\x03" * 500)  # active head

        migrate_staged_files(self.src_dir, self.dst_dir, [], is_final=False)

        self.assertTrue((self.dst_dir / "chunk_00000.wav").exists())
        self.assertTrue((self.dst_dir / "chunk_00001.wav").exists())
        self.assertFalse((self.dst_dir / "chunk_00002.wav").exists())

    def test_migrate_staged_files_single_chunk_bypassed_during_recording(self) -> None:
        """When only a single chunk exists and is_final=False, it is the active head and must be bypassed."""
        (self.src_dir / "chunk_00000.wav").write_bytes(b"RIFF" + b"\x01" * 500)

        migrate_staged_files(self.src_dir, self.dst_dir, [], is_final=False)

        self.assertFalse((self.dst_dir / "chunk_00000.wav").exists())

    def test_migrate_staged_files_includes_all_chunks_on_final_drain(self) -> None:
        """On final drain (is_final=True), all chunks including the last one are migrated."""
        (self.src_dir / "chunk_00000.wav").write_bytes(b"RIFF" + b"\x01" * 1000)
        (self.src_dir / "chunk_00001.wav").write_bytes(b"RIFF" + b"\x02" * 1000)
        (self.src_dir / "chunk_00002.wav").write_bytes(b"RIFF" + b"\x03" * 1000)

        migrate_staged_files(self.src_dir, self.dst_dir, [], is_final=True)

        self.assertTrue((self.dst_dir / "chunk_00000.wav").exists())
        self.assertTrue((self.dst_dir / "chunk_00001.wav").exists())
        self.assertTrue((self.dst_dir / "chunk_00002.wav").exists())

    def test_copy_chunk_if_missing_defers_and_cleans_tmp_on_growth(self) -> None:
        """When a chunk is modified during copy and is_final=False, defer non-fatally and remove .tmp."""
        chunk_file = self.src_dir / "chunk_00001.wav"
        chunk_file.write_bytes(b"RIFF" + b"\x00" * 1000)

        original_copy2 = shutil.copy2

        def growing_copy2(src, dst):
            result = original_copy2(src, dst)
            with open(src, "ab") as f_grow:
                f_grow.write(b"\x00" * 500)
            return result

        with patch("shutil.copy2", side_effect=growing_copy2):
            success = _copy_chunk_if_missing(chunk_file, self.dst_dir, is_final=False)

        self.assertFalse(success)
        self.assertFalse((self.dst_dir / "chunk_00001.wav").exists())
        # Verify no orphan .tmp files left behind
        tmp_files = list(self.dst_dir.glob(".tmp_*"))
        self.assertEqual(len(tmp_files), 0)

    def test_copy_chunk_if_missing_raises_on_final_mismatch(self) -> None:
        """When is_final=True and SHA mismatch occurs, fail closed with RuntimeError."""
        chunk_file = self.src_dir / "chunk_00001.wav"
        chunk_file.write_bytes(b"RIFF" + b"\x00" * 1000)

        original_copy2 = shutil.copy2

        def growing_copy2(src, dst):
            result = original_copy2(src, dst)
            with open(src, "ab") as f_grow:
                f_grow.write(b"\x00" * 500)
            return result

        with patch("shutil.copy2", side_effect=growing_copy2):
            with self.assertRaises(RuntimeError) as ctx:
                _copy_chunk_if_missing(chunk_file, self.dst_dir, is_final=True)

        self.assertIn("Integrity check failed: SHA256 mismatch", str(ctx.exception))

    def test_recorder_poll_loop_contains_staging_exceptions(self) -> None:
        """LiveMeetingRecorder._poll_and_process_chunks catches and logs staging migration errors non-fatally."""
        recorder = MagicMock(spec=LiveMeetingRecorder)
        recorder.chunks_dir = self.dst_dir
        recorder.capture_manager = MagicMock()
        recorder.capture_manager.chunks_dir = self.src_dir
        recorder._process_prior_chunks = MagicMock()
        recorder._process_latest_chunk = MagicMock()

        # Patch migrate_staged_files to raise an unexpected IOError
        with patch(
            "dual_pipeline.live_meeting_recorder.migrate_staged_files",
            side_effect=IOError("Simulated filesystem error during staging migration"),
        ):
            # Calling the unbound method with our mock instance should NOT raise
            last_file_sizes: dict = {}
            LiveMeetingRecorder._poll_and_process_chunks(recorder, last_file_sizes)

        # Confirm recorder did not crash and poll finished cleanly
        self.assertTrue(hasattr(recorder, "_poll_lag_sec"))
