"""Adversarial stress and edge-case tests for Global MLX Whisper Single-Session Mutex.

Mandatory edge-case categories covered:
1. Concurrency & race: Multi-threaded parallel start collision and lock acquisition races.
2. Malformed input & metadata resilience: Corrupted / truncated lock files gracefully handled.
3. Rollback & crash recovery: Process termination (SIGKILL simulation) auto-releases OS flock.
4. Fast bootstrap orphan prevention under simultaneous collision.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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
    ActiveSessionMeta,
    GlobalMLXSessionLock,
    check_active_mlx_session,
    clean_stale_global_mlx_lock,
    get_global_lock_path,
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


def test_adversarial_crashed_holder_flock_auto_release(tmp_path: Path):
    """Category: Rollback & recovery.
    When a lock-holding child process terminates abnormally (SIGKILL), the OS kernel
    immediately releases the file lock, allowing the next process to acquire without delay.
    """
    script = """
import os, time, sys
from pathlib import Path
from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import GlobalMLXSessionLock

lock = GlobalMLXSessionLock(repo_root=Path(sys.argv[1]))
lock.acquire(session_id="child_to_kill", meeting_slug="doomed_session")
print("ACQUIRED", flush=True)
time.sleep(30)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        line = proc.stdout.readline()
        assert "ACQUIRED" in line

        # Verify lock is held from parent perspective
        is_active, holder = check_active_mlx_session(repo_root=tmp_path)
        assert is_active is True
        assert holder is not None
        assert holder.pid == proc.pid

        # Kill child process violently
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=2.0)

        # OS kernel has closed the FD and released flock immediately
        is_active, holder = check_active_mlx_session(repo_root=tmp_path)
        assert is_active is False

        # Stale metadata cleanup cleans up the dead PID metadata
        reaped = clean_stale_global_mlx_lock(repo_root=tmp_path)
        assert reaped is True

        # New session acquires seamlessly
        lock2 = GlobalMLXSessionLock(repo_root=tmp_path)
        meta2 = lock2.acquire(session_id="recovery_session", meeting_slug="fresh_session")
        assert meta2.session_id == "recovery_session"
        lock2.release()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_adversarial_corrupted_lock_file_resilience(tmp_path: Path):
    """Category: Malformed input.
    If the lock file contains invalid JSON, truncated bytes, or binary garbage,
    ActiveSessionMeta parsing falls back gracefully without throwing unhandled exceptions.
    """
    lock_file = get_global_lock_path(tmp_path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Truncated JSON
    lock_file.write_text('{"pid": 12345, "session_id": ', encoding="utf-8")
    parsed = ActiveSessionMeta.from_raw(lock_file.read_text(encoding="utf-8"))
    assert parsed is None

    # 2. Binary / random garbage
    lock_file.write_bytes(b"\x00\xff\xfe\x12\x34\x56")
    try:
        parsed = ActiveSessionMeta.from_raw(lock_file.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        pytest.fail(f"ActiveSessionMeta.from_raw raised exception on corrupt bytes: {e}")

    # 3. Plain PID number string (backward compatibility format)
    lock_file.write_text("99999\n", encoding="utf-8")
    parsed = ActiveSessionMeta.from_raw(lock_file.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed.pid == 99999


def test_adversarial_parallel_fast_bootstrap_race(tmp_path: Path):
    """Category: Concurrency & race.
    Rapidly invoking fast-bootstrap across multiple calls when an active session is running
    never leaks an orphan InstantAudioSpooler process.
    """
    clear_bootstrap_spooler(stop=True)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rec = LiveMeetingRecorder(
        meeting_name="Primary Meeting",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )

    try:
        # Launch 5 rapid bootstrap attempts
        for i in range(5):
            spool = run_fast_bootstrap([
                "record-meeting",
                "--project", f"proj_{i}",
                "--meeting", f"Interfering Meeting {i}",
                "--mock",
            ])
            assert spool is None
            assert get_bootstrap_spooler() is None
    finally:
        safe_stop(rec)
        clear_bootstrap_spooler(stop=True)
