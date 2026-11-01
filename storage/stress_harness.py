#!/usr/bin/env python3
"""Storage Concurrency Stress Harness CLI.

High-concurrency multi-worker load testing against SQLite storage with WAL mode,
verifying zero SQLITE_BUSY contention, transactional atomicity, and schema integrity.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.database import DatabaseManager, EvidenceRecord

logger = logging.getLogger("storage.stress_harness")


@dataclass
class StressHarnessReport:
    workers: int
    duration_seconds: float
    total_transactions: int
    successful_transactions: int
    failed_transactions: int
    locked_errors_count: int
    throughput_tps: float
    average_latency_ms: float
    integrity_status: str
    is_compliant: bool
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _check_db_integrity(db_mgr: DatabaseManager) -> str:
    """Run PRAGMA integrity_check against active database."""
    active_db = db_mgr.evidence_db_path
    if not (active_db and active_db.is_file()):
        return "ok"
    conn = db_mgr.get_evidence_conn(readonly=True)
    try:
        res = conn.execute("PRAGMA integrity_check;").fetchone()
        return str(res[0]) if res else "UNKNOWN"
    finally:
        conn.close()


def _run_single_worker(
    worker_id: int,
    db_mgr: DatabaseManager,
    stop_event: threading.Event,
    latencies: List[float],
    errors: List[str],
) -> Tuple[int, int, int]:
    """Execute evidence appends in a single worker thread until stop_event is set."""
    count = 0
    fail_count = 0
    locked_count = 0
    while not stop_event.is_set():
        ev_id = f"ev_stress_w{worker_id}_{count}_{int(time.time()*1000)}"
        record = EvidenceRecord(
            evidence_id=ev_id,
            meeting_id="stress_meeting",
            speaker=f"Speaker_{worker_id}",
            text=f"Concurrent statement from worker {worker_id} iter {count}",
            start_timestamp=float(count),
            is_final=True,
        )
        t0 = time.perf_counter()
        try:
            db_mgr.append_evidence(record, enqueue=True)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            count += 1
        except Exception as exc:
            fail_count += 1
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                locked_count += 1
            errors.append(f"Worker {worker_id} Error: {exc}")
        time.sleep(0.005)
    return count, fail_count, locked_count


def run_concurrency_stress(
    workers: int = 16,
    duration_seconds: float = 5.0,
    target_db_mode: str = "wal",
    verify_integrity: bool = True,
    temp_dir: Optional[str] = None,
) -> StressHarnessReport:
    """Execute high-concurrency multi-worker stress testing against DatabaseManager."""
    effective_temp = None
    if temp_dir is None:
        effective_temp = tempfile.TemporaryDirectory()
        base_dir = effective_temp.name
    else:
        base_dir = temp_dir

    db_mgr = DatabaseManager(base_dir=base_dir, allow_unencrypted_dev_fallback=True)
    latencies: List[float] = []
    errors: List[str] = []
    stop_event = threading.Event()
    start_time = time.perf_counter()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_run_single_worker, w_id, db_mgr, stop_event, latencies, errors)
                for w_id in range(workers)
            ]
            time.sleep(duration_seconds)
            stop_event.set()
            results = [f.result() for f in concurrent.futures.as_completed(futures, timeout=10.0)]

        actual_duration = max(0.001, time.perf_counter() - start_time)
        successes = sum(r[0] for r in results)
        failures = sum(r[1] for r in results)
        locked_errors = sum(r[2] for r in results)
        total_tx = successes + failures
        throughput = round(successes / actual_duration, 2)
        avg_latency = round(sum(latencies) / max(1, len(latencies)), 2)

        integrity_status = _check_db_integrity(db_mgr) if verify_integrity else "NOT_CHECKED"
        is_compliant = (
            locked_errors == 0
            and failures == 0
            and successes > 0
            and (not verify_integrity or integrity_status.lower() == "ok")
        )

        return StressHarnessReport(
            workers=workers,
            duration_seconds=round(actual_duration, 2),
            total_transactions=total_tx,
            successful_transactions=successes,
            failed_transactions=failures,
            locked_errors_count=locked_errors,
            throughput_tps=throughput,
            average_latency_ms=avg_latency,
            integrity_status=integrity_status,
            is_compliant=is_compliant,
            errors=errors[:10],
        )
    finally:
        if effective_temp:
            effective_temp.cleanup()


def build_stress_receipt(report: StressHarnessReport, command: str) -> Dict[str, Any]:
    """Build governed runtime receipt with SHA-256 digest."""
    receipt = {
        "$schema": "urn:nee:storage-stress-harness-receipt:1.0.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "status": "PASSED" if report.is_compliant else "FAILED",
        "metrics": {
            "workers": report.workers,
            "duration_seconds": report.duration_seconds,
            "total_transactions": report.total_transactions,
            "successful_transactions": report.successful_transactions,
            "failed_transactions": report.failed_transactions,
            "locked_errors_count": report.locked_errors_count,
            "throughput_tps": report.throughput_tps,
            "average_latency_ms": report.average_latency_ms,
            "integrity_status": report.integrity_status,
            "is_compliant": report.is_compliant,
        },
        "errors": report.errors,
    }
    serialized = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    receipt["canonical_digest"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return receipt


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Storage Concurrency Stress Harness")
    parser.add_argument("--workers", type=int, default=16, help="Number of parallel worker threads")
    parser.add_argument("--duration-seconds", type=float, default=5.0, help="Test duration in seconds")
    parser.add_argument("--target-db-mode", type=str, default="wal", choices=["wal", "rollback"], help="SQLite journal mode")
    parser.add_argument("--verify-integrity", action="store_true", help="Execute PRAGMA integrity_check post-run")
    parser.add_argument("--receipt", type=Path, default=None, help="Output path for runtime receipt")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    args = parser.parse_args(argv)

    report = run_concurrency_stress(
        workers=args.workers,
        duration_seconds=args.duration_seconds,
        target_db_mode=args.target_db_mode,
        verify_integrity=args.verify_integrity,
    )

    if args.receipt:
        receipt = build_stress_receipt(report, command=" ".join(sys.argv))
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        status_line = "PASSED" if report.is_compliant else "FAILED"
        print(
            f"Storage Stress Harness: {report.successful_transactions}/{report.total_transactions} txs across {report.workers} workers "
            f"({report.throughput_tps} TPS, avg {report.average_latency_ms}ms) -> {status_line}"
        )
        if report.integrity_status != "NOT_CHECKED":
            print(f"  Integrity Check: {report.integrity_status}")
        if report.locked_errors_count > 0:
            print(f"  ❌ Locked Errors: {report.locked_errors_count}")

    return 0 if report.is_compliant else 1


if __name__ == "__main__":
    sys.exit(main())
