#!/usr/bin/env python3
"""Benchmark and verify incremental ingestion replay, staged vectors, and content-addressable reuse."""
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, utc_now
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.sidecar import build_sidecar, validate_sidecar
from dual_pipeline.index_core.vector_staging import encode_and_stage_records, init_vector_staging_db


class CallTrackingBackend:
    """Wrapper around real BGE-M3 backend to track neural forward pass calls."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.forward_pass_count = 0
        self.total_strings_encoded = 0

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def model_revision(self) -> str:
        return self._inner.model_revision

    @property
    def model_manifest_digest(self) -> str:
        return self._inner.model_manifest_digest

    @property
    def backend_id(self) -> str:
        return self._inner.backend_id

    @property
    def production_qualified(self) -> bool:
        return getattr(self._inner, "production_qualified", True)

    def encode(self, text: str) -> Dict[str, Any]:
        self.forward_pass_count += 1
        self.total_strings_encoded += 1
        return self._inner.encode(text)

    def encode_many(self, texts: List[str]) -> List[Dict[str, Any]]:
        if not texts:
            return []
        self.forward_pass_count += 1
        self.total_strings_encoded += len(texts)
        return self._inner.encode_many(texts)

    def encode_many_dense_only(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        self.forward_pass_count += 1
        self.total_strings_encoded += len(texts)
        return self._inner.encode_many_dense_only(texts)


def _setup_sandbox_corpus(
    base_graph_template: Path,
    target_graph: Path,
    count: int = 50,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Create initial graph and incremental replay graph with re-minted IDs and modifications."""
    target_graph.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base_graph_template, target_graph)

    conn = get_connection(target_graph)
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        a_rows = conn.execute(
            "SELECT assertion_id, scope_text, assertion_fingerprint FROM assertion LIMIT ?",
            (count,),
        ).fetchall()
        if not a_rows:
            raise RuntimeError("No assertions found in source graph template")

        aids = [r[0] for r in a_rows]
        placeholders = ",".join("?" for _ in aids)
        conn.execute(
            f"DELETE FROM assertion WHERE assertion_id NOT IN ({placeholders})",  # noqa: S608
            aids,
        )
        conn.commit()
    finally:
        conn.close()

    from dual_pipeline.index_core.records import render_retrieval_records
    initial_records = render_retrieval_records(target_graph, "initial_snap")

    return initial_records, a_rows


def _build_replay_graph(
    source_graph: Path,
    replay_graph: Path,
    mutated_ratio: float = 0.10,
    reminted_ratio: float = 0.30,
) -> Tuple[int, int, int]:
    """Create replay graph simulating incremental meeting ingestion with re-minted IDs."""
    shutil.copy2(source_graph, replay_graph)
    conn = get_connection(replay_graph)
    unchanged_cnt = 0
    reminted_cnt = 0
    mutated_cnt = 0

    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        rows = conn.execute(
            "SELECT assertion_id, scope_text, assertion_fingerprint FROM assertion"
        ).fetchall()
        total = len(rows)
        mut_end = max(1, int(total * mutated_ratio))
        remint_end = mut_end + max(1, int(total * reminted_ratio))

        # 1. Mutate text (genuinely new)
        for aid, scope, fp in rows[:mut_end]:
            conn.execute(
                "UPDATE assertion SET scope_text = ?, assertion_fingerprint = ? WHERE assertion_id = ?",
                (f"{scope} [NEW_MEETING_UPDATE: {utc_now()}]", digest(f"{fp}-mut"), aid),
            )
            mutated_cnt += 1

        # 2. Re-mint ID with unchanged text (content differential)
        for aid, scope, fp in rows[mut_end:remint_end]:
            conn.execute(
                "UPDATE assertion SET assertion_id = ?, assertion_fingerprint = ? WHERE assertion_id = ?",
                (f"{aid}-reminted-session", digest(f"{fp}-remint"), aid),
            )
            reminted_cnt += 1

        # 3. Completely unchanged
        unchanged_cnt = total - mutated_cnt - reminted_cnt
        conn.commit()
    finally:
        conn.close()

    return unchanged_cnt, reminted_cnt, mutated_cnt


