"""
tests/stress/test_concurrency_locks.py

16-Worker High-Concurrency SQLite Stress Gate.
Enforces Phase 2 CI Testing Rigor:
- Outlaws database lockups under high multi-tenant and live transcription load.
- Executes 16 concurrent workers (interleaved reads, writes, and cache hydrations).
- Enforces WAL mode and PRAGMA busy_timeout = 30000;.
- Asserts ZERO `OperationalError: database is locked` and 100% transaction integrity.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Set

import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.storage.governed_context import GovernedStorageContext
from tests.fixtures.high_fidelity_graph import create_high_fidelity_sqlite_graph


@pytest.fixture
def governed_stress_db(tmp_path: Path) -> Path:
    """Provisions a shared SQLite database governed by WAL mode and write serialization."""
    db_path = tmp_path / "concurrency_stress.sqlite"
    create_high_fidelity_sqlite_graph(db_path=db_path, runtime_dir=tmp_path)

    # Add audit log and session trace tables for concurrent write testing
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id INTEGER NOT NULL,
                iteration INTEGER NOT NULL,
                payload TEXT NOT NULL,
                written_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_trace (
                trace_id TEXT PRIMARY KEY,
                worker_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
    conn.close()
    return db_path


class TestConcurrencyLocks:
    def test_16_worker_interleaved_concurrency_stress(
        self, governed_stress_db: Path
    ) -> None:
        """
        Gate 1: Execute 16 concurrent workers across 3 roles:
        - 8 Readers: querying nodes and assertions.
        - 4 Writers: inserting audit logs and session traces.
        - 4 Hydrators: querying graph slices and validating in-memory topology.
        Assert 0 lock errors and 100% commit integrity across at least 320 transactions.
        """
        db_path = governed_stress_db
        num_workers = 16
        iterations_per_worker = 25  # Total 400 operations

        errors: List[str] = []
        written_log_ids: List[int] = []

        def worker_reader(worker_idx: int) -> int:
            read_count = 0
            for i in range(iterations_per_worker):
                try:
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    conn.execute("PRAGMA journal_mode = WAL;")
                    conn.execute("PRAGMA busy_timeout = 30000;")
                    cur = conn.cursor()
                    cur.execute("SELECT count(*) FROM node WHERE active = 1")
                    _ = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM assertion WHERE retrieval_eligible = 1")
                    _ = cur.fetchone()[0]
                    conn.close()
                    read_count += 1
                except sqlite3.OperationalError as exc:
                    errors.append(f"Reader {worker_idx} lock error: {exc}")
                except Exception as exc:
                    errors.append(f"Reader {worker_idx} unexpected error: {exc}")
                time.sleep(0.001)
            return read_count

        def worker_writer(worker_idx: int) -> int:
            write_count = 0
            for i in range(iterations_per_worker):
                try:
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    conn.execute("PRAGMA journal_mode = WAL;")
                    conn.execute("PRAGMA busy_timeout = 30000;")
                    now = time.time()
                    with conn:
                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO audit_log (worker_id, iteration, payload, written_at) VALUES (?, ?, ?, ?)",
                            (worker_idx, i, f"Audit payload from worker {worker_idx} step {i}", now),
                        )
                        trace_id = f"trace_w{worker_idx}_i{i}_{int(now*1000)}"
                        cur.execute(
                            "INSERT INTO session_trace (trace_id, worker_id, status, created_at) VALUES (?, ?, ?, ?)",
                            (trace_id, worker_idx, "COMMITTED", now),
                        )
                    conn.close()
                    write_count += 1
                except sqlite3.OperationalError as exc:
                    errors.append(f"Writer {worker_idx} lock error: {exc}")
                except Exception as exc:
                    errors.append(f"Writer {worker_idx} unexpected error: {exc}")
                time.sleep(0.001)
            return write_count

        def worker_hydrator(worker_idx: int) -> int:
            hydrate_count = 0
            for i in range(iterations_per_worker):
                try:
                    topo = InMemoryGraphTopology.load_from_sqlite(db_path)
                    assert len(topo.nodes) > 0
                    assert len(topo.assertions) > 0
                    hydrate_count += 1
                except sqlite3.OperationalError as exc:
                    errors.append(f"Hydrator {worker_idx} lock error: {exc}")
                except Exception as exc:
                    errors.append(f"Hydrator {worker_idx} unexpected error: {exc}")
                time.sleep(0.002)
            return hydrate_count

        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = []
            # 8 Readers
            for w in range(0, 8):
                futures.append(executor.submit(worker_reader, w))
            # 4 Writers
            for w in range(8, 12):
                futures.append(executor.submit(worker_writer, w))
            # 4 Hydrators
            for w in range(12, 16):
                futures.append(executor.submit(worker_hydrator, w))

            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        total_elapsed = time.perf_counter() - t0

        # Assert zero lock errors
        assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors during 16-worker stress: {errors[:5]}"

        # Assert 100% transaction integrity
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM audit_log")
        total_logs = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM session_trace")
        total_traces = cur.fetchone()[0]
        conn.close()

        expected_writes = 4 * iterations_per_worker  # 4 writers * 25 = 100
        assert total_logs == expected_writes, f"Expected {expected_writes} audit logs, found {total_logs}"
        assert total_traces == expected_writes, f"Expected {expected_writes} session traces, found {total_traces}"
        assert total_elapsed < 10.0, f"16-worker stress test took {total_elapsed:.2f}s, exceeding 10.0s deadline"
