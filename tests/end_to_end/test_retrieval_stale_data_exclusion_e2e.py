#!/usr/bin/env python3
"""End-to-End Test Suite for Stale Data Retrieval Exclusion & Lifecycle Invariants.

Verifies:
1. BP-1 & BP-4: AssertionTTLSweeper atomically soft-retires assertions across assertion and search_document tables.
2. BP-2: Default-deny filtering in search_fts joins and query_core traversal helpers excludes inactive/superseded data.
3. BP-6 & BP-7: Tier 2 assertion supersession sets retrieval_eligible = 0 and suppresses hits in search queries.
4. Graph GC orphan detection excludes foreign-key-bound registry example nodes and active edges.
"""
from __future__ import annotations

import sqlite3

import pytest

from dual_pipeline.query_core.traversal_helper import extract_attendee_nodes, extract_project_nodes
from dual_pipeline.staleness_core.ttl_sweeper import AssertionTTLSweeper
from tools.graph_gc import _find_orphan_nodes
from tools.tier2_dedup_core.supersessions import execute_assertion_supersessions


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            is_quarantined INTEGER DEFAULT 0
        );

        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT DEFAULT 'active',
            intentional_orphan INTEGER DEFAULT 0
        );

        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            status TEXT DEFAULT 'CONFIRMED',
            currentness_state TEXT DEFAULT 'current',
            canonical_status TEXT DEFAULT 'canonical',
            retrieval_eligible INTEGER DEFAULT 1,
            confidence_score REAL DEFAULT 0.85,
            confidence_label TEXT DEFAULT 'HIGH',
            primary_source_id TEXT,
            stale_after TEXT,
            superseded_at TEXT,
            superseded_by_assertion_id TEXT,
            updated_at TEXT
        );

        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            search_document_id TEXT UNIQUE,
            assertion_id TEXT,
            object_type TEXT,
            object_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            updated_at TEXT
        );

        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            edge_type TEXT,
            to_node_id TEXT,
            superseded_by TEXT
        );

        CREATE TABLE node_type_registry (
            node_type TEXT PRIMARY KEY,
            example_node_id TEXT
        );
    """)
    yield conn
    conn.close()


def test_ttl_sweeper_atomic_two_step_lifecycle(memory_db):
    """Verify AssertionTTLSweeper updates assertion and search_document atomically."""
    conn = memory_db
    conn.execute(
        """INSERT INTO node VALUES ('node_svc', 'Billing Service', 'Billing', 'system', 1, 0)"""
    )
    conn.execute(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, status, currentness_state, retrieval_eligible, stale_after)
           VALUES ('asrt_stale', 'node_svc', 'DEPLOYED_ON', 'CONFIRMED', 'current', 1, '2026-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO search_document (search_document_id, assertion_id, object_type, object_id, title, body, currentness_state, retrieval_eligible, status)
           VALUES ('sdoc_stale', 'asrt_stale', 'assertion', 'asrt_stale', 'Billing', 'Billing Service on ECS', 'current', 1, 'active')"""
    )
    conn.commit()

    sweeper = AssertionTTLSweeper(dry_run=False)
    res = sweeper.sweep_stale_assertions(conn, as_of_date="2026-06-01T00:00:00Z")

    assert res["status"] == "PASS"
    assert res["swept_count"] == 1

    # Verify assertion table state
    asrt = conn.execute("SELECT * FROM assertion WHERE assertion_id = 'asrt_stale'").fetchone()
    assert asrt["status"] == "SUPERSEDED"
    assert asrt["currentness_state"] == "historical"
    assert asrt["retrieval_eligible"] == 0
    assert asrt["superseded_at"] is not None

    # Verify search_document table state
    sdoc = conn.execute("SELECT * FROM search_document WHERE assertion_id = 'asrt_stale'").fetchone()
    assert sdoc["currentness_state"] == "historical"
    assert sdoc["retrieval_eligible"] == 0
    assert sdoc["status"] == "historical"


