"""
dual_pipeline/graph_csr_sidecar.py

Binary Compressed Sparse Row (CSR) adjacency cache for knowledge graph traversals.
Enforces sub-2ms pointer-chasing traversals with zero SQLite disk reads during query execution.
Provides memory-mapped binary compilation and reading with forward/reverse edge indices.
"""
from __future__ import annotations

import array
import hashlib
import json
import logging
import mmap
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from storage.database import sqlite3
from .core import connect_sqlite, sha256_file
from .graph_retrieval_core.capability_bridge import collect_csr_virtual_edge_pairs

logger = logging.getLogger(__name__)

# Binary CSR Header Magic and Version
MAGIC_HEADER = b"NEECSR01"
FORMAT_VERSION = 1
HEADER_FORMAT = "<8sIIIIQQQQ32s"  # magic, ver, V, E_out, E_in, off_nodes, off_out_ptr, off_out_idx, off_in_ptr, sha256
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


@dataclass(frozen=True)
class CSRCompilationResult:
    """Metadata resulting from CSR binary compilation."""
    binary_path: str
    node_count: int
    out_edge_count: int
    in_edge_count: int
    file_size_bytes: int
    sha256: str
    compilation_duration_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "binary_path": self.binary_path,
            "node_count": self.node_count,
            "out_edge_count": self.out_edge_count,
            "in_edge_count": self.in_edge_count,
            "file_size_bytes": self.file_size_bytes,
            "sha256": self.sha256,
            "compilation_duration_ms": round(self.compilation_duration_ms, 2),
        }


def _build_edge_sql(use_assertions: bool, edge_types: Optional[Sequence[str]]) -> Tuple[str, list[Any]]:
    if use_assertions:
        query = (
            "SELECT subject_node_id, object_node_id FROM assertion "
            "WHERE currentness_state = 'current' "
            "AND (superseded_by_assertion_id IS NULL OR superseded_by_assertion_id = '') "
            "AND object_node_id IS NOT NULL"
        )
        if not edge_types:
            return query, []
        placeholders = ",".join("?" for _ in edge_types)
        return (
            f"{query} AND (predicate IN ({placeholders}) OR legacy_predicate IN ({placeholders}))",
            list(edge_types) + list(edge_types),
        )
    query = "SELECT from_node_id, to_node_id FROM edge WHERE superseded_by IS NULL"
    if not edge_types:
        return query, []
    placeholders = ",".join("?" for _ in edge_types)
    return f"{query} AND edge_type IN ({placeholders})", list(edge_types)


def _merge_virtual_csr_edges(conn: Any, edges_rows: List[Tuple[str, str]]) -> None:
    existing = set(edges_rows)
    for u_id, v_id in sorted(collect_csr_virtual_edge_pairs(conn)):
        for pair in ((u_id, v_id), (v_id, u_id)):
            if pair not in existing:
                existing.add(pair)
                edges_rows.append(pair)


def _fetch_csr_nodes_and_edges(
    db_file: Path,
    edge_types: Optional[Sequence[str]],
    include_capability_bridges: bool = True,
) -> Tuple[List[str], List[Tuple[str, str]]]:
    conn = connect_sqlite(db_file, readonly=True)
    try:
        nodes_rows = conn.execute("SELECT node_id FROM node ORDER BY node_id").fetchall()
        node_ids = [str(r[0]).strip() for r in nodes_rows if str(r[0]).strip()]
        has_assertion = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion'"
        ).fetchone()
        has_edge = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='edge'"
        ).fetchone()
        use_assertions = bool(
            has_assertion
            and (
                not has_edge
                or conn.execute(
                    "SELECT 1 FROM assertion WHERE currentness_state='current' AND object_node_id IS NOT NULL LIMIT 1"
                ).fetchone()
            )
        )
        query, params = _build_edge_sql(use_assertions, edge_types)
        edges_rows = [(str(r[0]), str(r[1])) for r in conn.execute(query, params).fetchall()]
        if has_assertion and not edge_types and (include_capability_bridges or not edges_rows):
            _merge_virtual_csr_edges(conn, edges_rows)
        return node_ids, edges_rows
    finally:
        conn.close()


