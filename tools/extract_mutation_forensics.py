#!/usr/bin/env python3
"""
Extract empirical mutation records directly from active and baseline SQLite graph databases.
Zero mocks, zero synthetic test doubles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from storage.connection import create_sqlite_conn


def _dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def extract_mutations(active_db: Path, baseline_db: Path) -> Dict[str, Any]:
    active_bytes = active_db.read_bytes()
    baseline_bytes = baseline_db.read_bytes()

    a_sha = hashlib.sha256(active_bytes).hexdigest()
    b_sha = hashlib.sha256(baseline_bytes).hexdigest()

    conn_a = create_sqlite_conn(active_db, readonly=True)
    conn_a.row_factory = _dict_factory
    cur_a = conn_a.cursor()

    conn_b = create_sqlite_conn(baseline_db, readonly=True)
    conn_b.row_factory = _dict_factory
    cur_b = conn_b.cursor()

    # 1. Nodes
    active_nodes = {r["node_id"]: r for r in cur_a.execute("SELECT * FROM node").fetchall()}
    baseline_nodes = {r["node_id"]: r for r in cur_b.execute("SELECT * FROM node").fetchall()}

    added_node_ids = set(active_nodes.keys()) - set(baseline_nodes.keys())
    common_node_ids = set(active_nodes.keys()) & set(baseline_nodes.keys())
    updated_nodes = []
    for nid in common_node_ids:
        an = active_nodes[nid]
        bn = baseline_nodes[nid]
        if an != bn:
            updated_nodes.append({"node_id": nid, "before": bn, "after": an})

    added_nodes = [active_nodes[nid] for nid in sorted(added_node_ids)]
    for n in added_nodes:
        n_id_bytes = n["node_id"].encode("utf-8")
        n_name_bytes = (n["canonical_name"] or "").encode("utf-8")
        n["raw_byte_offset_id"] = active_bytes.find(n_id_bytes)
        n["raw_byte_offset_name"] = active_bytes.find(n_name_bytes) if n_name_bytes else -1

    # 2. Assertions
    active_assertions = {r["assertion_id"]: r for r in cur_a.execute("SELECT * FROM assertion").fetchall()}
    baseline_assertions = {r["assertion_id"]: r for r in cur_b.execute("SELECT * FROM assertion").fetchall()}

    added_assertion_ids = set(active_assertions.keys()) - set(baseline_assertions.keys())
    common_assertion_ids = set(active_assertions.keys()) & set(baseline_assertions.keys())
    updated_assertions = []
    for aid in common_assertion_ids:
        aa = active_assertions[aid]
        ba = baseline_assertions[aid]
        if aa != ba:
            updated_assertions.append({"assertion_id": aid, "before": ba, "after": aa})

    added_assertions = [active_assertions[aid] for aid in sorted(added_assertion_ids)]
    for a in added_assertions:
        a_id_bytes = a["assertion_id"].encode("utf-8")
        a["raw_byte_offset_id"] = active_bytes.find(a_id_bytes)

    # 3. Evidence Items
    active_ev = {r["evidence_id"]: r for r in cur_a.execute("SELECT * FROM evidence_item").fetchall()}
    baseline_ev = {r["evidence_id"]: r for r in cur_b.execute("SELECT * FROM evidence_item").fetchall()}
    added_ev_ids = set(active_ev.keys()) - set(baseline_ev.keys())
    added_evidence = [active_ev[eid] for eid in sorted(added_ev_ids)]

    # 4. Assertion Evidence Linkages
    active_ae = cur_a.execute("SELECT * FROM assertion_evidence").fetchall()
    baseline_ae = set(
        (r["assertion_id"], r["evidence_id"])
        for r in cur_b.execute("SELECT assertion_id, evidence_id FROM assertion_evidence").fetchall()
    )
    added_ae = [r for r in active_ae if (r["assertion_id"], r["evidence_id"]) not in baseline_ae]

    # 5. Audit Events
    active_audit = cur_a.execute(
        "SELECT * FROM audit_event ORDER BY event_timestamp DESC LIMIT 50"
    ).fetchall()

    return {
        "metadata": {
            "active_graph": str(active_db.resolve()),
            "active_sha256": a_sha,
            "active_size_bytes": len(active_bytes),
            "baseline_graph": str(baseline_db.resolve()),
            "baseline_sha256": b_sha,
            "baseline_size_bytes": len(baseline_bytes),
            "delta_summary": {
                "nodes_added": len(added_nodes),
                "nodes_updated": len(updated_nodes),
                "assertions_added": len(added_assertions),
                "assertions_updated": len(updated_assertions),
                "evidence_added": len(added_evidence),
                "assertion_evidence_links_added": len(added_ae),
            }
        },
        "added_nodes": added_nodes,
        "updated_nodes": updated_nodes,
        "added_assertions": added_assertions,
        "updated_assertions": updated_assertions,
        "added_evidence": added_evidence,
        "added_assertion_evidence": added_ae,
        "recent_audit_events": active_audit,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract raw empirical mutation data from graph databases.")
    parser.add_argument("--active", required=True, type=Path, help="Path to active graph.sqlite")
    parser.add_argument("--baseline", required=True, type=Path, help="Path to baseline graph.sqlite")
    parser.add_argument("--output", required=True, type=Path, help="Path to write extraction JSON")
    args = parser.parse_args()

    data = extract_mutations(args.active, args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Empirical extraction complete. Written to {args.output}")
    print(f"Nodes added: {data['metadata']['delta_summary']['nodes_added']}")
    print(f"Nodes updated: {data['metadata']['delta_summary']['nodes_updated']}")
    print(f"Assertions added: {data['metadata']['delta_summary']['assertions_added']}")
    print(f"Assertions updated: {data['metadata']['delta_summary']['assertions_updated']}")
    print(f"Evidence added: {data['metadata']['delta_summary']['evidence_added']}")


if __name__ == "__main__":
    main()
