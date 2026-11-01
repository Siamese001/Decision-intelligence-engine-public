"""Adversarial and Edge Case Tests for Graph Decoy Quarantine and Claim Deprecation Guards.

Covers mandatory categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte database files fail closed with GRAPH_UNAVAILABLE.
2. Malformed input: Corrupted stubs and schemas missing 'assertion' fail closed.
3. Invariant bypass attempt: Decoy file bypass and prohibited shadow tables fail closed.
4. Exact production regression: Retired July 2026 'claim' queries emit structured deprecation warnings.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from storage.connection import (
    GraphUnavailableError,
    create_sqlite_conn,
    validate_canonical_graph_db,
)
from tools.mcp_graphdb_server import graphdb_read_query


class TestLegacyClaimDeprecationAdversarial:
    """Adversarial coverage for decoy quarantine and claim table deprecation."""

    def test_category_1_boundary_payload_zero_byte_database_fails_closed(self, tmp_path: Path) -> None:
        """Boundary payload: 0-byte database stub must fail closed with GRAPH_UNAVAILABLE."""
        zero_byte_db = tmp_path / "zero_byte_decoy.sqlite"
        zero_byte_db.touch()

        # Direct validator
        with pytest.raises(GraphUnavailableError, match="GRAPH_UNAVAILABLE.*empty.*0 bytes"):
            validate_canonical_graph_db(zero_byte_db)

        # Connection creator in readonly mode
        with pytest.raises(GraphUnavailableError, match="GRAPH_UNAVAILABLE.*0-byte database"):
            create_sqlite_conn(zero_byte_db, readonly=True)

        # MCP tool
        res = graphdb_read_query("SELECT 1", db_path=str(zero_byte_db))
        assert "GRAPH_UNAVAILABLE" in res
        assert "0 bytes" in res

    def test_category_2_malformed_input_missing_assertion_table_fails_closed(self, tmp_path: Path) -> None:
        """Malformed input: SQLite database lacking required 'assertion' table fails closed."""
        fake_db = tmp_path / "legacy_schema_only.sqlite"
        conn = sqlite3.connect(fake_db)
        conn.execute("CREATE TABLE claim (claim_id TEXT PRIMARY KEY, text TEXT)")
        conn.execute("INSERT INTO claim VALUES ('c1', 'deprecated text')")
        conn.commit()
        conn.close()

        # Validator must reject missing assertion table
        with pytest.raises(GraphUnavailableError, match="missing required table 'assertion'"):
            validate_canonical_graph_db(fake_db)

        # MCP tool must reject missing assertion table
        res = graphdb_read_query("SELECT * FROM claim", db_path=str(fake_db))
        assert "GRAPH_UNAVAILABLE" in res
        assert "missing required table 'assertion'" in res
        assert "[DEPRECATION WARNING]" in res

    def test_category_3_invariant_bypass_shadow_graph_and_nonexistent_path(self, tmp_path: Path) -> None:
        """Invariant bypass attempt: Prohibited shadow graph tokens and missing paths fail closed."""
        # Nonexistent path
        missing_db = tmp_path / "does_not_exist.sqlite"
        res = graphdb_read_query("SELECT 1", db_path=str(missing_db))
        assert "GRAPH_UNAVAILABLE" in res or "not found" in res

        # Shadow graph inspection bypass attempt
        res_shadow = graphdb_read_query("SELECT * FROM shadow_graph_nodes")
        assert "Permission denied" in res_shadow

        res_stealth = graphdb_read_query("SELECT stealth_score FROM assertion")
        assert "Permission denied" in res_stealth

    def test_category_4_exact_production_regression_claim_deprecation_warning(self, tmp_path: Path) -> None:
        """Exact production regression: Any query referencing 'claim' emits deprecation warning.

        Verifies that callers targeting retired July 2026 'claim' schema receive
        actionable guidance directing them to 'assertion' and 'search_document'.
        """
        valid_db = tmp_path / "valid_graph.sqlite"
        conn = sqlite3.connect(valid_db)
        conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, content TEXT)")
        conn.execute("CREATE TABLE claim (claim_id TEXT PRIMARY KEY, text TEXT)")
        conn.execute("INSERT INTO assertion VALUES ('a1', 'grounded assertion')")
        conn.execute("INSERT INTO claim VALUES ('c1', 'legacy claim')")
        conn.commit()
        conn.close()

        # Successful query against legacy table should execute but return deprecation warning
        res = graphdb_read_query("SELECT * FROM claim", db_path=str(valid_db))
        data = json.loads(res)
        assert data["count"] == 1
        assert "deprecation_warning" in data
        assert "retired in July 2026" in data["deprecation_warning"]
        assert "assertion" in data["deprecation_warning"]
        assert "search_document" in data["deprecation_warning"]

        # Join query referencing claim
        res_join = graphdb_read_query(
            "SELECT a.assertion_id FROM assertion a JOIN claim c ON a.assertion_id = c.claim_id",
            db_path=str(valid_db),
        )
        data_join = json.loads(res_join)
        assert "deprecation_warning" in data_join

        # Syntax error query with claim still receives deprecation warning
        res_err = graphdb_read_query("SELECT * FROM claim WHERE", db_path=str(valid_db))
        assert "[DEPRECATION WARNING]" in res_err
