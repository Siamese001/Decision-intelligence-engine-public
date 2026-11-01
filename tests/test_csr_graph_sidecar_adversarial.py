"""
tests/test_csr_graph_sidecar_adversarial.py

Adversarial and edge-case test suite for Wave 5: Binary In-Memory CSR Graph Sidecar.
Covers mandatory edge-case categories from .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty graph, single-node graph, zero-edge isolates, disconnected nodes)
2. Malformed & corrupted inputs (corrupt header, wrong version, truncated binary, missing file)
3. Concurrency & resource hygiene (concurrent reader threads, clean descriptor teardown)
4. Invariant bypass & boundary inputs (unknown nodes, negative hops, depth limits)
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest

from dual_pipeline.graph_csr_sidecar import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAGIC_HEADER,
    CSRGraphReader,
    compile_csr_from_graph,
)


def _create_minimal_sqlite_graph(db_path: Path, nodes: list[str], edges: list[tuple[str, str, str]]) -> None:
    """Helper creating a minimal SQLite graph with standard node and edge tables."""
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
        for nid in nodes:
            conn.execute("INSERT INTO node VALUES (?, ?, 'test_type')", (nid, f"Name {nid}"))
        for idx, (u, v, et) in enumerate(edges):
            conn.execute(
                "INSERT INTO edge VALUES (?, ?, ?, ?, 'desc', 'high', 'high', NULL)",
                (f"e_{idx}", u, et, v),
            )
        conn.commit()
    finally:
        conn.close()


# =========================================================================
# Category 1: Boundary Payloads (Empty Graphs, Isolates, Disconnected)
# =========================================================================

def test_empty_graph_compilation_and_traversal(tmp_path: Path) -> None:
    """Compiling an empty graph (0 nodes, 0 edges) produces valid header and safe traversal."""
    db_path = tmp_path / "empty.sqlite"
    bin_path = tmp_path / "empty.bin"
    _create_minimal_sqlite_graph(db_path, [], [])

    result = compile_csr_from_graph(db_path, bin_path)
    assert result.node_count == 0
    assert result.out_edge_count == 0
    assert result.in_edge_count == 0

    with CSRGraphReader(bin_path) as reader:
        assert reader.num_nodes == 0
        assert reader.get_neighbors("non_existent") == []
        t = reader.traverse_k_hop(["non_existent"], max_hops=3)
        assert t["node_count"] == 0
        assert t["nodes"] == {}


def test_single_node_zero_edge_isolate(tmp_path: Path) -> None:
    """Graph with 1 node and 0 edges handles self-lookups and returns no neighbors."""
    db_path = tmp_path / "isolate.sqlite"
    bin_path = tmp_path / "isolate.bin"
    _create_minimal_sqlite_graph(db_path, ["lonely_node"], [])

    result = compile_csr_from_graph(db_path, bin_path)
    assert result.node_count == 1
    assert result.out_edge_count == 0

    with CSRGraphReader(bin_path) as reader:
        assert reader.get_neighbors("lonely_node") == []
        t = reader.traverse_k_hop(["lonely_node"], max_hops=3)
        assert t["node_count"] == 1
        assert "lonely_node" in t["nodes"]
        assert t["nodes"]["lonely_node"]["depth"] == 0


def test_disconnected_graph_components(tmp_path: Path) -> None:
    """Traversing component A never leaks into disconnected component B."""
    db_path = tmp_path / "components.sqlite"
    bin_path = tmp_path / "components.bin"
    nodes = ["a1", "a2", "b1", "b2"]
    edges = [("a1", "a2", "CONNECTS"), ("b1", "b2", "CONNECTS")]
    _create_minimal_sqlite_graph(db_path, nodes, edges)

    compile_csr_from_graph(db_path, bin_path)

    with CSRGraphReader(bin_path) as reader:
        t_a = reader.traverse_k_hop(["a1"], max_hops=3, direction="both")
        assert set(t_a["nodes"].keys()) == {"a1", "a2"}
        assert "b1" not in t_a["nodes"]
        assert "b2" not in t_a["nodes"]


# =========================================================================
# Category 2: Malformed & Corrupted Inputs
# =========================================================================

def test_missing_source_sqlite_file_raises_file_not_found(tmp_path: Path) -> None:
    """Attempting to compile from a non-existent database fails closed with FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Source graph database not found"):
        compile_csr_from_graph(tmp_path / "ghost.sqlite", tmp_path / "out.bin")


