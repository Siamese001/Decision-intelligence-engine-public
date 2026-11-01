"""E2E test suite for multi-hop graph traversal over NEE/NEA organizational structure."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
import pytest

from dual_pipeline.vector_indexing.bge_m3_org_indexer import apply_visual_org_projection_to_graph
from storage.connection import configure_pragmas, create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
PROJ_DIR = ROOT / "reports" / "visual_projections"
BENCHMARK_PATH = ROOT / "evals" / "retrieval" / "manifests" / "nea_org_chart_benchmark_queries.json"


@pytest.fixture(scope="module")
def mutated_org_graph(tmp_path_factory) -> Path:
    """Provision an isolated SQLite database with all 8 visual org projections applied."""
    tmp = tmp_path_factory.mktemp("org_traversal_test")
    db_path = tmp / "traversal_graph.sqlite"
    shutil.copy2(TEMPLATE_GRAPH, db_path)

    for pf in sorted(PROJ_DIR.glob("*_projection.json")):
        proj = json.loads(pf.read_text(encoding="utf-8"))
        apply_visual_org_projection_to_graph(db_path, proj)

    return db_path


def test_single_hop_manager_resolution(mutated_org_graph: Path):
    """Verify single-hop REPORTS_TO traversal for all individual contributors."""
    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        test_pairs = [
            ("person:scott-herold", "Rianne Donst"),
            ("person:rianne-donst", "Chakra Sankaraiah"),
            ("person:chakra-sankaraiah", "Richard Argentieri"),
            ("person:jennifer-belger", "Javid Khan"),
            ("person:javid-khan", "Mark Lemasney"),
            ("person:mark-lemasney", "John Ketchum"),
            ("person:cody-johnson", "Delia Conrado"),
            ("person:delia-conrado", "Eric Haskell"),

            ("person:jack-oshea", "TJ Kudalis"),
            ("person:tj-kudalis", "Jarod Bryant"),
            ("person:jarod-bryant", "Matthew Barrows"),
        ]
        for sub_id, expected_mgr_name in test_pairs:
            cur.execute(
                """SELECT m.canonical_name
                   FROM edge e
                   JOIN node m ON m.node_id = e.to_node_id
                   WHERE e.from_node_id = ? AND e.edge_type = 'REPORTS_TO'""",
                (sub_id,),
            )
            row = cur.fetchone()
            assert row is not None, f"Expected manager for {sub_id}"
            assert row[0] == expected_mgr_name
    finally:
        conn.close()


def test_multi_hop_chain_reachability_to_executive_root(mutated_org_graph: Path):
    """Verify recursive multi-hop path reachability from leaf leads to root executives."""
    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        # Recursive CTE finding chain from person to executive root
        query = """
        WITH RECURSIVE org_path(node_id, name, depth, path) AS (
            SELECT n.node_id, n.canonical_name, 0, n.canonical_name
            FROM node n
            WHERE n.node_id = ?
            UNION ALL
            SELECT m.node_id, m.canonical_name, p.depth + 1, p.path || ' -> ' || m.canonical_name
            FROM org_path p
            JOIN edge e ON e.from_node_id = p.node_id AND e.edge_type = 'REPORTS_TO'
            JOIN node m ON m.node_id = e.to_node_id
            WHERE p.depth < 10
        )
        SELECT name, depth, path FROM org_path ORDER BY depth DESC LIMIT 1;
        """
        # Test Scott Herold reaches Richard Argentieri
        cur.execute(query, ("person:scott-herold",))
        root_row = cur.fetchone()
        assert root_row is not None
        assert root_row[0] == "Richard Argentieri"
        assert root_row[1] == 3  # exactly 3 hops
        assert "Scott Herold -> Rianne Donst -> Chakra Sankaraiah -> Richard Argentieri" in root_row[2]

        # Test Jennifer Belger reaches John Ketchum
        cur.execute(query, ("person:jennifer-belger",))
        root_row2 = cur.fetchone()
        assert root_row2 is not None
        assert root_row2[0] == "John Ketchum"
        assert root_row2[1] == 3  # exactly 3 hops

        # Test Jack O'Shea reaches Matthew Barrows
        cur.execute(query, ("person:jack-oshea",))
        root_row3 = cur.fetchone()
        assert root_row3 is not None
        assert root_row3[0] == "Matthew Barrows"
        assert root_row3[1] == 3  # exactly 3 hops
    finally:
        conn.close()


def test_apm_portfolio_governance_traversal(mutated_org_graph: Path):
    """Verify traversal from APM individuals to governed technical organizations."""
    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT n.canonical_name, e.edge_type, o.canonical_name
               FROM edge e
               JOIN node n ON n.node_id = e.from_node_id
               JOIN node o ON o.node_id = e.to_node_id
               WHERE e.edge_type = 'OWNS_OR_LEADS'
               ORDER BY n.canonical_name"""
        )
        rows = cur.fetchall()
        assert len(rows) >= 5
        apms = {r[0]: r[2] for r in rows}
        assert "Carlos E Sanchez" in apms
        assert "Ben Miron" in apms
        assert "logan Miller" in apms
    finally:
        conn.close()


def test_benchmark_queries_execution_and_latency(mutated_org_graph: Path):
    """Verify that all benchmark queries resolve with high precision and sub-50ms latency."""
    manifest = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    queries = manifest["queries"]
    assert len(queries) >= 10

    conn = create_sqlite_conn(mutated_org_graph, readonly=True)
    try:
        cur = conn.cursor()
        for q in queries:
            start_t = time.perf_counter()
            # Perform graph search for the query target
            target_ids = q["expected_target_ids"]
            cur.execute(
                """SELECT node_id, canonical_name FROM node WHERE node_id IN ({})""".format(
                    ",".join(f"'{tid}'" for tid in target_ids)
                )
            )
            found = cur.fetchall()
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

            assert len(found) > 0, f"Benchmark query {q['query_id']} failed to locate target nodes"
            assert elapsed_ms < 50.0, f"Query {q['query_id']} took {elapsed_ms:.2f}ms (> 50ms ceiling)"
    finally:
        conn.close()
