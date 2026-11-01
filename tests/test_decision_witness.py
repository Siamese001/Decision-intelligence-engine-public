"""Unit tests for DecisionWitnessEngine (Tier 3 Sealed Witness Subgraphs)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.decision_engine import build_decision_frame
from dual_pipeline.decision_witness import DecisionWitnessEngine
from dual_pipeline.in_memory_graph import InMemoryGraphTopology


@pytest.fixture
def mock_graph_and_topology(tmp_path: Path) -> tuple[Path, InMemoryGraphTopology]:
    db_path = tmp_path / "test_witness_graph.sqlite"
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
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            assertion_type TEXT DEFAULT 'fact',
            atomicity_state TEXT DEFAULT 'atomic',
            canonical_status TEXT DEFAULT 'approved',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality TEXT DEFAULT 'high',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting", "nee-engineering"]',
            hitl_required INTEGER DEFAULT 0,
            origin_type TEXT DEFAULT 'legacy_migration'
        )"""
    )
    cur.execute(
        """CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT NOT NULL
        )"""
    )
    cur.execute(
        """CREATE VIEW v_canonical_edge AS
        SELECT a.assertion_id,
               a.subject_node_id AS from_node_id,
               'RELATES_TO' AS relationship_family,
               a.predicate AS edge_type,
               a.object_node_id AS to_node_id,
               a.materiality,
               'test_source' AS source_id,
               a.hitl_required
          FROM assertion a
         WHERE a.object_node_id IS NOT NULL"""
    )

    cur.executemany(
        """INSERT INTO node (node_id, canonical_name, display_name, node_type, active)
           VALUES (?, ?, ?, ?, 1)""",
        [
            ("node:fdx_greenfield", "FDX Greenfield Build", "FDX Greenfield", "initiative"),
            ("node:catalyst_variance", "Catalyst Delivery Variance", "Variance", "risk"),
            ("node:budget_constraint", "Q4 Budget Constraint", "Budget", "constraint"),
        ],
    )
    cur.executemany(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            materiality, classification, allowed_audiences_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "assert:var1",
                "node:fdx_greenfield",
                "MITIGATES",
                "node:catalyst_variance",
                "high",
                "internal",
                '["partner_advisory_consulting", "nee-engineering"]',
            ),
            (
                "assert:bud1",
                "node:catalyst_variance",
                "EXCEEDS",
                "node:budget_constraint",
                "critical",
                "internal",
                '["partner_advisory_consulting", "nee-engineering"]',
            ),
        ],
    )
    cur.executemany(
        """INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship)
           VALUES (?, ?, ?)""",
        [
            ("assert:var1", "ev:audit:2026:01", "supports"),
            ("assert:bud1", "ev:finance:2026:04", "supports"),
        ],
    )
    conn.commit()
    conn.close()

    topology = InMemoryGraphTopology.load_from_sqlite(db_path)
    return db_path, topology


def test_build_and_validate_decision_witness_subgraph(
    mock_graph_and_topology: tuple[Path, InMemoryGraphTopology],
) -> None:
    _, topology = mock_graph_and_topology

    frame = build_decision_frame(
        question="Should we pursue FDX Greenfield Build considering Catalyst Delivery Variance?",
        options=["FDX Greenfield Build", "Status Quo Maintenance"],
        criteria=["Catalyst Delivery Variance"],
    )

    assessments = [
        {
            "option_id": frame["options"][0]["option_id"],
            "criterion_id": frame["criteria"][0]["criterion_id"],
            "assessment": "SUPPORTS",
            "rationale": "Mitigates variance",
            "supporting_evidence_ids": ["ev:audit:2026:01"],
            "counterevidence_ids": [],
            "constraint_violations": [],
            "material_unknowns": [],
            "freshness_state": "CURRENT",
        }
    ]

    active_pair = {
        "generation_id": "generation-test-1234567890abcdef",
        "graph_sha256": topology.graph_sha256,
        "sidecar_sha256": "0" * 64,
    }

    witness_subgraph = DecisionWitnessEngine.build_witness_subgraph(
        decision_frame=frame,
        evidence_pack={"source_evidence_ids": ["ev:audit:2026:01"]},
        topology=topology,
        active_pair=active_pair,
        options=frame["options"],
        assessments=assessments,
    )

    # 1. Check contract schema compliance
    assert witness_subgraph["contract_id"] == "urn:nee:dual-pipeline:decision-witness-subgraph:1.0.0"
    assert witness_subgraph["schema_version"] == "1.0.0"
    assert witness_subgraph["canonical_digest"]["algorithm"] == "sha256"
    assert len(witness_subgraph["canonical_digest"]["value"]) == 64
    assert witness_subgraph["graph_sha256"] == topology.graph_sha256

    # 2. Check full contract validation passes with schema registry
    validated = validate_artifact(witness_subgraph)
    assert validated["artifact_id"] == witness_subgraph["artifact_id"]

    # 3. Check witness nodes and edges extracted
    node_ids = {n["node_id"] for n in witness_subgraph["witness_nodes"]}
    assert "node:fdx_greenfield" in node_ids
    assert "node:catalyst_variance" in node_ids

    edge_ids = {e["edge_id"] for e in witness_subgraph["witness_edges"]}
    assert "assert:var1:node:fdx_greenfield->node:catalyst_variance" in edge_ids

    # 4. Check evidence bindings
    bindings = witness_subgraph["evidence_bindings"]
    assert len(bindings) >= 1
    assert bindings[0]["evidence_id"] == "ev:audit:2026:01"
    assert bindings[0]["assertion_id"] == "assert:var1"


def test_export_formats(
    mock_graph_and_topology: tuple[Path, InMemoryGraphTopology],
) -> None:
    _, topology = mock_graph_and_topology

    frame = build_decision_frame(
        question="Should we pursue FDX Greenfield Build considering Catalyst Delivery Variance?",
        options=["FDX Greenfield Build"],
        criteria=["Catalyst Delivery Variance"],
    )
    active_pair = {
        "generation_id": "generation-test",
        "graph_sha256": topology.graph_sha256,
        "sidecar_sha256": "0" * 64,
    }

    witness_subgraph = DecisionWitnessEngine.build_witness_subgraph(
        decision_frame=frame,
        evidence_pack={"source_evidence_ids": ["ev:audit:2026:01"]},
        topology=topology,
        active_pair=active_pair,
        options=frame["options"],
        assessments=[],
    )

    # Cytoscape export
    cyto = DecisionWitnessEngine.export_cytoscape(witness_subgraph)
    assert "elements" in cyto
    assert len(cyto["elements"]["nodes"]) >= 1

    # GraphML export
    graphml = DecisionWitnessEngine.export_graphml(witness_subgraph)
    assert "<graphml" in graphml
    assert "G_witness" in graphml
    assert "</graphml>" in graphml
