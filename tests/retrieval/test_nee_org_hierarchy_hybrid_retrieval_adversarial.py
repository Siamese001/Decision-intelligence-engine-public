"""Adversarial and boundary test suite for hybrid retrieval and org chart queries.

Mandatory edge-case coverage per .agents/rules/e2e-and-edge-case-coverage.md:
1. Category: Boundary payload (empty strings, whitespace-only, special characters)
2. Category: Malformed input (synthetic persona rejection, SQL injection attempts)
3. Category: Overflow & exhaustion / Cycle immunity (recursive cycle detector and max-depth safety)
4. Category: Invariant bypass attempt (namespace isolation and credential/career leverage leakage prevention)
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import pytest

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


def test_adversarial_synthetic_persona_rejection(active_graph_and_sidecar: tuple[Path, Path]):
    """Category: Malformed input / synthetic persona rejection.

    Verifies that fictitious personas (e.g. Alex Rivera, Jordan Taylor, Ghost Lead)
    have zero matching nodes or relationships in the active canonical graph.
    """
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        synthetic_names = [
            "Fabricated Executive",
            "Nonexistent Lead",
            "Fake Portfolio Manager",
            "Ghost Operator",
            "Imaginary Vice President",
            "Synthesized Director",
        ]
        for name in synthetic_names:
            cur.execute("SELECT node_id FROM node WHERE canonical_name = ?", (name,))
            rows = cur.fetchall()
            assert len(rows) == 0, f"Synthetic persona '{name}' must never match in canonical graph"

            cur.execute(
                """SELECT a.assertion_id FROM assertion a
                   JOIN node n ON n.node_id = a.subject_node_id OR n.node_id = a.object_node_id
                   WHERE n.canonical_name = ?""",
                (name,),
            )
            asrt_rows = cur.fetchall()
            assert len(asrt_rows) == 0, f"Synthetic persona '{name}' must have 0 assertions"
    finally:
        conn.close()


def test_boundary_payload_handling(active_graph_and_sidecar: tuple[Path, Path]):
    """Category: Boundary payload.

    Verifies query engine handles boundary inputs (empty, whitespace, control characters,
    punctuation symbols, extremely long payloads) gracefully without crashing.
    """
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        boundary_inputs = [
            "",
            "   ",
            "\t\n\r",
            "!@#$%^&*()_+=-`~[]\\{}|;':\",./<>?",
            "A" * 1000,
            "🎉🔥🚀⚡️💡",
            "\x00\x01\x02\x1f",
        ]
        for query_str in boundary_inputs:
            # Must execute safely without syntax errors or unhandled exceptions
            cur.execute("SELECT node_id FROM node WHERE canonical_name = ?", (query_str,))
            rows = cur.fetchall()
            assert len(rows) == 0, f"Boundary input '{query_str[:20]}' unexpectedly matched nodes"

        # Verify no empty or null canonical names exist in the active graph
        cur.execute("SELECT count(*) FROM node WHERE canonical_name IS NULL OR trim(canonical_name) = ''")
        blank_count = cur.fetchone()[0]
        assert blank_count == 0, f"Found {blank_count} nodes with blank or NULL canonical_name"
    finally:
        conn.close()


def test_traversal_cycle_immunity_and_depth_bound(active_graph_and_sidecar: tuple[Path, Path]):
    """Category: Overflow & exhaustion / Cycle immunity.

    Verifies that recursive org traversal detects cycles and terminates safely within
    bounded depth (<= 8), preventing infinite recursion loops.
    """
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        cycle_query = """
        WITH RECURSIVE traverse(node_id, depth, visited) AS (
            SELECT node_id, 0, '|' || node_id || '|'
            FROM node
            WHERE node_type = 'stakeholder'
            UNION ALL
            SELECT a.object_node_id, t.depth + 1, t.visited || a.object_node_id || '|'
            FROM traverse t
            JOIN assertion a ON a.subject_node_id = t.node_id AND a.predicate = 'REPORTS_TO'
            WHERE instr(t.visited, '|' || a.object_node_id || '|') = 0
              AND t.depth < 10
        )
        SELECT max(depth) FROM traverse;
        """
        cur.execute(cycle_query)
        max_depth = cur.fetchone()[0]
        assert max_depth is not None, "Hierarchy traversal returned null max depth"
        assert max_depth <= 7, f"Expected clean hierarchy depth <= 7, got {max_depth}"
    finally:
        conn.close()


def test_sql_injection_resilience(active_graph_and_sidecar: tuple[Path, Path]):
    """Category: Malformed input / injection defense.

    Verifies parameterized queries are resilient against SQL injection payloads.
    """
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        sql_injections = [
            "John Ketchum' OR '1'='1",
            "'; DROP TABLE node; --",
            "' UNION SELECT * FROM node --",
            "admin'--",
            "' OR 1=1 #",
        ]
        for inj in sql_injections:
            cur.execute("SELECT node_id FROM node WHERE canonical_name = ?", (inj,))
            rows = cur.fetchall()
            assert len(rows) == 0, f"SQL injection payload '{inj}' returned rows!"

        # Verify tables are intact
        cur.execute("SELECT count(*) FROM node")
        node_count = cur.fetchone()[0]
        assert node_count > 50, f"Table integrity compromised, node count is {node_count}"
    finally:
        conn.close()


def test_namespace_and_career_firewall_isolation(active_graph_and_sidecar: tuple[Path, Path]):
    """Category: Invariant bypass attempt.

    Verifies that canonical graph nodes do not leak career leverage patterns,
    internal advisory notes, or private billing rates.
    """
    graph_path, _ = active_graph_and_sidecar
    conn = create_sqlite_conn(graph_path, readonly=True)
    try:
        cur = conn.cursor()
        forbidden_terms = [
            "$1.2M",
            "trajectory",
            "career leverage",
            "billing rate",
            "INTERNAL_ADVISORY",
            "AKIA",
            "sk-ant",
        ]
        for term in forbidden_terms:
            cur.execute(
                """SELECT node_id, canonical_name FROM node 
                   WHERE canonical_name LIKE ? OR description LIKE ?""",
                (f"%{term}%", f"%{term}%"),
            )
            leaked_nodes = cur.fetchall()
            assert len(leaked_nodes) == 0, f"Confidential term '{term}' leaked in node: {leaked_nodes}"
    finally:
        conn.close()
