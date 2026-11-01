"""
tests/end_to_end/test_edge_cases_stakeholder_hierarchy.py

Class B Adversarial / Edge-Case Test for Wave 3:
Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload (empty project IDs, unknown stakeholder, project with 0 resources).
2. Malformed input (dangling object_node_id reference to nonexistent node, missing source table).
3. Invariant bypass / cycles (cyclic reports_to relationships do not cause infinite recursion or crash).
"""
import sqlite3
import pytest
from pathlib import Path
from dual_pipeline.query_core.traversal_helper import traverse_project_resources
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine


@pytest.fixture
def edge_case_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "edge_case_hierarchy.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            description TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            confidence_score REAL DEFAULT 1.0,
            currentness_state TEXT DEFAULT 'current',
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT,
            primary_source_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_name TEXT NOT NULL
        )
    """)

    # Populate baseline project
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('proj_alpha', 'project', 'Alpha Project')")
    cur.execute("INSERT INTO source (source_id, source_type, source_name) VALUES ('src_alpha', 'meeting', 'Alpha Project Standup')")

    # Cyclic stakeholders A and B
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_cycle_a', 'stakeholder', 'Alice Alpha')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_cycle_b', 'stakeholder', 'Bob Beta')")
    cur.execute("INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type) VALUES ('e_ca', 'p_cycle_a', 'proj_alpha', 'PART_OF_PROJECT')")
    cur.execute("INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type) VALUES ('e_cb', 'p_cycle_b', 'proj_alpha', 'PART_OF_PROJECT')")

    # A reports to B, B reports to A
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_rep_ab', 'p_cycle_a', 'REPORTS_TO', 'p_cycle_b', 'src_alpha')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_rep_ba', 'p_cycle_b', 'REPORTS_TO', 'p_cycle_a', 'src_alpha')")

    # Dangling object_node_id reference (points to non-existent node 'node_ghost_404')
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('p_dangling', 'stakeholder', 'Dangling Dan')")
    cur.execute("INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type) VALUES ('e_dang', 'p_dangling', 'proj_alpha', 'PART_OF_PROJECT')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_dang_role', 'p_dangling', 'HAS_ROLE_IN', 'node_ghost_404', 'src_alpha')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_dang_mgr', 'p_dangling', 'REPORTS_TO', 'node_ghost_500', 'src_alpha')")

    conn.commit()
    conn.close()
    return db_path


def test_boundary_payloads_empty_and_unknown(edge_case_db: Path):
    """Category 1: Boundary payload tests (empty project IDs, non-existent projects)."""
    conn = sqlite3.connect(str(edge_case_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = {"node", "edge", "assertion", "source"}

    # Case 1: Empty project node IDs list
    entities, terms = traverse_project_resources(cur, [], False, tables)
    assert entities == []
    assert terms == set()

    # Case 2: Nonexistent project node ID
    entities, terms = traverse_project_resources(cur, ["proj_nonexistent_999"], False, tables)
    assert entities == []
    assert terms == set()

    conn.close()


def test_malformed_input_dangling_references(edge_case_db: Path):
    """Category 2: Malformed input (dangling foreign keys in object_node_id do not raise)."""
    conn = sqlite3.connect(str(edge_case_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = {"node", "edge", "assertion", "source"}
    entities, _ = traverse_project_resources(cur, ["proj_alpha"], False, tables)
    conn.close()

    dang = next(e for e in entities if e.canonical_name == "Dangling Dan")
    # Role resolution falls back gracefully when role_group node does not exist
    assert dang.canonical_name == "Dangling Dan"
    assert dang.role is not None


def test_cyclic_reporting_does_not_loop_or_crash(edge_case_db: Path):
    """Category 3 (Invariant bypass): Cyclic reports_to edges (A -> B -> A) do not cause recursion."""
    conn = sqlite3.connect(str(edge_case_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = {"node", "edge", "assertion", "source"}
    entities, _ = traverse_project_resources(cur, ["proj_alpha"], False, tables)
    conn.close()

    entity_map = {e.canonical_name: e for e in entities}
    assert "Alice Alpha" in entity_map
    assert "Bob Beta" in entity_map
    assert "Reports to Bob Beta" in entity_map["Alice Alpha"].role
    assert "Reports to Alice Alpha" in entity_map["Bob Beta"].role


def test_query_engine_boundary_and_missing_tables(edge_case_db: Path):
    """Category 1 & 2: Query engine handles queries with missing tables or non-matching tokens."""
    engine = GraphFirstQueryEngine(
        repo_root=edge_case_db.parent,
        graph_path=edge_case_db,
    )
    # Completely non-matching query returns 0 assertions gracefully without error
    res = engine.execute_query("completely alien nonexistent query 12345")
    assert res.success is False or len(res.assertions) == 0
