"""E2E Test: NEE Complete Org Hierarchy Multi-Hop Traversal & Vector Synchronization.

Validates recursive multi-hop path reachability up to the enterprise executive root
(John Ketchum) in <= 6 hops with 100% path accuracy, and verifies BGE-M3 1024-dim
vector sidecar synchronization and health.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
import pytest

from dual_pipeline.graph_delta_core.vector_health import inspect_vector_health
from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def active_graph_and_sidecar() -> tuple[Path, Path]:
    """Resolve active graph and sidecar paths from active_pair.json."""
    active_pair_file = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
    assert active_pair_file.is_file(), f"Missing active pair file: {active_pair_file}"
    active_pair = json.loads(active_pair_file.read_text(encoding="utf-8"))

    graph_path = ROOT / ".local" / "dual_pipeline" / active_pair["graph_path"]
    sidecar_path = ROOT / ".local" / "dual_pipeline" / active_pair["sidecar_path"]

    assert graph_path.is_file(), f"Missing graph database: {graph_path}"
    assert sidecar_path.is_file(), f"Missing sidecar database: {sidecar_path}"
    return graph_path, sidecar_path


def test_single_hop_leadership_reporting_resolution(active_graph_and_sidecar: tuple[Path, Path]):
    """Verify single-hop REPORTS_TO assertion resolution across executive and director leadership."""
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        test_pairs = [
            ("Petter Skantze", "John Ketchum"),
            ("Mark Lemasney", "John Ketchum"),
            ("Scott Bores", "John Ketchum"),
            ("Nicole Daggs", "John Ketchum"),
            ("Richard Argentieri", "Petter Skantze"),
            ("Matthew Barrows", "Richard Argentieri"),
            ("Jarod Bryant", "Matthew Barrows"),
            ("Lancey Lexima", "Jarod Bryant"),
            ("Tom Glasser", "Lancey Lexima"),
            ("Peter Sander", "Petter Skantze"),
            ("Christopher Nunalee", "Richard Argentieri"),
            ("Javid Khan", "Mark Lemasney"),
            ("Parker Vance", "Javid Khan"),
            ("Daniel Sabet", "Parker Vance"),
            ("Max Villwock", "Daniel Sabet"),
            ("Mahogany B. Campbell", "Nicole Daggs"),
        ]
        for sub_name, expected_mgr_name in test_pairs:
            cur.execute(
                """SELECT m.canonical_name
                   FROM assertion a
                   JOIN node s ON s.node_id = a.subject_node_id
                   JOIN node m ON m.node_id = a.object_node_id
                   WHERE s.canonical_name = ? AND a.predicate = 'REPORTS_TO'""",
                (sub_name,),
            )
            rows = [r[0] for r in cur.fetchall()]
            assert expected_mgr_name in rows, (
                f"Expected manager '{expected_mgr_name}' for '{sub_name}', got {rows}"
            )
    finally:
        conn.close()


def test_multi_hop_traversal_reachability_to_executive_root(active_graph_and_sidecar: tuple[Path, Path]):
    """Verify recursive CTE multi-hop path reachability from leaf leads to John Ketchum in <= 6 hops."""
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        query = """
        WITH RECURSIVE org_path(node_id, name, depth, path) AS (
            SELECT n.node_id, n.canonical_name, 0, n.canonical_name
            FROM node n
            WHERE n.canonical_name = ?
            UNION ALL
            SELECT m.node_id, m.canonical_name, p.depth + 1, p.path || ' -> ' || m.canonical_name
            FROM org_path p
            JOIN assertion a ON a.subject_node_id = p.node_id AND a.predicate = 'REPORTS_TO'
            JOIN node m ON m.node_id = a.object_node_id
            WHERE p.depth < 10
        )
        SELECT name, depth, path FROM org_path ORDER BY depth DESC LIMIT 1;
        """

        traversal_expectations = [
            ("Tom Glasser", "John Ketchum", 6, "Tom Glasser -> Lancey Lexima -> Jarod Bryant -> Matthew Barrows -> Richard Argentieri -> Petter Skantze -> John Ketchum"),
            ("Max Villwock", "John Ketchum", 5, "Max Villwock -> Daniel Sabet -> Parker Vance -> Javid Khan -> Mark Lemasney -> John Ketchum"),
            ("Peter Sander", "John Ketchum", 2, "Peter Sander -> Petter Skantze -> John Ketchum"),
            ("Christopher Nunalee", "John Ketchum", 3, "Christopher Nunalee -> Richard Argentieri -> Petter Skantze -> John Ketchum"),
            ("Mahogany B. Campbell", "John Ketchum", 2, "Mahogany B. Campbell -> Nicole Daggs -> John Ketchum"),
            ("Scott Bores", "John Ketchum", 1, "Scott Bores -> John Ketchum"),
            ("Lancey Lexima", "John Ketchum", 5, "Lancey Lexima -> Jarod Bryant -> Matthew Barrows -> Richard Argentieri -> Petter Skantze -> John Ketchum"),
        ]

        for start_name, expected_root, expected_hops, expected_subpath in traversal_expectations:
            cur.execute(query, (start_name,))
            row = cur.fetchone()
            assert row is not None, f"No traversal path found starting from '{start_name}'"
            root_name, depth, full_path = row
            assert root_name == expected_root, f"Expected root '{expected_root}' for '{start_name}', got '{root_name}'"
            assert depth == expected_hops, f"Expected {expected_hops} hops for '{start_name}', got {depth} (path: {full_path})"
            assert depth <= 6, f"Depth {depth} exceeds max 6-hop ceiling for '{start_name}'"
            assert expected_subpath in full_path, f"Path '{full_path}' does not contain expected sequence '{expected_subpath}'"
    finally:
        conn.close()


def test_vector_sidecar_synchronization_and_health(active_graph_and_sidecar: tuple[Path, Path]):
    """Verify BGE-M3 vector sidecar synchronization, 1024-dim dense vectors, and zero anomalies."""
    _, sidecar_path = active_graph_and_sidecar
    health = inspect_vector_health(sidecar_path)

    assert health["status"] == "HEALTHY", f"Sidecar vector health check failed: {health}"
    assert len(health.get("anomalies", [])) == 0, f"Detected vector anomalies: {health.get('anomalies')}"

    dense_stats = health["tables"]["dense_vector"]
    assert dense_stats["table_present"] is True
    assert dense_stats["total_rows"] >= 1500, f"Expected >= 1500 dense vectors, got {dense_stats['total_rows']}"
    assert dense_stats["nan_count"] == 0
    assert dense_stats["inf_count"] == 0
    assert dense_stats["zero_vector_count"] == 0
    assert dense_stats["dimension_mismatch_count"] == 0
    assert dense_stats["corrupt_blob_count"] == 0
    assert 0.99 <= dense_stats["min_norm"] <= 1.01
    assert 0.99 <= dense_stats["max_norm"] <= 1.01

    conn = create_sqlite_conn(sidecar_path, readonly=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT dimensions FROM dense_vector")
        dims = [r[0] for r in cur.fetchall()]
        assert dims == [1024], f"Expected all vectors to have dimension 1024, got {dims}"

        # Verify key leadership records are indexed
        cur.execute(
            """SELECT count(*) FROM retrieval_record
               WHERE artifact_json LIKE '%John Ketchum%'
                  OR artifact_json LIKE '%Petter Skantze%'
                  OR artifact_json LIKE '%Scott Bores%'"""
        )
        exec_record_count = cur.fetchone()[0]
        assert exec_record_count > 0, "No retrieval records found referencing key executive leadership"
    finally:
        conn.close()


def test_benchmark_traversal_execution_latency(active_graph_and_sidecar: tuple[Path, Path]):
    """Verify all multi-hop traversal queries resolve in sub-50ms latency."""
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        query = """
        WITH RECURSIVE org_path(node_id, name, depth, path) AS (
            SELECT n.node_id, n.canonical_name, 0, n.canonical_name
            FROM node n
            WHERE n.canonical_name = ?
            UNION ALL
            SELECT m.node_id, m.canonical_name, p.depth + 1, p.path || ' -> ' || m.canonical_name
            FROM org_path p
            JOIN assertion a ON a.subject_node_id = p.node_id AND a.predicate = 'REPORTS_TO'
            JOIN node m ON m.node_id = a.object_node_id
            WHERE p.depth < 10
        )
        SELECT name, depth, path FROM org_path ORDER BY depth DESC LIMIT 1;
        """

        benchmark_targets = [
            "Tom Glasser",
            "Max Villwock",
            "Peter Sander",
            "Christopher Nunalee",
            "Lancey Lexima",
            "Scott Bores",
        ]

        latencies_ms = []
        for target in benchmark_targets:
            start_t = time.perf_counter()
            cur.execute(query, (target,))
            row = cur.fetchone()
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            latencies_ms.append(elapsed_ms)

            assert row is not None, f"Query failed for '{target}'"
            assert elapsed_ms < 50.0, f"Query for '{target}' took {elapsed_ms:.2f}ms (> 50ms ceiling)"

        avg_latency = sum(latencies_ms) / len(latencies_ms)
        assert avg_latency < 20.0, f"Average latency {avg_latency:.2f}ms exceeds 20ms target"
    finally:
        conn.close()
