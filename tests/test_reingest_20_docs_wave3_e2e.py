"""
Wave 3 E2E verification for the atomic batch release of the last 20 documents,
11-gate BGE-M3 retrieval evaluation, and 5-check physical proof receipt.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.core import ROOT, sha256_file
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import resolve_active_graph_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_wave3_physical_proof_receipt_and_all_5_checks_pass() -> None:
    proof_path = REPO_ROOT / "reports" / "reingest_20_docs_physical_proof.json"
    assert proof_path.is_file(), "Missing reports/reingest_20_docs_physical_proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    assert proof.get("status") == "COMPLETE"
    assert proof.get("active_generation_id") != proof.get("previous_generation_id")

    checks = proof.get("checks", {})
    for check_name in ("file_changed", "raw_bytes", "integrity", "readback", "row_delta"):
        assert checks.get(check_name, {}).get("status") == "PASS", f"Check {check_name} failed: {checks.get(check_name)}"

    # Verify active_pair.json points to the new generation
    rt = resolve_authoritative_runtime_root(ROOT)
    active_pair = json.loads((rt / "active_pair.json").read_text(encoding="utf-8"))
    assert active_pair["generation_id"] == proof["active_generation_id"]


def test_wave3_11_bge_m3_retrieval_gates_passed() -> None:
    proof_path = REPO_ROOT / "reports" / "reingest_20_docs_physical_proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    eval_sum = proof.get("retrieval_evaluation_summary", {})
    assert eval_sum.get("status") == "PASS"
    assert eval_sum.get("gates_passed") == 11


def test_wave3_live_graph_readback_and_raw_bytes() -> None:
    active_graph = resolve_active_graph_path(ROOT)
    assert active_graph is not None and active_graph.is_file()
    raw_bytes = active_graph.read_bytes()
    for tid in (
        "entity-5aa8c4fc3147da748e4619005ff7",
        "entity-c075af3e7578b6c0c9a0442a6e90",
        "entity-fe18b65baa22eb3389d641a2a617",
        "entity-8364bee4afd00aa4f36a055aa8c7",
        "source-9619ee4078949a31625f7473",
    ):
        assert raw_bytes.find(tid.encode("utf-8")) != -1, f"Missing {tid} in raw SQLite bytes"

    conn = sqlite3.connect(f"file:{active_graph.resolve()}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    assert cur.fetchone()[0] == "ok"
    cur.execute(
        "SELECT COUNT(*) FROM node WHERE node_id IN ("
        "'entity-5aa8c4fc3147da748e4619005ff7', "
        "'entity-c075af3e7578b6c0c9a0442a6e90', "
        "'entity-fe18b65baa22eb3389d641a2a617', "
        "'entity-8364bee4afd00aa4f36a055aa8c7')"
    )
    assert cur.fetchone()[0] == 4
    conn.close()
