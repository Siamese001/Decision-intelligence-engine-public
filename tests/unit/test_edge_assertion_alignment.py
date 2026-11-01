"""tests/unit/test_edge_assertion_alignment.py

Unit test verifying 100.0% edge-assertion alignment in the active knowledge graph:
- Every edge in `edge` resolves in `legacy_edge_assertion_map`.
- Every mapped assertion exists in `assertion` and possesses a valid `assertion_type`.
- Database foreign key check and integrity check both return cleanly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from storage.connection import configure_pragmas, create_sqlite_conn


def resolve_active_graph_path() -> Path:
    runtime_root = resolve_authoritative_runtime_root()
    active_pair_path = runtime_root / "active_pair.json"
    assert active_pair_path.is_file(), f"Active pair missing at {active_pair_path}"
    pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    graph_path = runtime_root / pair["graph_path"]
    assert graph_path.is_file(), f"Graph database missing at {graph_path}"
    return graph_path


def test_100_percent_edge_assertion_alignment() -> None:
    """Verifies that 100% of edges in the graph have an aligned assertion type."""
    db_path = resolve_active_graph_path()
    conn = create_sqlite_conn(db_path, readonly=True)
    configure_pragmas(conn, readonly=True)

    try:
        cur = conn.cursor()

        # 1. Total edges count
        cur.execute("SELECT COUNT(*) FROM edge")
        total_edges = cur.fetchone()[0]
        assert total_edges > 0, "Graph must contain edges"

        # 2. Total mapped edges
        cur.execute("SELECT COUNT(*) FROM legacy_edge_assertion_map")
        total_mapped = cur.fetchone()[0]
        assert total_mapped >= total_edges, f"Mapped edges ({total_mapped}) must cover total edges ({total_edges})"

        # 3. Unmapped edges count must be exactly 0
        cur.execute(
            """
            SELECT COUNT(*)
            FROM edge e
            WHERE e.edge_id NOT IN (SELECT edge_id FROM legacy_edge_assertion_map)
            """
        )
        unmapped_count = cur.fetchone()[0]
        assert unmapped_count == 0, f"Expected 0 unmapped edges, got {unmapped_count}"

        # 4. Every mapped assertion must exist and have an assertion_type
        cur.execute(
            """
            SELECT COUNT(*)
            FROM edge e
            JOIN legacy_edge_assertion_map m ON e.edge_id = m.edge_id
            JOIN assertion a ON m.assertion_id = a.assertion_id
            WHERE a.assertion_type IS NOT NULL AND a.assertion_type != ''
            """
        )
        aligned_with_type = cur.fetchone()[0]
        assert aligned_with_type == total_edges, (
            f"Expected all {total_edges} edges to have non-empty assertion_type, got {aligned_with_type}"
        )

        # 5. Database integrity and foreign key validity
        cur.execute("PRAGMA integrity_check")
        integrity_row = cur.fetchone()
        assert integrity_row and integrity_row[0] == "ok", f"Integrity check failed: {integrity_row}"

        cur.execute("PRAGMA foreign_key_check")
        fk_violations = cur.fetchall()
        assert len(fk_violations) == 0, f"Foreign key violations found: {fk_violations}"

    finally:
        conn.close()
