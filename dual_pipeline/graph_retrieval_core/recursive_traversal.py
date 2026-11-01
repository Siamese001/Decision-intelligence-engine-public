"""Multi-hop relational graph neighborhood traversal via SQLite recursive CTEs.

Provides deterministic, cycle-safe 1-to-k hop traversal over the canonical
knowledge graph to discover connected deliverables, SOWs, patterns, and cards.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from storage.database import sqlite3
from ..core import connect_sqlite
from .capability_bridge import collect_capability_bridge_pairs, resolve_bridge_edge_type


def _resolve_default_graph_path() -> Path:
    """Resolve the active graph database path from the authoritative runtime root."""
    from ..runtime_root import (
        resolve_active_graph_path as _resolve_rt_graph,
        resolve_authoritative_runtime_root,
    )

    repo_root = Path(__file__).resolve().parents[2]
    try:
        auth_root = resolve_authoritative_runtime_root(repo_root)
        graph_file = _resolve_rt_graph(auth_root, enforce_integrity=False)
        if graph_file.is_file():
            return graph_file
    except Exception:
        pass

    candidate_roots = [
        Path.cwd() / ".local" / "dual_pipeline",
        Path.cwd().parent.parent / ".local" / "dual_pipeline",
    ]
    for root in candidate_roots:
        pair_file = root / "active_pair.json"
        if pair_file.is_file():
            import json

            try:
                data = json.loads(pair_file.read_text(encoding="utf-8"))
                graph_subpath = data.get("graph_path")
                if graph_subpath:
                    graph_file = root / graph_subpath
                    if graph_file.is_file():
                        return graph_file
            except Exception:
                pass
    raise RuntimeError("Active graph path could not be resolved from active_pair.json")


def _use_assertion_table(cursor: sqlite3.Cursor) -> bool:
    """Return True when the database has a populated assertion table."""
    row = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion'"
    ).fetchone()
    if not row:
        return False
    cnt = cursor.execute(
        "SELECT 1 FROM assertion WHERE currentness_state='current' AND object_node_id IS NOT NULL LIMIT 1"
    ).fetchone()
    return cnt is not None


def _expand_split_identity_seeds(
    cursor: sqlite3.Cursor,
    nodes_input: list[str],
) -> list[str]:
    """Expand seed node_ids with split-identity twins sharing normalized canonical_name or redirect."""
    if not nodes_input:
        return []
    placeholders = ",".join("?" for _ in nodes_input)
    try:
        rows = cursor.execute(
            f"SELECT node_id, canonical_name FROM node WHERE node_id IN ({placeholders})",
            nodes_input,
        ).fetchall()
    except Exception:
        return nodes_input
    norm_names = {
        " ".join(str(r[1] or "").lower().replace("-", " ").replace("_", " ").split())
        for r in rows
        if r[1]
    }
    expanded = list(dict.fromkeys(nodes_input))
    if norm_names:
        all_nodes = cursor.execute("SELECT node_id, canonical_name FROM node").fetchall()
        for nid, cname in all_nodes:
            norm = " ".join(str(cname or "").lower().replace("-", " ").replace("_", " ").split())
            if norm in norm_names and nid not in expanded:
                expanded.append(nid)
    return expanded


def _assertion_active_clause(include_candidates: bool, alias: str = "e") -> str:
    if include_candidates:
        return (
            f"{alias}.canonical_status IN ('canonical', 'candidate') "
            f"AND {alias}.currentness_state IN ('current', 'unknown') "
            f"AND ({alias}.superseded_by_assertion_id IS NULL OR {alias}.superseded_by_assertion_id = '') "
            f"AND {alias}.object_node_id IS NOT NULL"
        )
    return (
        f"{alias}.canonical_status = 'canonical' AND {alias}.canonical_eligible = 1 "
        f"AND {alias}.currentness_state = 'current' "
        f"AND ({alias}.superseded_by_assertion_id IS NULL OR {alias}.superseded_by_assertion_id = '') "
        f"AND {alias}.object_node_id IS NOT NULL"
    )


def _build_cte_query(
    nodes_input: list[str],
    bounded_hops: int,
    direction: str,
    edge_types: Iterable[str] | None,
    *,
    use_assertions: bool = True,
    include_candidates: bool = False,
) -> tuple[str, list[Any]]:
    """Build the recursive CTE query and bound parameters."""
    src_col = "e.subject_node_id" if use_assertions else "e.from_node_id"
    tgt_col = "e.object_node_id" if use_assertions else "e.to_node_id"

    if direction == "out":
        step_join, next_node = f"{src_col} = t.node_id", tgt_col
    elif direction == "in":
        step_join, next_node = f"{tgt_col} = t.node_id", src_col
    else:  # both
        step_join = f"({src_col} = t.node_id OR {tgt_col} = t.node_id)"
        next_node = f"CASE WHEN {src_col} = t.node_id THEN {tgt_col} ELSE {src_col} END"

    edge_type_clause = ""
    edge_params: list[Any] = []
    et_list = list(edge_types) if edge_types is not None else []
    if et_list:
        placeholders = ",".join("?" for _ in et_list)
        if use_assertions:
            edge_type_clause = f"AND (e.predicate IN ({placeholders}) OR e.legacy_predicate IN ({placeholders}))"
            edge_params.extend(et_list + et_list)
        else:
            edge_type_clause = f"AND e.edge_type IN ({placeholders})"
            edge_params.extend(et_list)

    start_placeholders = ",".join("?" for _ in nodes_input)
    rel_table = "assertion e" if use_assertions else "edge e"
    active_filter = f"AND {_assertion_active_clause(include_candidates, 'e')}" if use_assertions else "AND e.superseded_by IS NULL"

    cte_query = f"""
    WITH RECURSIVE traverse(node_id, depth, path) AS (
        SELECT node_id, 0, '|' || node_id || '|' FROM node WHERE node_id IN ({start_placeholders})
        UNION
        SELECT {next_node}, t.depth + 1, t.path || {next_node} || '|'
        FROM traverse t JOIN {rel_table} ON {step_join}
        WHERE t.depth < ? {active_filter} {edge_type_clause}
          AND instr(t.path, '|' || {next_node} || '|') = 0
    )
    SELECT t.node_id, MIN(t.depth) as min_depth, n.canonical_name, n.node_type
    FROM traverse t JOIN node n ON n.node_id = t.node_id
    GROUP BY t.node_id, n.canonical_name, n.node_type ORDER BY min_depth, t.node_id;
    """
    return cte_query, list(nodes_input) + [bounded_hops] + edge_params


def _fetch_subgraph_edges(
    cursor: sqlite3.Cursor,
    traversed_node_ids: list[str],
    *,
    use_assertions: bool = True,
    include_candidates: bool = False,
) -> list[dict[str, Any]]:
    """Fetch interconnecting active relationships between traversed nodes."""
    if len(traversed_node_ids) <= 1:
        return []
    edge_placeholders = ",".join("?" for _ in traversed_node_ids)
    if use_assertions:
        edge_query = f"""
        SELECT a.assertion_id, a.subject_node_id, COALESCE(NULLIF(a.legacy_predicate, ''), a.predicate) AS edge_type,
               a.object_node_id, COALESCE(a.scope_text, a.object_literal, '') AS description, a.confidence_label, a.materiality
        FROM assertion a
        WHERE {_assertion_active_clause(include_candidates, 'a')}
          AND a.subject_node_id IN ({edge_placeholders}) AND a.object_node_id IN ({edge_placeholders})
        ORDER BY a.subject_node_id, a.object_node_id;
        """
    else:
        edge_query = f"""
        SELECT e.edge_id, e.from_node_id, e.edge_type, e.to_node_id, e.description, e.confidence, e.materiality
        FROM edge e
        WHERE e.superseded_by IS NULL AND e.from_node_id IN ({edge_placeholders}) AND e.to_node_id IN ({edge_placeholders})
        ORDER BY e.from_node_id, e.to_node_id;
        """
    cursor.execute(edge_query, traversed_node_ids + traversed_node_ids)
    return [
        {"edge_id": eid, "from_node_id": src, "edge_type": etype, "to_node_id": tgt, "description": desc, "confidence": conf, "materiality": mat}
        for (eid, src, etype, tgt, desc, conf, mat) in cursor.fetchall()
    ]


def _load_active_bridge_maps(
    cursor: sqlite3.Cursor,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None:
    try:
        all_nodes = {
            r[0]: {"node_id": r[0], "canonical_name": r[1], "node_type": r[2]}
            for r in cursor.execute("SELECT node_id, canonical_name, node_type FROM node WHERE active=1").fetchall()
        }
        all_assertions = {
            r[0]: {"assertion_id": r[0], "subject_node_id": r[1], "object_node_id": r[2], "object_literal": r[3], "currentness_state": r[4]}
            for r in cursor.execute(
                "SELECT assertion_id, subject_node_id, object_node_id, COALESCE(object_literal, scope_text), currentness_state "
                "FROM assertion WHERE currentness_state='current'"
            ).fetchall()
        }
        return all_nodes, all_assertions
    except Exception:
        return None


def _expand_virtual_constraint_bridges(
    cursor: sqlite3.Cursor,
    nodes_dict: dict[str, dict[str, Any]],
    edges_list: list[dict[str, Any]],
    bounded_hops: int,
) -> None:
    """Expand traversed nodes and edges across virtual capability and constraint bridges."""
    loaded = _load_active_bridge_maps(cursor)
    if loaded is None:
        return
    all_nodes, all_assertions = loaded
    directed_pairs = [
        (src_id, tgt_id)
        for u_id, v_id in sorted(collect_capability_bridge_pairs(all_nodes, all_assertions))
        for src_id, tgt_id in ((u_id, v_id), (v_id, u_id))
        if tgt_id in all_nodes
    ]
    seen_edges = {(e["from_node_id"], e["to_node_id"], e["edge_type"]) for e in edges_list}
    for hop in range(1, bounded_hops + 1):
        frontier = {nid for nid, info in nodes_dict.items() if info["depth"] == hop - 1}
        for src_id, tgt_id in directed_pairs:
            if src_id not in frontier:
                continue
            nodes_dict.setdefault(
                tgt_id,
                {"node_id": tgt_id, "canonical_name": all_nodes[tgt_id]["canonical_name"], "node_type": all_nodes[tgt_id]["node_type"], "depth": hop},
            )
            etype = resolve_bridge_edge_type(src_id, tgt_id, all_nodes, all_assertions)
            if (src_id, tgt_id, etype) not in seen_edges:
                seen_edges.add((src_id, tgt_id, etype))
                edges_list.append({
                    "edge_id": f"cap-bridge:{src_id}->{tgt_id}",
                    "from_node_id": src_id,
                    "edge_type": etype,
                    "to_node_id": tgt_id,
                    "description": f"Virtual capability/constraint bridge ({etype})",
                    "confidence": "high",
                    "materiality": "high",
                })


def traverse_k_hop_neighborhood(
    start_node_ids: Iterable[str],
    *,
    max_hops: int = 3,
    graph_path: Path | str | None = None,
    edge_types: Iterable[str] | None = None,
    direction: str = "both",
    connection: sqlite3.Connection | None = None,
    include_candidates: bool = False,
    expand_split_twins: bool = False,
    include_constraint_bridges: bool = False,
) -> dict[str, Any]:
    """Execute recursive k-hop traversal from given start nodes using SQLite recursive CTE."""
    start_time = time.perf_counter()
    nodes_input = [str(nid).strip() for nid in start_node_ids if str(nid).strip()]
    if not nodes_input:
        return {"start_node_ids": [], "max_hops": max_hops, "nodes": {}, "edges": [], "node_count": 0, "edge_count": 0, "max_depth_reached": 0, "latency_ms": 0.0}

    bounded_hops = max(1, min(int(max_hops), 5))
    close_conn = False
    if connection is None:
        resolved_graph = _resolve_default_graph_path() if graph_path is None else Path(graph_path)
        if not resolved_graph.is_file():
            raise FileNotFoundError(f"Graph database not found: {resolved_graph}")
        conn, close_conn = connect_sqlite(resolved_graph, readonly=True), True
    else:
        conn = connection

    try:
        cursor = conn.cursor()
        use_assertions = _use_assertion_table(cursor)
        effective_seeds = _expand_split_identity_seeds(cursor, nodes_input) if expand_split_twins else nodes_input
        cte_query, full_params = _build_cte_query(
            effective_seeds, bounded_hops, direction, edge_types, use_assertions=use_assertions, include_candidates=include_candidates
        )
        cursor.execute(cte_query, full_params)
        raw_nodes = cursor.fetchall()
        traversed_node_ids = [row[0] for row in raw_nodes]
        nodes_dict = {row[0]: {"node_id": row[0], "canonical_name": row[2], "node_type": row[3], "depth": row[1]} for row in raw_nodes}
        edges_list = _fetch_subgraph_edges(cursor, traversed_node_ids, use_assertions=use_assertions, include_candidates=include_candidates)
        if include_constraint_bridges:
            _expand_virtual_constraint_bridges(cursor, nodes_dict, edges_list, bounded_hops)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return {
            "start_node_ids": nodes_input, "max_hops": bounded_hops, "nodes": nodes_dict, "edges": edges_list,
            "node_count": len(nodes_dict), "edge_count": len(edges_list),
            "max_depth_reached": max((n["depth"] for n in nodes_dict.values()), default=0), "latency_ms": round(elapsed_ms, 2),
        }
    finally:
        if close_conn:
            conn.close()
