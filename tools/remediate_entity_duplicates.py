#!/usr/bin/env python3
"""Wave 1: Remediate and consolidate near-duplicate entities in Knowledge Graph."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DUPLICATE_PAIRS = [
    {
        "from_node_id": "entity-b04be128143c4aa2a9823b4fea6b",
        "to_node_id": "org_nee",
        "canonical_name": "Apex Global Enterprises / NEE",
    },
    {
        "from_node_id": "entity-c4282c7adcaca1c87f6ef089ac6c",
        "to_node_id": "person_eduardo_more",
        "canonical_name": "Eduardo (Ed) Juan More",
    },
    {
        "from_node_id": "entity-c329f5db0e47b50f209f0ac2a235",
        "to_node_id": "entity-b49f2e3b4080a8ad1e3eeb0c544b",
        "canonical_name": "LENS Three-Pane Review and Validation UI",
    },
]


def consolidate_duplicates(
    graph_path: Path,
    ledger_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict:
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt_data = {
        "wave": 1,
        "title": "Near-Duplicate Entity Consolidation",
        "graph_path": str(graph_path),
        "timestamp": now_iso,
        "consolidated_pairs": [],
        "assertions_redirected": 0,
        "edges_redirected": 0,
        "nodes_deactivated": 0,
        "status": "PASS",
        "mutated": False,
    }

    with GovernedStorageContext.read_context(graph_path) as conn:
        cur = conn.cursor()
        for pair in DUPLICATE_PAIRS:
            f_id = pair["from_node_id"]
            t_id = pair["to_node_id"]
            cur.execute("SELECT count(*) FROM assertion WHERE subject_node_id = ? OR object_node_id = ?", (f_id, f_id))
            a_cnt = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM edge WHERE from_node_id = ? OR to_node_id = ?", (f_id, f_id))
            e_cnt = cur.fetchone()[0]
            receipt_data["assertions_redirected"] += a_cnt
            receipt_data["edges_redirected"] += e_cnt
            receipt_data["nodes_deactivated"] += 1
            receipt_data["consolidated_pairs"].append({
                "from_node_id": f_id,
                "to_node_id": t_id,
                "canonical_name": pair["canonical_name"],
                "assertions_impacted": a_cnt,
                "edges_impacted": e_cnt,
            })

    from dual_pipeline.hitl_governance_core.mutation_approval import request_mutation_approval
    approval = request_mutation_approval(
        action_type="REMEDIATE_ENTITY_DUPLICATES",
        target_resource=graph_path,
        table_deltas={"node": len(DUPLICATE_PAIRS), "assertion": receipt_data["assertions_redirected"]},
        description=f"Consolidate {len(DUPLICATE_PAIRS)} duplicate entity pairs",
        ledger_path=ledger_path,
        storage_root=runtime_root,
    )

    if not approval.get("cleared"):
        receipt_data["status"] = approval.get("status", "AWAITING_APPROVAL")
        receipt_data["approval_request_id"] = approval.get("request_id")
        receipt_data["delta_digest"] = approval.get("delta_digest")
        receipt_data["mutated"] = False
        return receipt_data

    logger.info("[LIVE] Applying entity consolidation for %d pairs...", len(DUPLICATE_PAIRS))
    with GovernedStorageContext.write_context(graph_path) as conn:
        cur = conn.cursor()
        receipt_data["assertions_redirected"] = 0
        receipt_data["edges_redirected"] = 0
        receipt_data["nodes_deactivated"] = 0
        receipt_data["consolidated_pairs"] = []
        for pair in DUPLICATE_PAIRS:
            f_id = pair["from_node_id"]
            t_id = pair["to_node_id"]
            merge_id = f"merge-{uuid.uuid4().hex[:24]}"

            # 1. Insert merge event
            cur.execute(
                """INSERT INTO entity_merge_event (
                    merge_event_id, canonical_node_id, event_type, status,
                    approval_reference, reason, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (merge_id, t_id, "merge", "completed", "HITL Wave 1 Approval", "Knowledge Graph Hygiene: Near-duplicate merge", now_iso, now_iso),
            )

            # 2. Insert redirect
            cur.execute(
                """INSERT OR REPLACE INTO entity_redirect (
                    from_node_id, to_node_id, merge_event_id, active, created_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (f_id, t_id, merge_id, 1, now_iso),
            )

            # 3. Update assertions
            cur.execute("UPDATE assertion SET subject_node_id = ? WHERE subject_node_id = ?", (t_id, f_id))
            sub_cnt = cur.rowcount
            cur.execute("UPDATE assertion SET object_node_id = ? WHERE object_node_id = ?", (t_id, f_id))
            obj_cnt = cur.rowcount
            a_cnt = sub_cnt + obj_cnt
            receipt_data["assertions_redirected"] += a_cnt

            # 4. Update edges
            cur.execute("UPDATE edge SET from_node_id = ? WHERE from_node_id = ?", (t_id, f_id))
            from_cnt = cur.rowcount
            cur.execute("UPDATE edge SET to_node_id = ? WHERE to_node_id = ?", (t_id, f_id))
            to_cnt = cur.rowcount
            e_cnt = from_cnt + to_cnt
            receipt_data["edges_redirected"] += e_cnt

            # 5. Deactivate from_node
            cur.execute("UPDATE node SET active = 0, updated_at = ? WHERE node_id = ?", (now_iso, f_id))
            receipt_data["nodes_deactivated"] += cur.rowcount

            receipt_data["consolidated_pairs"].append({
                "from_node_id": f_id,
                "to_node_id": t_id,
                "merge_event_id": merge_id,
                "canonical_name": pair["canonical_name"],
                "assertions_redirected": a_cnt,
                "edges_redirected": e_cnt,
            })
            receipt_data["mutated"] = True

    logger.info("[LIVE] Successfully consolidated %d pairs (%d assertions redirected).", len(DUPLICATE_PAIRS), receipt_data["assertions_redirected"])
    return receipt_data


def main():
    parser = argparse.ArgumentParser(description="Remediate near-duplicate entities.")
    parser.add_argument("--graph", required=True, type=Path, help="Path to SQLite graph database")
    parser.add_argument("--output-receipt", type=Path, default=ROOT / "reports" / "hygiene" / "wave1_entity_consolidation_receipt.json")
    args = parser.parse_args()

    res = consolidate_duplicates(args.graph)
    args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_receipt, "w") as f:
        json.dump(res, f, indent=2)
    print(f"Receipt written to {args.output_receipt}")


if __name__ == "__main__":
    main()
