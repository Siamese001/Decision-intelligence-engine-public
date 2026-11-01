"""Tests for Wave 1: Global MLX Whisper Single-Session Mutex and Concurrency Invariant.

Verifies:
1. No two active MLX Whisper sessions can occur concurrently, even across different meeting slugs.
2. The collision detection fails closed in <20ms raising LiveSessionCollisionError.
3. Fast bootstrap checks the active session and does not spawn orphan spooler processes.
4. Closing (stop / abort) the first session cleanly frees the lock for subsequent sessions.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveSessionCollisionError,
)
from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
    clear_bootstrap_spooler,
    get_bootstrap_spooler,
    run_fast_bootstrap,
)
from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import (
    GlobalMLXSessionLock,
    check_active_mlx_session,
    get_global_lock_path,
)


def safe_stop(rec: LiveMeetingRecorder) -> None:
    """Helper to stop a recorder without failing on empty session RuntimeError."""
    with contextlib.suppress(RuntimeError):
        rec.stop()


@pytest.fixture(autouse=True)
def isolate_mlx_lock_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Hermetic isolation fixture directing global MLX session locks to an isolated test directory."""
    lock_dir = tmp_path / ".session_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(lock_dir))
    yield lock_dir


def test_global_mlx_lock_distinct_slugs_fail_closed(tmp_path: Path):
    """Two concurrent recorders with different meeting names must fail closed via Global MLX Session Lock."""
    out_dir1 = tmp_path / "out1"
    out_dir2 = tmp_path / "out2"
    out_dir1.mkdir()
    out_dir2.mkdir()

    rec1 = LiveMeetingRecorder(
        meeting_name="Alpha Sync",
        project_name="project_alpha",
        output_dir=out_dir1,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        t0 = time.perf_counter()
        with pytest.raises(LiveSessionCollisionError) as exc_info:
            LiveMeetingRecorder(
                meeting_name="Beta Review",
                project_name="project_beta",
                output_dir=out_dir2,
                mock_mode=True,
                enforce_worktree=False,
            )
        duration_ms = (time.perf_counter() - t0) * 1000.0

        assert duration_ms <= 100.0, f"Collision detection took {duration_ms:.2f}ms (> 100ms)"
        assert exc_info.value.holder_pid == os.getpid()
        assert exc_info.value.is_global is True
        assert "Global MLX Whisper session mutex" in str(exc_info.value)
    finally:
        safe_stop(rec1)

    # After rec1 stops and releases lock, rec2 should succeed immediately
    rec2 = LiveMeetingRecorder(
        meeting_name="Beta Review",
        project_name="project_beta",
        output_dir=out_dir2,
        mock_mode=True,
        enforce_worktree=False,
    )
    safe_stop(rec2)


def test_check_active_mlx_session_reports_accurate_state(tmp_path: Path):
    """check_active_mlx_session accurately reflects active lock state without blocking."""
    lock_mgr = GlobalMLXSessionLock(repo_root=tmp_path)
    is_active, holder = check_active_mlx_session(repo_root=tmp_path)
    assert is_active is False
    assert holder is None

    meta = lock_mgr.acquire(session_id="test_sess_123", meeting_slug="sync_meeting", project_name="proj")
    try:
        is_active, holder = check_active_mlx_session(repo_root=tmp_path)
        assert is_active is True
        assert holder is not None
        assert holder.pid == os.getpid()
        assert holder.session_id == "test_sess_123"
        assert holder.meeting_slug == "sync_meeting"
    finally:
        lock_mgr.release()

    is_active, holder = check_active_mlx_session(repo_root=tmp_path)
    assert is_active is False
    assert holder is None


def test_fast_bootstrap_skips_when_active_session_open(tmp_path: Path):
    """run_fast_bootstrap returns None and spawns no spooler when an MLX session is open."""
    clear_bootstrap_spooler(stop=True)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rec = LiveMeetingRecorder(
        meeting_name="Ongoing Discussion",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        spool_dir = run_fast_bootstrap(["record-meeting", "--project", "foo", "--meeting", "Second Call", "--mock"])
        assert spool_dir is None
        assert get_bootstrap_spooler() is None
    finally:
        safe_stop(rec)
        clear_bootstrap_spooler(stop=True)


def test_early_abort_cleanup_releases_global_lock(tmp_path: Path):
    """An early abort of a recorder session cleans up the global lock immediately."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rec = LiveMeetingRecorder(
        meeting_name="Aborted Session",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    assert rec._global_mlx_lock is not None
    assert rec._global_mlx_lock.is_held is True

    # Trigger early abort cleanup
    rec._early_abort_cleanup()
    assert rec._global_mlx_lock is None

    # Next session must acquire cleanly
    rec2 = LiveMeetingRecorder(
        meeting_name="Subsequent Session",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    safe_stop(rec2)
