"""
Wave 3 Adversarial & Edge-Case tests for atomic batch release, concurrency, and rollback.
Covers 4 mandatory edge-case categories:
1. Boundary payload: Empty `run_ids` list rejected by `ReleaseManager.prepare_many`.
2. Concurrency & race: Parallel read-only SQLite connections against WAL-checkpointed active graph without lock contention.
3. Invariant bypass attempt: `verify_authoritative_release_gate` blocks tampered Output 03 file when SHA-256 drifts from sealed manifest.
4. Rollback & recovery: Non-existent or unapproved `run_id` fails closed without mutating `active_pair.json`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import pytest

from dual_pipeline.core import ROOT
from dual_pipeline.release import ReleaseManager
from dual_pipeline.release_policy import verify_authoritative_release_gate
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import resolve_active_graph_path


def test_boundary_empty_run_ids_rejected(tmp_path: Path) -> None:
    ag = resolve_active_graph_path(ROOT)
    mgr = ReleaseManager(tmp_path, ag)
    with pytest.raises(Exception):
        mgr.prepare_many(run_ids=[], approval_id="app-empty", backend=None)  # type: ignore[arg-type]


def test_concurrency_parallel_readonly_queries_on_active_graph() -> None:
    ag = resolve_active_graph_path(ROOT)
    assert ag is not None and ag.is_file()

    def _worker(idx: int) -> int:
        conn = sqlite3.connect(f"file:{ag.resolve()}?mode=ro", uri=True, timeout=30.0)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM node")
        cnt = int(cur.fetchone()[0])
        conn.close()
        return cnt

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_worker, range(16)))
    assert len(set(results)) == 1 and results[0] > 0


def test_invariant_bypass_tampered_output3_drifts_from_manifest(tmp_path: Path) -> None:
    src_dir = Path(__file__).resolve().parents[1] / "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement"
    sandbox_dir = tmp_path / "Hr360_Position_Request_Form_Clement"
    shutil.copytree(src_dir, sandbox_dir)

    f03 = sorted(sandbox_dir.glob("03_*.json"))[-1]
    f03.write_text(f03.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Output 3 content drifted"):
        verify_authoritative_release_gate([sandbox_dir / "intake_package.json"])


def test_rollback_invalid_run_id_preserves_active_pair() -> None:
    rt = resolve_authoritative_runtime_root(ROOT)
    ag = resolve_active_graph_path(ROOT)
    mgr = ReleaseManager(rt, ag)
    before_pair = mgr.active_pair()
    with pytest.raises(Exception):
        mgr.prepare_many(run_ids=["run-intake-nonexistent-000000"], approval_id="app-invalid", backend=None)  # type: ignore[arg-type]
    after_pair = mgr.active_pair()
    assert before_pair == after_pair
