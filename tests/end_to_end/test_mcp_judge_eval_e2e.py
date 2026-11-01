"""End-to-End lifecycle test for LLM Judge & Retrieval Evaluation MCP Server (Rank 4).

Exercises the full lifecycle:
1. Environment initialization (runtime root, active_pair, SQLite graph, SQLite sidecar)
2. 11-gate retrieval evaluation via MCP tool
3. Receipt sealing into contract-valid artifact (retrieval-release-evaluation:1.0.0)
4. Evaluation round-trip from sealed receipt
5. Quad-engine weight calibration verification
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.core import sha256_bytes, sha256_file
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, SIDECAR_SCHEMA
from tools.mcp_judge_eval_server import (
    CANONICAL_11_GATES,
    calibrate_hybrid_weights,
    eval_retrieval_11gates,
    seal_retrieval_receipt,
)


@pytest.fixture
def mock_runtime_pair(tmp_path: Path):
    runtime_root = tmp_path / ".local" / "dual_pipeline"
    runtime_root.mkdir(parents=True, exist_ok=True)
    eval_dir = runtime_root / "retrieval_evaluations"
    eval_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create mock graph
    graph_path = tmp_path / "mock_graph.sqlite"
    g_conn = sqlite3.connect(graph_path)
    g_conn.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE source_version (source_version_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE extraction_run (extraction_run_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT)")
    g_conn.execute("CREATE TABLE node_state (node_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, canonical_status TEXT)")
    g_conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
    g_conn.execute("CREATE TABLE search_document (doc_id TEXT PRIMARY KEY, canonical_status TEXT)")
    g_conn.execute("CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(doc_id, content)")
    g_conn.execute("INSERT INTO source VALUES ('src-1')")
    g_conn.execute("INSERT INTO node VALUES ('node-1', 'external_concept')")
    g_conn.execute("INSERT INTO search_document VALUES ('doc-1', 'approved')")
    g_conn.execute(
        "INSERT INTO search_fts (doc_id, content) VALUES ('doc-1', 'Sample search text')"
    )
    g_conn.commit()
    g_conn.close()

    # 2. Create mock sidecar
    sidecar_path = tmp_path / "mock_sidecar.sqlite"
    s_conn = sqlite3.connect(sidecar_path)
    s_conn.executescript(SIDECAR_SCHEMA)
    s_conn.execute("INSERT INTO metadata VALUES ('record_count', '1')")
    s_conn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', '32')")
    s_conn.execute(
        """INSERT INTO retrieval_record
           (record_id, lane, record_type, namespace, retrieval_text_hash,
            retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json)
           VALUES ('rec-1', 'approved', 'assertion', 'DEFAULT', 'hash1', 'assertion', 'approved', 'source_bound', 'factual_answer', '{\"record_id\":\"rec-1\",\"lane\":\"approved\"}')"""
    )
    vector_blob = b"\x00\x00\x80?" * DENSE_DIMENSIONS  # 1.0f32 repeated 1024 times
    s_conn.execute("INSERT INTO dense_vector VALUES ('rec-1', 1024, ?)", (vector_blob,))
    s_conn.execute("INSERT INTO sparse_weight VALUES ('rec-1', 'sample', 0.5)")
    colbert_blob = b"\x00\x00\x80?" * 32
    s_conn.execute(
        "INSERT INTO colbert_token VALUES ('rec-1', 0, 'sample', 32, ?)", (colbert_blob,)
    )
    s_conn.commit()
    s_conn.close()

    # 3. Create active_pair.json
    active_pair = {
        "schema_version": "1.0.0",
        "generation_id": "gen-test-00000000000000000000",
        "graph_snapshot": "snap-test-0000000000000000",
        "graph_path": str(graph_path),
        "index_path": str(sidecar_path),
        "graph_sha256": sha256_file(graph_path),
        "sidecar_sha256": sha256_file(sidecar_path),
        "embedding_backend": "flagembedding-bge-m3-dev",
        "model_id": "BAAI/bge-m3",
        "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
        "model_manifest_digest": sha256_bytes(b"manifest"),
        "production_qualified": False,
        "activated_at": "2026-09-30T10:00:00Z",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")

    return runtime_root, graph_path, sidecar_path


def test_mcp_judge_eval_full_lifecycle_e2e(mock_runtime_pair):
    """Verify end-to-end evaluation, sealing, and round-trip retrieval."""
    runtime_root, graph_path, sidecar_path = mock_runtime_pair

    # 1. Step 1: Execute 11-gate evaluation via MCP tool
    eval_res = eval_retrieval_11gates(runtime_root=str(runtime_root), synthetic_allowed=False)
    eval_data = json.loads(eval_res)

    assert "gates" in eval_data
    assert len(eval_data["gates"]) == 11
    assert eval_data["gates"]["real_bge_m3"]["passed"] is True
    assert eval_data["gates"]["pair_binding"]["passed"] is True
    assert eval_data["gates"]["coverage_complete"]["passed"] is True
    assert eval_data["gates"]["bm25_execution"]["passed"] is True

    # 2. Step 2: Seal retrieval receipt
    seal_res = seal_retrieval_receipt(
        json.dumps(eval_data),
        output_dir=str(runtime_root / "retrieval_evaluations"),
        receipt_id="receipt-test-e2e-01",
    )
    seal_data = json.loads(seal_res)
    assert seal_data["status"] == "RECEIPT_SEALED"
    assert "receipt_path" in seal_data
    assert seal_data["contract_id"] == "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"

    receipt_path = Path(seal_data["receipt_path"])
    assert receipt_path.is_file()

    # 3. Step 3: Round-trip evaluation verification
    re_eval_res = eval_retrieval_11gates(
        runtime_root=str(runtime_root),
        synthetic_allowed=False,
        evaluation_receipt_path=str(receipt_path),
    )
    re_eval_data = json.loads(re_eval_res)
    assert "gates" in re_eval_data
    assert len(re_eval_data["gates"]) == 11
    for gate_name in CANONICAL_11_GATES:
        assert gate_name in re_eval_data["gates"]

    # 4. Step 4: Calibrate Quad-Engine weights
    calib_res = calibrate_hybrid_weights(
        dense_weight=0.05, sparse_weight=0.15, bm25_weight=0.70, colbert_weight=0.10
    )
    calib_data = json.loads(calib_res)
    assert calib_data["status"] == "CALIBRATED"
    assert calib_data["sum"] == 1.0
    assert calib_data["engine_count"] == 4
