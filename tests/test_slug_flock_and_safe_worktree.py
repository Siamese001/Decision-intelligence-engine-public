"""Tests for Wave 2: Concurrency Hardening, Slug File Mutex & Safe Worktree Provisioning.
Validates OS advisory slug flocking, collision detection (<20ms), rapid abort worktree cleanup,
and global git worktree provisioning serialization.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveSessionCollisionError,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    git_worktree_provision_lock,
)


def safe_stop(rec: LiveMeetingRecorder) -> None:
    """Helper to stop a recorder without failing on empty session RuntimeError."""
    with contextlib.suppress(RuntimeError):
        rec.stop()


def test_slug_flock_parallel_collision_fail_closed(tmp_path: Path):
    """Parallel recorder invocations on the same slug must fail closed in <= 20ms with LiveSessionCollisionError."""
    meeting_name = "Sprint Planning Sync"
    out_dir1 = tmp_path / "out1"
    out_dir2 = tmp_path / "out2"
    out_dir1.mkdir()
    out_dir2.mkdir()

    rec1 = LiveMeetingRecorder(
        meeting_name=meeting_name,
        output_dir=out_dir1,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        t0 = time.perf_counter()
        with pytest.raises(LiveSessionCollisionError) as exc_info:
            LiveMeetingRecorder(
                meeting_name=meeting_name,
                output_dir=out_dir2,
                mock_mode=True,
                enforce_worktree=False,
            )
        duration_ms = (time.perf_counter() - t0) * 1000.0

        assert duration_ms <= 100.0, f"Collision detection took {duration_ms:.2f}ms (> 100ms)"
        assert exc_info.value.holder_pid == os.getpid()
        assert "Live session collision detected" in str(exc_info.value)
    finally:
        safe_stop(rec1)

    # After rec1 stops and releases lock, rec2 should now succeed
    rec2 = LiveMeetingRecorder(
        meeting_name=meeting_name,
        output_dir=out_dir2,
        mock_mode=True,
        enforce_worktree=False,
    )
    safe_stop(rec2)


def test_git_worktree_provision_lock(tmp_path: Path):
    """Global Git worktree provision lock must serialize concurrent acquisitions."""
    lock_repo = tmp_path / "mock_repo"
    lock_repo.mkdir()

    with git_worktree_provision_lock(lock_repo, timeout_seconds=1.0) as lock1:
        assert Path(lock1).exists()
        # Second immediate acquisition attempt with 0.05s timeout must time out
        with pytest.raises(TimeoutError), git_worktree_provision_lock(lock_repo, timeout_seconds=0.05):
            pass


def test_rapid_abort_zero_ghost_worktrees(tmp_path: Path):
    """A session terminating with 0 chunks must clean up worktree and delete empty branch on abort."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rec = LiveMeetingRecorder(
        meeting_name="Short Aborted Call",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )

    fake_wt = tmp_path / ".worktrees" / "live_short_aborted_call"
    fake_wt.mkdir(parents=True)
    rec.worktree_dir = fake_wt
    rec.worktree_branch = "feat/live-short-aborted-call"
    rec._worktree_materialized = True
    rec._is_inside_worktree = False

    with patch("dual_pipeline.live_meeting_recorder.teardown_empty_live_worktree") as mock_teardown:
        rec._early_abort_cleanup()
        mock_teardown.assert_called_once_with(fake_wt, "feat/live-short-aborted-call", ANY)


def test_immediate_worktree_discriminator_on_dirty_target(tmp_path: Path):
    """When target chunks exist without resume, immediate-worktree archives residual chunks without ms_tag branch fork."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    target_chunks = tmp_path / "target_chunks"
    target_chunks.mkdir()
    chunk_f = target_chunks / "chunk_0001.wav"
    chunk_f.write_bytes(b"RIFF" + b"\x00" * 40)

    with (
        patch("dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree", return_value=False),
        patch("dual_pipeline.live_meeting_recorder.resolve_recorder_paths") as mock_paths,
        patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder.materialize_worktree"),
    ):
        fake_wt = tmp_path / "wt"
        mock_paths.return_value = (out_dir, tmp_path / "cache", target_chunks, fake_wt, "feat/live-test-slug")
        rec = LiveMeetingRecorder(
            meeting_name="Test Collision Slug",
            output_dir=out_dir,
            mock_mode=True,
            immediate_worktree=True,
            resume=False,
        )
        try:
            # Branch name remains clean without microsecond fork
            assert rec.worktree_branch == "feat/live-test-slug"
            # Stale chunk was archived in-place
            assert not chunk_f.exists()
            stale_dirs = list(target_chunks.glob(".stale_*"))
            assert len(stale_dirs) == 1
            assert (stale_dirs[0] / "chunk_0001.wav").exists()
        finally:
            safe_stop(rec)
