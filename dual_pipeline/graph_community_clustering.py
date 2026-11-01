"""
dual_pipeline/graph_community_clustering.py

Hierarchical community detection and global portfolio rollup for knowledge graphs.
Partitions graph nodes into modular communities (modularity Q >= 0.60) and generates
synthetic community summary nodes for instant O(1) global portfolio summaries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import networkx as nx
import networkx.algorithms.community as nx_comm

from storage.database import sqlite3
from .core import connect_sqlite
from .parameters import get_graph_consolidation_parameters

logger = logging.getLogger(__name__)

DEFAULT_MIN_MODULARITY = float(get_graph_consolidation_parameters()["min_modularity"])


@dataclass
class CommunitySummary:
    """Synthetic community rollup node representing a discrete thematic graph cluster."""
    community_id: str
    name: str
    domain_theme: str
    member_count: int
    member_node_ids: List[str]
    workstreams: List[str] = field(default_factory=list)
    deliverables: List[str] = field(default_factory=list)
    key_patterns: List[str] = field(default_factory=list)
    key_risks: List[str] = field(default_factory=list)
    density: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "community_id": self.community_id,
            "name": self.name,
            "domain_theme": self.domain_theme,
            "member_count": self.member_count,
            "member_node_ids": list(self.member_node_ids),
            "workstreams": list(self.workstreams),
            "deliverables": list(self.deliverables),
            "key_patterns": list(self.key_patterns),
            "key_risks": list(self.key_risks),
            "density": round(self.density, 4),
        }


@dataclass
class ClusteringResult:
    """Comprehensive clustering outcome with modularity Q and O(1) lookup indices."""
    modularity_q: float
    community_count: int
    total_nodes_clustered: int
    communities: List[CommunitySummary]
    node_to_community: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modularity_q": round(self.modularity_q, 4),
            "community_count": self.community_count,
            "total_nodes_clustered": self.total_nodes_clustered,
            "communities": [c.to_dict() for c in self.communities],
            "node_to_community": dict(self.node_to_community),
        }


def _load_networkx_graph(
    source: nx.Graph | Path | str | sqlite3.Connection,
) -> nx.Graph:
    """Builds or extracts a NetworkX undirected graph from database or existing object."""
    if isinstance(source, nx.Graph):
        return source

    close_conn = False
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.is_file():
            raise FileNotFoundError(f"Graph database not found: {p}")
        conn = connect_sqlite(p, readonly=True)
        close_conn = True
    else:
        conn = source

    try:
        G = nx.Graph()
        # Add nodes with metadata
        node_rows = conn.execute("SELECT node_id, canonical_name, node_type FROM node").fetchall()
        for nid, cname, ntype in node_rows:
            G.add_node(str(nid), canonical_name=str(cname or nid), node_type=str(ntype or "unknown"))

        # Add active edges
        edge_rows = conn.execute(
            "SELECT from_node_id, to_node_id, edge_type FROM edge WHERE superseded_by IS NULL"
        ).fetchall()
        for u, v, et in edge_rows:
            if u and v:
                G.add_edge(str(u), str(v), edge_type=str(et or "RELATED"))
        return G
    finally:
        if close_conn:
            conn.close()


def _synthesize_community_theme(
    comm_idx: int,
    members: Set[str],
    G: nx.Graph,
) -> CommunitySummary:
    """Synthesizes human-readable thematic metadata for a discrete cluster."""
    workstreams: List[str] = []
    deliverables: List[str] = []
    patterns: List[str] = []
    risks: List[str] = []

    for nid in members:
        n_data = G.nodes.get(nid, {})
        ntype = n_data.get("node_type", "")
        name = n_data.get("canonical_name", nid)
        if ntype == "workstream":
            workstreams.append(name)
        elif ntype == "deliverable":
            deliverables.append(name)
        elif ntype == "pattern":
            patterns.append(name)
        elif ntype == "risk":
            risks.append(name)

    # Derive primary name from top workstream or dominant node
    if workstreams:
        primary_name = f"Portfolio Cluster: {workstreams[0]}"
        domain_theme = workstreams[0]
    elif deliverables:
        primary_name = f"Delivery Cluster: {deliverables[0]}"
        domain_theme = deliverables[0]
    else:
        # Pick highest degree node in the community
        subg = G.subgraph(members)
        top_node = max(members, key=lambda n: subg.degree(n)) if members else f"cluster_{comm_idx}"
        top_name = G.nodes[top_node].get("canonical_name", top_node)
        primary_name = f"Domain Cluster: {top_name}"
        domain_theme = top_name

    subg = G.subgraph(members)
    n_nodes = subg.number_of_nodes()
    n_edges = subg.number_of_edges()
    density = (2.0 * n_edges) / (n_nodes * (n_nodes - 1)) if n_nodes > 1 else 1.0

    return CommunitySummary(
        community_id=f"community_summary_{comm_idx:03d}",
        name=primary_name,
        domain_theme=domain_theme,
        member_count=len(members),
        member_node_ids=sorted(members),
        workstreams=sorted(workstreams),
        deliverables=sorted(deliverables),
        key_patterns=sorted(patterns),
        key_risks=sorted(risks),
        density=density,
    )


def detect_graph_communities(
    source: nx.Graph | Path | str | sqlite3.Connection,
    *,
    seed: int = 42,
    min_modularity: float = DEFAULT_MIN_MODULARITY,
    resolution: float = 1.0,
) -> ClusteringResult:
    """Executes modularity community detection and generates synthetic rollups.

    Args:
        source: NetworkX Graph, Path to SQLite DB, or open SQLite connection.
        seed: Random seed for deterministic partition reproducibility.
        min_modularity: Modularity ceiling gate (default 0.60).
        resolution: Louvain resolution parameter (default 1.0).

    Returns:
        ClusteringResult containing modularity, summaries, and O(1) node mapping.
    """
    G = _load_networkx_graph(source)
    if G.number_of_nodes() == 0:
        return ClusteringResult(
            modularity_q=0.0,
            community_count=0,
            total_nodes_clustered=0,
            communities=[],
            node_to_community={},
        )

    # Edge cases: 0 edges or 1 node
    if G.number_of_edges() == 0:
        # Each node is its own isolate community with Q = 0.0
        single_comms = [
            _synthesize_community_theme(i, {nid}, G)
            for i, nid in enumerate(G.nodes, start=1)
        ]
        node_map = {c.member_node_ids[0]: c.community_id for c in single_comms}
        return ClusteringResult(
            modularity_q=0.0,
            community_count=len(single_comms),
            total_nodes_clustered=len(G.nodes),
            communities=single_comms,
            node_to_community=node_map,
        )

    # Modularity community partitioning
    raw_communities = nx_comm.louvain_communities(G, seed=seed, resolution=resolution)
    modularity_q = float(nx_comm.modularity(G, raw_communities))

    if modularity_q < min_modularity:
        logger.warning(
            "Community modularity Q=%.4f fell below threshold %.2f",
            modularity_q, min_modularity,
        )

    # Sort communities by size descending
    sorted_comms = sorted(raw_communities, key=len, reverse=True)

    summaries: List[CommunitySummary] = []
    node_to_community: Dict[str, str] = {}

    for idx, member_set in enumerate(sorted_comms, start=1):
        summary = _synthesize_community_theme(idx, member_set, G)
        summaries.append(summary)
        for nid in member_set:
            node_to_community[nid] = summary.community_id

    return ClusteringResult(
        modularity_q=modularity_q,
        community_count=len(summaries),
        total_nodes_clustered=G.number_of_nodes(),
        communities=summaries,
        node_to_community=node_to_community,
    )


class CommunityPortfolioIndex:
    """In-memory O(1) query interface for portfolio-level community rollups."""

    def __init__(self, clustering_result: ClusteringResult) -> None:
        self.result = clustering_result
        self.communities_by_id: Dict[str, CommunitySummary] = {
            c.community_id: c for c in clustering_result.communities
        }
        self.node_to_comm: Dict[str, str] = dict(clustering_result.node_to_community)

    def get_community_for_node(self, node_id: str) -> Optional[CommunitySummary]:
        """O(1) lookup of parent community summary node for any given graph entity."""
        comm_id = self.node_to_comm.get(node_id)
        return self.communities_by_id.get(comm_id) if comm_id else None

    def get_community(self, community_id: str) -> Optional[CommunitySummary]:
        """O(1) lookup of community summary by community ID."""
        return self.communities_by_id.get(community_id)

    def list_communities(self, min_size: int = 1) -> List[CommunitySummary]:
        """Returns all communities satisfying minimum member size."""
        return [c for c in self.result.communities if c.member_count >= min_size]

    def global_portfolio_summary(self) -> Dict[str, Any]:
        """Instant O(1) global rollup across all portfolio communities without graph iteration."""
        total_workstreams = sum(len(c.workstreams) for c in self.result.communities)
        total_deliverables = sum(len(c.deliverables) for c in self.result.communities)
        total_risks = sum(len(c.key_risks) for c in self.result.communities)

        major_clusters = [
            {
                "community_id": c.community_id,
                "name": c.name,
                "theme": c.domain_theme,
                "member_count": c.member_count,
                "workstream_count": len(c.workstreams),
                "deliverable_count": len(c.deliverables),
            }
            for c in self.result.communities if c.member_count >= 5
        ]

        return {
            "modularity_q": round(self.result.modularity_q, 4),
            "total_communities": self.result.community_count,
            "total_nodes_clustered": self.result.total_nodes_clustered,
            "total_workstreams_covered": total_workstreams,
            "total_deliverables_covered": total_deliverables,
            "total_risks_covered": total_risks,
            "major_clusters": major_clusters,
        }
