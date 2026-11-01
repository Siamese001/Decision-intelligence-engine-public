#!/usr/bin/env python3
"""Wave 1: Remediate future-dated assertions in the Knowledge Graph."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def remediate_future_facts(
    graph_path: Path,
    today_str: str = "2026-10-06",
    ledger_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict:
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    select_sql = """
        SELECT assertion_id, subject_node_id, predicate, object_literal, valid_from, recorded_at
        FROM assertion
        WHERE valid_from IS NOT NULL AND valid_from != '' AND valid_from > ?
    """

    receipt_data = {
        "wave": 1,
        "title": "Future-Dated Assertion Realignment",
        "graph_path": str(graph_path),
        "timestamp": now_iso,
        "today": today_str,
        "future_facts_found": 0,
        "assertions_realigned": 0,
        "impacted_assertions": [],
        "status": "PASS",
        "mutated": False,
    }

    with GovernedStorageContext.read_context(graph_path) as conn:
        cur = conn.cursor()
        rows = cur.execute(select_sql, (today_str,)).fetchall()
        receipt_data["future_facts_found"] = len(rows)
        for r in rows:
            receipt_data["impacted_assertions"].append({
                "assertion_id": r[0],
                "subject_node_id": r[1],
                "predicate": r[2],
                "object_literal": r[3],
                "future_valid_from": r[4],
                "recorded_at": r[5],
                "realigned_valid_from": (r[5] or today_str)[:10],
            })

    if not receipt_data["impacted_assertions"]:
        return receipt_data

    from dual_pipeline.hitl_governance_core.mutation_approval import request_mutation_approval
    approval = request_mutation_approval(
        action_type="REMEDIATE_FUTURE_FACTS",
        target_resource=graph_path,
        table_deltas={"assertion": len(receipt_data["impacted_assertions"])},
        description=f"Realign {len(receipt_data['impacted_assertions'])} future-dated assertions",
        ledger_path=ledger_path,
        storage_root=runtime_root,
    )

    if not approval.get("cleared"):
        receipt_data["status"] = approval.get("status", "AWAITING_APPROVAL")
        receipt_data["approval_request_id"] = approval.get("request_id")
        receipt_data["delta_digest"] = approval.get("delta_digest")
        receipt_data["mutated"] = False
        return receipt_data

    logger.info("[LIVE] Realigning %d future-dated assertion(s)...", receipt_data["future_facts_found"])
    with GovernedStorageContext.write_context(graph_path) as conn:
        cur = conn.cursor()
        for item in receipt_data["impacted_assertions"]:
            cur.execute(
                """UPDATE assertion
                   SET valid_from = ?,
                       date_status = 'estimated_from_source_snapshot',
                       updated_at = ?
                   WHERE assertion_id = ?""",
                (item["realigned_valid_from"], now_iso, item["assertion_id"]),
            )
            receipt_data["assertions_realigned"] += cur.rowcount

    receipt_data["mutated"] = True
    logger.info("[LIVE] Successfully realigned %d assertion(s).", receipt_data["assertions_realigned"])
    return receipt_data


def main():
    parser = argparse.ArgumentParser(description="Remediate future-dated assertions.")
    parser.add_argument("--graph", required=True, type=Path, help="Path to SQLite graph database")
    parser.add_argument("--today", type=str, default="2026-10-06", help="Reference current date")
    parser.add_argument(
        "--output-receipt",
        type=Path,
        default=ROOT / "reports" / "hygiene" / "wave1_future_facts_receipt.json",
    )
    parser.add_argument("--ledger-path", type=Path, default=None, help="Path to HITL decision ledger")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Path to runtime root")
    args = parser.parse_args()

    res = remediate_future_facts(
        args.graph,
        today_str=args.today,
        ledger_path=args.ledger_path,
        runtime_root=args.runtime_root,
    )
    args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_receipt, "w") as f:
        json.dump(res, f, indent=2)
    print(f"Receipt written to {args.output_receipt}")


if __name__ == "__main__":
    main()
