#!/usr/bin/env python3
"""Phase-Timeline Latency Benchmark Harness for Live Meeting Recording.

Measures wall-clock timings across interpreter, import, recorder, spooler,
and MLX readiness, emitting a cryptographic JSON receipt with summary statistics.
Enforces Wave 5 latency and diagnosis invariants:
- Monotonic source-bound PCM evidence.
- Missing PCM stays missing (never substituted with wall-clock time).
- Failed starts and missing PCM are explicitly tracked in the denominator.
- Independent warm and cold cohort reporting.
- Strict budget qualification gating.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda it, **_: it  # type: ignore # noqa: E731

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_latency_core.probe import run_isolated_probe_cycle
from tools.benchmark_latency_core.stats import (
    compute_latency_stats,
    evaluate_startup_budget_qualification,
)


def _measure_cmd_overhead(python_bin: str, cmd_str: str) -> float:
    t0 = time.perf_counter()
    res = subprocess.run([python_bin, "-c", cmd_str], capture_output=True, check=False, cwd=str(ROOT))
    return round(time.perf_counter() - t0, 4) if res.returncode == 0 else 0.0


def _measure_interpreter_overhead(python_bin: str) -> float:
    return _measure_cmd_overhead(python_bin, "import sys; sys.exit(0)")


def _measure_cli_import_overhead(python_bin: str) -> float:
    return _measure_cmd_overhead(python_bin, "import dual_pipeline.cli; sys.exit(0)")


def run_benchmark(
    mode: str = "mock",
    runs: int = 5,
    cold_runs: int = 1,
    out_path: Optional[Path] = None,
    compare_path: Optional[Path] = None,
    project: str = "project_nexus",
    meeting_title: str = "Latency Benchmark Session",
) -> Dict[str, Any]:
    """Runs N benchmark cycles and compiles a governed latency receipt with cohort isolation."""
    python_bin = str(ROOT / ".venv" / "bin" / "python")
    if not os.path.exists(python_bin):
        python_bin = sys.executable

    print(f"\n🚀 Launching Record-Start Latency Benchmark [mode={mode}, total_runs={runs}, cold_runs={cold_runs}]")
    print(f"   Python Interpreter: {python_bin}")
    print(f"   Repository Root:    {ROOT}")

    t_interp = _measure_interpreter_overhead(python_bin)
    t_cli_import_isolated = _measure_cli_import_overhead(python_bin)

    all_records: List[Dict[str, Any]] = []
    pbar = tqdm(range(1, runs + 1), desc="Benchmarking Startup Latency", unit="run")

    for i in pbar:
        cohort = "cold" if i <= cold_runs else "warm"
        cycle_result = run_isolated_probe_cycle(
            python_bin=python_bin,
            repo_root=ROOT,
            mode=mode,
            project=project,
            meeting_title=meeting_title,
            run_idx=i,
            cohort=cohort,
        )
        cycle_result["t_interpreter_s"] = t_interp
        all_records.append(cycle_result)

        if cycle_result.get("status") == "PASS":
            pcm_val = cycle_result.get("t_first_pcm_byte_s")
            pcm_str = f"{pcm_val:.2f}s" if pcm_val is not None else "MISSING"
            pbar.set_postfix({
                "cohort": cohort,
                "confirmed_s": f"{cycle_result.get('t_recording_confirmed_s', 0):.2f}s",
                "pcm_s": pcm_str,
            })
        else:
            pbar.set_postfix({"cohort": cohort, "status": "FAILED"})

    # Denominator tracking
    total_attempts = len(all_records)
    successful_runs = [r for r in all_records if r.get("status") == "PASS" and r.get("t_first_pcm_byte_s") is not None]
    failed_runs = [r for r in all_records if r not in successful_runs]
    missing_pcm_count = sum(1 for r in all_records if r.get("t_first_pcm_byte_s") is None)

    keys = [
        "t_interpreter_s",
        "t_cli_import_s",
        "t_capture_spawn_s",
        "t_first_pcm_byte_s",
        "t_first_nonzero_sample_s",
        "t_recorder_init_s",
        "t_mlx_ready_s",
        "t_recording_confirmed_s",
        "t_total_startup_wall_s",
    ]

    # Compute cohort statistics
    warm_records = [r for r in all_records if r.get("cohort") == "warm"]
    cold_records = [r for r in all_records if r.get("cohort") == "cold"]

    def _stats_for_records(recs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        stats_map: Dict[str, Dict[str, Any]] = {}
        for k in keys:
            vals = [r.get(k) for r in recs]
            stats_map[k] = compute_latency_stats(vals)
        return stats_map

    warm_stats = _stats_for_records(warm_records if warm_records else all_records)
    cold_stats = _stats_for_records(cold_records) if cold_records else None
    overall_stats = _stats_for_records(all_records)

    qualification = evaluate_startup_budget_qualification(
        warm_stats=warm_stats,
        cold_stats=cold_stats,
        total_missing_pcm=missing_pcm_count,
        total_failed_starts=len(failed_runs),
    )

    receipt_body: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "denominator_accounting": {
            "total_attempts": total_attempts,
            "successful_starts": len(successful_runs),
            "failed_starts": len(failed_runs),
            "missing_pcm_count": missing_pcm_count,
            "success_rate": round(len(successful_runs) / total_attempts, 4) if total_attempts > 0 else 0.0,
        },
        "environment": {
            "os": sys.platform,
            "python_version": sys.version.split()[0],
            "interpreter_overhead_s": t_interp,
            "cli_import_overhead_s": t_cli_import_isolated,
            "has_real_audio": bool(os.environ.get("NEE_REAL_AUDIO") == "1"),
            "has_real_mlx": bool(os.environ.get("NEE_REAL_MLX") == "1"),
        },
        "qualification": qualification,
        "statistics": {
            "overall": overall_stats,
            "warm_cohort": warm_stats,
            "cold_cohort": cold_stats,
        },
        "runs": all_records,
    }

    serialized = json.dumps(receipt_body, sort_keys=True, indent=2)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    receipt_body["receipt_sha256"] = digest

    # Print summary
    verdict = "✅ QUALIFIED" if qualification["qualified"] else "❌ QUALIFICATION FAILED"
    header = f"\n{'='*80}\n📊 RECORD-START LATENCY BENCHMARK REPORT [mode={mode}] — {verdict}\n   Receipt Digest: {digest[:16]}...\n{'='*80}"
    cols = f"{'Phase / Metric':<30} | {'P50 (s)':<8} | {'P90 (s)':<8} | {'P95 (s)':<8} | {'Mean (s)':<8} | {'Max (s)':<8}\n{'-'*80}"
    rows = "\n".join(
        f"{k:<30} | {warm_stats[k]['p50']:<8.3f} | {warm_stats[k]['p90']:<8.3f} | {warm_stats[k]['p95']:<8.3f} | {warm_stats[k]['mean']:<8.3f} | {warm_stats[k]['max']:<8.3f}"
        for k in keys
    )
    gates_summary = "\n".join(
        f"   - {g_name}: measured={g.get('measured_s', g.get('measured_missing', g.get('measured_failed', g.get('measured_max_s'))))} (threshold={g.get('threshold_s', g.get('threshold_allowed'))}) -> {'PASS' if g['pass'] else 'FAIL'}"
        for g_name, g in qualification["gates"].items()
    )
    print(f"{header}\n[WARM COHORT METRICS]\n{cols}\n{rows}\n\n[QUALIFICATION GATES]\n{gates_summary}\n{'='*80}")

    if out_path:
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(receipt_body, indent=2), encoding="utf-8")
        print(f"✅ Baseline receipt written to: {out_p}")

    return receipt_body


def main() -> int:
    parser = argparse.ArgumentParser(description="Record-Start Latency Benchmark Harness")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock", help="Execution mode (default: mock)")
    parser.add_argument("--runs", type=int, default=5, help="Number of benchmark iterations (default: 5)")
    parser.add_argument("--cold-runs", type=int, default=1, help="Number of cold iterations (default: 1)")
    parser.add_argument("--out", type=str, default="reports/record_start_latency_baseline.json", help="Path to save output JSON receipt")
    parser.add_argument("--compare", type=str, default=None, help="Path to baseline JSON receipt for comparison")
    parser.add_argument("--project", type=str, default="project_nexus", help="Project name (default: project_nexus)")
    parser.add_argument("--meeting", type=str, default="Latency Benchmark Session", help="Meeting title")

    args = parser.parse_args()
    try:
        run_benchmark(
            mode=args.mode,
            runs=args.runs,
            cold_runs=args.cold_runs,
            out_path=Path(args.out) if args.out else None,
            compare_path=Path(args.compare) if args.compare else None,
            project=args.project,
            meeting_title=args.meeting,
        )
        return 0
    except Exception as e:
        sys.stderr.write(f"❌ Benchmark failed: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
