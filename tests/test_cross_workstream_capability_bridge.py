"""Hermetic tests for cross-workstream technical alias and capability-domain bridging."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Set

from dual_pipeline.graph_csr_sidecar import CSRGraphReader, compile_csr_from_graph
from dual_pipeline.graph_retrieval_core.capability_bridge import (
    SHARED_CAPABILITY_RELATIONSHIP,
    SPLIT_IDENTITY_RELATIONSHIP,
    collect_capability_bridge_pairs,
    collect_technical_alias_pairs,
)
from dual_pipeline.graph_retrieval_core.graph_cache import read_graph_from_disk
from tools.audit_orphan_technical_anchors import audit_technical_anchors

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "intake"
    / "dan_okray_drone_cv_s3_bridge_package.json"
)


def _create_disjoint_workstream_db(db_path: Path) -> None:
    """Create a hermetic SQLite graph with disjoint Transmission (Vanguard) and Analytics (LENS) islands."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                materiality TEXT DEFAULT 'high',
                canonical_status TEXT DEFAULT 'canonical',
                validation_state TEXT DEFAULT 'accepted_legacy',
                currentness_state TEXT DEFAULT 'current',
                retrieval_eligible INTEGER DEFAULT 1,
                primary_source_id TEXT,
                superseded_by_assertion_id TEXT,
                recorded_at TEXT DEFAULT '2026-10-30T00:00:00Z',
                created_at TEXT DEFAULT '2026-10-30T00:00:00Z',
                confidence_score REAL DEFAULT 0.92
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_relationship TEXT NOT NULL DEFAULT 'supports'
            );
            CREATE TABLE v_canonical_edge (
                assertion_id TEXT NOT NULL,
                from_node_id TEXT NOT NULL,
                relationship_family TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                materiality TEXT NOT NULL DEFAULT 'high',
                source_id TEXT
            );
            CREATE TABLE v_conflicting_assertions (
                assertion_id_1 TEXT NOT NULL,
                assertion_id_2 TEXT NOT NULL,
                resolution_state TEXT NOT NULL DEFAULT 'resolved'
            );
            """
        )
        nodes = [
            # Transmission (Vanguard) island - zero canonical edges on Percepto & Buzz Solutions
            ("entity-percepto", "Percepto", "technical_anchor", 1),
            ("entity-skydio", "Skydio Drone", "technical_anchor", 1),
            ("entity-buzz", "Buzz Solutions", "technical_anchor", 1),
            ("entity-pano", "Pano AI", "technical_anchor", 1),
            ("entity-pano-alerts", "Pano camera alerts", "technical_anchor", 1),
            # Analytics (LENS) island - split S3 identities and isolated Vision Model
            ("entity-amazon-s3", "Amazon S3", "technical_anchor", 1),
            ("entity-aws-s3", "AWS S3", "technical_anchor", 1),
            ("entity-lens-s3-pipe", "LENS S3 Ingestion Pipeline", "technical_anchor", 1),
            ("entity-vision-model", "Vision Model", "technical_anchor", 1),
            ("entity-few-shot", "Few-Shot Vision Prompting", "technical_anchor", 1),
        ]
        conn.executemany(
            "INSERT INTO node (node_id, canonical_name, node_type, active) VALUES (?, ?, ?, ?)",
            nodes,
        )
        assertions = [
            (
                "asrt-percepto",
                "entity-percepto",
                "HAS_CLAIM_TEXT",
                "Percepto: Autonomous drones depicted inspecting stations and structures.",
                "source-vanguard",
            ),
            (
                "asrt-skydio",
                "entity-skydio",
                "HAS_CLAIM_TEXT",
                "Skydio Drone: Dispatches resources from one automated system.",
                "source-vanguard",
            ),
            (
                "asrt-buzz",
                "entity-buzz",
                "HAS_CLAIM_TEXT",
                "Buzz Solutions: Image-analysis technology depicted scoring damage severity from drone imagery.",
                "source-vanguard",
            ),
            (
                "asrt-amazon-s3",
                "entity-amazon-s3",
                "HAS_CLAIM_TEXT",
                "Amazon S3: Cloud object storage service hosting inspection imagery.",
                "source-lens",
            ),
            (
                "asrt-aws-s3",
                "entity-aws-s3",
                "HAS_CLAIM_TEXT",
                "AWS S3: Upload destination for technician nameplate images.",
                "source-lens",
            ),
            (
                "asrt-lens-s3",
                "entity-lens-s3-pipe",
                "HAS_CLAIM_TEXT",
                "LENS S3 Ingestion Pipeline: Image ingestion pipeline detecting, validating, and queueing nameplate images from AWS S3.",
                "source-lens",
            ),
            (
                "asrt-vision",
                "entity-vision-model",
                "HAS_CLAIM_TEXT",
                "Vision Model: Computer vision model guided by few-shot prompt context.",
                "source-lens",
            ),
        ]
        conn.executemany(
            """INSERT INTO assertion (
                   assertion_id, subject_node_id, predicate, object_literal, primary_source_id
               ) VALUES (?, ?, ?, ?, ?)""",
            assertions,
        )
        conn.commit()
    finally:
        conn.close()


def _bfs_reachable(adjacency: Dict[str, list], start_id: str, max_hops: int = 2) -> Set[str]:
    visited: Set[str] = {start_id}
    frontier: Set[str] = {start_id}
    for _ in range(max_hops):
        next_frontier: Set[str] = set()
        for current in frontier:
            for edge in adjacency.get(current, []):
                target = edge["to_node_id"]
                if target not in visited:
                    visited.add(target)
                    next_frontier.add(target)
        frontier = next_frontier
    return visited


def test_technical_alias_unifies_split_s3_and_pano_nodes(tmp_path: Path) -> None:
    db_path = tmp_path / "test_graph.sqlite"
    _create_disjoint_workstream_db(db_path)
    nodes, assertions, _ev, adjacency, _contra, _conf, _excl = read_graph_from_disk(db_path)

    alias_pairs = collect_technical_alias_pairs(nodes)
    assert ("entity-amazon-s3", "entity-aws-s3") in alias_pairs
    assert ("entity-amazon-s3", "entity-lens-s3-pipe") in alias_pairs
    assert ("entity-pano", "entity-pano-alerts") in alias_pairs

    s3_edges = [
        e for e in adjacency["entity-amazon-s3"]
        if e["to_node_id"] == "entity-aws-s3" and e["relationship_family"] == SPLIT_IDENTITY_RELATIONSHIP
    ]
    assert len(s3_edges) == 1


def test_zero_edge_drone_and_vision_anchors_connect_across_workstreams(tmp_path: Path) -> None:
    db_path = tmp_path / "test_graph.sqlite"
    _create_disjoint_workstream_db(db_path)
    nodes, assertions, _ev, adjacency, _contra, _conf, _excl = read_graph_from_disk(db_path)

    assert len(adjacency["entity-percepto"]) > 0
    assert len(adjacency["entity-buzz"]) > 0
    assert any(
        e["relationship_family"] == SHARED_CAPABILITY_RELATIONSHIP
        for e in adjacency["entity-percepto"]
    )

    reachable_from_percepto = _bfs_reachable(adjacency, "entity-percepto", max_hops=2)
    assert "entity-skydio" in reachable_from_percepto
    assert "entity-buzz" in reachable_from_percepto
    assert "entity-vision-model" in reachable_from_percepto
    assert "entity-amazon-s3" in reachable_from_percepto

    cap_pairs = collect_capability_bridge_pairs(nodes, assertions)
    assert len(cap_pairs) >= 6


def test_csr_sidecar_and_orphan_anchor_audit_parity(tmp_path: Path) -> None:
    db_path = tmp_path / "test_graph.sqlite"
    csr_path = tmp_path / "test_graph.csr"
    _create_disjoint_workstream_db(db_path)

    result = compile_csr_from_graph(db_path, csr_path)
    assert result.node_count == 10
    assert result.out_edge_count > 0

    _nodes, _assertions, _ev, adjacency, _contra, _conf, _excl = read_graph_from_disk(db_path)
    with CSRGraphReader(csr_path) as reader:
        for nid in ("entity-percepto", "entity-buzz", "entity-amazon-s3", "entity-vision-model"):
            mem_neighbors = sorted({e["to_node_id"] for e in adjacency[nid]})
            csr_neighbors = reader.get_neighbors(nid, direction="both")
            assert csr_neighbors == mem_neighbors
        hop_res = reader.traverse_k_hop(["entity-percepto"], max_hops=2)
        assert "entity-amazon-s3" in hop_res["nodes"]
        assert "entity-vision-model" in hop_res["nodes"]

    audit_report = audit_technical_anchors(db_path)
    assert audit_report["unreachable_tracked_count"] == 0


def test_end_to_end_drone_cv_s3_query(tmp_path: Path) -> None:
    db_path = tmp_path / "test_graph.sqlite"
    _create_disjoint_workstream_db(db_path)
    pkg = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    conn = sqlite3.connect(str(db_path))
    try:
        for n in pkg["nodes"]:
            conn.execute(
                "INSERT OR IGNORE INTO node (node_id, canonical_name, node_type, active) VALUES (?, ?, ?, 1)",
                (n["node_id"], n["canonical_name"], n["node_type"]),
            )
        for a in pkg["assertions"]:
            conn.execute(
                """INSERT INTO assertion (
                       assertion_id, subject_node_id, predicate, object_node_id,
                       object_literal, materiality, confidence_score, primary_source_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a["assertion_id"],
                    a["subject_node_id"],
                    a["predicate"],
                    a["object_node_id"],
                    a["object_literal"],
                    a["materiality"],
                    a["confidence_score"],
                    pkg["source"]["source_id"],
                ),
            )
            if a["object_node_id"]:
                conn.execute(
                    """INSERT INTO v_canonical_edge (
                           assertion_id, from_node_id, relationship_family, edge_type, to_node_id, materiality, source_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        a["assertion_id"],
                        a["subject_node_id"],
                        a["predicate"],
                        a["predicate"],
                        a["object_node_id"],
                        a["materiality"],
                        pkg["source"]["source_id"],
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    nodes, assertions, _ev, adjacency, _contra, _conf, _excl = read_graph_from_disk(db_path)
    reachable = _bfs_reachable(adjacency, "entity-noteworthy-ai", max_hops=2)
    for target in (
        "entity-nee-drone-cv-fleet",
        "entity-percepto",
        "entity-skydio",
        "entity-buzz",
        "entity-amazon-s3",
        "entity-aws-s3",
        "entity-vision-model",
    ):
        assert target in reachable, f"Expected {target} reachable from Noteworthy AI within 2 hops"
    assert nodes["entity-noteworthy-ai"]["canonical_name"] == "Noteworthy AI"
    assert assertions["asrt-dan-okray-nee-drone-cv-s3"]["confidence_score"] >= 0.90
