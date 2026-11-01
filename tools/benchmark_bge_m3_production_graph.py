#!/usr/bin/env python3
"""Benchmark BGE-M3 sidecar build performance against production graph corpus."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from storage.connection import get_connection
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, utc_now
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.records import render_retrieval_records
from dual_pipeline.index_core.sidecar import build_sidecar, validate_sidecar


def _create_sandbox_graph_with_records(
    src_graph: Path,
    dst_graph: Path,
    limit: Optional[int] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Create a minimal sandbox graph database containing a subset of retrieval records."""
    dst_graph.parent.mkdir(parents=True, exist_ok=True)
    if dst_graph.exists():
        dst_graph.unlink()

    # Exact physical clone of the source graph
    shutil.copy2(src_graph, dst_graph)

    if limit is not None:
        conn = get_connection(dst_graph)
        try:
            conn.execute("PRAGMA foreign_keys = OFF;")
            aids = [r[0] for r in conn.execute("SELECT assertion_id FROM assertion LIMIT ?", (limit,)).fetchall()]
            if aids:
                placeholders = ",".join("?" for _ in aids)
                conn.execute(
                    f"DELETE FROM assertion WHERE assertion_id NOT IN ({placeholders})",  # noqa: S608
                    aids,
                )
                conn.commit()
        finally:
            conn.close()

    records = render_retrieval_records(dst_graph, "benchmark_snapshot")
    return len(records), records


def _simulate_differential_mutation(
    graph_path: Path,
    mutate_ratio: float = 0.10,
) -> Tuple[int, int]:
    """Simulate realistic changes: mutate text for half, re-mint IDs for unchanged text."""
    conn = get_connection(graph_path)
    mutated_count = 0
    reminted_count = 0
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        rows = conn.execute("SELECT assertion_id, scope_text, assertion_fingerprint FROM assertion").fetchall()
        if not rows:
            return 0, 0
        total = len(rows)
        target_count = max(2, int(total * mutate_ratio))
        # 1. Mutate text
        half = target_count // 2
        for aid, scope_text, fp in rows[:half]:
            new_scope = f"{scope_text} [MODIFIED: {utc_now()}]"
            new_fp = digest(f"{fp}-mutated")
            conn.execute(
                "UPDATE assertion SET scope_text = ?, assertion_fingerprint = ? WHERE assertion_id = ?",
                (new_scope, new_fp, aid),
            )
            mutated_count += 1
        # 2. Re-mint ID with unchanged text
        for aid, scope_text, fp in rows[half:target_count]:
            new_aid = f"{aid}-reminted"
            new_fp = digest(f"{fp}-reminted")
            conn.execute(
                "UPDATE assertion SET assertion_id = ?, assertion_fingerprint = ? WHERE assertion_id = ?",
                (new_aid, new_fp, aid),
            )
            reminted_count += 1
        conn.commit()
    finally:
        conn.close()
    return mutated_count, reminted_count


def _verify_dense_norms(sidecar_path: Path) -> Tuple[float, float, int]:
    """Check L2 norms of dense vectors in sidecar."""
    conn = get_connection(sidecar_path, readonly=True)
    try:
        rows = conn.execute("SELECT vector FROM dense_vector").fetchall()
        if not rows:
            return 0.0, 0.0, 0
        norms = []
        for (blob,) in rows:
            arr = np.frombuffer(blob, dtype="<f4")
            if not np.isfinite(arr).all():
                raise ValueError("Non-finite values found in dense vector buffer")
            norm = float(np.linalg.norm(arr))
            norms.append(norm)
        return float(min(norms)), float(max(norms)), len(norms)
    finally:
        conn.close()