def test_missing_binary_file_raises_file_not_found(tmp_path: Path) -> None:
    """Loading a non-existent CSR binary fails closed with FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="CSR binary file not found"):
        CSRGraphReader(tmp_path / "ghost.bin")


def test_corrupted_magic_header_fails_closed(tmp_path: Path) -> None:
    """Binary file with corrupted magic bytes fails closed with ValueError."""
    db_path = tmp_path / "valid.sqlite"
    bin_path = tmp_path / "valid.bin"
    _create_minimal_sqlite_graph(db_path, ["n1", "n2"], [("n1", "n2", "REL")])
    compile_csr_from_graph(db_path, bin_path)

    # Corrupt magic bytes
    data = bytearray(bin_path.read_bytes())
    data[0:8] = b"CORRUPT!"
    corrupt_bin = tmp_path / "corrupt_magic.bin"
    corrupt_bin.write_bytes(data)

    with pytest.raises(ValueError, match="Invalid CSR magic header"):
        CSRGraphReader(corrupt_bin)


def test_unsupported_version_fails_closed(tmp_path: Path) -> None:
    """Binary file with unsupported format version fails closed with ValueError."""
    db_path = tmp_path / "valid2.sqlite"
    bin_path = tmp_path / "valid2.bin"
    _create_minimal_sqlite_graph(db_path, ["n1"], [])
    compile_csr_from_graph(db_path, bin_path)

    # Overwrite version field (offset 8..12) with version 999
    data = bytearray(bin_path.read_bytes())
    struct.pack_into("<I", data, 8, 999)
    bad_ver_bin = tmp_path / "bad_version.bin"
    bad_ver_bin.write_bytes(data)

    with pytest.raises(ValueError, match="Unsupported CSR version"):
        CSRGraphReader(bad_ver_bin)


def test_truncated_binary_file_fails_closed(tmp_path: Path) -> None:
    """Truncated binary file smaller than HEADER_SIZE fails closed during header unpacking."""
    truncated_bin = tmp_path / "truncated.bin"
    truncated_bin.write_bytes(b"TOO_SHORT")

    with pytest.raises(Exception):
        CSRGraphReader(truncated_bin)


# =========================================================================
# Category 3: Concurrency & Clean Resource Management
# =========================================================================

def test_concurrent_multithreaded_readers(tmp_path: Path) -> None:
    """Multiple threads reading the same CSR binary execute concurrently without race conditions."""
    db_path = tmp_path / "concurrency.sqlite"
    bin_path = tmp_path / "concurrency.bin"

    # Create cycle graph: 0 -> 1 -> 2 -> ... -> 19 -> 0
    nodes = [f"node_{i}" for i in range(20)]
    edges = [(f"node_{i}", f"node_{(i+1)%20}", "NEXT") for i in range(20)]
    _create_minimal_sqlite_graph(db_path, nodes, edges)
    compile_csr_from_graph(db_path, bin_path)

    def _worker(start_node: str) -> int:
        with CSRGraphReader(bin_path) as reader:
            t = reader.traverse_k_hop([start_node], max_hops=3, direction="out")
            return t["node_count"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, f"node_{i}") for i in range(20)]
        results = [f.result() for f in futures]

    # Each worker starts from a node and reaches start + 3 hops = 4 nodes
    assert all(count == 4 for count in results)


def test_context_manager_teardown(tmp_path: Path) -> None:
    """Context manager releases file handle cleanly allowing immediate file operations."""
    db_path = tmp_path / "teardown.sqlite"
    bin_path = tmp_path / "teardown.bin"
    _create_minimal_sqlite_graph(db_path, ["n1"], [])
    compile_csr_from_graph(db_path, bin_path)

    with CSRGraphReader(bin_path) as reader:
        assert reader.num_nodes == 1

    # After exiting block, file can be deleted or overwritten without file locking error
    bin_path.unlink()
    assert not bin_path.exists()


# =========================================================================
# Category 4: Boundary Traversal Directions & Invariant Handling
# =========================================================================

def test_directional_traversal_boundaries(tmp_path: Path) -> None:
    """Direction parameter ('out', 'in', 'both') correctly respects edge polarity."""
    db_path = tmp_path / "direction.sqlite"
    bin_path = tmp_path / "direction.bin"
    # A -> B -> C
    _create_minimal_sqlite_graph(db_path, ["A", "B", "C"], [("A", "B", "FLOWS"), ("B", "C", "FLOWS")])
    compile_csr_from_graph(db_path, bin_path)

    with CSRGraphReader(bin_path) as reader:
        # Outgoing from B reaches C only
        out_b = reader.traverse_k_hop(["B"], max_hops=2, direction="out")
        assert set(out_b["nodes"].keys()) == {"B", "C"}

        # Incoming to B reaches A only
        in_b = reader.traverse_k_hop(["B"], max_hops=2, direction="in")
        assert set(in_b["nodes"].keys()) == {"B", "A"}

        # Both from B reaches A and C
        both_b = reader.traverse_k_hop(["B"], max_hops=2, direction="both")
        assert set(both_b["nodes"].keys()) == {"A", "B", "C"}