def test_traversal_helper_excludes_inactive_nodes(memory_db):
    """Verify traversal_helper functions strictly exclude nodes where active = 0."""
    conn = memory_db
    cursor = conn.cursor()

    # Active and inactive project nodes
    conn.execute("INSERT INTO node VALUES ('project_active', 'Project Alpha', 'Project Alpha', 'project', 1, 0)")
    conn.execute("INSERT INTO node VALUES ('project_retired', 'Project Alpha Old', 'Project Alpha Old', 'project', 0, 0)")

    # Active and inactive attendee nodes
    conn.execute("INSERT INTO node VALUES ('person_active', 'Amit Ayer', 'Amit Ayer', 'person', 1, 0)")
    conn.execute("INSERT INTO node VALUES ('person_retired', 'Amit Ayer Legacy', 'Amit Ayer Legacy', 'person', 0, 0)")
    conn.commit()

    # Test extract_project_nodes
    entities, _ = extract_project_nodes(cursor, "Project Alpha", has_quarantine=True)
    entity_ids = [e.node_id for e in entities]
    assert "project_active" in entity_ids
    assert "project_retired" not in entity_ids

    # Test extract_attendee_nodes
    attendees, _ = extract_attendee_nodes(cursor, ["Amit Ayer"], has_quarantine=True, max_entities=5)
    attendee_ids = [a.node_id for a in attendees]
    assert "person_active" in attendee_ids
    assert "person_retired" not in attendee_ids


def test_tier2_assertion_supersession_clears_retrieval_eligibility(memory_db):
    """Verify execute_assertion_supersessions sets retrieval_eligible = 0 on losers."""
    conn = memory_db
    conn.execute("INSERT INTO node VALUES ('node_a', 'System Alpha', 'System Alpha', 'system', 1, 0)")
    conn.execute("INSERT INTO node VALUES ('node_b', 'System Beta', 'System Beta', 'system', 1, 0)")

    # Two competing assertions on same fact: asrt_high has higher confidence than asrt_low
    conn.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, canonical_status, currentness_state, confidence_score, retrieval_eligible)
        VALUES ('asrt_high', 'node_a', 'DEPENDS_ON', 'node_b', 'canonical', 'current', 0.95, 1)
    """)
    conn.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, canonical_status, currentness_state, confidence_score, retrieval_eligible)
        VALUES ('asrt_low', 'node_a', 'DEPENDS_ON', 'node_b', 'canonical', 'current', 0.70, 1)
    """)
    conn.execute("""
        INSERT INTO search_document (search_document_id, assertion_id, object_type, object_id, title, body, currentness_state, retrieval_eligible, status)
        VALUES ('sdoc_low', 'asrt_low', 'assertion', 'asrt_low', 'Dependency', 'Low confidence fact', 'current', 1, 'active')
    """)
    conn.commit()

    def dummy_audit(*args):
        pass

    count, supersessions = execute_assertion_supersessions(conn, dummy_audit)
    assert count == 1
    assert supersessions[0]["winner"] == "asrt_high"
    assert supersessions[0]["loser"] == "asrt_low"

    # Verify loser state
    loser = conn.execute("SELECT * FROM assertion WHERE assertion_id = 'asrt_low'").fetchone()
    assert loser["currentness_state"] == "historical"
    assert loser["canonical_status"] == "historical"
    assert loser["retrieval_eligible"] == 0
    assert loser["superseded_by_assertion_id"] == "asrt_high"

    # Verify search document sync
    sdoc = conn.execute("SELECT * FROM search_document WHERE assertion_id = 'asrt_low'").fetchone()
    assert sdoc["currentness_state"] == "historical"
    assert sdoc["retrieval_eligible"] == 0
    assert sdoc["status"] == "historical"


def test_graph_gc_protects_registry_example_nodes_and_edges(memory_db):
    """Verify _find_orphan_nodes protects nodes referenced as registry examples or edge endpoints."""
    conn = memory_db
    conn.execute("INSERT INTO node VALUES ('node_orphan', 'Orphan Entity', 'Orphan', 'entity', 1, 0)")
    conn.execute("INSERT INTO node VALUES ('node_example', 'Example Builder', 'Builder', 'capability', 1, 0)")
    conn.execute("INSERT INTO node VALUES ('node_edge_src', 'Edge Source', 'Edge Source', 'system', 1, 0)")
    conn.execute("INSERT INTO node VALUES ('node_edge_dst', 'Edge Destination', 'Edge Destination', 'system', 1, 0)")

    # node_example is referenced in node_type_registry
    conn.execute("INSERT INTO node_type_registry VALUES ('capability', 'node_example')")

    # node_edge_src and node_edge_dst are connected by an edge
    conn.execute("INSERT INTO edge VALUES ('edge_1', 'node_edge_src', 'CONNECTS_TO', 'node_edge_dst', NULL)")
    conn.commit()

    orphans = _find_orphan_nodes(conn)
    orphan_ids = [o["node_id"] for o in orphans]

    assert "node_orphan" in orphan_ids
    assert "node_example" not in orphan_ids
    assert "node_edge_src" not in orphan_ids
    assert "node_edge_dst" not in orphan_ids
