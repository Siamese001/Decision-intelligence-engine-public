#!/usr/bin/env python3
"""Multi-worker concurrency stress and memory resilience harness for BGE-M3."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import resource
import shutil
from storage.connection import get_connection, OperationalError
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import utc_now
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.batching_optimizer import prepare_length_sorted_batches


def _get_process_memory_mb() -> float:
    """Return process RSS in megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # On macOS ru_maxrss is in bytes; on Linux in kilobytes
    if sys.platform == "darwin":
        return round(usage / (1024 * 1024), 2)
    return round(usage / 1024, 2)


def _init_stress_db(db_path: Path) -> None:
    """Initialize test SQLite database with governed WAL pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = get_connection(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stress_records (
                record_id TEXT PRIMARY KEY,
                text_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stress_hash ON stress_records(text_hash);")
        conn.commit()
    finally:
        conn.close()


def run_concurrency_stress_benchmark(
    num_workers: int = 16,
    num_requests: int = 500,
    sandbox_dir: Optional[Path] = None,
    output_report: Optional[Path] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Execute high-concurrency stress across inference and database paths."""
    cleanup_sandbox = False
    if sandbox_dir is None:
        sandbox_dir = Path(tempfile.mkdtemp(prefix="bge_m3_concurrency_"))
        cleanup_sandbox = True
    else:
        sandbox_dir.mkdir(parents=True, exist_ok=True)

    db_path = sandbox_dir / "stress_wal.sqlite"
    _init_stress_db(db_path)

    backend = get_backend("bge-m3-dev", allow_dev_backend=True)
    mem_before = _get_process_memory_mb()

    if verbose:
        print("=" * 80)
        print("  BGE-M3 CONCURRENCY STRESS & MEMORY RESILIENCE HARNESS")
        print(f"  Workers:      {num_workers}")
        print(f"  Requests:     {num_requests}")
        print(f"  Initial RSS:  {mem_before} MB")
        print(f"  Database WAL: {db_path}")
        print("=" * 80)

    # Workload generators
    test_queries = [
        "Concurrent stress test query Alpha",
        "Short assertion token",
        "A somewhat longer passage summarizing decision intelligence governance and release thresholds",
        "Identical duplicate query string",
        "Fourth variation for length sorted batching verification",
    ]

    lock_errors: List[str] = []
    latencies_ms: List[float] = []

    def _worker_task(worker_id: int, request_idx: int) -> Dict[str, Any]:
        t0 = time.perf_counter()
        op_type = "read" if request_idx % 4 != 0 else "write"

        # 1. Batch inference stress
        q_slice = test_queries[request_idx % len(test_queries) : (request_idx % len(test_queries)) + 2]
        sorted_texts, inv, _ = prepare_length_sorted_batches(q_slice)
        _ = backend.encode_many_dense_only(sorted_texts)

        # 2. Database concurrency stress
        conn = get_connection(db_path, readonly=(op_type != "write"))
        try:
            if op_type == "write":
                conn.execute(
                    "INSERT OR REPLACE INTO stress_records VALUES (?, ?, ?, ?)",
                    (
                        f"rec-{worker_id}-{request_idx}",
                        f"hash-{request_idx % 20}",
                        f"payload-data-{request_idx}",
                        utc_now(),
                    ),
                )
                conn.commit()
            else:
                rows = conn.execute(
                    "SELECT record_id, payload FROM stress_records WHERE text_hash = ? LIMIT 5",
                    (f"hash-{request_idx % 20}",),
                ).fetchall()
                _ = len(rows)
        except OperationalError as e:
            lock_errors.append(str(e))
        finally:
            conn.close()

        dur_ms = (time.perf_counter() - t0) * 1000.0
        return {"worker_id": worker_id, "duration_ms": dur_ms, "op_type": op_type}

    t0_suite = time.perf_counter()
    completed_ops = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(_worker_task, req_i % num_workers, req_i)
            for req_i in range(num_requests)
        ]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            latencies_ms.append(res["duration_ms"])
            completed_ops += 1

    t_suite_dur = round(time.perf_counter() - t0_suite, 3)
    mem_after = _get_process_memory_mb()
    mem_delta = round(mem_after - mem_before, 2)

    latencies_sorted = sorted(latencies_ms)
    p50 = round(latencies_sorted[int(len(latencies_sorted) * 0.50)], 2)
    p95 = round(latencies_sorted[int(len(latencies_sorted) * 0.95)], 2)
    p99 = round(latencies_sorted[int(len(latencies_sorted) * 0.99)], 2)
    max_lat = round(max(latencies_sorted), 2)
    throughput = round(completed_ops / max(t_suite_dur, 0.001), 2)

    if verbose:
        print(f"\n✅ Completed {completed_ops}/{num_requests} concurrent operations in {t_suite_dur}s ({throughput} ops/sec)")
        print(f"   Lock Timeouts / OperationalErrors: {len(lock_errors)}")
        print(f"   Latency Percentiles: P50={p50}ms | P95={p95}ms | P99={p99}ms | Max={max_lat}ms")
        print(f"   Memory Delta:        {mem_delta} MB (Before: {mem_before} MB -> After: {mem_after} MB)")

    result = {
        "timestamp_utc": utc_now(),
        "workers_configured": num_workers,
        "requests_executed": completed_ops,
        "suite_duration_seconds": t_suite_dur,
        "throughput_ops_per_sec": throughput,
        "lock_errors_count": len(lock_errors),
        "latencies_ms": {
            "p50": p50,
            "p95": p95,
            "p99": p99,
            "max": max_lat,
        },
        "memory_profile": {
            "initial_mb": mem_before,
            "final_mb": mem_after,
            "delta_mb": mem_delta,
            "leak_detected": mem_delta > 50.0,
        },
        "terminal_outcome": "PASS" if len(lock_errors) == 0 and completed_ops == num_requests else "FAIL",
    }

    if output_report:
        output_report.parent.mkdir(parents=True, exist_ok=True)
        output_report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if verbose:
            print(f"\n📊 Concurrency receipt written to: {output_report}")

    if cleanup_sandbox and sandbox_dir.exists():
        shutil.rmtree(sandbox_dir, ignore_errors=True)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-worker concurrency stress harness for BGE-M3.")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent worker count.")
    parser.add_argument("--requests", type=int, default=500, help="Total requests to dispatch.")
    parser.add_argument(
        "--output-report",
        type=Path,
        default=ROOT / "reports" / "benchmarks" / "bge_m3_concurrency_stress_receipt.json",
        help="Destination path for JSON receipt report.",
    )

    args = parser.parse_args()
    res = run_concurrency_stress_benchmark(
        num_workers=args.workers,
        num_requests=args.requests,
        output_report=args.output_report,
    )
    return 0 if res.get("terminal_outcome") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
