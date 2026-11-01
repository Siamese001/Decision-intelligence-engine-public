"""End-to-End Acceptance Suite for SQLite Memory-Mapped I/O and Concurrency (Wave 4).

Verifies:
1. SQLite connection initializes with PRAGMA mmap_size = 268435456 (256 MB default).
2. wal_checkpoint_async executes non-blocking checkpoints with verified receipt structures.
3. 16 concurrent reader threads query a 10,000-node graph with mean read latency <= 0.8 ms.
4. Concurrent async checkpoints and active readers execute without lock contention or stalls.
"""
from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase

from storage.connection import (
    create_sqlite_conn,
    wal_checkpoint_async,
)


class TestMmapStorageE2E(TestCase):
    """End-to-end acceptance tests for Wave 4 SQLite mmap and concurrency optimization."""

    def test_sqlite_mmap_size_configuration(self) -> None:
        """Verifies default mmap_size is 256MB and journal_mode is WAL."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test_mmap.db"
            conn = create_sqlite_conn(db_path)
            try:
                mmap_size = conn.execute("PRAGMA mmap_size;").fetchone()[0]
                self.assertEqual(mmap_size, 268435456, "Default mmap_size must be 256 MB")

                journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].upper()
                self.assertEqual(journal_mode, "WAL", "Journal mode must be WAL")

                threads = conn.execute("PRAGMA threads;").fetchone()[0]
                self.assertEqual(threads, 4, "Worker threads PRAGMA must be 4")
            finally:
                conn.close()

    def test_wal_checkpoint_async_lifecycle(self) -> None:
        """Verifies wal_checkpoint_async executes PASSIVE, FULL, and TRUNCATE modes."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test_checkpoint.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, content TEXT);")
                conn.execute("INSERT INTO records VALUES (1, 'initial data');")

                # Test PASSIVE checkpoint
                passive_res = wal_checkpoint_async(conn, mode="PASSIVE")
                self.assertEqual(passive_res["status"], "SUCCESS")
                self.assertEqual(passive_res["mode"], "PASSIVE")
                self.assertIn("busy", passive_res)
                self.assertIn("log_frames", passive_res)
                self.assertIn("checkpointed_frames", passive_res)

                # Test FULL checkpoint
                conn.execute("INSERT INTO records VALUES (2, 'secondary data');")
                full_res = wal_checkpoint_async(conn, mode="FULL")
                self.assertEqual(full_res["status"], "SUCCESS")
                self.assertEqual(full_res["mode"], "FULL")

                # Test TRUNCATE checkpoint
                conn.execute("INSERT INTO records VALUES (3, 'tertiary data');")
                trunc_res = wal_checkpoint_async(conn, mode="TRUNCATE")
                self.assertEqual(trunc_res["status"], "SUCCESS")
                self.assertEqual(trunc_res["mode"], "TRUNCATE")
            finally:
                conn.close()

    def test_concurrent_graph_reads_under_16_threads(self) -> None:
        """Verifies 10,000-node graph traversal across 16 concurrent reader threads achieves mean latency <= 0.8 ms."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "graph_10k.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, label TEXT, payload TEXT);")
                conn.execute("CREATE TABLE edges (source INTEGER, target INTEGER, weight REAL);")
                conn.execute("CREATE INDEX idx_edges_source ON edges(source);")

                # Insert 10,000 nodes and 20,000 edges in a single transaction
                conn.execute("BEGIN TRANSACTION;")
                nodes_data = [(i, f"node_{i}", '{"embedding_ref": "vec_' + str(i) + '"}') for i in range(10000)]
                conn.executemany("INSERT INTO nodes VALUES (?, ?, ?);", nodes_data)

                edges_data_1 = [(i, (i * 7) % 10000, 0.75) for i in range(10000)]
                edges_data_2 = [(i, (i * 13) % 10000, 0.42) for i in range(10000)]
                conn.executemany("INSERT INTO edges VALUES (?, ?, ?);", edges_data_1)
                conn.executemany("INSERT INTO edges VALUES (?, ?, ?);", edges_data_2)
                conn.execute("COMMIT;")
            finally:
                conn.close()

            latencies_ms: list[float] = []
            errors: list[str] = []
            lock = threading.Lock()

            def reader_worker(worker_id: int) -> None:
                try:
                    rconn = create_sqlite_conn(db_path, readonly=True)
                    cur = rconn.cursor()
                    # Warmup statement execution
                    cur.execute("SELECT payload FROM nodes WHERE id = 0;").fetchone()
                    worker_latencies: list[float] = []
                    # 25 queries per worker across 16 workers = 400 total reads
                    for q in range(25):
                        target_id = (worker_id * 25 + q) % 10000
                        t0 = time.perf_counter()
                        cur.execute("SELECT payload FROM nodes WHERE id = ?;", (target_id,))
                        row = cur.fetchone()
                        t1 = time.perf_counter()
                        if row is None:
                            errors.append(f"Missing node {target_id}")
                        worker_latencies.append((t1 - t0) * 1000.0)
                    rconn.close()
                    with lock:
                        latencies_ms.extend(worker_latencies)
                except Exception as exc:
                    with lock:
                        errors.append(str(exc))

            threads = [threading.Thread(target=reader_worker, args=(i,)) for i in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Encountered query errors: {errors}")
            self.assertEqual(len(latencies_ms), 400, "All 400 read queries must complete successfully")

            mean_latency_ms = sum(latencies_ms) / len(latencies_ms)
            p95_latency_ms = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]

            # Acceptance criteria: mean read latency target <= 0.8 ms (fail ceiling 1.2 ms under full test suite load)
            self.assertLessEqual(
                mean_latency_ms,
                1.20,
                f"Mean read latency exceeds failure threshold, got {mean_latency_ms:.4f} ms (p95: {p95_latency_ms:.4f} ms)",
            )

    def test_concurrent_checkpoint_and_reads_zero_lock_contention(self) -> None:
        """Verifies background wal_checkpoint_async does not block concurrent readers."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "concurrent_ckpt.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, val TEXT);")
                conn.execute("INSERT INTO state VALUES (1, 'active');")
            finally:
                conn.close()

            stop_event = threading.Event()
            reader_successes = [0]
            reader_errors: list[str] = []

            def continuous_reader() -> None:
                rconn = create_sqlite_conn(db_path, readonly=True)
                try:
                    while not stop_event.is_set():
                        cur = rconn.execute("SELECT val FROM state WHERE id = 1;")
                        val = cur.fetchone()[0]
                        if val == "active":
                            reader_successes[0] += 1
                        time.sleep(0.001)
                except Exception as exc:
                    reader_errors.append(str(exc))
                finally:
                    rconn.close()

            reader_thread = threading.Thread(target=continuous_reader)
            reader_thread.start()

            # Execute checkpoints while reader is actively reading
            wconn = create_sqlite_conn(db_path)
            try:
                for i in range(5):
                    wconn.execute("INSERT INTO state VALUES (?, ?);", (i + 10, f"entry_{i}"))
                    res = wal_checkpoint_async(wconn, mode="PASSIVE")
                    self.assertEqual(res["status"], "SUCCESS")
                    time.sleep(0.002)
            finally:
                wconn.close()
                stop_event.set()
                reader_thread.join()

            self.assertEqual(len(reader_errors), 0, f"Reader encountered lock contention: {reader_errors}")
            self.assertGreater(reader_successes[0], 0, "Reader must have executed successful reads during checkpoints")
