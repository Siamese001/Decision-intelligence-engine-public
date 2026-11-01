"""Adversarial and Edge-Case Test Suite for SQLite Memory-Mapped I/O and Concurrency (Wave 4).

Covers 4 mandatory adversarial categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: Zero-record tables, 0-byte mmap size parameter fallback.
2. Malformed input: Invalid checkpoint mode strings, closed connection objects.
3. Concurrency & race: Rapid concurrent checkpoint attempts from multiple threads.
4. Invariant bypass attempt: Attempting writes on readonly connections (query_only enforcement).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from storage.connection import (
    create_sqlite_conn,
    wal_checkpoint_async,
)


class TestMmapStorageAdversarial(TestCase):
    """Adversarial and edge-case tests for Wave 4 storage optimizations."""

    def test_boundary_zero_records_empty_tables(self) -> None:
        """Verifies querying completely empty tables with mmap enabled returns empty results without fault."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "empty.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE empty_table (id INT, val TEXT);")
                res = wal_checkpoint_async(conn, mode="PASSIVE")
                self.assertEqual(res["status"], "SUCCESS")
            finally:
                conn.close()

            rconn = create_sqlite_conn(db_path, readonly=True)
            try:
                rows = rconn.execute("SELECT * FROM empty_table WHERE id = 999;").fetchall()
                self.assertEqual(len(rows), 0)
            finally:
                rconn.close()

    def test_boundary_mmap_disabled_fallback(self) -> None:
        """Verifies setting mmap_size to 0 disables memory mapping gracefully without failure."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "mmap_zero.db"
            with patch.dict(os.environ, {"NEE_PARAM_STORAGE_SQLITE_MMAP_SIZE_BYTES": "0"}):
                conn = create_sqlite_conn(db_path)
                try:
                    mmap_size = conn.execute("PRAGMA mmap_size;").fetchone()[0]
                    self.assertEqual(mmap_size, 0, "mmap_size should reflect 0 when explicitly disabled")
                    conn.execute("CREATE TABLE t (id INT);")
                    conn.execute("INSERT INTO t VALUES (42);")
                    row = conn.execute("SELECT id FROM t;").fetchone()
                    self.assertEqual(row[0], 42)
                finally:
                    conn.close()

    def test_malformed_checkpoint_mode_fallback(self) -> None:
        """Verifies invalid or malformed checkpoint mode strings safely default to PASSIVE."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "malformed_mode.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE t (id INT);")
                # Invalid mode strings
                for bad_mode in ["INVALID", "DROP_DATABASE", "123", "", "none"]:
                    res = wal_checkpoint_async(conn, mode=bad_mode)
                    self.assertEqual(res["status"], "SUCCESS")
                    self.assertEqual(res["mode"], "PASSIVE", "Malformed mode must fallback to PASSIVE")
            finally:
                conn.close()

    def test_malformed_closed_connection_handling(self) -> None:
        """Verifies passing a closed connection returns an ERROR dict rather than crashing."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "closed.db"
            conn = create_sqlite_conn(db_path)
            conn.close()

            res = wal_checkpoint_async(conn, mode="PASSIVE")
            self.assertEqual(res["status"], "ERROR")
            self.assertIn("error", res)

    def test_invariant_bypass_readonly_blocks_mutations(self) -> None:
        """Verifies readonly connections strictly block INSERT, UPDATE, DELETE, and schema changes."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "readonly_test.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
                conn.execute("INSERT INTO items VALUES (1, 'initial');")
            finally:
                conn.close()

            rconn = create_sqlite_conn(db_path, readonly=True)
            try:
                # Read is permitted
                row = rconn.execute("SELECT name FROM items WHERE id = 1;").fetchone()
                self.assertEqual(row[0], "initial")

                # Writes must fail closed
                with self.assertRaises(sqlite3.OperationalError):
                    rconn.execute("INSERT INTO items VALUES (2, 'unauthorized');")

                with self.assertRaises(sqlite3.OperationalError):
                    rconn.execute("UPDATE items SET name = 'corrupted' WHERE id = 1;")

                with self.assertRaises(sqlite3.OperationalError):
                    rconn.execute("DROP TABLE items;")
            finally:
                rconn.close()

    def test_concurrency_race_rapid_parallel_checkpoints(self) -> None:
        """Verifies rapid parallel checkpoint invocations complete safely without SQLITE_BUSY deadlocks."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "parallel_ckpt.db"
            conn = create_sqlite_conn(db_path)
            try:
                conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts REAL);")
                for i in range(100):
                    conn.execute("INSERT INTO events VALUES (?, ?);", (i, float(i)))
            finally:
                conn.close()

            checkpoint_results: list[dict] = []
            lock = threading.Lock()

            def checkpoint_worker() -> None:
                worker_conn = create_sqlite_conn(db_path)
                try:
                    for _ in range(5):
                        res = wal_checkpoint_async(worker_conn, mode="PASSIVE")
                        with lock:
                            checkpoint_results.append(res)
                finally:
                    worker_conn.close()

            threads = [threading.Thread(target=checkpoint_worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(checkpoint_results), 20)
            for res in checkpoint_results:
                self.assertIn(res["status"], ["SUCCESS", "NOOP"])
