"""Unit tests for Wave 1 SQLite concurrency self-healing & stale lock eviction."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from storage.connection import (
    configure_pragmas,
    create_sqlite_conn,
    evict_stale_session_locks,
    execute_with_retry,
    is_pid_alive,
)


class TestSQLiteStaleLockHealerUnit:
    """Class A unit tests verifying SQLite lock recovery and stale lockfile eviction."""

    def test_is_pid_alive_current_and_invalid(self) -> None:
        """Verifies liveness check on current process and non-existent PID."""
        assert is_pid_alive(os.getpid()) is True
        # PID 999999 is extraordinarily unlikely to exist
        assert is_pid_alive(99999999) is False
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    def test_evict_stale_session_locks(self, tmp_path: Path) -> None:
        """Verifies deceased PID locks are evicted while living PID locks remain untouched."""
        lock_dir = tmp_path / ".session_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)

        # 1. Stale lock with dead PID
        dead_lock = lock_dir / "stale_session.flock"
        dead_lock.write_text("99999999\n")

        # 2. Active lock with current PID
        live_lock = lock_dir / "active_session.flock"
        live_lock.write_text(f"{os.getpid()}\n")

        evicted = evict_stale_session_locks(lock_dir=lock_dir)

        assert "stale_session.flock" in evicted
        assert not dead_lock.exists()
        assert live_lock.exists()

    def test_execute_with_retry_succeeds_normally(self, tmp_path: Path) -> None:
        """Verifies standard query execution passes cleanly."""
        db_path = tmp_path / "test.db"
        conn = create_sqlite_conn(db_path)
        cur = execute_with_retry(conn, "CREATE TABLE demo (id INTEGER PRIMARY KEY, val TEXT);")
        assert cur is not None
        execute_with_retry(conn, "INSERT INTO demo (val) VALUES (?);", ("alpha",))
        row = conn.execute("SELECT val FROM demo WHERE id = 1;").fetchone()
        assert row["val"] == "alpha"
        conn.close()

    def test_execute_with_retry_recovers_from_transient_lock(self, tmp_path: Path) -> None:
        """Verifies query retry succeeds when a transient lock is held by another thread."""
        db_path = tmp_path / "locked_demo.db"
        conn1 = create_sqlite_conn(db_path)
        conn1.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, count INTEGER);")
        conn1.execute("INSERT INTO counter VALUES (1, 0);")

        lock_acquired = threading.Event()
        lock_released = threading.Event()

        def locking_worker() -> None:
            # Open second connection and hold an exclusive transaction
            c2 = sqlite3.connect(str(db_path), timeout=0.1)
            c2.execute("BEGIN EXCLUSIVE;")
            lock_acquired.set()
            # Hold lock for 150ms then commit
            time.sleep(0.15)
            c2.execute("COMMIT;")
            c2.close()
            lock_released.set()

        t = threading.Thread(target=locking_worker)
        t.start()
        lock_acquired.wait(timeout=2.0)

        # conn1 attempts to write while conn2 holds lock; execute_with_retry should back off and succeed
        cur = execute_with_retry(
            conn1,
            "UPDATE counter SET count = 42 WHERE id = 1;",
            max_retries=5,
            base_delay_ms=50.0,
        )
        assert cur is not None

        t.join(timeout=2.0)
        row = conn1.execute("SELECT count FROM counter WHERE id = 1;").fetchone()
        assert row["count"] == 42
        conn1.close()

    def test_pragmas_configured_correctly(self, tmp_path: Path) -> None:
        """Verifies WAL journal mode and busy_timeout PRAGMAs are initialized."""
        db_path = tmp_path / "pragma_test.db"
        conn = create_sqlite_conn(db_path)
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert busy_timeout >= 30000
        conn.close()
