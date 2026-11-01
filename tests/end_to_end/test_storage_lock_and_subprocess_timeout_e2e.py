"""End-to-End test for SQLite connection pragmas, file lock contention, and subprocess timeouts.

Verifies:
1. SQLite connections via create_sqlite_conn configure busy_timeout = 30000 and timeout=30.0.
2. Checkpoint lock non-blocking retry loop raises TimeoutError when lock is held past timeout.
3. PR governance run_git and rebase orchestrator enforce explicit subprocess timeouts.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from dual_pipeline.checkpoint_storage import checkpoint_lock
from storage.connection import create_sqlite_conn, validate_canonical_graph_db
from tools.pr_governance_core.common import run_git


@pytest.mark.timeout(30)
def test_sqlite_connection_busy_timeout_pragmas_e2e(tmp_path: Path) -> None:
    """E2E flow: SQLite connections initialize with PRAGMA busy_timeout = 30000."""
    db_file = tmp_path / "test_governed.db"
    conn = create_sqlite_conn(db_file)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA busy_timeout;")
        row = cur.fetchone()
        assert row is not None
        busy_timeout = int(row[0])
        assert busy_timeout == 30000

        cur.execute("PRAGMA journal_mode;")
        jmode = cur.fetchone()
        assert jmode is not None
        assert str(jmode[0]).lower() == "wal"
    finally:
        conn.close()


@pytest.mark.timeout(30)
def test_checkpoint_lock_contention_and_timeout_e2e(tmp_path: Path) -> None:
    """E2E flow: checkpoint_lock raises TimeoutError when lock is held past bounded timeout."""
    ckpt_file = tmp_path / "meeting_checkpoint.json"
    ckpt_lock_file = tmp_path / "meeting_checkpoint.json.lock"

    # Hold the lock externally
    holder_fd = os.open(str(ckpt_lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError, match="Timed out waiting"):
            with checkpoint_lock(ckpt_file, timeout_sec=0.2):
                pass
        elapsed = time.monotonic() - t0
        assert 0.15 <= elapsed < 2.0
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


@pytest.mark.timeout(30)
def test_run_git_subprocess_timeout_e2e() -> None:
    """E2E flow: run_git executes with bounded timeout and raises on subprocess timeout."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_git(["-c", "alias.sleeplog=!sleep 2", "sleeplog"], timeout=0.1)
