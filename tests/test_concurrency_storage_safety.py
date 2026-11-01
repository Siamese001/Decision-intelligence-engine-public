"""
tests/test_concurrency_storage_safety.py

16-worker parallel stress test and concurrency isolation suite for GovernedStorageContext.
Verifies zero SQLITE_BUSY errors, thread-safe write serialization, and non-blocking reads.
"""

from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.storage.governed_context import GovernedStorageContext


class TestConcurrencyStorageSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_concurrency.db"

        # Initialize schema
        with GovernedStorageContext.write_context(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stress_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    worker_id INTEGER,
                    sequence_num INTEGER,
                    payload TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_16_worker_parallel_write_stress(self) -> None:
        """16 parallel workers execute concurrent write transactions with 0 SQLITE_BUSY failures."""
        worker_count = 16
        iterations_per_worker = 20
        total_expected_records = worker_count * iterations_per_worker

        def worker_task(worker_id: int) -> int:
            written = 0
            for seq in range(iterations_per_worker):
                with GovernedStorageContext.write_context(self.db_path) as conn:
                    conn.execute(
                        "INSERT INTO stress_log (worker_id, sequence_num, payload) VALUES (?, ?, ?)",
                        (worker_id, seq, f"payload_{worker_id}_{seq}"),
                    )
                written += 1
            return written

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker_task, w) for w in range(worker_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(sum(results), total_expected_records)

        with GovernedStorageContext.read_context(self.db_path) as conn:
            row_count = conn.execute("SELECT COUNT(*) FROM stress_log").fetchone()[0]
            self.assertEqual(row_count, total_expected_records)

    def test_concurrent_read_write_splitting(self) -> None:
        """Concurrent readers querying via mode=ro do not block writers and read consistent snapshots."""
        writer_done = False
        reader_counts: list[int] = []

        def writer_loop() -> None:
            nonlocal writer_done
            for i in range(30):
                with GovernedStorageContext.write_context(self.db_path) as conn:
                    conn.execute(
                        "INSERT INTO stress_log (worker_id, sequence_num, payload) VALUES (?, ?, ?)",
                        (999, i, f"writer_stream_{i}"),
                    )
            writer_done = True

        def reader_loop() -> None:
            while not writer_done:
                with GovernedStorageContext.read_context(self.db_path) as conn:
                    cnt = conn.execute("SELECT COUNT(*) FROM stress_log").fetchone()[0]
                    reader_counts.append(cnt)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            writer_future = executor.submit(writer_loop)
            reader_futures = [executor.submit(reader_loop) for _ in range(3)]
            writer_future.result()
            for rf in reader_futures:
                rf.result()

        self.assertTrue(len(reader_counts) > 0)
        with GovernedStorageContext.read_context(self.db_path) as conn:
            final_cnt = conn.execute("SELECT COUNT(*) FROM stress_log WHERE worker_id=999").fetchone()[0]
            self.assertEqual(final_cnt, 30)

    def test_atomic_rollback_on_failure(self) -> None:
        """An unhandled error inside write_context triggers complete rollback."""
        initial_count = 0
        with GovernedStorageContext.read_context(self.db_path) as conn:
            initial_count = conn.execute("SELECT COUNT(*) FROM stress_log").fetchone()[0]

        with self.assertRaises(RuntimeError):
            with GovernedStorageContext.write_context(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO stress_log (worker_id, sequence_num, payload) VALUES (?, ?, ?)",
                    (888, 1, "will_rollback"),
                )
                raise RuntimeError("Simulated transaction fault")

        with GovernedStorageContext.read_context(self.db_path) as conn:
            after_count = conn.execute("SELECT COUNT(*) FROM stress_log").fetchone()[0]
            self.assertEqual(after_count, initial_count)

    def test_cross_module_write_serializer_unification(self) -> None:
        """Verifies storage.connection and dual_pipeline.storage share identical write lock instances."""
        from dual_pipeline.storage.governed_context import _SERIALIZER
        from storage.connection import GLOBAL_WRITE_SERIALIZER

        # 1. Identity check
        self.assertIs(GLOBAL_WRITE_SERIALIZER, _SERIALIZER)

        # 2. Lock identity check for canonical path
        lock_a = GLOBAL_WRITE_SERIALIZER.get_lock(str(self.db_path))
        lock_b = _SERIALIZER.get_lock(str(self.db_path))
        self.assertIs(lock_a, lock_b)

        # 3. WAL checkpoint test
        GovernedStorageContext.checkpoint(self.db_path)


if __name__ == "__main__":
    unittest.main()