def run_ingest_replay_benchmark(
    base_graph: Path,
    records_count: int = 50,
    sandbox_dir: Optional[Path] = None,
    output_report: Optional[Path] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Run incremental ingestion replay profiling neural forward pass savings."""
    cleanup_sandbox = False
    if sandbox_dir is None:
        sandbox_dir = Path(tempfile.mkdtemp(prefix="bge_m3_replay_bench_"))
        cleanup_sandbox = True
    else:
        sandbox_dir.mkdir(parents=True, exist_ok=True)

    try:
        if verbose:
            print("=" * 80)
            print("  BGE-M3 INCREMENTAL INGESTION REPLAY & STAGED VECTOR PROFILER")
            print(f"  Base Graph:    {base_graph}")
            print(f"  Sandbox:       {sandbox_dir}")
            print(f"  Records Target: {records_count}")
            print("=" * 80)

        real_backend = get_backend("bge-m3-dev", allow_dev_backend=True)
        tracking_backend = CallTrackingBackend(real_backend)

        # 1. Initial build (prior sidecar)
        initial_graph_p = sandbox_dir / "initial_graph.sqlite"
        initial_recs, _ = _setup_sandbox_corpus(base_graph, initial_graph_p, count=records_count)
        initial_sidecar_p = sandbox_dir / "initial_sidecar.sqlite"

        if verbose:
            print(f"\nBuilding Initial Prior Sidecar ({len(initial_recs)} records)...")
        t0_init = time.perf_counter()
        build_sidecar(
            graph_path=initial_graph_p,
            sidecar_path=initial_sidecar_p,
            generation_id="gen-init",
            graph_snapshot="snap-init",
            backend=tracking_backend,
        )
        t_init_dur = round(time.perf_counter() - t0_init, 3)
        init_calls = tracking_backend.forward_pass_count
        init_strings = tracking_backend.total_strings_encoded

        if verbose:
            print(f"✅ Initial Sidecar Built in {t_init_dur}s ({init_calls} forward passes, {init_strings} texts)")

        # 2. Stage vectors for replay
        replay_graph_p = sandbox_dir / "replay_graph.sqlite"
        unchanged, reminted, mutated = _build_replay_graph(
            initial_graph_p, replay_graph_p, mutated_ratio=0.10, reminted_ratio=0.30
        )
        from dual_pipeline.index_core.records import render_retrieval_records
        replay_recs = render_retrieval_records(replay_graph_p, "snap-replay")

        staging_db_p = sandbox_dir / "vector_staging.sqlite"
        init_vector_staging_db(staging_db_p, tracking_backend)

        # Pre-stage mutated records to simulate ParallelDocumentIngestor background staging
        mutated_recs = [r for r in replay_recs if "[NEW_MEETING_UPDATE:" in r.get("retrieval_text", "")]
        if mutated_recs:
            if verbose:
                print(f"\nSimulating ParallelDocumentIngestor Pre-Staging ({len(mutated_recs)} mutated records)...")
            encode_and_stage_records(
                staging_db_path=staging_db_p,
                records=mutated_recs,
                backend=tracking_backend,
            )

        # 3. Incremental Replay Sidecar Build
        replay_sidecar_p = sandbox_dir / "replay_sidecar.sqlite"
        tracking_backend.forward_pass_count = 0
        tracking_backend.total_strings_encoded = 0

        if verbose:
            print(f"\nExecuting Incremental Replay Build (unchanged: {unchanged}, reminted: {reminted}, mutated: {mutated})...")
        t0_replay = time.perf_counter()
        replay_manifest = build_sidecar(
            graph_path=replay_graph_p,
            sidecar_path=replay_sidecar_p,
            generation_id="gen-replay",
            graph_snapshot="snap-replay",
            backend=tracking_backend,
            prior_sidecar_path=initial_sidecar_p,
            staged_vector_paths=[staging_db_p],
        )
        t_replay_dur = round(time.perf_counter() - t0_replay, 3)

        replay_forward_passes = tracking_backend.forward_pass_count
        replay_strings_encoded = tracking_backend.total_strings_encoded
        replay_val = validate_sidecar(replay_sidecar_p)

        reused_records = replay_manifest.get("reused_records", 0)
        staged_records = replay_manifest.get("staged_records", 0)
        total_eval_recs = len(replay_recs)
        savings_rate = round((total_eval_recs - replay_strings_encoded) / max(total_eval_recs, 1), 4)

        if verbose:
            print(f"✅ Replay Build Finished in {t_replay_dur}s")
            print(f"   Forward Passes Executed: {replay_forward_passes} (Texts Encoded: {replay_strings_encoded})")
            print(f"   Prior Differential Hits: {reused_records}/{total_eval_recs}")
            print(f"   Staged Vector Hits:      {staged_records}/{total_eval_recs}")
            print(f"   Neural Workload Savings: {savings_rate:.1%}")
            print(f"   Sidecar Validation:      {replay_val.get('status')}")

        result = {
            "timestamp_utc": utc_now(),
            "records_evaluated": total_eval_recs,
            "composition": {
                "unchanged_records": unchanged,
                "reminted_id_records": reminted,
                "mutated_records": mutated,
            },
            "initial_build": {
                "duration_seconds": t_init_dur,
                "forward_passes": init_calls,
                "strings_encoded": init_strings,
            },
            "replay_build": {
                "duration_seconds": t_replay_dur,
                "forward_passes": replay_forward_passes,
                "strings_encoded": replay_strings_encoded,
                "differential_reused": reused_records,
                "staged_reused": staged_records,
                "neural_savings_rate": savings_rate,
                "sidecar_validation": replay_val.get("status"),
            },
            "terminal_outcome": "PASS" if replay_val.get("status") == "PASS" and savings_rate >= 0.80 else "FAIL",
        }

        if output_report:
            output_report.parent.mkdir(parents=True, exist_ok=True)
            output_report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            if verbose:
                print(f"\n📊 Replay report written to: {output_report}")

        return result
    finally:
        if cleanup_sandbox and sandbox_dir.exists():
            shutil.rmtree(sandbox_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark BGE-M3 incremental ingestion replay.")
    parser.add_argument(
        "--base-graph",
        type=Path,
        default=ROOT / ".local" / "dual_pipeline" / "generations" / "generation-ed8ef28b770e8c0fde837a2dd72d" / "graph.sqlite",
        help="Path to production graph template.",
    )
    parser.add_argument("--records", type=int, default=50, help="Number of records to benchmark.")
    parser.add_argument("--verify", action="store_true", help="Verify replay savings.")
    parser.add_argument(
        "--output-report",
        type=Path,
        default=ROOT / "reports" / "benchmarks" / "bge_m3_ingest_replay_receipt.json",
        help="Destination path for JSON receipt report.",
    )

    args = parser.parse_args()
    res = run_ingest_replay_benchmark(
        base_graph=args.base_graph,
        records_count=args.records,
        output_report=args.output_report,
    )
    return 0 if res.get("terminal_outcome") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
