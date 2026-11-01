#!/usr/bin/env python3
"""Untracked, append-only gate execution ledger.

Records gate name, tier, wall-clock seconds, cache-hit flag, status, and metadata
to an untracked JSONL ledger (default: .local/gate_run_ledger.jsonl).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

ROOT = Path(__file__).resolve().parent.parent


def get_default_ledger_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or ROOT
    # Prefer .local directory which is untracked / gitignored
    return root / ".local" / "gate_run_ledger.jsonl"


def record_gate_run(
    gate_name: str,
    tier: str,
    wall_clock_seconds: float,
    status: str,
    cache_hit: bool = False,
    cache_decision: Optional[str] = None,
    parent_phase: Optional[str] = None,
    scope: Optional[str] = None,
    retry: int = 0,
    started_at_utc: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Records a single gate execution event into the JSONL ledger.

    Guaranteed to be no-op safe: will not raise exceptions that disrupt caller execution.
    """
    path = ledger_path or get_default_ledger_path(repo_root)
    duration = max(0.0, float(wall_clock_seconds))
    finished_at = dt.datetime.now(dt.timezone.utc)
    if started_at_utc is None:
        started_at_utc = (finished_at - dt.timedelta(seconds=duration)).isoformat()
    event: Dict[str, Any] = {
        "timestamp": finished_at.isoformat(),
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at.isoformat(),
        "gate_name": gate_name,
        "tier": tier,
        "parent_phase": parent_phase,
        "scope": scope,
        "retry": max(0, int(retry)),
        "wall_clock_seconds": round(duration, 4),
        "status": status.upper(),
        "cache_hit": bool(cache_hit),
        "cache_decision": cache_decision or ("hit" if cache_hit else "disabled"),
        "metadata": metadata or {},
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        # Fallback to no-op if disk/perms fail; never abort caller
        pass

    return event


@contextlib.contextmanager
def track_gate_execution(
    gate_name: str,
    tier: str = "readiness",
    cache_hit: bool = False,
    cache_decision: Optional[str] = None,
    parent_phase: Optional[str] = None,
    scope: Optional[str] = None,
    retry: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Generator[Callable[[str], None], None, None]:
    """Context manager to measure and record gate execution duration and status."""
    t0 = time.monotonic()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    status_holder = ["PASS"]

    def set_status(new_status: str) -> None:
        status_holder[0] = new_status

    try:
        yield set_status
    except Exception:
        status_holder[0] = "FAIL"
        raise
    finally:
        elapsed = time.monotonic() - t0
        record_gate_run(
            gate_name=gate_name,
            tier=tier,
            wall_clock_seconds=elapsed,
            status=status_holder[0],
            cache_hit=cache_hit,
            cache_decision=cache_decision,
            parent_phase=parent_phase,
            scope=scope,
            retry=retry,
            started_at_utc=started_at,
            metadata=metadata,
            repo_root=repo_root,
            ledger_path=ledger_path,
        )


def read_gate_runs(
    ledger_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    since_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Reads all recorded gate runs from the ledger, optionally filtered by timestamp."""
    path = ledger_path or get_default_ledger_path(repo_root)
    if not path.exists():
        return []

    runs: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if since_iso and entry.get("timestamp", "") < since_iso:
                        continue
                    runs.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    return runs


def summarize_gate_runs(
    ledger_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Produces statistical summary (p50, p90, p95, max, counts) across recorded gates."""
    runs = read_gate_runs(ledger_path=ledger_path, repo_root=repo_root)
    by_tier: Dict[str, List[float]] = {}
    by_gate: Dict[str, List[float]] = {}
    total_runs = len(runs)
    passes = sum(1 for r in runs if r.get("status") == "PASS")
    fails = sum(1 for r in runs if r.get("status") == "FAIL")
    cache_hits = sum(1 for r in runs if r.get("cache_hit"))

    for r in runs:
        tier = r.get("tier", "unknown")
        gate = r.get("gate_name", "unknown")
        dur = float(r.get("wall_clock_seconds", 0.0))
        by_tier.setdefault(tier, []).append(dur)
        by_gate.setdefault(gate, []).append(dur)

    def calc_percentiles(durations: List[float]) -> Dict[str, float]:
        if not durations:
            return {"count": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
        s = sorted(durations)
        n = len(s)

        def _pct(p: float) -> float:
            k = int(round(p * (n - 1)))
            return round(s[min(max(0, k), n - 1)], 4)

        return {
            "count": n,
            "p50": _pct(0.50),
            "p90": _pct(0.90),
            "p95": _pct(0.95),
            "max": round(s[-1], 4),
        }

    return {
        "total_runs": total_runs,
        "passes": passes,
        "fails": fails,
        "cache_hits": cache_hits,
        "by_tier": {tier: calc_percentiles(durs) for tier, durs in sorted(by_tier.items())},
        "by_gate": {gate: calc_percentiles(durs) for gate, durs in sorted(by_gate.items())},
    }


def benchmark_command(
    cmd: List[str],
    gate_name: str,
    tier: str = "benchmark",
    n_runs: int = 5,
    repo_root: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Executes a gate command multiple times to establish repeated benchmark percentiles."""
    root = repo_root or ROOT
    durations: List[float] = []
    successes = 0

    print(f"Benchmarking '{gate_name}' ({n_runs} runs)...", file=sys.stderr)
    for i in range(n_runs):
        t0 = time.monotonic()
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        dur = time.monotonic() - t0
        durations.append(dur)
        status = "PASS" if res.returncode == 0 else "FAIL"
        if status == "PASS":
            successes += 1
        record_gate_run(
            gate_name=gate_name,
            tier=tier,
            wall_clock_seconds=dur,
            status=status,
            cache_hit=False,
            metadata={"run_index": i + 1, "returncode": res.returncode},
            repo_root=root,
            ledger_path=ledger_path,
        )

    s = sorted(durations)
    n = len(s)
    p50 = s[n // 2]
    p90 = s[min(int(0.9 * n), n - 1)]
    p95 = s[min(int(0.95 * n), n - 1)]

    return {
        "gate_name": gate_name,
        "command": cmd,
        "n_runs": n_runs,
        "success_rate": round(successes / n_runs, 3),
        "durations_seconds": [round(d, 4) for d in durations],
        "p50_seconds": round(p50, 4),
        "p90_seconds": round(p90, 4),
        "p95_seconds": round(p95, 4),
        "min_seconds": round(s[0], 4),
        "max_seconds": round(s[-1], 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate Run Ledger")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    list_p = subparsers.add_parser("list", help="List recorded gate runs")
    list_p.add_argument("--since", help="Filter events since ISO timestamp")
    list_p.add_argument("--limit", type=int, default=50, help="Max entries to display")

    subparsers.add_parser("summary", help="Summarize gate run durations")

    bench_p = subparsers.add_parser("benchmark", help="Run benchmark loops for a command")
    bench_p.add_argument("--gate-name", required=True, help="Name of the gate")
    bench_p.add_argument("--tier", default="benchmark", help="Tier classification")
    bench_p.add_argument("--runs", type=int, default=5, help="Number of repetitions")
    bench_p.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run")

    args = parser.parse_args()

    if args.subcommand == "list":
        runs = read_gate_runs(since_iso=args.since)
        for r in runs[-args.limit :]:
            print(
                f"{r['timestamp']} | {r['tier']:<12} | {r['gate_name']:<35} | {r['status']:<4} | {r['wall_clock_seconds']:.3f}s | cache={r['cache_hit']}"
            )
        print(f"\nTotal runs displayed: {min(len(runs), args.limit)} of {len(runs)}")
        return 0

    if args.subcommand == "summary":
        summary = summarize_gate_runs()
        print(json.dumps(summary, indent=2))
        return 0

    if args.subcommand == "benchmark":
        cmd = args.cmd
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            print("Error: empty command to benchmark", file=sys.stderr)
            return 1
        res = benchmark_command(cmd, gate_name=args.gate_name, tier=args.tier, n_runs=args.runs)
        print(json.dumps(res, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
