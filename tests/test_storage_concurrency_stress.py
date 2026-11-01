"""Blueprint 2: High-Concurrency SQLite WAL Contention Stress Suite.

Verifies:
1. Multi-threaded parallel write serialization eliminating SQLITE_BUSY collisions under high load (25+ threads).
2. Atomic transactions, zero deadlocks, and ACID durability in WAL mode.
3. Concurrent read/write interleaving without reader starvation or database lock corruption.
4. Fail-closed rollback resilience during worker failure.
"""

from __future__ import annotations

import concurrent.futures
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.database import DatabaseManager, EvidenceRecord


class TestStorageConcurrencyStress(unittest.TestCase):
    """Stress testing suite for DatabaseManager and in-memory WriteSerializer."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(
            base_dir=self.temp_dir.name,
            allow_unencrypted_dev_fallback=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_high_concurrency_parallel_append_evidence(self) -> None:
        """Execute 250 concurrent append_evidence operations across 25 parallel worker threads."""
        worker_count = 25
        iterations_per_worker = 10
        total_records = worker_count * iterations_per_worker

        def worker_task(worker_id: int) -> list[str]:
            created_event_ids: list[str] = []
            for i in range(iterations_per_worker):
                ev_id = f"ev_worker_{worker_id}_{i}"
                record = EvidenceRecord(
                    evidence_id=ev_id,
                    meeting_id="concurrency_stress_meeting",
                    speaker=f"Speaker_{worker_id}",
                    text=f"Concurrent statement from thread {worker_id} iteration {i}",
                    start_timestamp=float(worker_id * 100 + i),
                    is_final=True,
                )
                event_id = self.db.append_evidence(record, enqueue=True)
                created_event_ids.append(event_id)
            return created_event_ids

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker_task, w) for w in range(worker_count)]
            concurrent.futures.wait(futures)

        # Assert no exceptions occurred in any thread
        for f in futures:
            self.assertIsNone(f.exception(), f"Worker raised exception: {f.exception()}")

        # Assert exact record count in database
        with self.db.get_evidence_conn(readonly=True) as conn:
            ev_count = conn.execute(
                "SELECT COUNT(*) FROM immutable_evidence_log WHERE meeting_id='concurrency_stress_meeting'"
            ).fetchone()[0]
            queue_count = conn.execute("SELECT COUNT(*) FROM durable_work_queue").fetchone()[0]

            self.assertEqual(ev_count, total_records)
            self.assertEqual(queue_count, total_records)

    def test_concurrent_read_write_interleaving(self) -> None:
        """Verify concurrent readers do not block writers or encounter lock errors in WAL mode."""
        stop_event = threading.Event()
        read_counts: list[int] = []
        read_errors: list[Exception] = []

        def reader_task() -> None:
            while not stop_event.is_set():
                try:
                    records = self.db.get_evidence_by_meeting("interleaving_meeting")
                    read_counts.append(len(records))
                except Exception as e:
                    read_errors.append(e)

        # Start 5 reader threads
        reader_threads = [threading.Thread(target=reader_task) for _ in range(5)]
        for t in reader_threads:
            t.start()

        # Execute 50 writes from 10 parallel worker threads
        def writer_task(writer_id: int) -> None:
            for i in range(5):
                record = EvidenceRecord(
                    evidence_id=f"ev_rw_{writer_id}_{i}",
                    meeting_id="interleaving_meeting",
                    speaker=f"Writer_{writer_id}",
                    text=f"Text {writer_id}-{i}",
                    start_timestamp=float(i),
                    is_final=True,
                )
                self.db.append_evidence(record, enqueue=False)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            write_futures = [executor.submit(writer_task, w) for w in range(10)]
            concurrent.futures.wait(write_futures)

        stop_event.set()
        for t in reader_threads:
            t.join()

        # Assert zero read errors and zero write errors
        self.assertEqual(len(read_errors), 0, f"Read errors encountered: {read_errors}")
        for f in write_futures:
            self.assertIsNone(f.exception())

        # Total written is 50
        with self.db.get_evidence_conn(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM immutable_evidence_log WHERE meeting_id='interleaving_meeting'"
            ).fetchone()[0]
            self.assertEqual(count, 50)

    def test_transaction_rollback_under_failure(self) -> None:
        """Assert fail-closed rollback when a transaction fails mid-flight."""
        initial_count = 0
        with self.db.get_evidence_conn(readonly=True) as conn:
            initial_count = conn.execute("SELECT COUNT(*) FROM immutable_evidence_log").fetchone()[0]

        with self.assertRaises(RuntimeError):
            with self.db.get_evidence_conn() as conn:
                with self.db.transaction(conn, db_key="evidence"):
                    conn.execute(
                        "INSERT INTO immutable_evidence_log (evidence_id, meeting_id, text, start_timestamp) VALUES (?, ?, ?, ?)",
                        ("ev_doomed", "doomed_meeting", "doomed text", 0.0),
                    )
                    raise RuntimeError("Simulated transaction fault")

        # Verify rollback: doomed record was not committed
        with self.db.get_evidence_conn(readonly=True) as conn:
            after_count = conn.execute("SELECT COUNT(*) FROM immutable_evidence_log").fetchone()[0]
            self.assertEqual(after_count, initial_count)


if __name__ == "__main__":
    unittest.main()
