#!/usr/bin/env python3
"""Wave 2: Resynchronize full-text search indexes (node_fts and claim_fts)."""
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


def resync_fts_indexes(
    graph_path: Path,
    ledger_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict:
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    receipt_data = {
        "wave": 2,
        "title": "Legacy FTS Index Resynchronization",
        "graph_path": str(graph_path),
        "timestamp": now_iso,
        "node_fts_before": 0,
        "node_fts_orphans": 0,
        "node_fts_missing": 0,
        "claim_fts_before": 0,
        "claim_fts_missing": 0,
        "projected_node_fts_after": 0,
        "projected_claim_fts_after": 0,
        "status": "PASS",
        "mutated": False,
    }

    with GovernedStorageContext.read_context(graph_path) as conn:
        cur = conn.cursor()
        node_cnt = cur.execute("SELECT count(*) FROM node").fetchone()[0]
        node_fts_cnt = cur.execute("SELECT count(*) FROM node_fts").fetchone()[0]
        node_orphans = cur.execute("SELECT count(*) FROM node_fts WHERE node_id NOT IN (SELECT node_id FROM node)").fetchone()[0]
        node_missing = cur.execute("SELECT count(*) FROM node WHERE node_id NOT IN (SELECT node_id FROM node_fts)").fetchone()[0]

        claim_cnt = cur.execute("SELECT count(*) FROM claim").fetchone()[0]
        claim_fts_cnt = cur.execute("SELECT count(*) FROM claim_fts").fetchone()[0]
        claim_missing = cur.execute("SELECT count(*) FROM claim WHERE claim_id NOT IN (SELECT claim_id FROM claim_fts)").fetchone()[0]

        receipt_data["node_fts_before"] = node_fts_cnt
        receipt_data["node_fts_orphans"] = node_orphans
        receipt_data["node_fts_missing"] = node_missing
        receipt_data["projected_node_fts_after"] = node_cnt

        receipt_data["claim_fts_before"] = claim_fts_cnt
        receipt_data["claim_fts_missing"] = claim_missing
        receipt_data["projected_claim_fts_after"] = claim_cnt

    needs_resync = (node_orphans > 0 or node_missing > 0 or claim_missing > 0)
    if not needs_resync:
        return receipt_data

    from dual_pipeline.hitl_governance_core.mutation_approval import request_mutation_approval
    approval = request_mutation_approval(
        action_type="RESYNC_FTS_INDEXES",
        target_resource=graph_path,
        table_deltas={"node_fts": node_cnt, "claim_fts": claim_cnt},
        description=f"Resync FTS indexes: node_fts ({node_cnt} target rows), claim_fts ({claim_cnt} target rows)",
        ledger_path=ledger_path,
        storage_root=runtime_root,
    )

    if not approval.get("cleared"):
        receipt_data["status"] = approval.get("status", "AWAITING_APPROVAL")
        receipt_data["approval_request_id"] = approval.get("request_id")
        receipt_data["delta_digest"] = approval.get("delta_digest")
        receipt_data["mutated"] = False
        return receipt_data

    logger.info("[LIVE] Resynchronizing node_fts, claim_fts, and search_fts...")
    with GovernedStorageContext.write_context(graph_path) as conn:
        cur = conn.cursor()
        # Clean and rebuild node_fts
        cur.execute("PRAGMA table_info(node_fts)")
        nfts_cols = {r[1] for r in cur.fetchall()}
        cur.execute("DELETE FROM node_fts")
        if "aliases" in nfts_cols and "node_type" in nfts_cols:
            cur.execute("""
                INSERT INTO node_fts (node_id, canonical_name, description, aliases, node_type)
                SELECT node_id, canonical_name, COALESCE(description, ''), '', node_type FROM node
            """)
        else:
            cur.execute("""
                INSERT INTO node_fts (node_id, canonical_name, display_name, description)
                SELECT node_id, canonical_name, display_name, description FROM node
            """)
        # Clean and rebuild claim_fts
        cur.execute("DELETE FROM claim_fts")
        cur.execute("""
            INSERT INTO claim_fts (claim_id, claim_text)
            SELECT claim_id, claim_text FROM claim
        """)
        # Rebuild search_fts so candidate search_document rows are FTS-indexed
        if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'").fetchone():
            rebuild_fts_sql = "INSERT INTO search_fts(search_fts) VALUES('rebuild')"
            cur.execute(rebuild_fts_sql)

    receipt_data["mutated"] = True
    logger.info("[LIVE] FTS resynchronization completed successfully.")
    return receipt_data


def main():
    parser = argparse.ArgumentParser(description="Resynchronize legacy FTS tables.")
    parser.add_argument("--graph", required=True, type=Path, help="Path to SQLite graph database")
    parser.add_argument(
        "--output-receipt",
        type=Path,
        default=ROOT / "reports" / "hygiene" / "wave2_fts_resync_receipt.json",
    )
    parser.add_argument("--ledger-path", type=Path, default=None, help="Path to HITL decision ledger")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Path to runtime root")
    args = parser.parse_args()

    res = resync_fts_indexes(
        args.graph,
        ledger_path=args.ledger_path,
        runtime_root=args.runtime_root,
    )
    args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_receipt, "w") as f:
        json.dump(res, f, indent=2)
    print(f"Receipt written to {args.output_receipt}")


if __name__ == "__main__":
    main()
