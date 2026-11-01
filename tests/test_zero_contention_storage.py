"""Unit & Invariant test suite for Wave 5: State Ownership, Sidecars & UWG Centralization.

Verifies:
1. Multi-threaded parallel write serialization under 16 concurrent workers with zero contention.
2. Zero SQLITE_BUSY or database locked collisions in WAL mode.
3. Concurrent read-write interleaving without reader starvation.
4. Fail-closed atomic rollback resilience preserving schema integrity.
5. Connection PRAGMA compliance (30,000ms busy timeout, WAL, foreign keys).
6. Layer-0 storage semantic cache consolidation and schema initialization.
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dual_pipeline.storage.governed_context import (
    GLOBAL_WRITE_SERIALIZER,
    GovernedStorageContext,
)
from storage.connection import create_sqlite_conn
from storage.database import DatabaseManager, EvidenceRecord
from storage.semantic_cache import (
    SEMANTIC_CACHE_SCHEMA,
    init_semantic_cache_schema,
)


class TestZeroContentionStorage(unittest.TestCase):
    """High-concurrency storage and write serializer invariant test suite."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "test_concurrency.sqlite"
        self.db_mgr = DatabaseManager(
            base_dir=str(self.base_path),
            allow_unencrypted_dev_fallback=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_connection_pragma_compliance(self) -> None:
        """Assert that connection pragmas enforce 30s busy timeout, WAL, and foreign keys."""
        # Writable connection
        conn = create_sqlite_conn(self.db_path, readonly=False)
        try:
            busy = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]

            self.assertGreaterEqual(busy, 30000)
            self.assertEqual(fk, 1)
            self.assertEqual(jm.lower(), "wal")
        finally:
            conn.close()

        # Read-only connection
        ro_conn = create_sqlite_conn(self.db_path, readonly=True)
        try:
            qo = ro_conn.execute("PRAGMA query_only;").fetchone()[0]
            self.assertEqual(qo, 1)
        finally:
            ro_conn.close()

    def test_16_worker_parallel_write_serialization(self) -> None:
        """Assert 16 concurrent workers perform writes with zero SQLITE_BUSY errors."""
        worker_count = 16
        iterations_per_worker = 15
        total_tx = worker_count * iterations_per_worker
        errors: list[str] = []

        def worker_task(w_id: int) -> int:
            success = 0
            for i in range(iterations_per_worker):
                ev = EvidenceRecord(
                    evidence_id=f"ev_w{w_id}_{i}_{int(time.time()*1000)}",
                    meeting_id="meeting_concurrency_16",
                    speaker=f"Worker_{w_id}",
                    text=f"Payload from worker {w_id} iter {i}",
                    start_timestamp=float(i),
                    is_final=True,
                )
                try:
                    self.db_mgr.append_evidence(ev, enqueue=True)
                    success += 1
                except Exception as exc:
                    errors.append(f"Worker {w_id} iter {i} error: {exc}")
            return success

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker_task, w) for w in range(worker_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        self.assertEqual(sum(results), total_tx)

        # Verify integrity and row counts
        with self.db_mgr.get_evidence_conn(readonly=True) as check_conn:
            check = check_conn.execute("PRAGMA integrity_check;").fetchone()[0]
            self.assertEqual(check.lower(), "ok")
            count = check_conn.execute(
                "SELECT COUNT(*) FROM immutable_evidence_log WHERE meeting_id='meeting_concurrency_16'"
            ).fetchone()[0]
            self.assertEqual(count, total_tx)

    def test_concurrent_read_write_interleaving_no_starvation(self) -> None:
        """Assert readers never starve or lock during concurrent serialized writes."""
        stop_event = threading.Event()
        read_successes: list[int] = []
        read_errors: list[str] = []

        def reader_loop(r_id: int) -> None:
            count = 0
            while not stop_event.is_set():
                try:
                    self.db_mgr.get_evidence_by_meeting("meeting_interleave")
                    count += 1
                except Exception as e:
                    read_errors.append(f"Reader {r_id} error: {e}")
                time.sleep(0.002)
            read_successes.append(count)

        # Launch 4 parallel reader threads
        reader_threads = [threading.Thread(target=reader_loop, args=(r,)) for r in range(4)]
        for rt in reader_threads:
            rt.start()

        # Execute 50 concurrent writes
        write_errors: list[str] = []
        for i in range(50):
            ev = EvidenceRecord(
                evidence_id=f"ev_inter_{i}",
                meeting_id="meeting_interleave",
                speaker="InterleaveWorker",
                text=f"Interleaved statement {i}",
                start_timestamp=float(i),
                is_final=True,
            )
            try:
                self.db_mgr.append_evidence(ev, enqueue=False)
            except Exception as e:
                write_errors.append(f"Writer error at {i}: {e}")

        stop_event.set()
        for rt in reader_threads:
            rt.join(timeout=5.0)

        self.assertEqual(len(write_errors), 0, f"Write errors: {write_errors}")
        self.assertEqual(len(read_errors), 0, f"Reader starvation errors: {read_errors}")
        self.assertGreater(sum(read_successes), 0)

    def test_atomic_rollback_on_failure_preserves_integrity(self) -> None:
        """Assert write_context rolls back on exception leaving DB clean and uncorrupted."""
        db_file = self.base_path / "rollback_test.sqlite"

        # Initialize schema
        with GovernedStorageContext.write_context(db_file) as conn:
            conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, value TEXT NOT NULL);")
            conn.execute("INSERT INTO test_data (id, value) VALUES (1, 'initial');")

        # Attempt transactional write that fails midway
        with self.assertRaises(ValueError):
            with GovernedStorageContext.write_context(db_file) as conn:
                conn.execute("INSERT INTO test_data (id, value) VALUES (2, 'second');")
                raise ValueError("Simulated worker failure")

        # Verify row 2 was rolled back and row 1 remains
        with GovernedStorageContext.read_context(db_file) as conn:
            rows = conn.execute("SELECT id, value FROM test_data;").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 1)
            self.assertEqual(rows[0][1], "initial")

            # Integrity check
            check = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            self.assertEqual(check.lower(), "ok")

    def test_global_write_serializer_lock_reentrancy_and_canonicalization(self) -> None:
        """Assert GLOBAL_WRITE_SERIALIZER uses reentrant canonical path locks."""
        lock1 = GLOBAL_WRITE_SERIALIZER.get_lock(str(self.db_path))
        lock2 = GLOBAL_WRITE_SERIALIZER.get_lock(str(self.db_path.resolve()))
        self.assertIs(lock1, lock2)

        # Test reentrancy
        with lock1, lock2:
            self.assertTrue(True)

    def test_semantic_cache_storage_consolidation(self) -> None:
        """Assert storage.semantic_cache exports primitives and initializes schema."""
        self.assertIn("CREATE TABLE IF NOT EXISTS semantic_cache_entry", SEMANTIC_CACHE_SCHEMA)
        self.assertIn("semantic_cache_embedding_binding", SEMANTIC_CACHE_SCHEMA)

        sc_db = self.base_path / "semantic_cache.sqlite"
        with GovernedStorageContext.write_context(sc_db) as conn:
            init_semantic_cache_schema(conn)

            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            ]
            self.assertIn("semantic_cache_entry", tables)
            self.assertIn("semantic_cache_embedding_binding", tables)


if __name__ == "__main__":
    unittest.main()
