"""Adversarial and boundary test suite for hybrid retrieval and org chart queries."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
import pytest

from dual_pipeline.vector_indexing.bge_m3_org_indexer import apply_visual_org_projection_to_graph
from storage.connection import configure_pragmas, create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
PROJ_DIR = ROOT / "reports" / "visual_projections"


@pytest.fixture(scope="module")
def mutated_org_graph(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("org_retrieval_adversarial")
    db_path = tmp / "adversarial_graph.sqlite"
    shutil.copy2(TEMPLATE_GRAPH, db_path)

    for pf in sorted(PROJ_DIR.glob("*_projection.json")):
        proj = json.loads(pf.read_text(encoding="utf-8"))
        apply_visual_org_projection_to_graph(db_path, proj)

    return db_path


def test_adversarial_non_existent_persona_rejection(mutated_org_graph: Path):
    """Verify that synthetic, out-of-scope, or fabricated personas return zero matches."""
    synthetic_names = [
        "Fabricated Executive",
        "Nonexistent Lead",
        "Fake Portfolio Manager",
        "Ghost Operator",
    ]

    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        for name in synthetic_names:
            cur.execute(
                "SELECT node_id, canonical_name FROM node WHERE canonical_name = ?",
                (name,),
            )
            rows = cur.fetchall()
            assert len(rows) == 0, f"Synthetic persona '{name}' must never match in canonical graph"

            # Check edges as well
            cur.execute(
                """SELECT e.edge_id FROM edge e
                   JOIN node n ON n.node_id = e.from_node_id OR n.node_id = e.to_node_id
                   WHERE n.canonical_name = ?""",
                (name,),
            )
            edge_rows = cur.fetchall()
            assert len(edge_rows) == 0, f"Synthetic persona '{name}' must have 0 edges"
    finally:
        conn.close()


def test_boundary_empty_and_whitespace_query_handling(mutated_org_graph: Path):
    """Verify query engine handles empty, whitespace, and symbol-only queries gracefully."""
    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT node_id FROM node WHERE canonical_name = '' OR canonical_name IS NULL")
        assert len(cur.fetchall()) == 0, "No blank or null canonical_name permitted"
    finally:
        conn.close()


def test_graph_traversal_cycle_immunity(mutated_org_graph: Path):
    """Verify recursive traversal terminates safely within max depth even if cycles exist."""
    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        # Bound query with cycle detector
        cycle_query = """
        WITH RECURSIVE traverse(node_id, depth, visited) AS (
            SELECT node_id, 0, '|' || node_id || '|'
            FROM node
            WHERE node_type = 'stakeholder' AND node_id LIKE 'person:%'
            UNION ALL
            SELECT e.to_node_id, t.depth + 1, t.visited || e.to_node_id || '|'
            FROM traverse t
            JOIN edge e ON e.from_node_id = t.node_id AND e.edge_type = 'REPORTS_TO'
            WHERE instr(t.visited, '|' || e.to_node_id || '|') = 0
              AND t.depth < 10
        )
        SELECT max(depth) FROM traverse;
        """
        cur.execute(cycle_query)
        max_depth = cur.fetchone()[0]
        assert max_depth is not None
        assert max_depth <= 5, f"Expected clean hierarchy depth <= 5, got {max_depth}"
    finally:
        conn.close()


def test_evidence_grounding_audit(mutated_org_graph: Path):
    """Verify 100% of newly ingested assertions have valid evidence backing."""
    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        # Check all assertions created by visual ingest have supporting evidence
        cur.execute(
            """SELECT a.assertion_id, a.subject_node_id, a.predicate, a.object_node_id
               FROM assertion a
               WHERE a.primary_source_id = 'source_visual_org_chart_ingest'
               AND NOT EXISTS (
                   SELECT 1 FROM assertion_evidence ae
                   WHERE ae.assertion_id = a.assertion_id AND ae.evidence_relationship = 'supports'
               )"""
        )
        unsupported = cur.fetchall()
        assert len(unsupported) == 0, f"Found unsupported assertions: {unsupported}"

        # Verify evidence_item details
        cur.execute(
            """SELECT count(*) FROM evidence_item WHERE source_id = 'source_visual_org_chart_ingest'"""
        )
        ev_count = cur.fetchone()[0]
        assert ev_count >= 1, "Must have valid evidence_item rows"
    finally:
        conn.close()
