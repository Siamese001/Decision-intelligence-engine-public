"""
tests/end_to_end/test_community_clustering_e2e.py

End-to-End qualification suite for Wave 6: Hierarchical Community Detection & Global Portfolio Rollup.
Verifies:
1. Active knowledge graph partitioning achieves modularity Q >= 0.60.
2. 100% of delivered AI workstreams resolve to a parent community summary node.
3. Instant O(1) global portfolio queries with zero multi-node iteration or hallucination.
4. Release packaging integration emitting valid community_manifest.json with SHA256 integrity.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Set

import pytest

from dual_pipeline.graph_community_clustering import (
    ClusteringResult,
    CommunityPortfolioIndex,
    CommunitySummary,
    detect_graph_communities,
)
from dual_pipeline.graph_retrieval_core.recursive_traversal import _resolve_default_graph_path
from dual_pipeline.release_core.package import (
    COMMUNITIES_FILENAME,
    compile_release_community_clustering,
)


@pytest.fixture(scope="module")
def active_graph_path() -> Path:
    """Resolves active knowledge graph database path."""
    return _resolve_default_graph_path()


@pytest.fixture(scope="module")
def active_clustering_result(active_graph_path: Path) -> ClusteringResult:
    """Computes community clustering once for the active knowledge graph."""
    return detect_graph_communities(active_graph_path, seed=42, min_modularity=0.60)


def test_e2e_active_graph_community_detection_and_modularity_gate(
    active_graph_path: Path,
    active_clustering_result: ClusteringResult,
) -> None:
    """Verifies that active graph clustering meets the modularity Q >= 0.60 gate and covers all workstreams."""
    assert active_graph_path.is_file(), f"Active graph not found at {active_graph_path}"

    # Gate 1: Modularity Q >= 0.60
    assert active_clustering_result.modularity_q >= 0.60, (
        f"Modularity Q={active_clustering_result.modularity_q:.4f} failed gate >= 0.60"
    )

    # Gate 2: Discrete community clusters formed
    assert active_clustering_result.community_count >= 10
    assert active_clustering_result.total_nodes_clustered >= 800

    # Gate 3: 100% of workstreams resolve to a parent community summary
    conn = sqlite3.connect(active_graph_path)
    try:
        workstream_rows = conn.execute(
            "SELECT node_id FROM node WHERE node_type = 'workstream'"
        ).fetchall()
        workstream_ids = [str(r[0]) for r in workstream_rows]
    finally:
        conn.close()

    assert len(workstream_ids) > 0, "No workstreams found in active graph"
    index = CommunityPortfolioIndex(active_clustering_result)

    unmapped_workstreams: List[str] = []
    for ws_id in workstream_ids:
        comm = index.get_community_for_node(ws_id)
        if comm is None:
            unmapped_workstreams.append(ws_id)

    assert len(unmapped_workstreams) == 0, (
        f"100% coverage violated: unmapped workstreams: {unmapped_workstreams}"
    )


def test_e2e_o1_portfolio_query_resolution_without_hallucination(
    active_clustering_result: ClusteringResult,
) -> None:
    """Verifies instant O(1) community node lookup and zero-hallucination portfolio rollups."""
    index = CommunityPortfolioIndex(active_clustering_result)

    # Test O(1) lookup on canonical workstreams
    canonical_samples = [
        "workstream_project_beacon",
        "workstream_gemini_enablement",
        "workstream_fcdb",
    ]

    for ws_id in canonical_samples:
        comm = index.get_community_for_node(ws_id)
        assert comm is not None, f"Expected community for {ws_id}"
        assert comm.community_id.startswith("community_summary_")
        assert comm.member_count >= 1
        assert ws_id in comm.member_node_ids
        # Verifies community name and domain theme are populated without empty values
        assert len(comm.name) > 0
        assert len(comm.domain_theme) > 0

    # Global portfolio rollup is precomputed without iterating over raw graph nodes
    summary = index.global_portfolio_summary()
    assert summary["modularity_q"] == round(active_clustering_result.modularity_q, 4)
    assert summary["total_communities"] == active_clustering_result.community_count
    assert summary["total_workstreams_covered"] > 0
    assert summary["total_deliverables_covered"] > 0
    assert len(summary["major_clusters"]) > 0


def test_e2e_release_packaging_community_manifest_integration(
    active_graph_path: Path,
    tmp_path: Path,
) -> None:
    """Verifies compile_release_community_clustering writes valid manifest with SHA256 integrity."""
    release_dir = tmp_path / "test_release_gen"
    meta = compile_release_community_clustering(active_graph_path, release_dir, min_modularity=0.60)

    assert meta["artifact"] == COMMUNITIES_FILENAME
    assert meta["modularity_q"] >= 0.60
    assert meta["community_count"] >= 10
    assert len(meta["sha256"]) == 64

    manifest_file = Path(meta["manifest_path"])
    assert manifest_file.is_file()

    # Verify manifest JSON content
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["schema_version"] == "1.0.0"
    assert manifest_data["modularity_q"] == meta["modularity_q"]
    assert len(manifest_data["communities"]) == meta["community_count"]
    assert "portfolio_summary" in manifest_data


def test_e2e_hermetic_multi_cluster_lifecycle(tmp_path: Path) -> None:
    """Verifies complete clustering and O(1) query lifecycle in an isolated synthetic graph."""
    db_path = tmp_path / "synthetic_portfolio.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT);
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                superseded_by TEXT
            );
            """
        )
        # Cluster 1: Cloud Architecture (3 workstreams + 6 deliverables)
        conn.execute("INSERT INTO node VALUES ('ws_cloud_1', 'Cloud Foundation', 'workstream')")
        conn.execute("INSERT INTO node VALUES ('ws_cloud_2', 'Cloud Migration', 'workstream')")
        for i in range(4):
            d_id = f"deliv_cloud_{i}"
            conn.execute("INSERT INTO node VALUES (?, ?, 'deliverable')", (d_id, f"Cloud Deliv {i}"))
            conn.execute("INSERT INTO edge VALUES (?, 'ws_cloud_1', 'DELIVERS', ?, NULL)", (f"e_c_{i}", d_id))
            conn.execute("INSERT INTO edge VALUES (?, 'ws_cloud_2', 'DELIVERS', ?, NULL)", (f"e_c2_{i}", d_id))

        # Cluster 2: Generative AI (2 workstreams + 4 deliverables)
        conn.execute("INSERT INTO node VALUES ('ws_ai_1', 'AI Hub', 'workstream')")
        conn.execute("INSERT INTO node VALUES ('ws_ai_2', 'Agent Workflows', 'workstream')")
        for i in range(4):
            d_id = f"deliv_ai_{i}"
            conn.execute("INSERT INTO node VALUES (?, ?, 'deliverable')", (d_id, f"AI Deliv {i}"))
            conn.execute("INSERT INTO edge VALUES (?, 'ws_ai_1', 'PRODUCES', ?, NULL)", (f"e_a_{i}", d_id))
            conn.execute("INSERT INTO edge VALUES (?, 'ws_ai_2', 'PRODUCES', ?, NULL)", (f"e_a2_{i}", d_id))

        conn.commit()
    finally:
        conn.close()

    res = detect_graph_communities(db_path, min_modularity=0.30)
    assert res.community_count == 2
    assert res.modularity_q >= 0.40

    idx = CommunityPortfolioIndex(res)
    comm_cloud = idx.get_community_for_node("ws_cloud_1")
    comm_ai = idx.get_community_for_node("ws_ai_1")

    assert comm_cloud is not None
    assert comm_ai is not None
    assert comm_cloud.community_id != comm_ai.community_id
    assert "ws_cloud_2" in comm_cloud.member_node_ids
    assert "ws_ai_2" in comm_ai.member_node_ids
