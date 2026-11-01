"""Tests for Wave 1: Publication Lock Reentrancy & Concurrency Safety E2E.
Validates thread-reentrant file locks, timeout bounds, nested SQLite write contexts,
HITL ledger lock reentrancy, GraphMutationLedger reentrancy, and MeetingIngestionLock safety.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.concurrency import MeetingIngestionLock
from dual_pipeline.core import file_lock
from dual_pipeline.exceptions import ConcurrentIngestionError
from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    WriteSerializer,
    _normalize_repo_path,
)


def test_file_lock_same_thread_reentrant(tmp_path: Path):
    """File lock must allow nested re-entry in the same thread without deadlocking."""
    lock_file = tmp_path / "test.lock"

    depth = 0
    with file_lock(lock_file, timeout_seconds=1.0):
        depth += 1
        with file_lock(lock_file, timeout_seconds=1.0):
            depth += 1
            with file_lock(lock_file, timeout_seconds=1.0):
                depth += 1
                assert depth == 3
            depth -= 1
            assert depth == 2
        depth -= 1
        assert depth == 1

    assert depth == 1


def test_file_lock_timeout_across_threads(tmp_path: Path):
    """File lock acquisition across separate threads must time out cleanly."""
    lock_file = tmp_path / "timeout.lock"
    acquired_event = threading.Event()
    release_event = threading.Event()
    thread_result: queue.Queue[str] = queue.Queue()

    def holder():
        with file_lock(lock_file, timeout_seconds=2.0):
            acquired_event.set()
            release_event.wait(timeout=2.0)

    def contender():
        acquired_event.wait(timeout=2.0)
        t0 = time.monotonic()
        try:
            with file_lock(lock_file, timeout_seconds=0.15, poll_interval=0.01):
                thread_result.put("ACQUIRED")
        except TimeoutError:
            duration = time.monotonic() - t0
            thread_result.put(f"TIMEOUT:{duration:.3f}")
        except Exception as e:
            thread_result.put(f"ERROR:{type(e).__name__}")

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=contender)
    t1.start()
    t2.start()

    res = thread_result.get(timeout=3.0)
    release_event.set()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert res.startswith("TIMEOUT:"), f"Expected timeout, got {res}"


def test_governed_storage_write_context_nested_reentrant(tmp_path: Path):
    """GovernedStorageContext.write_transaction must support nested writes via savepoints."""
    db_path = tmp_path / "nested.db"

    with GovernedStorageContext.write_transaction(db_path) as conn1:
        conn1.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
        conn1.execute("INSERT INTO items (name) VALUES ('item1');")

        # Nested write transaction in the same thread
        with GovernedStorageContext.write_transaction(db_path) as conn2:
            assert conn1 is conn2  # Reuses same active transaction connection
            conn2.execute("INSERT INTO items (name) VALUES ('item2');")

    with GovernedStorageContext.read_context(db_path) as r_conn:
        rows = r_conn.execute("SELECT name FROM items ORDER BY id;").fetchall()
        assert [r[0] for r in rows] == ["item1", "item2"]


def test_governed_storage_write_context_nested_rollback(tmp_path: Path):
    """Inner write transaction failure must roll back to savepoint without breaking outer."""
    db_path = tmp_path / "rollback.db"

    with GovernedStorageContext.write_transaction(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
        conn.execute("INSERT INTO items (name) VALUES ('committed');")

        try:
            with GovernedStorageContext.write_transaction(db_path) as inner_conn:
                inner_conn.execute("INSERT INTO items (name) VALUES ('will_abort');")
                raise ValueError("Intentional inner failure")
        except ValueError:
            pass

    with GovernedStorageContext.read_context(db_path) as r_conn:
        rows = r_conn.execute("SELECT name FROM items;").fetchall()
        assert [r[0] for r in rows] == ["committed"]


def test_hitl_decision_store_ledger_lock_reentrant(tmp_path: Path):
    """HITLDecisionStore._ledger_lock must be thread-reentrant without 60s timeout spin."""
    ledger_path = tmp_path / "hitl_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_path, use_sqlite=False)

    t0 = time.monotonic()
    with store._ledger_lock(timeout_seconds=2.0):
        with store._ledger_lock(timeout_seconds=2.0):
            with store._ledger_lock(timeout_seconds=2.0):
                pass
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"Nested ledger_lock took too long ({elapsed:.3f}s), possible spin!"


def test_graph_mutation_ledger_reentrant_records(tmp_path: Path):
    """GraphMutationLedger operations must succeed even when caller already holds file_lock."""
    ledger_path = tmp_path / "mutation_ledger.json"
    ledger = GraphMutationLedger(ledger_path=ledger_path)

    with file_lock(ledger._lock_path()):
        prep_count = ledger.record_package_preparation(
            run_id="run-prep-1",
            proposed_node_count=10,
            proposed_assertion_count=20,
        )
        assert prep_count == 1

        mut_count = ledger.record_mutation(
            run_id="run-mut-1",
            node_count=10,
            assertion_count=20,
            target_graph_path=tmp_path / "sandbox.db",
        )
        assert mut_count == 1

        act_count = ledger.record_release_activation(
            run_id="run-act-1",
            generation_id="gen-001",
            target_graph_path=tmp_path / "sandbox.db",
        )
        assert act_count == 1

    assert ledger.get_total_mutations() == 1


def test_meeting_ingestion_lock_reentrant_same_thread(tmp_path: Path):
    """MeetingIngestionLock must allow reentrant acquisition in the same thread."""
    lock1 = MeetingIngestionLock(lock_key="meet_123", lock_dir=tmp_path)
    lock2 = MeetingIngestionLock(lock_key="meet_123", lock_dir=tmp_path)

    with lock1:
        # Reentrant lock with same key in same thread
        with lock2:
            assert lock1._reentrant_depth == 1
            assert lock2._reentrant_depth == 1

    # Clean release: lock file is accessible again
    with MeetingIngestionLock(lock_key="meet_123", lock_dir=tmp_path):
        pass


def test_meeting_ingestion_lock_different_threads_blocks(tmp_path: Path):
    """MeetingIngestionLock across threads must raise ConcurrentIngestionError."""
    acquired_event = threading.Event()
    release_event = threading.Event()
    res_queue: queue.Queue[str] = queue.Queue()

    def t1_body():
        with MeetingIngestionLock(lock_key="meet_diff", lock_dir=tmp_path):
            acquired_event.set()
            release_event.wait(timeout=2.0)

    def t2_body():
        acquired_event.wait(timeout=2.0)
        try:
            with MeetingIngestionLock(lock_key="meet_diff", lock_dir=tmp_path):
                res_queue.put("ACQUIRED")
        except ConcurrentIngestionError:
            res_queue.put("BLOCKED")
        except Exception as err:
            res_queue.put(f"ERROR:{type(err).__name__}")

    t1 = threading.Thread(target=t1_body)
    t2 = threading.Thread(target=t2_body)
    t1.start()
    t2.start()

    res = res_queue.get(timeout=3.0)
    release_event.set()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert res == "BLOCKED"


def test_write_serializer_macos_symlink_normalization(tmp_path: Path):
    """WriteSerializer must map /private/var and /var to the identical RLock on macOS."""
    serializer = WriteSerializer()
    p1 = "/var/folders/test/db.sqlite"
    p2 = "/private/var/folders/test/db.sqlite"

    lock1 = serializer.get_lock(p1)
    lock2 = serializer.get_lock(p2)

    assert lock1 is lock2


def test_subprocess_can_acquire_lock_immediately_after_exit(tmp_path: Path):
    """Child subprocess can acquire LOCK_EX | LOCK_NB immediately after context exits."""
    import subprocess
    import sys

    db_path = tmp_path / "subproc_test.db"
    lock_path = Path(f"{db_path}.lock")

    with GovernedStorageContext.write_transaction(db_path) as conn:
        conn.execute("CREATE TABLE test (id INT);")

    code = f"""
import fcntl
with open({repr(str(lock_path))}, 'a+b') as fh:
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
print('OK')
"""
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert res.stdout.strip() == "OK"

