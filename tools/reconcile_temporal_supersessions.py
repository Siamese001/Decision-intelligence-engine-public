#!/usr/bin/env python3
"""Wave 3: Reconcile temporal contradictions and supersession alignment."""
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

from dual_pipeline.graph_scout_core.scans import scan_temporal_contradictions
from dual_pipeline.storage.governed_context import GovernedStorageContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def reconcile_temporal_supersessions(
    graph_path: Path,
    ledger_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict:
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    receipt_data = {
        "wave": 3,
        "title": "Historical Contradiction & Supersession Alignment",
        "graph_path": str(graph_path),
        "timestamp": now_iso,
        "contradictions_found": 0,
        "assertions_superseded": 0,
        "search_docs_updated": 0,
        "sample_superseded_pairs": [],
        "status": "PASS",
        "mutated": False,
    }

    with GovernedStorageContext.read_context(graph_path) as conn:
        findings = scan_temporal_contradictions(conn)

    # De-duplicate by older assertion id to take latest newer assertion
    pair_map = {}
    for f in findings:
        pair_map[f.assertion_id_older] = f

    receipt_data["contradictions_found"] = len(findings)
    receipt_data["assertions_superseded"] = len(pair_map)
    receipt_data["sample_superseded_pairs"] = [
        {
            "subject": f.subject_name,
            "predicate": f.predicate,
            "older_assertion_id": f.assertion_id_older,
            "newer_assertion_id": f.assertion_id_newer,
            "older_date": f.timestamp_older,
            "newer_date": f.timestamp_newer,
        }
        for f in list(pair_map.values())[:10]
    ]

    if not pair_map:
        return receipt_data

    from dual_pipeline.hitl_governance_core.mutation_approval import request_mutation_approval
    approval = request_mutation_approval(
        action_type="RECONCILE_TEMPORAL_SUPERSESSIONS",
        target_resource=graph_path,
        table_deltas={"assertion": len(pair_map), "search_document": len(pair_map)},
        description=f"Supersede {len(pair_map)} older historical assertions",
        ledger_path=ledger_path,
        storage_root=runtime_root,
    )

    if not approval.get("cleared"):
        receipt_data["status"] = approval.get("status", "AWAITING_APPROVAL")
        receipt_data["approval_request_id"] = approval.get("request_id")
        receipt_data["delta_digest"] = approval.get("delta_digest")
        receipt_data["mutated"] = False
        return receipt_data

    logger.info("[LIVE] Applying supersession updates to %d assertions...", len(pair_map))
    with GovernedStorageContext.write_context(graph_path) as conn:
        cur = conn.cursor()
        for older_id, f in pair_map.items():
            cur.execute(
                """UPDATE assertion
                   SET currentness_state = 'historical',
                       validation_state = 'superseded',
                       retrieval_eligible = 0,
                       superseded_by_assertion_id = ?,
                       superseded_at = ?,
                       updated_at = ?
                   WHERE assertion_id = ?""",
                (f.assertion_id_newer, now_iso, now_iso, older_id),
            )
            cur.execute(
                """UPDATE search_document
                   SET currentness_state = 'historical',
                       retrieval_eligible = 0,
                       updated_at = ?
                   WHERE assertion_id = ?""",
                (now_iso, older_id),
            )
            receipt_data["search_docs_updated"] += cur.rowcount

    receipt_data["mutated"] = True
    logger.info(
        "[LIVE] Successfully updated %d assertions to superseded status (%d search documents updated).",
        len(pair_map),
        receipt_data["search_docs_updated"],
    )
    return receipt_data


def repair_wrongfully_superseded_claim_text(graph_path: Path) -> dict:
    """Restore non-functional assertions (like HAS_CLAIM_TEXT) that were wrongfully superseded."""
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with GovernedStorageContext.write_context(graph_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE assertion
               SET currentness_state = 'current',
                   validation_state = 'source_statement',
                   retrieval_eligible = 1,
                   superseded_by_assertion_id = NULL,
                   superseded_at = NULL,
                   updated_at = ?
               WHERE predicate = 'HAS_CLAIM_TEXT'
                 AND validation_state = 'superseded'""",
            (now_iso,),
        )
        restored_assertions = cur.rowcount
        cur.execute(
            """UPDATE search_document
               SET currentness_state = 'current',
                   retrieval_eligible = 1,
                   updated_at = ?
               WHERE assertion_id IN (
                   SELECT assertion_id FROM assertion WHERE predicate = 'HAS_CLAIM_TEXT'
               )""",
            (now_iso,),
        )
        restored_docs = cur.rowcount
    logger.info("[REPAIR] Restored %d assertions and %d search docs.", restored_assertions, restored_docs)
    return {
        "restored_assertions": restored_assertions,
        "restored_search_documents": restored_docs,
        "timestamp": now_iso,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile temporal contradictions and align supersession."
    )
    parser.add_argument("--graph", required=True, type=Path, help="Path to SQLite graph database")
    parser.add_argument(
        "--output-receipt",
        type=Path,
        default=ROOT / "reports" / "hygiene" / "wave3_supersession_receipt.json",
    )
    parser.add_argument("--ledger-path", type=Path, default=None, help="Path to HITL decision ledger")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Path to runtime root")
    parser.add_argument(
        "--repair-claim-text",
        action="store_true",
        help="Restore wrongfully superseded HAS_CLAIM_TEXT assertions",
    )
    args = parser.parse_args()

    if args.repair_claim_text:
        repair_res = repair_wrongfully_superseded_claim_text(args.graph)
        print(f"Repaired claim text assertions: {repair_res}")

    res = reconcile_temporal_supersessions(args.graph, ledger_path=args.ledger_path, runtime_root=args.runtime_root)
    args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_receipt, "w") as f:
        json.dump(res, f, indent=2)
    print(f"Receipt written to {args.output_receipt}")


if __name__ == "__main__":
    main()

