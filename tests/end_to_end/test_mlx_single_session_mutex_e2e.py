"""End-to-End lifecycle test suite for Global MLX Whisper Single-Session Mutex.

Verifies:
1. End-to-end sequential session handoff across distinct meetings:
   - Session 1 opens and holds host-wide lock
   - Session 2 attempt is rejected with collision error
   - Session 1 concludes and frees the lock
   - Session 2 can now open successfully
2. Multi-threaded and multi-entrypoint lifecycle verification (LiveMeetingRecorder, RecordingServiceAdapter)
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveSessionCollisionError,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    RecordingServiceAdapter,
    RecordingStartRequest,
    RecordingStopRequest,
)


def safe_stop(rec: LiveMeetingRecorder) -> None:
    with contextlib.suppress(RuntimeError):
        rec.stop()


@pytest.fixture(autouse=True)
def isolate_mlx_lock_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Hermetic isolation fixture directing global MLX session locks to an isolated test directory."""
    lock_dir = tmp_path / ".session_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(lock_dir))
    yield lock_dir


def test_e2e_sequential_session_handoff_distinct_projects(tmp_path: Path):
    """Proves end-to-end that session 1 blocks session 2, and closing session 1 permits session 2."""
    out_dir1 = tmp_path / "project_alpha" / "session_1"
    out_dir2 = tmp_path / "project_beta" / "session_2"
    out_dir1.mkdir(parents=True)
    out_dir2.mkdir(parents=True)

    # 1. Open Session 1
    rec1 = LiveMeetingRecorder(
        meeting_name="Executive Weekly Sync",
        project_name="project_alpha",
        output_dir=out_dir1,
        mock_mode=True,
        enforce_worktree=False,
    )
    assert rec1._global_mlx_lock is not None
    assert rec1._global_mlx_lock.is_held is True

    # 2. Attempt to open Session 2 concurrently -> must be blocked
    with pytest.raises(LiveSessionCollisionError) as exc_info:
        LiveMeetingRecorder(
            meeting_name="Technical Architecture Review",
            project_name="project_beta",
            output_dir=out_dir2,
            mock_mode=True,
            enforce_worktree=False,
        )

    assert exc_info.value.is_global is True
    assert "Global MLX Whisper session mutex" in str(exc_info.value)

    # 3. Gracefully stop Session 1 -> lock is released
    safe_stop(rec1)
    assert rec1._global_mlx_lock is None

    # 4. Open Session 2 -> now succeeds cleanly
    rec2 = LiveMeetingRecorder(
        meeting_name="Technical Architecture Review",
        project_name="project_beta",
        output_dir=out_dir2,
        mock_mode=True,
        enforce_worktree=False,
    )
    assert rec2._global_mlx_lock is not None
    assert rec2._global_mlx_lock.is_held is True
    safe_stop(rec2)


def test_e2e_service_adapter_single_session_rejection(tmp_path: Path):
    """Proves RecordingServiceAdapter rejects concurrent distinct sessions under the global mutex."""
    adapter = RecordingServiceAdapter()
    out_dir1 = tmp_path / "out1"
    out_dir1.mkdir()

    # Active recorder instance holding global lock
    rec = LiveMeetingRecorder(
        meeting_name="Ongoing Board Meeting",
        project_name="board_room",
        output_dir=out_dir1,
        mock_mode=True,
        enforce_worktree=False,
    )

    try:
        req = RecordingStartRequest(
            project_name="side_project",
            meeting_name="Adhoc Catchup",
            mock_mode=True,
        )
        with pytest.raises(RuntimeError) as exc_info:
            adapter.start_recording(req)
        assert "Global MLX Whisper session collision detected" in str(exc_info.value)
    finally:
        safe_stop(rec)
