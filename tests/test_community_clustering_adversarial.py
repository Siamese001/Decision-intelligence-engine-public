"""
tests/test_community_clustering_adversarial.py

Adversarial and edge-case test suite for Wave 6: Hierarchical Community Detection & Global Portfolio Rollup.
Covers mandatory edge-case categories from .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty graph, single-node isolate, 10 disconnected isolates, full clique)
2. Modularity threshold enforcement and warnings
3. Malformed inputs & superseded edge exclusion
4. O(1) index lookup edge cases (unknown nodes, unknown community IDs)
5. Partition determinism and density bounds
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import networkx as nx
import pytest

from dual_pipeline.graph_community_clustering import (
    CommunityPortfolioIndex,
    CommunitySummary,
    detect_graph_communities,
)


def _build_test_sqlite_db(
    db_path: Path,
    nodes: list[tuple[str, str, str]],
    edges: list[tuple[str, str, str, str | None]],
) -> None:
    """Helper building test SQLite graph with superseded_by column."""
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
        for nid, cname, ntype in nodes:
            conn.execute("INSERT INTO node VALUES (?, ?, ?)", (nid, cname, ntype))
        for idx, (u, v, et, sup) in enumerate(edges):
            conn.execute(
                "INSERT INTO edge VALUES (?, ?, ?, ?, ?)",
                (f"e_{idx}", u, et, v, sup),
            )
        conn.commit()
    finally:
        conn.close()


# =========================================================================
# Category 1: Boundary Payloads (Empty, Single Isolate, Disconnected, Clique)
# =========================================================================

def test_empty_graph_returns_empty_result() -> None:
    """Clustering an empty graph produces 0 communities and 0.0 modularity safely."""
    G = nx.Graph()
    res = detect_graph_communities(G)
    assert res.community_count == 0
    assert res.total_nodes_clustered == 0
    assert res.modularity_q == 0.0
    assert res.communities == []
    assert res.node_to_community == {}


def test_single_node_isolate_returns_single_community() -> None:
    """Single node graph produces 1 community without error."""
    G = nx.Graph()
    G.add_node("lone_node", canonical_name="Lone Node", node_type="workstream")
    res = detect_graph_communities(G)
    assert res.community_count == 1
    assert res.total_nodes_clustered == 1
    assert res.modularity_q == 0.0
    assert res.communities[0].member_node_ids == ["lone_node"]
    assert res.node_to_community["lone_node"] == res.communities[0].community_id


def test_all_disconnected_isolates_return_individual_communities() -> None:
    """A graph of 10 disconnected nodes results in 10 single-node communities."""
    G = nx.Graph()
    for i in range(10):
        G.add_node(f"node_{i}", canonical_name=f"Node {i}", node_type="task")
    res = detect_graph_communities(G)
    assert res.community_count == 10
    assert res.total_nodes_clustered == 10
    assert res.modularity_q == 0.0


def test_complete_clique_graph_forms_single_community() -> None:
    """A complete graph K_5 forms exactly 1 community containing all nodes."""
    G = nx.complete_graph(5)
    for n in G.nodes:
        G.nodes[n]["canonical_name"] = f"Node {n}"
        G.nodes[n]["node_type"] = "deliverable"
    res = detect_graph_communities(G)
    assert res.community_count == 1
    assert res.total_nodes_clustered == 5
    assert len(res.communities[0].member_node_ids) == 5
    assert res.communities[0].density == 1.0


# =========================================================================
# Category 2: Modularity Thresholds & Fallbacks
# =========================================================================

def test_weak_modularity_logs_warning_without_crashing() -> None:
    """When graph has weak community structure (e.g. star graph), result is returned safely."""
    # Star graph: center connected to 10 leaves has low modularity
    G = nx.star_graph(10)
    for n in G.nodes:
        G.nodes[n]["canonical_name"] = f"StarNode {n}"
        G.nodes[n]["node_type"] = "task"
    res = detect_graph_communities(G, min_modularity=0.60)
    assert res.total_nodes_clustered == 11
    # Louvain partitions star graph, but modularity is low; system preserves result
    assert isinstance(res.modularity_q, float)


def test_strong_two_cluster_graph_exceeds_threshold() -> None:
    """Two dense cliques connected by a single bridge edge achieves high modularity (Q > 0.4)."""
    G = nx.Graph()
    # Clique 1: c1_0 .. c1_9
    for i in range(10):
        G.add_node(f"c1_{i}", canonical_name=f"Cluster1_{i}", node_type="workstream")
        for j in range(i + 1, 10):
            G.add_edge(f"c1_{i}", f"c1_{j}")

    # Clique 2: c2_0 .. c2_9
    for i in range(10):
        G.add_node(f"c2_{i}", canonical_name=f"Cluster2_{i}", node_type="deliverable")
        for j in range(i + 1, 10):
            G.add_edge(f"c2_{i}", f"c2_{j}")

    # Weak bridge between cliques
    G.add_edge("c1_0", "c2_0")

    res = detect_graph_communities(G, min_modularity=0.40)
    assert res.community_count == 2
    assert res.modularity_q >= 0.40
    assert res.total_nodes_clustered == 20


# =========================================================================
# Category 3: Malformed Inputs & Superseded Edge Exclusion
# =========================================================================

def test_missing_sqlite_file_raises_file_not_found(tmp_path: Path) -> None:
    """Non-existent database file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Graph database not found"):
        detect_graph_communities(tmp_path / "non_existent.sqlite")


