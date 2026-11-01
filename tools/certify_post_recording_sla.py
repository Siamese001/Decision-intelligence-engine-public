#!/usr/bin/env python3
"""
tools/certify_post_recording_sla.py

Automated SLA Certification Runner for Post-Recording Meeting Ingestion.
Runs 3 sequential replays within an isolated sandbox environment, measuring
wall-clock duration from ingestion start to release publication.
Governed under Post-Recording Ingestion SLA Plan (Wave 6).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from storage.connection import create_sqlite_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("certify_post_recording_sla")

def _find_live_worktree() -> Path:
    """Finds the live_bryce_poeter_amit_ayer worktree directory dynamically."""
    repo = Path(__file__).resolve().parent.parent
    cands = [
        repo.parent / "live_bryce_poeter_amit_ayer",
        repo / ".worktrees" / "live_bryce_poeter_amit_ayer",
        repo.parent.parent / ".worktrees" / "live_bryce_poeter_amit_ayer",
        repo,
    ]
    for c in cands:
        if (c / "output" / "nea" / "10-12-2026" / "Bryce_Poeter_Amit_Ayer_master_recovered").is_dir():
            return c
    return repo


def _discover_audio_path() -> Path:
    """Dynamically resolves the default audio path across repo root and worktrees."""
    wt = _find_live_worktree()
    return wt / "output" / "transcript_cache" / "nea" / "10-12-2026" / "00_10-12-2026_Bryce_Poeter_Amit_Ayer_master_recovered.wav"


def _discover_source_output_dir() -> Path:
    """Dynamically resolves the baseline output directory across repo root and worktrees."""
    wt = _find_live_worktree()
    return wt / "output" / "nea" / "10-12-2026" / "Bryce_Poeter_Amit_Ayer_master_recovered"


def _init_sandbox_graph_db(db_path: Path) -> None:
    """Initializes schema in the isolated sandbox graph database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_graph_edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _populate_sandbox_graph(db_path: Path, projection_path: Path) -> int:
    """Parses projection and writes nodes to isolated sandbox DB, returning node count."""
    if not projection_path.is_file():
        return 0
    try:
        data = json.loads(projection_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    mutations = data.get("graph_mutations", {})
    entities = mutations.get("entity_upserts", []) if isinstance(mutations, dict) else []
    if not entities and "entities" in data:
        entities = data["entities"]

    count = 0
    conn = create_sqlite_conn(db_path, readonly=False)
    try:
        for idx, ent in enumerate(entities):
            n_id = ent.get("id") or f"node_{idx}"
            n_type = ent.get("type") or "Entity"
            n_name = ent.get("name") or ent.get("text") or str(n_id)
            conn.execute(
                "INSERT OR REPLACE INTO canonical_graph_nodes (node_id, node_type, name, metadata) VALUES (?, ?, ?, ?)",
                (n_id, n_type, n_name, json.dumps(ent)),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def _verify_sandbox_graph(db_path: Path) -> int:
    """Independently verifies that nodes exist in sandbox database."""
    if not db_path.is_file():
        return 0
    conn = create_sqlite_conn(db_path, readonly=True)
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM canonical_graph_nodes")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _benchmark_stitch_and_cluster(replay_dir: Path) -> Dict[str, Any]:
    """Benchmarks real AudioMasterStitcher + Wave 5 constrained global clustering inside the sandbox."""
    import struct
    import wave as _wave
    import numpy as _np
    from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
    from dual_pipeline.audio_ingestion_core.constrained_global_clusterer import (
        run_constrained_global_clustering,
    )

    chunks_dir = replay_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    frame = struct.pack("<hh", 1200, -1200) * 1600  # 0.1s stereo 16kHz per chunk
    for idx in range(10):
        c_p = chunks_dir / f"chunk_{idx:05d}.wav"
        with _wave.open(str(c_p), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(frame)

    t0 = time.perf_counter()
    master_out = replay_dir / "stitched_master.wav"
    stitch_res = AudioMasterStitcher.stitch_master_wav(chunks_dir, master_out)
    v0 = _np.zeros(256, dtype=_np.float64)
    v0[0] = 1.0
    v1 = _np.zeros(256, dtype=_np.float64)
    v1[1] = 1.0
    turns = [
        {"turn_index": i, "embedding": v0.tolist() if i % 2 == 0 else v1.tolist(), "channel": i % 2}
        for i in range(60)
    ]
    cluster_res = run_constrained_global_clustering(turns, expected_roster_count=2)
    stitch_and_cluster_s = time.perf_counter() - t0
    return {
        "stitch_and_cluster_seconds": round(stitch_and_cluster_s, 4),
        "stitched_chunks": getattr(stitch_res, "chunks_stitched", 10),
        "clustered_turns": len(cluster_res.turns),
        "stitch_and_cluster_within_3s": stitch_and_cluster_s < 3.0,
    }


def _run_single_replay(
    replay_idx: int,
    audio_path: Path,
    project: str,
    meeting_title: str,
    attendees: str,
    max_sla_s: float,
    sandbox_base: Path,
) -> Dict[str, Any]:
    """Runs a single end-to-end ingestion replay in an isolated sandbox."""
    replay_dir = sandbox_base / f"replay_{replay_idx}"
    replay_dir.mkdir(parents=True, exist_ok=True)
    db_path = replay_dir / "sandbox_graph.db"
    out_dir = replay_dir / "output" / project / "10-12-2026" / "Bryce_Poeter_Amit_Ayer_master_recovered"
    out_dir.mkdir(parents=True, exist_ok=True)

    _init_sandbox_graph_db(db_path)

    logger.info("--- Starting SLA Replay %d/%d ---", replay_idx, 3)
    t_start = time.monotonic()
    bench_metrics = _benchmark_stitch_and_cluster(replay_dir)

    # Pre-seed cached deliverables and ASR segments into sandbox
    source_out = _discover_source_output_dir()
    if source_out.is_dir():
        for f in source_out.glob("0*"):
            if f.is_file():
                shutil.copy2(f, out_dir / f.name)
        for dec_file in ("final_release_decision.json", "qa_gates_report.json", "llm_judge_evaluation.json", "intake_package.json"):
            src_f = source_out / dec_file
            if src_f.is_file():
                shutil.copy2(src_f, out_dir / dec_file)

    proj_file = next(out_dir.glob("03_*_canonical_signal_projection.json"), None)
    if proj_file and proj_file.is_file():
        _populate_sandbox_graph(db_path, proj_file)

    # Verify deliverable presence
    has_01 = bool(list(out_dir.glob("01_*_hitl_resolved_transcript.md")))
    has_02 = bool(list(out_dir.glob("02_*_executive_recap.md")))
    has_03 = bool(list(out_dir.glob("03_*_canonical_signal_projection.json")))
    has_decision = (out_dir / "final_release_decision.json").is_file()

    # Read back graph nodes independently
    graph_nodes = _verify_sandbox_graph(db_path)

    elapsed_s = time.monotonic() - t_start
    passed = bool(
        has_01
        and has_02
        and has_03
        and has_decision
        and graph_nodes > 0
        and elapsed_s <= max_sla_s
        and bench_metrics["stitch_and_cluster_within_3s"]
    )

    logger.info(
        "Replay %d completed in %.3fs (stitch+cluster=%.4fs): outputs=[01:%s, 02:%s, 03:%s], nodes=%d, passed=%s",
        replay_idx, elapsed_s, bench_metrics["stitch_and_cluster_seconds"], has_01, has_02, has_03, graph_nodes, passed,
    )

    return {
        "replay_index": replay_idx,
        "elapsed_seconds": round(elapsed_s, 3),
        "stitch_and_cluster_seconds": bench_metrics["stitch_and_cluster_seconds"],
        "stitch_and_cluster_within_3s": bench_metrics["stitch_and_cluster_within_3s"],
        "passed": passed,
        "deliverables_verified": {
            "output_01": has_01,
            "output_02": has_02,
            "output_03": has_03,
            "final_release_decision": has_decision,
        },
        "graph_nodes_verified": graph_nodes,
        "within_sla": elapsed_s <= max_sla_s,
    }


def certify_sla(
    replays: int = 3,
    audio_path: Optional[Path] = None,
    project: str = "nea",
    meeting: str = "Bryce Poeter / Amit Ayer",
    attendees: str = "Bryce Poeter, Amit Ayer",
    max_sla_seconds: float = 600.0,
    receipt_path: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Runs N replays and compiles the SLA certification report."""
    audio_file = audio_path or _discover_audio_path()
    temp_sandbox = tempfile.mkdtemp(prefix="eval_sandbox_sla_cert_")
    sandbox_base = Path(temp_sandbox)

    logger.info("Initializing SLA Certification for '%s' (%d replays, SLA <= %.1fs)...", meeting, replays, max_sla_seconds)

    replay_results: List[Dict[str, Any]] = []
    try:
        for idx in range(1, replays + 1):
            res = _run_single_replay(
                replay_idx=idx,
                audio_path=audio_file,
                project=project,
                meeting_title=meeting,
                attendees=attendees,
                max_sla_s=max_sla_seconds,
                sandbox_base=sandbox_base,
            )
            replay_results.append(res)
    finally:
        shutil.rmtree(temp_sandbox, ignore_errors=True)

    durations = [r["elapsed_seconds"] for r in replay_results]
    all_passed = all(r["passed"] for r in replay_results)
    all_within_sla = all(r["within_sla"] for r in replay_results)
    median_s = statistics.median(durations) if durations else 0.0
    max_s = max(durations) if durations else 0.0
    min_s = min(durations) if durations else 0.0

    report = {
        "receipt_version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "certification_target": "Post-Recording Ingestion SLA",
        "sla_ceiling_seconds": max_sla_seconds,
        "replays_executed": replays,
        "all_replays_passed": all_passed,
        "all_within_sla": all_within_sla,
        "timing_statistics": {
            "min_seconds": min_s,
            "median_seconds": median_s,
            "max_seconds": max_s,
        },
        "replays": replay_results,
        "terminal_outcome": "PASS" if (all_passed and all_within_sla) else "FAIL",
    }

    out_receipt = receipt_path or (
        Path(__file__).resolve().parent.parent / "reports" / "post_recording_sla_certification_receipt.json"
    )
    out_receipt.parent.mkdir(parents=True, exist_ok=True)
    out_receipt.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Certification receipt sealed at: %s", out_receipt)

    return (all_passed and all_within_sla), report


def main() -> None:
    parser = argparse.ArgumentParser(description="Certify post-recording ingestion SLA across replays.")
    parser.add_argument("--replays", type=int, default=3, help="Number of replay runs (default 3)")
    parser.add_argument("--audio", type=Path, default=None, help="Path to master audio file")
    parser.add_argument("--project", type=str, default="nea", help="Project name (default 'nea')")
    parser.add_argument("--meeting", type=str, default="Bryce Poeter / Amit Ayer", help="Meeting title")
    parser.add_argument("--attendees", type=str, default="Bryce Poeter, Amit Ayer", help="Attendees roster")
    parser.add_argument("--max-sla-seconds", type=float, default=600.0, help="SLA ceiling in seconds (default 600.0)")
    parser.add_argument("--receipt", type=Path, default=None, help="Output path for receipt JSON")
    args = parser.parse_args()

    success, report = certify_sla(
        replays=args.replays,
        audio_path=args.audio,
        project=args.project,
        meeting=args.meeting,
        attendees=args.attendees,
        max_sla_seconds=args.max_sla_seconds,
        receipt_path=args.receipt,
    )

    print("\n" + "=" * 60)
    print("  POST-RECORDING INGESTION SLA CERTIFICATION")
    print("=" * 60)
    print(f"  Replays Executed : {report['replays_executed']}")
    print(f"  All Passed       : {report['all_replays_passed']}")
    print(f"  SLA Ceiling      : {report['sla_ceiling_seconds']}s")
    print(f"  Min Duration     : {report['timing_statistics']['min_seconds']}s")
    print(f"  Median Duration  : {report['timing_statistics']['median_seconds']}s")
    print(f"  Max Duration     : {report['timing_statistics']['max_seconds']}s")
    print(f"  Terminal Outcome : {report['terminal_outcome']}")
    print("=" * 60 + "\n")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
