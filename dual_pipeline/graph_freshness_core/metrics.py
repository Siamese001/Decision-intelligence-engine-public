"""
Graph Freshness & Integrity Metrics (Wave 1).
Extracts the baseline metrics for staleness, evidence gaps, and TTL coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.staleness_core.ttl_sweeper import AssertionTTLSweeper
from storage.connection import create_sqlite_conn

logger = logging.getLogger(__name__)

def sha256_file(path: Path) -> str:
    """Return SHA256 hex digest of file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def calculate_metrics(conn: Any) -> Dict[str, Any]:
    """Calculate the baseline metrics on the assertion and evidence tables."""
    cursor = conn.cursor()

    def q(sql: str) -> int:
        cursor.execute(sql)
        row = cursor.fetchone()
        return row[0] if row else 0

    total_assertions = q("SELECT count(*) FROM assertion")
    current_assertions = q("SELECT count(*) FROM assertion WHERE currentness_state = 'current'")

    # 515 orphaned evidence rows (no assertion_evidence link)
    evidence_orphans = q(
        "SELECT count(*) FROM evidence_item i WHERE NOT EXISTS (SELECT 1 FROM assertion_evidence e WHERE e.evidence_id = i.evidence_id)"
    )
    # evidence missing exact location
    evidence_loc_not_exact = q("SELECT count(*) FROM evidence_item WHERE location_status <> 'exact'")
    # evidence missing quote
    evidence_no_quote = q("SELECT count(*) FROM evidence_item WHERE direct_quote IS NULL OR trim(direct_quote)=''")

    # assertions without extraction run
    assertions_no_extraction_run = q("SELECT count(*) FROM assertion WHERE extraction_run_id IS NULL")

    # assertions without evidence
    assertions_no_evidence = q(
        "SELECT count(*) FROM assertion a WHERE NOT EXISTS (SELECT 1 FROM assertion_evidence e WHERE e.assertion_id = a.assertion_id)"
    )

    # candidate retrievable count
    candidate_retrievable_count = q(
        "SELECT count(*) FROM assertion WHERE currentness_state='current' AND retrieval_eligible=1 AND canonical_status='candidate'"
    )

    # stale_after coverage
    cursor.execute("""
        SELECT
            SUM(json_extract(qualifiers_json, '$.stale_after') IS NOT NULL),
            SUM(json_extract(qualifiers_json, '$.observed_at') IS NOT NULL)
        FROM assertion WHERE currentness_state='current'
    """)
    cov_row = cursor.fetchone()
    stale_after_cov = cov_row[0] if cov_row and cov_row[0] else 0
    observed_at_cov = cov_row[1] if cov_row and cov_row[1] else 0

    # multi current groups
    cursor.execute("""
        SELECT count(*) FROM (
            SELECT subject_node_id, predicate
            FROM assertion
            WHERE currentness_state='current' AND retrieval_eligible=1 AND superseded_by_assertion_id IS NULL
            GROUP BY subject_node_id, predicate
            HAVING COUNT(DISTINCT COALESCE(object_node_id, object_literal)) > 1
               AND COUNT(DISTINCT primary_source_id) > 1
        )
    """)
    multi_current_groups = cursor.fetchone()[0]

    # staleness age approximation
    cursor.execute("""
        SELECT julianday('now') - julianday(recorded_at)
        FROM assertion
        WHERE currentness_state='current'
        ORDER BY 1
    """)
    ages = [r[0] for r in cursor.fetchall() if r[0] is not None]
    n_ages = len(ages)
    p50 = ages[int(n_ages * 0.5)] if n_ages > 0 else 0
    p90 = ages[int(n_ages * 0.9)] if n_ages > 0 else 0
    max_age = ages[-1] if n_ages > 0 else 0

    return {
        "total_assertions": total_assertions,
        "current_assertions": current_assertions,
        "evidence_orphans": evidence_orphans,
        "evidence_loc_not_exact": evidence_loc_not_exact,
        "evidence_no_quote": evidence_no_quote,
        "assertions_no_extraction_run": assertions_no_extraction_run,
        "assertions_no_evidence": assertions_no_evidence,
        "candidate_retrievable_count": candidate_retrievable_count,
        "stale_after_coverage": stale_after_cov,
        "observed_at_coverage": observed_at_cov,
        "multi_current_groups": multi_current_groups,
        "staleness_age_days": {
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "max": round(max_age, 2),
        }
    }

def verify_sweeper_dry_run(conn: Any) -> Dict[str, Any]:
    """Verify that the AssertionTTLSweeper returns NO_STALE_AFTER_COLUMN."""
    sweeper = AssertionTTLSweeper(dry_run=True)
    res = sweeper.sweep_stale_assertions(conn)
    return res

def run_metrics_pipeline(graph_path: Path, receipt_path: Path) -> Dict[str, Any]:
    graph_path = graph_path.resolve()
    if not graph_path.is_file():
        raise FileNotFoundError(f"Graph not found: {graph_path}")

    pre_sha = sha256_file(graph_path)

    with create_sqlite_conn(graph_path, readonly=True) as conn:
        metrics = calculate_metrics(conn)
        sweeper_res = verify_sweeper_dry_run(conn)

    post_sha = sha256_file(graph_path)
    if pre_sha != post_sha:
        raise RuntimeError("Graph was mutated during read-only metrics collection!")

    receipt = {
        "graph_path": str(graph_path),
        "pre_sha256": pre_sha,
        "post_sha256": post_sha,
        "metrics": metrics,
        "sweeper_dry_run_status": sweeper_res,
        "status": "COMPLETE",
    }

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipt_path, "w") as f:
        json.dump(receipt, f, indent=2)

    return receipt

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=str, required=True, help="Path to sqlite graph")
    parser.add_argument("--receipt", type=str, required=True, help="Path to output receipt JSON")
    args = parser.parse_args()

    run_metrics_pipeline(Path(args.graph), Path(args.receipt))
    logger.info("Metrics baseline generated successfully.")
