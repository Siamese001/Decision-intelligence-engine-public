#!/usr/bin/env python3
"""Governed maintenance tool to repair false cross-object supersessions and sync candidate retrieval eligibility."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_writer_core.assertions import AssertionOpsMixin
from dual_pipeline.storage.governed_context import GovernedStorageContext
from tools.graph_mutation_proof_core.proof import prove, snapshot


def run_remediation(db_path: Path, receipt_path: Path | None = None) -> dict[str, Any]:
    """Execute governed supersession & eligibility remediation with 5-check physical proof."""
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    before_snap = snapshot(db_path)

    conn = GovernedStorageContext.connect(db_path, readonly=False, timeout=30.0)
    try:
        counts = AssertionOpsMixin.remediate_supersession_and_candidate_eligibility(conn, now_iso)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    after_snap = snapshot(db_path)
    sample_ids = ["asrt_edge_ce678f8934e40e77104d89ca", "asrt_claim_claim_v31_cohydrated_model_selected"]
    readback_probes = {
        "probe_no_bad_retrieval_eligible": (
            "SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM assertion WHERE (canonical_status != 'canonical' OR currentness_state != 'current') AND retrieval_eligible = 1)",
            (),
        )
    }
    proof_res = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=sample_ids,
        expected_deltas={},
        readback_probes=readback_probes,
        strict_delta=False,
    )
    payload = {
        "timestamp": now_iso,
        "db_path": str(db_path),
        "remediation_counts": counts,
        "physical_proof": proof_res,
    }
    if receipt_path is not None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Remediate cross-object supersessions and candidate eligibility.")
    parser.add_argument("--db-path", type=Path, required=True, help="Target SQLite graph database.")
    parser.add_argument("--receipt-out", type=Path, default=None, help="Optional JSON receipt output path.")
    args = parser.parse_args()
    res = run_remediation(args.db_path.resolve(), args.receipt_out.resolve() if args.receipt_out else None)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
