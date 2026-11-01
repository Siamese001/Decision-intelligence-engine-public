"""
dual_pipeline/release_core/package.py

Release packaging routines for knowledge graph releases.
Compiles high-performance binary Compressed Sparse Row (CSR) sidecars,
computes Leiden/Louvain community rollups with modularity Q >= 0.60,
and verifies release payload integrity.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..core import sha256_file
from ..graph_community_clustering import (
    ClusteringResult,
    CommunityPortfolioIndex,
    detect_graph_communities,
)
from ..graph_csr_sidecar import CSRCompilationResult, compile_csr_from_graph

logger = logging.getLogger(__name__)

CSR_FILENAME = "adjacency_csr.bin"
COMMUNITIES_FILENAME = "community_manifest.json"


def compile_release_csr_sidecar(
    graph_path: Path | str,
    output_dir: Path | str,
    *,
    edge_types: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compiles binary CSR adjacency sidecar into the target release generation directory."""
    g_path = Path(graph_path)
    out_dir = Path(output_dir)

    if not g_path.is_file():
        raise FileNotFoundError(f"Target graph SQLite file does not exist: {g_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    bin_target = out_dir / CSR_FILENAME

    logger.info("Compiling release CSR sidecar from %s to %s", g_path, bin_target)
    result: CSRCompilationResult = compile_csr_from_graph(
        graph_db_path=g_path,
        output_bin_path=bin_target,
        edge_types=edge_types,
    )

    return {
        "artifact": CSR_FILENAME,
        "binary_path": str(bin_target),
        "node_count": result.node_count,
        "out_edge_count": result.out_edge_count,
        "in_edge_count": result.in_edge_count,
        "file_size_bytes": result.file_size_bytes,
        "sha256": result.sha256,
        "compilation_duration_ms": result.compilation_duration_ms,
    }


def compile_release_community_clustering(
    graph_path: Path | str,
    output_dir: Path | str,
    *,
    min_modularity: float = 0.60,
    seed: int = 42,
) -> Dict[str, Any]:
    """Computes graph community partitioning and emits community manifest artifact."""
    g_path = Path(graph_path)
    out_dir = Path(output_dir)

    if not g_path.is_file():
        raise FileNotFoundError(f"Target graph SQLite file does not exist: {g_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_target = out_dir / COMMUNITIES_FILENAME

    logger.info("Detecting release graph communities for %s (min_modularity=%.2f)", g_path, min_modularity)
    clustering: ClusteringResult = detect_graph_communities(
        source=g_path,
        seed=seed,
        min_modularity=min_modularity,
    )

    index = CommunityPortfolioIndex(clustering)
    portfolio_summary = index.global_portfolio_summary()

    # Emit community manifest artifact
    manifest_data = {
        "artifact": COMMUNITIES_FILENAME,
        "schema_version": "1.0.0",
        "modularity_q": clustering.modularity_q,
        "community_count": clustering.community_count,
        "total_nodes_clustered": clustering.total_nodes_clustered,
        "portfolio_summary": portfolio_summary,
        "communities": [c.to_dict() for c in clustering.communities],
        "node_to_community": clustering.node_to_community,
    }

    manifest_target.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    manifest_sha256 = sha256_file(manifest_target)

    return {
        "artifact": COMMUNITIES_FILENAME,
        "manifest_path": str(manifest_target),
        "modularity_q": clustering.modularity_q,
        "community_count": clustering.community_count,
        "total_nodes_clustered": clustering.total_nodes_clustered,
        "sha256": manifest_sha256,
        "portfolio_summary": portfolio_summary,
    }


def compile_full_release_package(
    graph_path: Path | str,
    output_dir: Path | str,
    *,
    min_modularity: float = 0.60,
    edge_types: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compiles both CSR binary sidecar and community rollup manifest."""
    csr_meta = compile_release_csr_sidecar(graph_path, output_dir, edge_types=edge_types)
    comm_meta = compile_release_community_clustering(graph_path, output_dir, min_modularity=min_modularity)
    return {
        "csr": csr_meta,
        "communities": comm_meta,
        "passed": comm_meta["modularity_q"] >= min_modularity,
    }
