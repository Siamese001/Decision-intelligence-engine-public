"""Unit and integration tests for Wave 2: Multi-Hop Counterfactual & Causal Traversal."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.decision_engine import build_decision_frame
from dual_pipeline.decision_witness import DecisionWitnessEngine
from dual_pipeline.graph_retrieval_core.recursive_traversal import traverse_k_hop_neighborhood
from dual_pipeline.in_memory_graph import InMemoryGraphTopology


def _build_wpb_constraint_graph(db_path: Path) -> InMemoryGraphTopology:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            assertion_type TEXT DEFAULT 'fact',
            atomicity_state TEXT DEFAULT 'atomic',
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality TEXT DEFAULT 'high',
            confidence_label TEXT DEFAULT 'high',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting", "nee-engineering"]',
            hitl_required INTEGER DEFAULT 0,
            origin_type TEXT DEFAULT 'legacy_migration',
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT NOT NULL
        );
        CREATE VIEW v_canonical_edge AS
        SELECT a.assertion_id,
               a.subject_node_id AS from_node_id,
               'RELATES_TO' AS relationship_family,
               a.predicate AS edge_type,
               a.object_node_id AS to_node_id,
               a.materiality,
               'test_source' AS source_id,
               a.hitl_required
          FROM assertion a
         WHERE a.object_node_id IS NOT NULL;
        """
    )
    cur.executemany(
        "INSERT INTO node (node_id, canonical_name, display_name, node_type, active) VALUES (?, ?, ?, ?, 1)",
        [
            ("entity_accelerate_power", "Accelerate Power", "Accelerate Power", "entity"),
            ("person_chakra", "Chakra Sankaraiah", "Chakra Sankaraiah", "person"),
            ("person_amit", "Amit Ayer", "Amit Ayer", "person"),
            ("node_nee_bar_constraint", "NEE Qualification Bar Constraint", "Qualification Bar", "constraint"),
        ],
    )
    cur.executemany(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal, scope_text
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                "asrt_acc_pitch",
                "entity_accelerate_power",
                "HAS_CLAIM_TEXT",
                None,
                "Accelerate Power proposes forward deployed teams billing 42 hours a week at six hundred thousand per quarter.",
                "Accelerate Power proposes forward deployed teams billing 42 hours a week at six hundred thousand per quarter.",
            ),
            (
                "asrt_chakra_pods",
                "person_chakra",
                "HAS_CLAIM_TEXT",
                None,
                "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
            ),
            (
                "asrt_amit_bar",
                "person_amit",
                "GOVERNS",
                "node_nee_bar_constraint",
                "Calibrate candidate screening against NEA 5%-10% qualification bar and pass rate.",
                "Calibrate candidate screening against NEA 5%-10% qualification bar and pass rate.",
            ),
        ],
    )
    cur.executemany(
        "INSERT INTO assertion_evidence VALUES (?, ?, 'supports')",
        [
            ("asrt_acc_pitch", "ev_acc_pitch"),
            ("asrt_chakra_pods", "ev_chakra_pods"),
            ("asrt_amit_bar", "ev_amit_bar"),
        ],
    )
    conn.commit()
    conn.close()
    return InMemoryGraphTopology.load_from_sqlite(db_path)


def test_witness_subgraph_seeds_options_criteria_and_constraints_without_masking(tmp_path: Path) -> None:
    db_path = tmp_path / "witness_constraints.sqlite"
    topology = _build_wpb_constraint_graph(db_path)
    frame = build_decision_frame(
        question="Should we adopt Accelerate Power local pods considering Chakra Sankaraiah operating model?",
        options=["Accelerate Power Local Pods", "Shared Platform Pods"],
        criteria=["Chakra Sankaraiah Operating Model"],
    )
    witness = DecisionWitnessEngine.build_witness_subgraph(
        decision_frame=frame,
        evidence_pack={
            "source_evidence_ids": ["ev_acc_pitch", "ev_chakra_pods", "ev_amit_bar"],
            "constraints": [
                {
                    "record_id": "rec_chakra",
                    "title": "Chakra Sankaraiah pod ratio",
                    "summary": "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                    "evidence_id": "ev_chakra_pods",
                },
                {
                    "record_id": "rec_amit",
                    "title": "NEE Qualification Bar Constraint",
                    "summary": "5%-10% qualification bar pass rate.",
                    "evidence_id": "ev_amit_bar",
                },
            ],
        },
        topology=topology,
        active_pair={
            "generation_id": "gen-wave2-test",
            "graph_sha256": topology.graph_sha256,
            "sidecar_sha256": "0" * 64,
        },
        options=frame["options"],
        assessments=[],
    )
    validate_artifact(witness)
    node_roles = {n["node_id"]: n["role"] for n in witness["witness_nodes"]}
    assert node_roles.get("entity_accelerate_power") == "seed_option"
    assert node_roles.get("person_chakra") == "seed_criterion"
    assert "node_nee_bar_constraint" in witness["extensions"]["com.partner_advisory.nee.constraint-seed-node-ids"]
    edge_types = {e["edge_type"] for e in witness["witness_edges"]}
    assert "CONSTRAINS_CAPACITY_OF" in edge_types or "GOVERNED_BY_STANDARD" in edge_types
    assert len(witness["causal_paths"]) >= 2


def test_recursive_traversal_includes_virtual_constraint_bridges(tmp_path: Path) -> None:
    db_path = tmp_path / "recursive_bridges.sqlite"
    _build_wpb_constraint_graph(db_path)
    res = traverse_k_hop_neighborhood(
        ["entity_accelerate_power"],
        max_hops=2,
        graph_path=db_path,
        include_constraint_bridges=True,
    )
    assert "person_chakra" in res["nodes"]
    assert "person_amit" in res["nodes"]
    edge_types = {e["edge_type"] for e in res["edges"]}
    assert "CONSTRAINS_CAPACITY_OF" in edge_types
    assert "GOVERNED_BY_STANDARD" in edge_types
