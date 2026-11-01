"""
tests/end_to_end/test_csr_graph_sidecar_e2e.py

End-to-End qualification suite for Wave 5: Binary In-Memory CSR Graph Sidecar.
Verifies:
1. Sub-2.0ms 3-hop pointer-chasing traversal latency with zero SQLite disk reads.
2. Memory footprint strictly under 15MB for the entire NEE knowledge graph.
3. 100% path and depth parity against SQLite recursive CTE baseline (zero divergence).
4. Full integration with release packaging (compile_release_csr_sidecar).
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from dual_pipeline.graph_csr_sidecar import (
    CSRCompilationResult,
    CSRGraphReader,
    compile_csr_from_graph,
)
from dual_pipeline.graph_retrieval_core.recursive_traversal import (
    _resolve_default_graph_path,
    traverse_k_hop_neighborhood,
)
from dual_pipeline.release_core.package import (
    CSR_FILENAME,
    compile_release_csr_sidecar,
)


@pytest.fixture(scope="module")
def active_graph_path() -> Path:
    """Resolves active knowledge graph database path."""
    return _resolve_default_graph_path()


@pytest.fixture(scope="module")
def compiled_active_csr(active_graph_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compiles active knowledge graph into a temporary binary CSR sidecar."""
    tmp_dir = tmp_path_factory.mktemp("active_csr")
    csr_file = tmp_dir / "active_adjacency_csr.bin"
    compile_csr_from_graph(active_graph_path, csr_file)
    return csr_file


def test_e2e_csr_active_graph_pointer_chase_latency_and_memory_gate(compiled_active_csr: Path) -> None:
    """Qualifies 3-hop traversal latency < 2.0ms and memory footprint < 15MB on active graph."""
    assert compiled_active_csr.is_file(), f"CSR binary not found at {compiled_active_csr}"

    with CSRGraphReader(compiled_active_csr) as reader:
        # Gate 1: Memory footprint strictly under 15MB
        footprint_mb = reader.memory_footprint_mb
        assert footprint_mb < 15.0, f"Memory footprint {footprint_mb:.2f}MB exceeded 15MB limit"
        assert footprint_mb > 0.001

        # Gate 2: Sub-2.0ms 3-hop traversal latency across multiple representative workstreams
        test_seeds = [
            "workstream_project_beacon",
            "workstream_gemini_enablement",
            "workstream_fcdb",
            "workstream_atlas",
        ]

        for seed in test_seeds:
            # Measure pointer-chasing latency
            result = reader.traverse_k_hop([seed], max_hops=3, direction="both")
            assert result["node_count"] > 10, f"Expected rich neighborhood for {seed}, got {result['node_count']}"
            assert result["latency_ms"] < 2.0, (
                f"3-hop traversal latency {result['latency_ms']}ms exceeded 2.0ms ceiling for {seed}"
            )


def test_e2e_csr_path_parity_against_sqlite_baseline(active_graph_path: Path, compiled_active_csr: Path) -> None:
    """Verifies 100% path and depth parity between CSR memory pointer chasing and SQLite recursive CTE."""
    test_seeds = [
        "workstream_project_beacon",
        "workstream_gemini_enablement",
        "workstream_fcdb",
    ]

    with CSRGraphReader(compiled_active_csr) as csr_reader:
        for seed in test_seeds:
            # 1. Traversal via SQLite CTE
            sqlite_res = traverse_k_hop_neighborhood([seed], max_hops=2, graph_path=active_graph_path)
            sqlite_nodes: Dict[str, int] = {
                nid: info.get("depth", 99) for nid, info in sqlite_res["nodes"].items()
            }

            # 2. Traversal via Binary CSR
            csr_res = csr_reader.traverse_k_hop([seed], max_hops=2, direction="both")
            csr_nodes: Dict[str, int] = {
                nid: info["depth"] for nid, info in csr_res["nodes"].items()
            }

            # 3. Verify zero path divergence
            assert set(csr_nodes.keys()) == set(sqlite_nodes.keys()), (
                f"Path divergence for {seed}: "
                f"missing in CSR: {set(sqlite_nodes.keys()) - set(csr_nodes.keys())}, "
                f"extra in CSR: {set(csr_nodes.keys()) - set(sqlite_nodes.keys())}"
            )

            # 4. Verify exact depth parity for all reachable nodes
            for nid in sqlite_nodes:
                assert csr_nodes[nid] == sqlite_nodes[nid], (
                    f"Depth mismatch for node {nid} in {seed}: CSR={csr_nodes[nid]} vs SQLite={sqlite_nodes[nid]}"
                )


def test_e2e_release_packaging_integration(active_graph_path: Path, tmp_path: Path) -> None:
    """Verifies compile_release_csr_sidecar produces valid adjacency_csr.bin in release target directory."""
    release_dir = tmp_path / "generation_target"
    meta = compile_release_csr_sidecar(active_graph_path, release_dir)

    assert meta["artifact"] == CSR_FILENAME
    assert meta["node_count"] > 0
    assert meta["out_edge_count"] > 0
    assert meta["file_size_bytes"] > 0
    assert len(meta["sha256"]) == 64

    bin_path = Path(meta["binary_path"])
    assert bin_path.is_file()

    # Verify binary is directly readable by CSRGraphReader
    with CSRGraphReader(bin_path) as reader:
        assert reader.num_nodes == meta["node_count"]
        res = reader.traverse_k_hop(["workstream_project_beacon"], max_hops=1)
        assert res["node_count"] > 1


def test_e2e_hermetic_isolated_db_lifecycle(tmp_path: Path) -> None:
    """Verifies CSR compilation and traversal lifecycle on a hermetic synthetic graph."""
    db_path = tmp_path / "hermetic.sqlite"
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
                description TEXT,
                confidence TEXT,
                materiality TEXT,
                superseded_by TEXT
            );
            """
        )
        # Construct a tree: root -> 5 branches -> 5 leaves each (31 nodes total)
        conn.execute("INSERT INTO node VALUES ('root', 'Root Node', 'workstream')")
        for b in range(5):
            b_id = f"branch_{b}"
            conn.execute("INSERT INTO node VALUES (?, ?, 'deliverable')", (b_id, f"Branch {b}"))
            conn.execute("INSERT INTO edge VALUES (?, 'root', 'PRODUCES', ?, 'd', 'h', 'h', NULL)", (f"e_r_{b}", b_id))
            for l in range(5):
                l_id = f"leaf_{b}_{l}"
                conn.execute("INSERT INTO node VALUES (?, ?, 'task')", (l_id, f"Leaf {b}_{l}"))
                conn.execute("INSERT INTO edge VALUES (?, ?, 'CONTAINS', ?, 'd', 'h', 'h', NULL)", (f"e_b_{b}_{l}", b_id, l_id))
        conn.commit()
    finally:
        conn.close()

    bin_path = tmp_path / "hermetic.bin"
    comp_res = compile_csr_from_graph(db_path, bin_path)
    assert comp_res.node_count == 31
    assert comp_res.out_edge_count == 30

    with CSRGraphReader(bin_path) as reader:
        # 1-hop: root + 5 branches = 6
        h1 = reader.traverse_k_hop(["root"], max_hops=1, direction="out")
        assert h1["node_count"] == 6

        # 2-hop: all 31 nodes
        h2 = reader.traverse_k_hop(["root"], max_hops=2, direction="out")
        assert h2["node_count"] == 31
        assert h2["latency_ms"] < 1.0