def _build_csr_arrays(
    node_ids: List[str], edges_rows: List[Tuple[str, str]]
) -> Tuple[array.array, array.array, array.array, array.array]:
    node_to_idx = {nid: idx for idx, nid in enumerate(node_ids)}
    num_nodes = len(node_ids)
    out_adj: List[List[int]] = [[] for _ in range(num_nodes)]
    in_adj: List[List[int]] = [[] for _ in range(num_nodes)]
    for u_id, v_id in edges_rows:
        u_idx, v_idx = node_to_idx.get(u_id), node_to_idx.get(v_id)
        if u_idx is not None and v_idx is not None:
            out_adj[u_idx].append(v_idx)
            in_adj[v_idx].append(u_idx)
    out_indptr, out_indices = array.array("I", [0]), array.array("I")
    for neighbors in out_adj:
        neighbors.sort()
        out_indices.extend(neighbors)
        out_indptr.append(len(out_indices))
    in_indptr, in_indices = array.array("I", [0]), array.array("I")
    for neighbors in in_adj:
        neighbors.sort()
        in_indices.extend(neighbors)
        in_indptr.append(len(in_indices))
    return out_indptr, out_indices, in_indptr, in_indices


def compile_csr_from_graph(
    graph_db_path: Path | str,
    output_bin_path: Path | str,
    *,
    edge_types: Optional[Sequence[str]] = None,
    include_capability_bridges: bool = True,
) -> CSRCompilationResult:
    """Compiles directed edge adjacency from SQLite graph into binary CSR sidecar."""
    t0 = time.perf_counter()
    db_file, out_file = Path(graph_db_path), Path(output_bin_path)
    if not db_file.is_file():
        raise FileNotFoundError(f"Source graph database not found: {db_file}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    graph_hash = sha256_file(db_file)
    node_ids, edges_rows = _fetch_csr_nodes_and_edges(db_file, edge_types, include_capability_bridges)
    num_nodes = len(node_ids)
    out_indptr, out_indices, in_indptr, in_indices = _build_csr_arrays(node_ids, edges_rows)
    node_table_bytes = json.dumps(node_ids).encode("utf-8")
    offset_node_table = HEADER_SIZE
    offset_out_ptr = offset_node_table + len(node_table_bytes)
    offset_out_idx = offset_out_ptr + len(out_indptr.tobytes())
    offset_in_ptr = offset_out_idx + len(out_indices.tobytes())
    header_bytes = struct.pack(
        HEADER_FORMAT,
        MAGIC_HEADER,
        FORMAT_VERSION,
        num_nodes,
        len(out_indices),
        len(in_indices),
        offset_node_table,
        offset_out_ptr,
        offset_out_idx,
        offset_in_ptr,
        bytes.fromhex(graph_hash)[:32],
    )
    with open(out_file, "wb") as f:
        for chunk in (header_bytes, node_table_bytes, out_indptr.tobytes(), out_indices.tobytes(), in_indptr.tobytes(), in_indices.tobytes()):
            f.write(chunk)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    csr_sha256, file_size = sha256_file(out_file), out_file.stat().st_size
    return CSRCompilationResult(
        binary_path=str(out_file),
        node_count=num_nodes,
        out_edge_count=len(out_indices),
        in_edge_count=len(in_indices),
        file_size_bytes=file_size,
        sha256=csr_sha256,
        compilation_duration_ms=elapsed_ms,
    )


class CSRGraphReader:
    """Memory-mapped binary CSR graph reader for high-throughput zero-I/O traversals."""

    def __init__(self, binary_path: Path | str) -> None:
        self.path = Path(binary_path)
        if not self.path.is_file():
            raise FileNotFoundError(f"CSR binary file not found: {self.path}")

        self._file = open(self.path, "rb")
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)

        # Unpack header
        header_data = self._mmap[:HEADER_SIZE]
        (
            magic,
            version,
            self.num_nodes,
            self.num_out_edges,
            self.num_in_edges,
            self.off_nodes,
            self.off_out_ptr,
            self.off_out_idx,
            self.off_in_ptr,
            self.source_graph_sha,
        ) = struct.unpack(HEADER_FORMAT, header_data)

        if magic != MAGIC_HEADER:
            self.close()
            raise ValueError(f"Invalid CSR magic header: {magic!r}, expected {MAGIC_HEADER!r}")
        if version != FORMAT_VERSION:
            self.close()
            raise ValueError(f"Unsupported CSR version: {version}, expected {FORMAT_VERSION}")

        # Load node table
        nodes_raw = self._mmap[self.off_nodes:self.off_out_ptr]
        self.node_ids: List[str] = json.loads(nodes_raw.decode("utf-8"))
        self.node_to_idx: Dict[str, int] = {nid: i for i, nid in enumerate(self.node_ids)}

        # Memory views for arrays (uint32)
        out_ptr_bytes = self.num_nodes + 1
        self.off_in_idx = self.off_in_ptr + (out_ptr_bytes * 4)

        self._out_indptr = array.array("I")
        self._out_indptr.frombytes(self._mmap[self.off_out_ptr:self.off_out_idx])

        self._out_indices = array.array("I")
        self._out_indices.frombytes(self._mmap[self.off_out_idx:self.off_in_ptr])

        self._in_indptr = array.array("I")
        self._in_indptr.frombytes(self._mmap[self.off_in_ptr:self.off_in_idx])

        self._in_indices = array.array("I")
        self._in_indices.frombytes(self._mmap[self.off_in_idx:])

    @property
    def memory_footprint_mb(self) -> float:
        """Memory footprint of binary CSR in megabytes."""
        return self.path.stat().st_size / (1024.0 * 1024.0)

    def close(self) -> None:
        """Release memory-mapped view and file descriptors."""
        if hasattr(self, "_mmap") and self._mmap is not None:
            self._mmap.close()
            self._mmap = None  # type: ignore
        if hasattr(self, "_file") and self._file is not None:
            self._file.close()
            self._file = None  # type: ignore

    def __enter__(self) -> "CSRGraphReader":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def get_neighbors(self, node_id: str, direction: str = "both") -> List[str]:
        """Look up neighbor node IDs in O(degree) memory-mapped lookups."""
        u_idx = self.node_to_idx.get(node_id)
        if u_idx is None:
            return []

        neighbors: Set[int] = set()
        if direction in ("out", "both"):
            start = self._out_indptr[u_idx]
            end = self._out_indptr[u_idx + 1]
            neighbors.update(self._out_indices[start:end])

        if direction in ("in", "both"):
            start = self._in_indptr[u_idx]
            end = self._in_indptr[u_idx + 1]
            neighbors.update(self._in_indices[start:end])

        return [self.node_ids[idx] for idx in sorted(neighbors)]

    def _step_node_neighbors(
        self,
        u: int,
        direction: str,
        visited_edges: Set[Tuple[int, int]],
    ) -> List[int]:
        """Collect neighbor indices for node u while tracking visited edges."""
        nbrs: List[int] = []
        if direction in ("out", "both"):
            s, e = self._out_indptr[u], self._out_indptr[u + 1]
            for v in self._out_indices[s:e]:
                visited_edges.add((u, v))
                nbrs.append(v)
        if direction in ("in", "both"):
            s, e = self._in_indptr[u], self._in_indptr[u + 1]
            for v in self._in_indices[s:e]:
                visited_edges.add((v, u))
                nbrs.append(v)
        return nbrs

    def _expand_bfs_layer(
        self,
        current_layer: List[int],
        next_depth: int,
        direction: str,
        depth_map: Dict[int, int],
        visited_edges: Set[Tuple[int, int]],
    ) -> List[int]:
        """Expand one BFS depth level across all nodes in the current frontier."""
        next_layer: List[int] = []
        for u in current_layer:
            for v in self._step_node_neighbors(u, direction, visited_edges):
                if v not in depth_map:
                    depth_map[v] = next_depth
                    next_layer.append(v)
        return next_layer

    def traverse_k_hop(
        self,
        start_node_ids: Iterable[str],
        *,
        max_hops: int = 3,
        direction: str = "both",
    ) -> Dict[str, Any]:
        """Sub-millisecond pointer-chasing k-hop neighborhood traversal in memory."""
        t0 = time.perf_counter()
        valid_start = [nid for nid in start_node_ids if nid in self.node_to_idx]
        if not valid_start:
            return {
                "start_node_ids": list(start_node_ids),
                "max_hops": max_hops,
                "nodes": {},
                "node_count": 0,
                "edge_count": 0,
                "max_depth_reached": 0,
                "latency_ms": 0.0,
            }

        depth_map: Dict[int, int] = {}
        visited_edges: Set[Tuple[int, int]] = set()
        current_layer: List[int] = []

        for nid in valid_start:
            u_idx = self.node_to_idx[nid]
            depth_map[u_idx] = 0
            current_layer.append(u_idx)

        current_depth = 0
        while current_layer and current_depth < max_hops:
            next_depth = current_depth + 1
            current_layer = self._expand_bfs_layer(
                current_layer, next_depth, direction, depth_map, visited_edges
            )
            current_depth = next_depth

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        nodes_dict: Dict[str, Dict[str, Any]] = {}
        for idx, depth in depth_map.items():
            nid = self.node_ids[idx]
            nodes_dict[nid] = {
                "node_id": nid,
                "depth": depth,
            }

        max_depth = max(depth_map.values()) if depth_map else 0

        return {
            "start_node_ids": list(start_node_ids),
            "max_hops": max_hops,
            "nodes": nodes_dict,
            "node_count": len(nodes_dict),
            "edge_count": len(visited_edges),
            "max_depth_reached": max_depth,
            "latency_ms": round(elapsed_ms, 3),
        }
