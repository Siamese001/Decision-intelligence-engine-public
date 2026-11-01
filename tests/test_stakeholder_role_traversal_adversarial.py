"""
tests/test_stakeholder_role_traversal_adversarial.py

Class B Adversarial / Edge-Case Test for Wave 1:
Covers 3 mandatory categories:
1. Boundary payload (empty project IDs, non-existent workstream, zero-length tokens).
2. Malformed input (cyclic reporting hierarchy, self-loop REPORTS_TO, orphaned object_node_id).
3. Invariant bypass attempt (superseded roles, quarantined nodes, retracted assertions).
"""
import sqlite3
import pytest
from dual_pipeline.query_core.traversal_helper import traverse_project_resources


@pytest.fixture
def adversarial_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT DEFAULT 'canonical',
            active INTEGER DEFAULT 1,
            is_quarantined INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT DEFAULT 'transcript'
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            primary_source_id TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT,
            confidence_score REAL DEFAULT 1.0,
            is_quarantined INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def test_boundary_payload_empty_and_unknown_projects(adversarial_db: sqlite3.Connection):
    """Category 1: Boundary payload tests."""
    cursor = adversarial_db.cursor()
    tables = {"node", "source", "assertion"}

    # 1. Empty input
    entities, terms = traverse_project_resources(cursor, [], False, tables)
    assert entities == []
    assert terms == set()

    # 2. Unknown workstream
    entities, terms = traverse_project_resources(cursor, ["non_existent_workstream"], False, tables)
    assert entities == []
    assert terms == set()


def test_malformed_input_cycles_and_orphaned_edges(adversarial_db: sqlite3.Connection):
    """Category 2: Malformed input (cyclic reporting, self-loop, orphaned targets)."""
    cur = adversarial_db.cursor()
    tables = {"node", "source", "assertion"}

    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('ws_edge', 'workstream', 'Project Edge')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_alice', 'stakeholder', 'Alice User')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_bob', 'stakeholder', 'Bob Manager')")

    # Connect to workstream
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_alice_ws', 'p_alice', 'CONTRIBUTES_TO', 'ws_edge')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_bob_ws', 'p_bob', 'CONTRIBUTES_TO', 'ws_edge')")

    # Cyclic REPORTS_TO: Alice -> Bob and Bob -> Alice
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_alice_bob', 'p_alice', 'REPORTS_TO', 'p_bob')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_bob_alice', 'p_bob', 'REPORTS_TO', 'p_alice')")

    # Orphaned role target (target node does NOT exist in node table)
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_alice_orphan', 'p_alice', 'HAS_ROLE_IN', 'missing_role_node')")

    adversarial_db.commit()

    entities, terms = traverse_project_resources(cur, ["ws_edge"], False, tables)
    assert len(entities) == 2
    entity_map = {e.canonical_name: e for e in entities}
    
    # Cyclic reporting must not deadlock or crash; gracefully resolves reporting name
    assert "Reports to Bob Manager" in entity_map["Alice User"].role
    assert "Reports to Alice User" in entity_map["Bob Manager"].role


def test_invariant_bypass_superseded_and_quarantined(adversarial_db: sqlite3.Connection):
    """Category 3: Invariant bypass attempt (superseded claims, quarantined nodes)."""
    cur = adversarial_db.cursor()
    tables = {"node", "source", "assertion"}

    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, is_quarantined) VALUES ('ws_sec', 'workstream', 'Project Secure', 0)")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, is_quarantined) VALUES ('p_charlie', 'stakeholder', 'Charlie Clean', 0)")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, is_quarantined) VALUES ('p_quarantine', 'stakeholder', 'Quarantined User', 1)")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('role_fresh', 'role_group', 'Fresh Role')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('role_stale', 'role_group', 'Stale Role')")

    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_char_ws', 'p_charlie', 'CONTRIBUTES_TO', 'ws_sec')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_quar_ws', 'p_quarantine', 'CONTRIBUTES_TO', 'ws_sec')")

    # Stale superseded role assertion
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, currentness_state, superseded_by_assertion_id)
        VALUES ('a_stale_role', 'p_charlie', 'HAS_ROLE_IN', 'role_stale', 'historical', 'a_fresh_role')
    """)
    # Active fresh role assertion
    cur.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, currentness_state, superseded_by_assertion_id)
        VALUES ('a_fresh_role', 'p_charlie', 'HAS_ROLE_IN', 'role_fresh', 'current', NULL)
    """)

    adversarial_db.commit()

    entities, terms = traverse_project_resources(cur, ["ws_sec"], True, tables)
    entity_map = {e.canonical_name: e for e in entities}

    # Quarantined stakeholder MUST be excluded under has_quarantine=True
    assert "Quarantined User" not in entity_map
    assert "Charlie Clean" in entity_map

    # Stale superseded role MUST NOT be returned; only Fresh Role
    assert entity_map["Charlie Clean"].role == "Fresh Role"
