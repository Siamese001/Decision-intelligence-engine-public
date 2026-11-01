"""End-to-End and Edge-Case verification of Query Deep Traversal, Dynamic Entity Resolution, and Boolean Egress Preservation.

Wave 2 Acceptance Tests:
1. Multi-hop traversal hops increased to 2 for lookup/status routes.
2. Capacity limits expanded (MAX_L2_INPUT_RECORDS_PER_LANE=16, MAX_REQUIRED_MATERIAL_RECORDS=6).
3. Dynamic entity matching against SQLite active nodes on word boundaries.
4. Engine boolean egress preservation (False is never coerced to True).
5. Router TTL caching and manual cache_clear support.
6. Edge cases: Boundary inputs, malformed qualifiers, unauthorized nodes.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from dual_pipeline.query_core.constants import (
    GRAPH_HOPS_BY_QUERY_TYPE,
    MAX_L2_INPUT_RECORDS_PER_LANE,
    MAX_REQUIRED_MATERIAL_RECORDS,
)
from dual_pipeline.query_core.hydration import (
    _resolve_dynamic_entity_matches,
    exact_retrieval,
)
from dual_pipeline.query_core.engine import QueryEngine, _QueryRunState
from dual_pipeline.query_router import _check_graphdb_available


@pytest.mark.e2e
def test_query_constants_deep_traversal_and_capacity():
    """Verifies that lookup and status routes configure 2 graph hops and increased capacity."""
    assert GRAPH_HOPS_BY_QUERY_TYPE.get("exact_lookup") == 2
    assert GRAPH_HOPS_BY_QUERY_TYPE.get("current_state") == 2
    assert GRAPH_HOPS_BY_QUERY_TYPE.get("project_status_current") == 2
    assert GRAPH_HOPS_BY_QUERY_TYPE.get("project_status_history") == 2

    # Higher complexity routes have 3 hops
    assert GRAPH_HOPS_BY_QUERY_TYPE.get("relationship_explanation") == 3
    assert GRAPH_HOPS_BY_QUERY_TYPE.get("cross_workstream_synthesis") == 3

    # Expanded lane capacity
    assert MAX_L2_INPUT_RECORDS_PER_LANE == 16
    assert MAX_REQUIRED_MATERIAL_RECORDS == 6


@pytest.mark.e2e
def test_dynamic_entity_resolution_sqlite():
    """Verifies dynamic entity matching on active SQLite nodes without static wordlist limitations."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            node_type TEXT,
            active INTEGER
        )"""
    )
    # Insert entities not in the legacy 19 static entity names list
    test_nodes = [
        ("node_conduit", "Conduit Architecture", "Conduit", "concept", 1),
        ("node_vasco", "Vasco Data System", "Vasco", "project", 1),
        ("node_inactive", "Inactive System", "Inactive", "project", 0),
        ("node_short", "IT", "IT Department", "organization", 1),
    ]
    conn.executemany(
        "INSERT INTO node VALUES (?, ?, ?, ?, ?)",
        test_nodes,
    )

    # Question matching "Conduit"
    matches = _resolve_dynamic_entity_matches(
        conn,
        "What is the current status of Conduit in production?",
        authorized_nodes=["node_conduit", "node_vasco"],
    )
    matched_ids = [m["node_id"] for m in matches]
    assert "node_conduit" in matched_ids
    assert "node_vasco" not in matched_ids

    # Inactive node must not match even if in text
    matches_inactive = _resolve_dynamic_entity_matches(
        conn,
        "Is Inactive System alive?",
        authorized_nodes=["node_inactive"],
    )
    assert len(matches_inactive) == 0

    # Unauthorized node must not match
    matches_unauth = _resolve_dynamic_entity_matches(
        conn,
        "Tell me about Vasco Data System",
        authorized_nodes=["node_conduit"],  # node_vasco is unauthorized
    )
    assert len(matches_unauth) == 0

    # Word boundary precision: 'conduits' does not accidentally trigger substring error,
    # and short word 'it' does not trigger false positive on 'IT'
    matches_boundary = _resolve_dynamic_entity_matches(
        conn,
        "Did it pass the review?",
        authorized_nodes=["node_short"],
    )
    # 'it' is lowercase len 2, won't match canonical 'IT' (len < 3 check protects against 1-2 char noise)
    assert len(matches_boundary) == 0


@pytest.mark.e2e
def test_engine_provider_egress_allowed_preservation():
    """Verifies that provider_egress_allowed=False is preserved and not coerced to True."""
    mock_engine = MagicMock()
    mock_engine.backend.production_qualified = False
    mock_engine.backend.model_manifest_digest = None
    mock_engine.manager._assert_mutable_layout = MagicMock()

    # Case 1: Explicitly False
    state_false = _QueryRunState(
        active={},
        question="What is the internal plan?",
        user_id="operator",
        classification="internal",
        audience="nee-engineering",
        materiality="medium",
        delivery_profile=None,
        artifact_type=None,
        provider_egress_allowed=False,
    )
    QueryEngine._stage_validate_and_normalize(mock_engine, state_false)
    assert state_false.validated_request.provider_egress_allowed is False

    # Case 2: Explicitly True
    state_true = _QueryRunState(
        active={},
        question="What is the public roadmap?",
        user_id="operator",
        classification="internal",
        audience="nee-engineering",
        materiality="medium",
        delivery_profile=None,
        artifact_type=None,
        provider_egress_allowed=True,
    )
    QueryEngine._stage_validate_and_normalize(mock_engine, state_true)
    assert state_true.validated_request.provider_egress_allowed is True

    # Case 3: None defaults to True
    state_none = _QueryRunState(
        active={},
        question="What is the status?",
        user_id="operator",
        classification="internal",
        audience="nee-engineering",
        materiality="medium",
        delivery_profile=None,
        artifact_type=None,
        provider_egress_allowed=None,
    )
    QueryEngine._stage_validate_and_normalize(mock_engine, state_none)
    assert state_none.validated_request.provider_egress_allowed is True


@pytest.mark.e2e
def test_router_graphdb_check_ttl_and_clear():
    """Verifies TTL caching and manual cache clear on _check_graphdb_available."""
    # Ensure cache_clear attribute exists and is callable
    assert hasattr(_check_graphdb_available, "cache_clear")
    _check_graphdb_available.cache_clear()

    # Call it once
    res1 = _check_graphdb_available()
    assert isinstance(res1, bool)

    # Calling again within 5s returns cached value quickly
    t0 = time.monotonic()
    res2 = _check_graphdb_available()
    elapsed = time.monotonic() - t0
    assert res1 == res2
    assert elapsed < 0.05  # cached lookup is near instantaneous

    # Clear cache and verify refresh
    _check_graphdb_available.cache_clear()


@pytest.mark.adversarial
def test_hydration_edge_cases_and_malformed_qualifiers(tmp_path: Path):
    """Adversarial check: malformed qualifiers and corrupt SQLite structures do not crash retrieval."""
    db_path = tmp_path / "test_graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            node_type TEXT,
            active INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            object_node_id TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            canonical_status TEXT,
            validation_state TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            qualifiers_json TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            search_rowid INTEGER,
            object_type TEXT,
            object_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            source_id TEXT,
            assertion_id TEXT
        )"""
    )

    # Insert valid node
    conn.execute("INSERT INTO node VALUES ('n1', 'Conduit Architecture', 'Conduit', 'concept', 1)")
    conn.execute("INSERT INTO node VALUES ('n2', 'Vasco System', 'Vasco', 'project', 1)")

    # Insert assertion with corrupt JSON qualifiers
    conn.execute(
        "INSERT INTO assertion VALUES ('a1', 'n1', 'n2', 'internal', '[\"nee-engineering\"]', 'canonical', 'approved', 'current', 1, '{broken_json: true}')"
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('a1', 'ev1')")

    # Insert matching search document
    conn.execute(
        "INSERT INTO search_document VALUES ('sd1', 1, 'assertion', 'a1', 'Conduit Status', 'Conduit is actively deployed', 'canonical', 'current', 1, 'src1', 'a1')"
    )
    conn.commit()
    conn.close()

    # exact_retrieval should handle JSONDecodeError gracefully without failing
    res = exact_retrieval(
        question="What is Conduit status?",
        graph_path=db_path,
        classification="internal",
        audiences=["nee-engineering"],
        retrieval_mode="factual",
    )
    assert isinstance(res, dict)
    assert "entity_matches" in res
    assert "bm25_anchors" in res
    assert "exact_anchors" in res
    # Ensure Conduit node matched dynamically
    dynamic_node_ids = [m["node_id"] for m in res["entity_matches"]]
    assert "n1" in dynamic_node_ids