def run_production_graph_benchmark(
    graph_path: Path,
    records_limit: Optional[int] = None,
    sandbox_dir: Optional[Path] = None,
    output_report: Optional[Path] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Execute complete cold vs. warm sidecar benchmarking against production graph."""
    cleanup_sandbox = False
    if sandbox_dir is None:
        sandbox_dir = Path(tempfile.mkdtemp(prefix="bge_m3_prod_bench_"))
        cleanup_sandbox = True
    else:
        sandbox_dir.mkdir(parents=True, exist_ok=True)

    try:
        if verbose:
            print("=" * 80)
            print("  BGE-M3 PRODUCTION GRAPH A/B BENCHMARK & THROUGHPUT PROFILER")
            print(f"  Source Graph: {graph_path}")
            print(f"  Sandbox:      {sandbox_dir}")
            print(f"  Record Limit: {records_limit or 'Full Corpus'}")
            print("=" * 80)

        # 1. Provision sandbox graph
        cold_graph_p = sandbox_dir / "cold_graph.sqlite"
        rec_count, _ = _create_sandbox_graph_with_records(graph_path, cold_graph_p, limit=records_limit)
        if verbose:
            print(f"Loaded {rec_count} production records into sandbox graph.")

        # 2. Acquire real BGE-M3 backend
        backend = get_backend("bge-m3-dev", allow_dev_backend=True)

        # 3. Cold Build
        cold_sidecar_p = sandbox_dir / "cold_sidecar.sqlite"
        if verbose:
            print("\nExecuting Cold Sidecar Build (0% prior vectors)...")
        t_cold_start = time.perf_counter()
        cold_manifest = build_sidecar(
            graph_path=cold_graph_p,
            sidecar_path=cold_sidecar_p,
            generation_id="gen-bench-cold",
            graph_snapshot="snap-bench-cold",
            backend=backend,
        )
        t_cold_dur = round(time.perf_counter() - t_cold_start, 3)
        cold_throughput = round(rec_count / max(t_cold_dur, 0.001), 2)

        # Validate cold sidecar
        cold_val = validate_sidecar(cold_sidecar_p)
        min_norm, max_norm, norm_count = _verify_dense_norms(cold_sidecar_p)

        if verbose:
            print(f"✅ Cold Build Finished in {t_cold_dur}s ({cold_throughput} rec/s)")
            print(f"   Sidecar Validation: {cold_val.get('status')} | Vector Norms: [{min_norm:.3f}, {max_norm:.3f}]")

        # 4. Warm Differential Build (with simulated mutations)
        warm_graph_p = sandbox_dir / "warm_graph.sqlite"
        shutil.copy2(cold_graph_p, warm_graph_p)
        mutated, reminted = _simulate_differential_mutation(warm_graph_p, mutate_ratio=0.10)

        warm_sidecar_p = sandbox_dir / "warm_sidecar.sqlite"
        if verbose:
            print(f"\nExecuting Warm Differential Build ({mutated} mutated, {reminted} re-minted IDs)...")
        t_warm_start = time.perf_counter()
        warm_manifest = build_sidecar(
            graph_path=warm_graph_p,
            sidecar_path=warm_sidecar_p,
            generation_id="gen-bench-warm",
            graph_snapshot="snap-bench-warm",
            backend=backend,
            prior_sidecar_path=cold_sidecar_p,
        )
        t_warm_dur = round(time.perf_counter() - t_warm_start, 3)
        warm_throughput = round(rec_count / max(t_warm_dur, 0.001), 2)
        speedup = round(t_cold_dur / max(t_warm_dur, 0.001), 2)

        warm_val = validate_sidecar(warm_sidecar_p)
        w_min_norm, w_max_norm, _ = _verify_dense_norms(warm_sidecar_p)

        reused_count = warm_manifest.get("reused_records", warm_manifest.get("differential_reused_count", 0))
        reuse_rate = round(reused_count / max(rec_count, 1), 4)

        if verbose:
            print(f"✅ Warm Build Finished in {t_warm_dur}s ({warm_throughput} rec/s, speedup: {speedup}x)")
            print(f"   Differential Reused: {reused_count}/{rec_count} ({reuse_rate:.1%})")
            print(f"   Sidecar Validation:  {warm_val.get('status')} | Vector Norms: [{w_min_norm:.3f}, {w_max_norm:.3f}]")

        result = {
            "timestamp_utc": utc_now(),
            "records_evaluated": rec_count,
            "cold_build": {
                "duration_seconds": t_cold_dur,
                "throughput_records_per_sec": cold_throughput,
                "sidecar_validation": cold_val.get("status"),
                "dense_norm_range": [min_norm, max_norm],
            },
            "warm_differential_build": {
                "duration_seconds": t_warm_dur,
                "throughput_records_per_sec": warm_throughput,
                "speedup_ratio": speedup,
                "differential_reused_count": reused_count,
                "differential_reuse_rate": reuse_rate,
                "mutated_records_count": mutated,
                "reminted_records_count": reminted,
                "sidecar_validation": warm_val.get("status"),
                "dense_norm_range": [w_min_norm, w_max_norm],
            },
            "hardware_device": getattr(backend, "_device", "mps"),
            "terminal_outcome": "PASS" if speedup >= 2.0 and cold_val.get("status") == "PASS" else "FAIL",
        }

        if output_report:
            output_report.parent.mkdir(parents=True, exist_ok=True)
            output_report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            if verbose:
                print(f"\n📊 Benchmark profile written to: {output_report}")

        return result
    finally:
        if cleanup_sandbox and sandbox_dir.exists():
            shutil.rmtree(sandbox_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark BGE-M3 sidecar build against production graph.")
    parser.add_argument(
        "--graph-path",
        type=Path,
        default=ROOT / ".local" / "dual_pipeline" / "generations" / "generation-ed8ef28b770e8c0fde837a2dd72d" / "graph.sqlite",
        help="Path to production graph SQLite database.",
    )
    parser.add_argument("--records", type=int, default=500, help="Number of records to benchmark.")
    parser.add_argument("--sandbox", action="store_true", help="Use ephemeral sandbox directory.")
    parser.add_argument(
        "--output-report",
        type=Path,
        default=ROOT / "reports" / "benchmarks" / "bge_m3_production_graph_profile.json",
        help="Destination path for JSON benchmark report.",
    )

    args = parser.parse_args()
    if not args.graph_path.exists():
        print(f"❌ Error: Graph database not found at {args.graph_path}", file=sys.stderr)
        return 1

    res = run_production_graph_benchmark(
        graph_path=args.graph_path,
        records_limit=args.records,
        output_report=args.output_report,
    )
    return 0 if res.get("terminal_outcome") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
