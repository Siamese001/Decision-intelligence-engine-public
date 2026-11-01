#!/usr/bin/env python3
"""Unit tests for storage.connection module."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from storage.connection import (
    WriteSerializer,
    create_sqlite_conn,
    get_connection,
)


class TestStorageConnectionUnit(unittest.TestCase):
    """Tests for storage connection and WriteSerializer primitives."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_storage.db"

    def test_write_serializer_same_key_returns_same_lock(self) -> None:
        serializer = WriteSerializer()
        lock1 = serializer.get_lock("canonical")
        lock2 = serializer.get_lock("canonical")
        self.assertIs(lock1, lock2)

    def test_write_serializer_different_keys_return_distinct_locks(self) -> None:
        serializer = WriteSerializer()
        lock1 = serializer.get_lock("db_a")
        lock2 = serializer.get_lock("db_b")
        self.assertIsNot(lock1, lock2)

    def test_write_serializer_multithreaded_lock_consistency(self) -> None:
        serializer = WriteSerializer()
        locks = []
        threads = []

        def worker() -> None:
            l = serializer.get_lock("shared_db")
            locks.append(l)

        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(locks), 10)
        first_lock = locks[0]
        for l in locks[1:]:
            self.assertIs(first_lock, l)

    def test_create_sqlite_conn_read_write(self) -> None:
        conn = create_sqlite_conn(self.db_path, readonly=False)
        self.assertEqual(conn.row_factory, sqlite3.Row)
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT);")
        conn.execute("INSERT INTO items (val) VALUES ('test_val');")
        cur = conn.execute("SELECT val FROM items WHERE id = 1;")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["val"], "test_val")
        conn.close()

    def test_create_sqlite_conn_readonly_enforcement(self) -> None:
        # First initialize DB
        conn_init = create_sqlite_conn(self.db_path, readonly=False)
        conn_init.execute("CREATE TABLE ro_test (id INTEGER PRIMARY KEY);")
        conn_init.close()

        # Open in readonly mode
        conn_ro = create_sqlite_conn(self.db_path, readonly=True)
        with self.assertRaises(sqlite3.OperationalError):
            conn_ro.execute("INSERT INTO ro_test (id) VALUES (1);")
        conn_ro.close()

    def test_get_connection_alias_matches_create_sqlite_conn(self) -> None:
        self.assertIs(get_connection, create_sqlite_conn)


if __name__ == "__main__":
    unittest.main()
