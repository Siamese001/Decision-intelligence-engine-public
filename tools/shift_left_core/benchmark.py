"""
tools/shift_left_core/benchmark.py

Pre-commit latency benchmarking utility enforcing <6.0s P95 execution budget.
"""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import time
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent


def run_benchmark(
    verify_fn: Optional[Callable[..., int]] = None,
    runs: int = 10,
    verbose: bool = False,
    output_path: Optional[Path] = None,
) -> int:
    """Benchmarks pre-commit latency over N iterations and records report."""
    if verify_fn is None:
        from tools.verify_commit import verify_commit

        target_fn = verify_commit
    else:
        target_fn = verify_fn

    print("=" * 80)
    print(f"  PRE-COMMIT LATENCY BENCHMARK ({runs} iterations)")
    print("=" * 80)

    durations: List[float] = []
    rc = 0

    for i in range(1, runs + 1):
        t0 = time.monotonic()
        cur_rc = target_fn(staged=True, quiet=True)
        dur = round(time.monotonic() - t0, 3)
        durations.append(dur)
        if cur_rc != 0 and rc == 0:
            rc = cur_rc
        print(f"  Run {i:02d}/{runs:02d}: {dur:.3f}s {'(PASS)' if cur_rc == 0 else '(FAIL)'}")

    durations_sorted = sorted(durations)
    p50 = statistics.median(durations)
    p95_idx = int(0.95 * len(durations_sorted)) - 1
    p95 = durations_sorted[max(0, p95_idx)]
    min_d = min(durations)
    max_d = max(durations)
    mean_d = statistics.mean(durations)

    print("-" * 80)
    print(f"  Benchmark Summary: Min: {min_d:.3f}s | Mean: {mean_d:.3f}s | Max: {max_d:.3f}s")
    print(f"  P50 Latency:       {p50:.3f}s")
    print(f"  P95 Latency:       {p95:.3f}s (Threshold: < 6.000s)")
    print("=" * 80)

    report: Dict[str, Any] = {
        "runs": runs,
        "p50_seconds": round(p50, 4),
        "p95_seconds": round(p95, 4),
        "min_seconds": round(min_d, 4),
        "max_seconds": round(max_d, 4),
        "mean_seconds": round(mean_d, 4),
        "durations": durations,
        "p95_passed": p95 < 6.0,
    }
    out_p = output_path or (ROOT / "reports" / "aidlc" / "w4_pre_commit_benchmark.json")
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if p95 >= 6.0:
        print(f"❌ BENCHMARK FAILED: P95 latency {p95:.3f}s exceeds 6.0s threshold.")
        return 1
    print(f"✅ BENCHMARK PASSED: P95 latency {p95:.3f}s meets <6.0s threshold.")
    return rc
