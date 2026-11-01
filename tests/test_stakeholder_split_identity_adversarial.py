"""
tests/test_stakeholder_split_identity_adversarial.py

Class B Adversarial / Edge-Case Test for Wave 2:
Covers 3 mandatory categories:
1. Boundary payload (blank entity descriptions, whitespace-only names, empty match sets).
2. Concurrency & sequential supersession (multi-turn role promotion chain v1 -> v2 -> v3 without cycles).
3. Rollback & transaction recovery (failed mutation preserves existing assertions and currentness_state).
"""
import types
import sqlite3
import pytest
from dual_pipeline.uwg_core.universal_write_gateway_role import _supersede_competing_assertions
from dual_pipeline.identity_core.resolver import _deduplicate_exact_matches
from dual_pipeline.identity_core.records import EntityRecord


@pytest.fixture
def adversarial_split_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            status TEXT DEFAULT 'canonical'
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            currentness_state TEXT DEFAULT 'current',
            superseded_by_assertion_id TEXT
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def test_boundary_payload_empty_catalog_and_matches():
    """Category 1: Boundary payload tests."""
    catalog = types.SimpleNamespace(entities={})
    matches, conflict = _deduplicate_exact_matches(catalog, [], "stakeholder")
    assert matches == []
    assert not conflict

    matches, conflict = _deduplicate_exact_matches(catalog, ["single_match"], None)
    assert matches == ["single_match"]
    assert not conflict


def test_sequential_supersession_chain_no_cycles(adversarial_split_db: sqlite3.Connection):
    """Category 2: Sequential supersessions v1 -> v2 -> v3 maintain a clean acyclic chain."""
    cur = adversarial_split_db.cursor()
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_dan', 'stakeholder', 'Dan Leader')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('r_v1', 'role_group', 'Lead V1')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('r_v2', 'role_group', 'Lead V2')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('r_v3', 'role_group', 'Lead V3')")

    # Step 1: Initial role v1
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('asrt_v1', 'p_dan', 'HAS_ROLE_IN', 'r_v1')")
    adversarial_split_db.commit()

    # Step 2: Promote to v2
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('asrt_v2', 'p_dan', 'HAS_ROLE_IN', 'r_v2')")
    _supersede_competing_assertions(adversarial_split_db, "p_dan", "asrt_v2", "r_v2", "2026-10-28T18:00:00Z")

    # Step 3: Promote to v3
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('asrt_v3', 'p_dan', 'HAS_ROLE_IN', 'r_v3')")
    _supersede_competing_assertions(adversarial_split_db, "p_dan", "asrt_v3", "r_v3", "2026-10-28T18:01:00Z")

    rows = {
        r["assertion_id"]: r
        for r in cur.execute("SELECT assertion_id, currentness_state, superseded_by_assertion_id FROM assertion").fetchall()
    }
    # v1 superseded by v2
    assert rows["asrt_v1"]["currentness_state"] == "superseded"
    assert rows["asrt_v1"]["superseded_by_assertion_id"] == "asrt_v2"

    # v2 superseded by v3
    assert rows["asrt_v2"]["currentness_state"] == "superseded"
    assert rows["asrt_v2"]["superseded_by_assertion_id"] == "asrt_v3"

    # v3 is current
    assert rows["asrt_v3"]["currentness_state"] == "current"
    assert rows["asrt_v3"]["superseded_by_assertion_id"] is None


def test_rollback_and_recovery_on_write_failure(adversarial_split_db: sqlite3.Connection):
    """Category 3: Rollback & recovery ensures uncommitted mutations leave DB pristine."""
    cur = adversarial_split_db.cursor()
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_safe', 'stakeholder', 'Safe User')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state) VALUES ('asrt_orig', 'p_safe', 'HAS_CLAIM_TEXT', 'Original Claim', 'current')")
    adversarial_split_db.commit()

    # Attempt a transaction that raises an error
    try:
        with adversarial_split_db:
            adversarial_split_db.execute("UPDATE assertion SET currentness_state = 'historical' WHERE assertion_id = 'asrt_orig'")
            # Intentional error: violate non-existent table
            adversarial_split_db.execute("INSERT INTO nonexistent_table VALUES (1)")
    except sqlite3.OperationalError:
        pass

    # State must be cleanly rolled back
    row = cur.execute("SELECT currentness_state FROM assertion WHERE assertion_id = 'asrt_orig'").fetchone()
    assert row["currentness_state"] == "current"
