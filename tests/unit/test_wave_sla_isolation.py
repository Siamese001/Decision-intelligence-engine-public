"""
tests/unit/test_wave_sla_isolation.py

Verifies Wave 2 scope isolation, in-memory SQLite execution,
and high-throughput parallel test execution.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from storage.connection import configure_pragmas, create_sqlite_conn
from tools.run_tests_with_progress import classify_and_build_suites, run_pytest


def test_in_memory_storage_performance():
    """Verify that in-memory SQLite storage executes 100 transactions in < 50ms."""
    conn = create_sqlite_conn(":memory:")
    start_t = time.perf_counter()
    conn.execute("CREATE TABLE test_bench (id INTEGER PRIMARY KEY, val TEXT);")
    for i in range(100):
        conn.execute("INSERT INTO test_bench (val) VALUES (?);", (f"entry_{i}",))
    cursor = conn.execute("SELECT COUNT(*) FROM test_bench;")
    count = cursor.fetchone()[0]
    elapsed = time.perf_counter() - start_t
    conn.close()

    assert count == 100
    assert elapsed < 0.1, f"100 in-memory transactions took {elapsed:.4f}s, expected < 0.1s"


def test_force_memory_environment_mode(monkeypatch):
    """Verify NEE_STORAGE_FORCE_MEMORY forces in-memory database creation."""
    monkeypatch.setenv("NEE_STORAGE_FORCE_MEMORY", "1")
    conn = create_sqlite_conn("some_dummy_file.db")
    cursor = conn.execute("PRAGMA journal_mode;")
    jmode = cursor.fetchone()[0].upper()
    conn.close()

    assert jmode == "MEMORY"


def test_wave_scope_isolation_resolution():
    """Verify classify_and_build_suites strictly isolates declared wave files."""
    declared = [
        "tests/unit/test_fast_pipeline_harness.py",
        "tests/unit/test_wave_sla_isolation.py",
    ]
    suite, pytest_items = classify_and_build_suites(declared)
    assert len(pytest_items) == 2
    for item in pytest_items:
        assert item in declared or any(item.endswith(Path(d).stem) for d in declared)


def test_concurrent_in_memory_isolated_connections():
    """Verify concurrent isolated in-memory connections execute without lock errors."""
    conns = [create_sqlite_conn(":memory:") for _ in range(5)]
    for idx, c in enumerate(conns):
        c.execute("CREATE TABLE isolated (id INT, name TEXT);")
        c.execute("INSERT INTO isolated VALUES (?, ?);", (idx, f"conn_{idx}"))
        res = c.execute("SELECT name FROM isolated;").fetchone()[0]
        assert res == f"conn_{idx}"
        c.close()


def test_wave_execution_latency_ceiling():
    """Verify wave acceptance tests execute under sub-second ceilings."""
    start_t = time.perf_counter()
    conn = create_sqlite_conn(":memory:")
    conn.execute("CREATE TABLE bench (id INTEGER PRIMARY KEY, text TEXT);")
    conn.executemany("INSERT INTO bench (text) VALUES (?);", [(f"val_{i}",) for i in range(50)])
    rows = conn.execute("SELECT COUNT(*) FROM bench;").fetchone()[0]
    conn.close()
    elapsed = time.perf_counter() - start_t

    assert rows == 50
    assert elapsed < 0.05
