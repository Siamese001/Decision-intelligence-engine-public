#!/usr/bin/env python3
"""Read-only diagnostic and reachability verifier for technical_anchor nodes.

Audits `v_canonical_edge` orphan technical anchors (`edge_cnt == 0`) and verifies
that cross-workstream capability and technical alias bridges connect drone,
computer vision, camera, and cloud storage anchors in the traversal adjacency index.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_retrieval_core.capability_bridge import (
    CAPABILITY_DOMAIN_KEYWORDS,
    normalize_entity_text,
)
from dual_pipeline.graph_retrieval_core.graph_cache import read_graph_from_disk


def _resolve_default_graph_path() -> Path:
    for base in (ROOT, ROOT.parent.parent):
        gen_dir = base / ".local" / "dual_pipeline" / "generations"
        if gen_dir.is_dir():
            dbs = sorted(
                gen_dir.glob("generation-*/graph.sqlite"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if dbs:
                return dbs[0].resolve()
        candidate = base / "graph.sqlite"
        if candidate.is_file() and candidate.stat().st_size > 4096:
            return candidate.resolve()
    return (ROOT / "graph.sqlite").resolve()


def _is_capability_tracked_node(node_name: str, claim_texts: list[str]) -> bool:
    combined = f" {normalize_entity_text(node_name)} " + " ".join(
        normalize_entity_text(t) for t in claim_texts
    ) + " "
    for keywords in CAPABILITY_DOMAIN_KEYWORDS.values():
        if any(f" {kw} " in combined for kw in keywords):
            return True
    return False


def audit_technical_anchors(db_path: Path) -> dict[str, Any]:
    """Inspect canonical edge counts and bridged adjacency degrees for technical anchors."""
    conn = connect_sqlite(db_path, readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT n.node_id, n.canonical_name, n.node_type, COUNT(e.assertion_id) AS canonical_edge_cnt
              FROM node n
              LEFT JOIN v_canonical_edge e
                ON (n.node_id = e.from_node_id OR n.node_id = e.to_node_id)
             WHERE n.active = 1 AND n.node_type = 'technical_anchor'
             GROUP BY n.node_id, n.canonical_name, n.node_type
             ORDER BY n.canonical_name
            """
        ).fetchall()
    finally:
        conn.close()

    nodes, assertions, _ev, adjacency, _contra, _conf, _excl = read_graph_from_disk(db_path)
    claims_by_subj: dict[str, list[str]] = {}
    for assn in assertions.values():
        if assn.get("currentness_state") == "current" and assn.get("object_literal"):
            subj = str(assn.get("subject_node_id") or "")
            claims_by_subj.setdefault(subj, []).append(str(assn["object_literal"]))

    tracked_anchors: list[dict[str, Any]] = []
    unreachable_tracked: list[dict[str, Any]] = []

    for row in rows:
        nid = str(row["node_id"])
        name = str(row["canonical_name"])
        raw_edges = int(row["canonical_edge_cnt"])
        bridged_degree = len(adjacency.get(nid, []))
        is_tracked = _is_capability_tracked_node(name, claims_by_subj.get(nid, []))
        entry = {
            "node_id": nid,
            "canonical_name": name,
            "canonical_edge_cnt": raw_edges,
            "bridged_adjacency_degree": bridged_degree,
        }
        if is_tracked:
            tracked_anchors.append(entry)
            if bridged_degree == 0:
                unreachable_tracked.append(entry)

    return {
        "db_path": str(db_path),
        "total_technical_anchors": len(rows),
        "tracked_capability_anchors": len(tracked_anchors),
        "unreachable_tracked_count": len(unreachable_tracked),
        "unreachable_tracked": unreachable_tracked,
        "sample_bridged_anchors": [
            a for a in tracked_anchors if a["canonical_edge_cnt"] == 0 and a["bridged_adjacency_degree"] > 0
        ][:15],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit technical anchor reachability across workstreams.")
    parser.add_argument("--db", type=Path, default=None, help="Path to SQLite graph database.")
    parser.add_argument(
        "--verify-reachability",
        action="store_true",
        help="Fail closed if any tracked drone/vision/storage technical anchor has 0 bridged degree.",
    )
    args = parser.parse_args(argv)
    db_path = (args.db or _resolve_default_graph_path()).resolve()
    if not db_path.is_file():
        print(f"[ERROR] Graph database not found: {db_path}", file=sys.stderr)
        return 1

    report = audit_technical_anchors(db_path)
    print(json.dumps(report, indent=2))
    if args.verify_reachability and report["unreachable_tracked_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
