"""
tests/test_copilot_scope_alignment_e2e.py

End-to-end lifecycle verification for multi-tenancy scope alignment in Executive Copilot.
Verifies:
1. SQLite assertions lacking explicit engagement_id columns are correctly hydrated from scope_text and source table.
2. CopilotGraphRetriever bound to an active project (e.g. project:project_nexus) indexes non-empty salient assertions.
3. Queries for enterprise anchors like "SAP" and "Maximo" return verified assertions.
4. Foreign tenant assertions (e.g. project_lens) are excluded unless permitted.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import is_assertion_in_scope
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def _create_scoped_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1
        )"""
    )
    cur.execute(
        """CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )"""
    )
    cur.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT DEFAULT 'ACTIVE',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality REAL DEFAULT 1.0,
            classification TEXT DEFAULT 'INTERNAL',
            allowed_audiences_json TEXT DEFAULT '[]',
            scope_text TEXT,
            primary_source_id TEXT
        )"""
    )
    cur.execute(
        """CREATE VIEW v_canonical_edge AS
        SELECT assertion_id, subject_node_id AS from_node_id,
               'RELATION' AS relationship_family, predicate AS edge_type,
               object_node_id AS to_node_id, materiality,
               primary_source_id AS source_id, 0 AS hitl_required
          FROM assertion
         WHERE object_node_id IS NOT NULL"""
    )

    # Insert nodes
    cur.executemany(
        """INSERT INTO node (node_id, canonical_name, display_name, node_type, description, active)
           VALUES (?, ?, ?, ?, ?, 1)""",
        [
            ("entity-sap", "SAP", "SAP ERP", "technical_anchor", "Enterprise ERP system"),
            ("entity-maximo", "Maximo", "IBM Maximo", "technical_anchor", "Asset management"),
            ("node-nexus", "Nexus Portal", "Nexus", "system", "Document AI interface"),
            ("node-lens", "Lens Inspection", "Lens", "system", "Computer vision drone inspection"),
        ],
    )

    # Insert sources
    cur.executemany(
        """INSERT INTO source (source_id, name) VALUES (?, ?)""",
        [
            ("src-nexus-01", "20261009 Project Nexus Standup"),
            ("src-lens-01", "20261005 Lens Architecture Review"),
            ("src-enterprise-01", "Enterprise Technical Anchor Architecture"),
        ],
    )

    # Insert assertions
    cur.executemany(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, object_literal, scope_text, primary_source_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            ("ast-nexus-1", "node-nexus", "USES", "entity-sap", None, "Project Nexus uses SAP for ERP integration.", "src-nexus-01"),
            ("ast-lens-1", "node-lens", "USES", "entity-maximo", None, "Project Lens uses Maximo for work orders.", "src-lens-01"),
            ("ast-sap-anchor", "entity-sap", "HAS_CLAIM_TEXT", None, "SAP is an identified technical_anchor across Apex Global Enterprises.", "V47.2 legacy canonical graph relationship", "src-enterprise-01"),
            ("ast-maximo-anchor", "entity-maximo", "HAS_CLAIM_TEXT", None, "Maximo is an identified technical_anchor for asset management.", "V47.2 legacy canonical graph relationship", "src-enterprise-01"),
        ],
    )

    conn.commit()
    conn.close()


def test_in_memory_graph_scope_hydration_e2e(tmp_path: Path):
    """Verifies that InMemoryGraphTopology hydrates engagement_id from sources and scope_text."""
    db_path = tmp_path / "test_graph.sqlite"
    _create_scoped_test_db(db_path)

    topo = InMemoryGraphTopology.load_from_sqlite(db_path)

    ast_nexus = topo.assertions["ast-nexus-1"]
    assert ast_nexus["engagement_id"] == "project:project_nexus"

    ast_lens = topo.assertions["ast-lens-1"]
    assert ast_lens["engagement_id"] == "project:project_lens"

    ast_sap = topo.assertions["ast-sap-anchor"]
    assert ast_sap["engagement_id"] == "project:enterprise"

    ast_maximo = topo.assertions["ast-maximo-anchor"]
    assert ast_maximo["engagement_id"] == "project:enterprise"


def test_copilot_graph_retriever_scope_filtering_e2e(tmp_path: Path):
    """Verifies CopilotGraphRetriever indexes assertions and enforces boundaries with enterprise anchors."""
    db_path = tmp_path / "test_graph.sqlite"
    _create_scoped_test_db(db_path)

    topo = InMemoryGraphTopology.load_from_sqlite(db_path)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    # Indexing must succeed
    assert len(retriever._salient_assertions) > 0

    # Enterprise technical anchors (SAP, Maximo) are retrievable
    sap_results = retriever.retrieve_structured_assertions("SAP")
    assert len(sap_results) >= 1
    sap_ids = [r["assertion_id"] for r in sap_results]
    assert "ast-sap-anchor" in sap_ids or "ast-nexus-1" in sap_ids

    # Nexus-specific assertion is retrievable
    nexus_results = retriever.retrieve_structured_assertions("Nexus")
    assert len(nexus_results) >= 1
    assert "ast-nexus-1" in [r["assertion_id"] for r in nexus_results]

    # Foreign tenant assertion (ast-lens-1) must be filtered out
    lens_results = retriever.retrieve_structured_assertions("Lens")
    lens_ids = [r["assertion_id"] for r in lens_results]
    assert "ast-lens-1" not in lens_ids


def test_active_graph_sqlite_real_world_e2e():
    """Verifies scope alignment against the real-world active graph database if present."""
    test_dir = Path(__file__).resolve().parent
    repo_cand = test_dir.parent
    if repo_cand.parent.name == ".worktrees":
        primary_repo = repo_cand.parent.parent
    else:
        primary_repo = repo_cand

    active_graph = (
        primary_repo
        / ".local"
        / "dual_pipeline"
        / "generations"
        / "generation-9b0ac81b16d44cb65d410da618b7"
        / "graph.sqlite"
    )
    if not active_graph.is_file():
        pytest.skip(f"Active graph not found at {active_graph}")

    topo = InMemoryGraphTopology.load_from_sqlite(active_graph)
    retriever = CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")

    # Critical fix check: salient assertions count must be strictly greater than 0
    assert len(retriever._salient_assertions) > 0, "Salient assertions must not be empty under project_nexus"

    # Searching for SAP must return candidate assertions
    sap_results = retriever.retrieve_structured_assertions("SAP")
    assert len(sap_results) > 0, "Expected non-zero SAP assertions under project_nexus binding"

    # Check that entity-a5c25654c704b7037bc01a8d81eb (SAP node) has indexed entries
    sap_node_id = "entity-a5c25654c704b7037bc01a8d81eb"
    assert sap_node_id in retriever._salient_assertions
    assert len(retriever._salient_assertions[sap_node_id]) > 0
