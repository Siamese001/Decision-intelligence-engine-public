"""End-to-End test for Vector Health Inspection & Sidecar Sync Alignment.

Validates the full lifecycle:
1. Synchronized graph and sidecar databases with valid unit-normalized vectors
2. Vector health inspection verifying norm bounds, dimensions, and zero vector absence
3. Sidecar sync verification across candidate and canonical search documents
4. Detection and fail-closed reporting of unnormalized vectors
5. Detection and fail-closed reporting of desynchronized search documents
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import pytest

from dual_pipeline.graph_delta_core.vector_health import inspect_vector_health
from tools.mcp_graphdb_server import (
    graphdb_verify_sidecar_sync,
)


@pytest.fixture
def synchronized_pair(tmp_path: Path):
    graph_db = tmp_path / "e2e_graph.sqlite"
    sidecar_db = tmp_path / "e2e_sidecar.sqlite"

    # 1. Graph DB
    g_conn = sqlite3.connect(graph_db)
    g_conn.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE source_version (version_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY)")
    g_conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT)")
    g_conn.execute("CREATE TABLE node_state (node_id TEXT PRIMARY KEY, approval_state TEXT)")
    g_conn.execute(
        "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, canonical_status TEXT, currentness_state TEXT, superseded_at TEXT)"
    )
    g_conn.execute("CREATE TABLE search_document (doc_id TEXT PRIMARY KEY, canonical_status TEXT)")
    g_conn.execute("CREATE TABLE audit_event (event_id TEXT PRIMARY KEY)")
    g_conn.execute("INSERT INTO source VALUES ('src:01')")
    g_conn.execute("INSERT INTO search_document VALUES ('doc:01', 'approved')")
    g_conn.execute("INSERT INTO search_document VALUES ('doc:02', 'candidate')")
    g_conn.commit()
    g_conn.close()

    # 2. Sidecar DB
    s_conn = sqlite3.connect(sidecar_db)
    s_conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    s_conn.execute(
        """CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            lane TEXT,
            record_type TEXT
        )"""
    )
    s_conn.execute(
        "CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)"
    )
    s_conn.execute(
        "CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))"
    )
    s_conn.execute(
        "CREATE TABLE colbert_token (record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))"
    )

    val = 1.0 / (1024**0.5)
    unit_vec = struct.pack("<1024f", *([val] * 1024))
    colbert_vec = struct.pack("<32f", *([val] * 32))

    for rid in ("doc:01", "doc:02"):
        s_conn.execute("INSERT INTO retrieval_record VALUES (?, 'approved', 'assertion')", (rid,))
        s_conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", (rid, unit_vec))
        s_conn.execute("INSERT INTO sparse_weight VALUES (?, 'token', 1.0)", (rid,))
        s_conn.execute(
            "INSERT INTO colbert_token VALUES (?, 0, 'token', 32, ?)", (rid, colbert_vec)
        )

    s_conn.commit()
    s_conn.close()

    return graph_db, sidecar_db


def test_vector_health_and_sync_lifecycle_e2e(synchronized_pair):
    graph_db, sidecar_db = synchronized_pair

    # Step 1: Health audit of clean, synchronized pair
    health = inspect_vector_health(sidecar_db, sample_limit=0)
    assert health["status"] == "HEALTHY"
    assert health["is_healthy"] is True
    assert health["tables"]["dense_vector"]["norm_out_of_bounds_count"] == 0
    assert health["tables"]["dense_vector"]["total_rows"] == 2
    assert health["tables"]["retrieval_record"]["missing_vector_count"] == 0

    # Step 2: Verify sidecar sync via MCP tool
    sync_res = graphdb_verify_sidecar_sync(str(graph_db), str(sidecar_db))
    sync_data = json.loads(sync_res)
    assert sync_data["status"] == "IN_SYNC"
    assert sync_data["aligned"] is True
    assert sync_data["total_search_documents"] == 2
    assert sync_data["dense_vectors"] == 2

    # Step 3: Inject unnormalized vector (norm = 25.0) -> MUST flag UNHEALTHY
    val_large = 25.0 / (1024**0.5)
    large_vec = struct.pack("<1024f", *([val_large] * 1024))
    s_conn = sqlite3.connect(sidecar_db)
    s_conn.execute("INSERT INTO dense_vector VALUES ('doc:03', 1024, ?)", (large_vec,))
    s_conn.execute("INSERT INTO retrieval_record VALUES ('doc:03', 'candidate', 'assertion')")
    s_conn.commit()
    s_conn.close()

    health_unnorm = inspect_vector_health(sidecar_db)
    assert health_unnorm["status"] == "UNHEALTHY"
    assert health_unnorm["is_healthy"] is False
    assert health_unnorm["tables"]["dense_vector"]["norm_out_of_bounds_count"] >= 1
    assert any("norm outside" in issue for issue in health_unnorm["issues"])

    # Step 4: Inject unindexed search documents -> MUST flag OUT_OF_SYNC
    g_conn = sqlite3.connect(graph_db)
    g_conn.execute("INSERT INTO search_document VALUES ('doc:04', 'approved')")
    g_conn.execute("INSERT INTO search_document VALUES ('doc:05', 'approved')")
    g_conn.execute("INSERT INTO search_document VALUES ('doc:06', 'approved')")
    g_conn.commit()
    g_conn.close()

    sync_desync = graphdb_verify_sidecar_sync(str(graph_db), str(sidecar_db))
    desync_data = json.loads(sync_desync)
    assert desync_data["status"] == "OUT_OF_SYNC"
    assert desync_data["aligned"] is False
    assert desync_data["total_search_documents"] == 5
    assert desync_data["dense_vectors"] == 3
