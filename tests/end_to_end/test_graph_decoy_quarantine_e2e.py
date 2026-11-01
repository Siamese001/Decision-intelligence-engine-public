"""End-to-End Tests for Graph Decoy Quarantine and Schema Deprecation Guards.

Verifies:
1. Default graph resolution targets the authoritative active generation (>1MB with assertion table).
2. MCP graphdb_read_query executes against the active graph and reads real assertions.
3. Querying retired 'claim' table returns structured deprecation warning.
4. Storage connection validate_graph flag enforces schema and size bounds.
5. Workspace roots are clean of 0-byte decoy stubs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.runtime_root import resolve_active_graph_path
from storage.connection import (
    GraphUnavailableError,
    create_sqlite_conn,
    validate_canonical_graph_db,
)
from tools.mcp_graphdb_server import (
    _resolve_default_graph_path,
    graphdb_diagnose_locks,
    graphdb_read_query,
)


class TestGraphDecoyQuarantineE2E:
    """End-to-End verification of decoy quarantine and schema guards."""

    def test_e2e_default_graph_resolution_points_to_active_generation(self) -> None:
        """Verify default graph path resolves to the active generation with valid size and schema."""
        resolved = _resolve_default_graph_path()
        assert resolved.is_file(), f"Resolved graph path does not exist: {resolved}"

        # Must be at least 1MB
        size = resolved.stat().st_size
        assert size >= 1024 * 1024, f"Active graph {resolved} size ({size}) is less than 1MB"

        # Must contain assertion table
        validated = validate_canonical_graph_db(resolved)
        assert validated == resolved

    def test_e2e_graphdb_read_query_active_assertions(self) -> None:
        """Verify graphdb_read_query reads assertions from authoritative active graph."""
        res = graphdb_read_query("SELECT count(*) as total FROM assertion")
        data = json.loads(res)
        assert data["count"] == 1
        total_assertions = data["rows"][0]["total"]
        assert total_assertions > 0, "Active generation has zero assertions"
        assert "deprecation_warning" not in data

    def test_e2e_graphdb_read_query_claim_deprecation_on_active_graph(self) -> None:
        """Verify querying 'claim' on active graph fails with clear deprecation notice."""
        res = graphdb_read_query("SELECT * FROM claim LIMIT 1")
        assert "[DEPRECATION WARNING]" in res
        assert "retired in July 2026" in res
        assert "assertion" in res

    def test_e2e_storage_connection_validation_gate(self, tmp_path: Path) -> None:
        """Verify create_sqlite_conn validate_graph parameter passes active graph and blocks decoys."""
        active_graph = resolve_active_graph_path(enforce_integrity=False)
        conn = create_sqlite_conn(active_graph, readonly=True, validate_graph=True)
        cur = conn.execute("SELECT count(*) as total FROM assertion")
        assert cur.fetchone()["total"] > 0
        conn.close()

        # Decoy empty stub must fail closed
        decoy = tmp_path / "CANONICAL_GRAPH.db"
        decoy.touch()
        with pytest.raises(GraphUnavailableError, match="GRAPH_UNAVAILABLE"):
            create_sqlite_conn(decoy, readonly=True, validate_graph=True)

    def test_e2e_workspace_decoys_quarantined(self) -> None:
        """Verify known decoy stubs do not exist in repo root or .local/."""
        repo_root = Path(__file__).resolve().parents[2]
        decoys = [
            repo_root / "CANONICAL_GRAPH.db",
            repo_root / "canonical_graph.sqlite",
            repo_root / "dual_pipeline" / "CANONICAL_GRAPH.db",
            repo_root / "graph" / "CANONICAL_GRAPH.db",
            repo_root / "storage" / "CANONICAL_GRAPH.db",
            repo_root / ".local" / "CANONICAL_GRAPH.db",
            repo_root / ".local" / "dual_pipeline" / "CANONICAL_GRAPH.db",
            repo_root / ".local" / "dual_pipeline" / "canonical_graph.sqlite",
            repo_root / ".local" / "dual_pipeline" / "graph.db",
            repo_root / ".local" / "dual_pipeline" / "runtime" / "graph.sqlite",
        ]
        for decoy in decoys:
            assert not decoy.exists(), f"Decoy database stub still present at {decoy}"

    def test_e2e_diagnose_locks_on_active_graph(self) -> None:
        """Verify graphdb_diagnose_locks executes on active graph cleanly."""
        res = graphdb_diagnose_locks()
        data = json.loads(res)
        assert data["exists"] is True
        assert data["size_bytes"] >= 1024 * 1024
        assert data["lock_status"] == "ACCESSIBLE_NO_LOCK_CONTENTION"
