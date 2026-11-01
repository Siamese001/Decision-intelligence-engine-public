"""Adversarial and edge-case tests for storage locks, SQLite contention, and subprocess timeouts.

Categories covered:
1. Concurrency & race: Contended _waiters_file_lock with LOCK_NB retry loop terminates cleanly on timeout.
2. Boundary payload: Subprocess termination with SIGKILL escalation when process ignores SIGINT/SIGTERM.
3. Rollback & recovery: Checkpoint lock releases lock cleanly on exception, allowing immediate re-acquisition.
4. Invariant bypass attempt: AST timeout linter catches unmanaged sqlite3.connect without timeout=.
"""

from __future__ import annotations

import ast
import fcntl
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.checkpoint_storage import checkpoint_lock
from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import terminate_spooler_ffmpeg
from tools.lint_timeout_enforcement import TimeoutEnforcementVisitor
from tools.pr_governance_core.merge_queue import QUEUE_FILE_NAME, _waiters_file_lock


def test_waiters_file_lock_timeout_under_contention(tmp_path: Path) -> None:
    """Concurrency & race: _waiters_file_lock raises TimeoutError within timeout when held."""
    lock_file = tmp_path / f"{QUEUE_FILE_NAME}.lock"
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError, match="Timed out waiting"):
            with _waiters_file_lock(tmp_path, timeout_sec=0.2):
                pass
        elapsed = time.monotonic() - t0
        assert 0.15 <= elapsed < 2.0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_terminate_spooler_ffmpeg_sigkill_escalation() -> None:
    """Boundary payload: terminate_spooler_ffmpeg escalates to SIGKILL if SIGINT and SIGTERM time out."""
    proc_mock = MagicMock()
    # First wait (after SIGINT) times out
    # Second wait (after terminate) times out
    # Third wait (after kill) succeeds
    proc_mock.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="ffmpeg", timeout=2.0),
        subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1.0),
        0,
    ]

    p, pid = terminate_spooler_ffmpeg(proc_mock, captured_pid=12345)

    assert p is None
    proc_mock.send_signal.assert_called_once()
    proc_mock.terminate.assert_called_once()
    proc_mock.kill.assert_called_once()


def test_checkpoint_lock_exception_recovery_and_reacquisition(tmp_path: Path) -> None:
    """Rollback & recovery: checkpoint_lock cleanly unlocks even when block raises, allowing immediate re-acquisition."""
    ckpt_file = tmp_path / "test_ckpt.json"

    with pytest.raises(RuntimeError, match="Simulated crash"):
        with checkpoint_lock(ckpt_file, timeout_sec=1.0):
            raise RuntimeError("Simulated crash inside lock")

    # Immediate re-acquisition should succeed without timeout
    reacquired = False
    with checkpoint_lock(ckpt_file, timeout_sec=0.5):
        reacquired = True

    assert reacquired is True


def test_ast_linter_detects_unmanaged_sqlite_and_flock() -> None:
    """Invariant bypass attempt: AST linter catches raw unmanaged sqlite3.connect and blocking flock."""
    bad_code = """
import sqlite3
import fcntl

def bad_func():
    conn = sqlite3.connect("unmanaged.db")
    fcntl.flock(conn.fileno(), fcntl.LOCK_EX)
"""
    tree = ast.parse(bad_code)
    visitor = TimeoutEnforcementVisitor("bad_sample.py", check_subprocess=True)
    visitor.visit(tree)

    codes = [v[1] for v in visitor.violations]
    assert "SQLITE_TIMEOUT_MISSING" in codes
    assert "BLOCKING_FLOCK_DETECTED" in codes
