"""Unit tests for validation_closeout_core.targets and source_retirement_core.targets."""
from __future__ import annotations

import sqlite3

import pytest

from dual_pipeline.source_retirement_core.targets import (
    _derived_targets,
    _in_clause,
    _query_ids,
    _row_by_id,
    _source_scoped_ids,
)
from dual_pipeline.validation_closeout_core.targets import (
    _assert_removal_dependencies_clear,
    _canonical_name_confirmation_authority,
    _mapping_confirmation_authority,
    _removal_targets,
)


def _setup_closeout_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE edge (edge_id TEXT, from_node_id TEXT, to_node_id TEXT, superseded_by TEXT);
        CREATE TABLE claim (claim_id TEXT, subject_node_id TEXT, superseded_by TEXT);
        CREATE TABLE assertion (
            assertion_id TEXT,
            subject_node_id TEXT,
            object_node_id TEXT,
            legacy_record_type TEXT,
            legacy_record_ids_json TEXT,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE validation_queue (
            validation_id TEXT,
            node_id TEXT,
            edge_id TEXT,
            claim_id TEXT
        );
        CREATE TABLE candidate_entity (candidate_entity_id TEXT, matched_node_id TEXT);
        CREATE TABLE candidate_assertion (
            candidate_assertion_id TEXT,
            subject_candidate_or_node_id TEXT,
            object_candidate_node_id TEXT
        );
        CREATE TABLE entity_redirect (from_node_id TEXT, to_node_id TEXT);
        CREATE TABLE entity_merge_event (canonical_node_id TEXT);
        CREATE TABLE node_type_registry (example_node_id TEXT);
        INSERT INTO edge VALUES ('e1', 'n1', 'n2', NULL);
        INSERT INTO claim VALUES ('c1', 'n1', NULL);
        INSERT INTO assertion VALUES ('a1', 'n1', 'n2', 'claim', '["c1"]', NULL);
        INSERT INTO validation_queue VALUES ('v1', 'n1', NULL, NULL);
        INSERT INTO candidate_entity VALUES ('ce1', 'n1');
        INSERT INTO candidate_assertion VALUES ('ca1', 'n1', 'ce1');
        """
    )
    return conn


def test_validation_closeout_removal_targets_and_dependencies() -> None:
    conn = _setup_closeout_db()
    row = conn.execute("SELECT * FROM validation_queue WHERE validation_id='v1'").fetchone()
    targets = _removal_targets(conn, row)
    assert targets["node_ids"] == ["n1"]
    assert targets["edge_ids"] == ["e1"]
    assert targets["claim_ids"] == ["c1"]
    assert targets["assertion_ids"] == ["a1"]
    assert targets["candidate_entity_ids"] == ["ce1"]
    assert targets["candidate_assertion_ids"] == ["ca1"]

    _assert_removal_dependencies_clear(conn, targets)

    conn.execute("INSERT INTO entity_redirect VALUES ('n1', 'n9')")
    with pytest.raises(RuntimeError, match="entity redirect"):
        _assert_removal_dependencies_clear(conn, targets)


def test_validation_closeout_empty_target_raises_and_authorities() -> None:
    conn = _setup_closeout_db()
    conn.execute("INSERT INTO validation_queue VALUES ('v_empty', NULL, NULL, NULL)")
    row = conn.execute("SELECT * FROM validation_queue WHERE validation_id='v_empty'").fetchone()
    with pytest.raises(RuntimeError, match="requires a node, edge, or claim target"):
        _removal_targets(conn, row)

    map_auth = _mapping_confirmation_authority(
        graph_sha256="a" * 64,
        validation_id="v1",
        resolution="CONFIRMED",
        directive_approval_id="da-1",
        targets={"assertions": [{"assertion_id": "a1"}], "edges": [{"edge_id": "e1"}]},
    )
    assert map_auth["approval_type"] == "validation_transition"
    assert map_auth["status"] == "approved"

    name_auth = _canonical_name_confirmation_authority(
        graph_sha256="a" * 64,
        validation_id="v1",
        resolution="CONFIRMED",
        directive_approval_id="da-1",
        targets={"node": {"node_id": "n1"}, "confirmed_name": "Node 1"},
    )
    assert name_auth["approval_type"] == "validation_transition"
    assert name_auth["approved_by"] == "Amit Ayer"


def test_source_retirement_targets_and_derived_queries() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE source (source_id TEXT, title TEXT);
        CREATE TABLE evidence_item (evidence_id TEXT, source_id TEXT);
        CREATE TABLE validation_queue (validation_id TEXT, claim_id TEXT, edge_id TEXT);
        CREATE TABLE canonical_route (route_id TEXT, object_type TEXT, object_id TEXT);
        CREATE TABLE search_document (search_document_id TEXT, assertion_id TEXT, object_type TEXT, object_id TEXT);
        INSERT INTO source VALUES ('s1', 'Source One');
        INSERT INTO evidence_item VALUES ('ev2', 's1'), ('ev1', 's1');
        INSERT INTO validation_queue VALUES ('vq1', 'c1', NULL);
        INSERT INTO canonical_route VALUES ('rt1', 'assertion', 'a1');
        INSERT INTO search_document VALUES ('sd1', 'a1', 'assertion', 'a1');
        """
    )
    row = _row_by_id(conn, "source", "source_id", "s1")
    assert row is not None and row["title"] == "Source One"
    assert _row_by_id(conn, "source", "source_id", "missing") is None

    ev_ids = _source_scoped_ids(
        conn, table="evidence_item", id_field="evidence_id", source_field="source_id", source_id="s1"
    )
    assert ev_ids == ["ev1", "ev2"]

    assert _in_clause("claim_id", []) == ("0", ())
    clause, params = _in_clause("claim_id", ["c1", "c2"])
    assert "IN (?,?)" in clause and params == ("c1", "c2")

    q_ids = _query_ids(conn, "SELECT evidence_id FROM evidence_item WHERE source_id=?", ("s1",), "evidence_id")
    assert q_ids == ["ev1", "ev2"]

    derived = _derived_targets(
        conn,
        {
            "assertion_ids": ["a1"],
            "claim_ids": ["c1"],
            "edge_ids": ["e1"],
            "alias_ids": [],
            "canonical_alias_ids": [],
        },
    )
    assert derived["validation_ids"] == ["vq1"]
    assert derived["canonical_route_ids"] == ["rt1"]
    assert derived["search_document_ids"] == ["sd1"]
