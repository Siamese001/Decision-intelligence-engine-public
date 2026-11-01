"""Tests for Governed GraphDB & Sidecar State MCP Server (Rank 1)."""

import json
import sqlite3
from pathlib import Path

import pytest

from tools.mcp_graphdb_server import (
    graphdb_diagnose_locks,
    graphdb_diff_breakdown,
    graphdb_inspect_vector_health,
    graphdb_read_query,
    graphdb_snapshot_state,
    graphdb_verify_sidecar_sync,
)


@pytest.fixture
def temp_graphdb_fixtures(tmp_path: Path):
    graph_db = tmp_path / "test_graph.sqlite"
    sidecar_db = tmp_path / "test_sidecar.sqlite"

    # Setup graph database
    conn = sqlite3.connect(graph_db)
    conn.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE source_version (version_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT)")
    conn.execute("CREATE TABLE node_state (node_id TEXT PRIMARY KEY, approval_state TEXT)")
    conn.execute(
        "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, canonical_status TEXT, currentness_state TEXT, superseded_at TEXT)"
    )
    conn.execute("CREATE TABLE search_document (doc_id TEXT PRIMARY KEY, canonical_status TEXT)")
    conn.execute("CREATE TABLE audit_event (event_id TEXT PRIMARY KEY)")

    conn.execute("INSERT INTO source VALUES ('src1')")
    conn.execute("INSERT INTO node VALUES ('node1', 'external_concept')")
    conn.execute("INSERT INTO search_document VALUES ('doc1', 'candidate')")
    conn.commit()
    conn.close()

    # Setup sidecar database
    import struct

    val = 1.0 / (1024**0.5)
    unit_vec = struct.pack("<1024f", *([val] * 1024))
    s_conn = sqlite3.connect(sidecar_db)
    s_conn.execute(
        "CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB)"
    )
    s_conn.execute(
        "CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token))"
    )
    s_conn.execute(
        "CREATE TABLE colbert_token (record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB, PRIMARY KEY (record_id, position))"
    )
    s_conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY)")
    s_conn.execute("INSERT INTO dense_vector VALUES ('doc1', 1024, ?)", (unit_vec,))
    s_conn.execute("INSERT INTO sparse_weight VALUES ('doc1', 'tok1', 1.0)")
    s_conn.execute("INSERT INTO retrieval_record VALUES ('doc1')")
    s_conn.commit()
    s_conn.close()

    return graph_db, sidecar_db


def test_graphdb_snapshot_and_diff(temp_graphdb_fixtures):
    graph_db, sidecar_db = temp_graphdb_fixtures

    # Snapshot baseline
    res_base = graphdb_snapshot_state("base1", str(graph_db), str(sidecar_db))
    base_data = json.loads(res_base)
    assert base_data["snapshot_id"] == "base1"
    assert base_data["graph_counts"]["source"] == 1
    assert base_data["sidecar_counts"]["dense_vector"] == 1

    # Mutate graph
    conn = sqlite3.connect(graph_db)
    conn.execute("INSERT INTO source VALUES ('src2')")
    conn.execute("INSERT INTO search_document VALUES ('doc2', 'candidate')")
    conn.commit()
    conn.close()

    # Snapshot activated
    res_act = graphdb_snapshot_state("act1", str(graph_db), str(sidecar_db))
    act_data = json.loads(res_act)
    assert act_data["graph_counts"]["source"] == 2

    # Compute diff breakdown
    diff_md = graphdb_diff_breakdown("base1", "act1")
    assert "Granular Graph DB Delta Table" in diff_md
    assert "| `source` (source) | 1 | 2 | `+1` | +1 sources |" in diff_md
    assert "MUTATION_RECORDED" in diff_md


def test_graphdb_read_query(temp_graphdb_fixtures):
    graph_db, _ = temp_graphdb_fixtures
    res = graphdb_read_query("SELECT source_id FROM source ORDER BY source_id", str(graph_db))
    data = json.loads(res)
    assert data["count"] >= 1
    assert data["rows"][0]["source_id"] == "src1"


def test_graphdb_read_query_blocked():
    res = graphdb_read_query("DELETE FROM source")
    assert "Error: Only read-only queries" in res


def test_graphdb_verify_sidecar_sync(temp_graphdb_fixtures):
    graph_db, sidecar_db = temp_graphdb_fixtures
    res = graphdb_verify_sidecar_sync(str(graph_db), str(sidecar_db))
    data = json.loads(res)
    assert data["status"] == "IN_SYNC"
    assert data["aligned"] is True
    assert "search_documents" in data


def test_graphdb_diagnose_locks(temp_graphdb_fixtures):
    graph_db, _ = temp_graphdb_fixtures
    res = graphdb_diagnose_locks(str(graph_db))
    data = json.loads(res)
    assert data["lock_status"] == "ACCESSIBLE_NO_LOCK_CONTENTION"
    assert "journal_mode" in data


def test_graphdb_inspect_vector_health(temp_graphdb_fixtures):
    _, sidecar_db = temp_graphdb_fixtures
    res = graphdb_inspect_vector_health(str(sidecar_db))
    data = json.loads(res)
    assert "status" in data
    assert "tables" in data
    assert "is_healthy" in data