def test_superseded_edges_are_strictly_excluded(tmp_path: Path) -> None:
    """Edges marked with superseded_by IS NOT NULL must be excluded from clustering."""
    db_path = tmp_path / "superseded.sqlite"
    nodes = [("A", "Alpha", "workstream"), ("B", "Beta", "workstream")]
    # Active edge: none, Superseded edge: A -> B
    edges = [("A", "B", "OLD_RELATION", "replacement_edge_123")]
    _build_test_sqlite_db(db_path, nodes, edges)

    res = detect_graph_communities(db_path)
    # Because the only edge is superseded, graph has 0 active edges -> 2 isolate communities
    assert res.community_count == 2
    assert res.modularity_q == 0.0


# =========================================================================
# Category 4: O(1) Index Lookup Edge Cases
# =========================================================================

def test_portfolio_index_non_existent_node_returns_none() -> None:
    """Querying an unknown node from CommunityPortfolioIndex returns None safely."""
    G = nx.Graph()
    G.add_node("existing_node", canonical_name="Existing", node_type="workstream")
    res = detect_graph_communities(G)
    index = CommunityPortfolioIndex(res)

    assert index.get_community_for_node("non_existent_node") is None
    assert index.get_community("invalid_comm_id") is None


def test_portfolio_index_global_summary_rollup() -> None:
    """Instant global portfolio summary correctly counts workstreams, deliverables, and risks."""
    G = nx.Graph()
    G.add_node("ws1", canonical_name="WS Alpha", node_type="workstream")
    G.add_node("deliv1", canonical_name="Deliverable 1", node_type="deliverable")
    G.add_node("risk1", canonical_name="Risk 1", node_type="risk")
    G.add_edge("ws1", "deliv1")
    G.add_edge("deliv1", "risk1")

    res = detect_graph_communities(G)
    index = CommunityPortfolioIndex(res)
    summary = index.global_portfolio_summary()

    assert summary["total_workstreams_covered"] == 1
    assert summary["total_deliverables_covered"] == 1
    assert summary["total_risks_covered"] == 1
    assert summary["total_nodes_clustered"] == 3


# =========================================================================
# Category 5: Partition Determinism & Density Bounds
# =========================================================================

def test_partition_determinism_across_runs() -> None:
    """Pinned random seed ensures identical community partitioning across runs."""
    G = nx.erdos_renyi_graph(30, 0.2, seed=123)
    for n in G.nodes:
        G.nodes[n]["canonical_name"] = f"Node_{n}"
        G.nodes[n]["node_type"] = "task"

    run1 = detect_graph_communities(G, seed=42)
    run2 = detect_graph_communities(G, seed=42)

    assert run1.modularity_q == run2.modularity_q
    assert run1.community_count == run2.community_count
    assert run1.node_to_community == run2.node_to_community
