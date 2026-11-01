"""
Graph Freshness Orchestrator (Waves 7 & 8).
Executes the full 10-agent panel and outputs a deterministic remediation plan.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dual_pipeline.graph_freshness_core.agents.delta import DeltaAuditor
from dual_pipeline.graph_freshness_core.agents.detection import (
    AccuracyReviewer,
    EvidenceReaper,
    FreshnessWarden,
    LineageInspector,
    TopologyCartographer,
)
from dual_pipeline.graph_freshness_core.agents.historian import MetricHistorian
from dual_pipeline.graph_freshness_core.agents.judge import RemediationJudge
from dual_pipeline.graph_freshness_core.agents.sentinel import RetrievalSentinel
from dual_pipeline.graph_freshness_core.agents.supersession import SupersessionResolver
from dual_pipeline.graph_freshness_core.metrics import calculate_metrics
from dual_pipeline.graph_freshness_core.policy import load_policy
from storage.connection import create_sqlite_conn

logger = logging.getLogger(__name__)


def run_orchestrator(graph_path: Path, output_path: Path, history_path: Path) -> dict:
    policy = load_policy()

    a1 = TopologyCartographer()
    a2 = DeltaAuditor()
    a3 = AccuracyReviewer()
    a4 = SupersessionResolver()
    a5 = EvidenceReaper()
    a6 = FreshnessWarden()
    a7 = LineageInspector()
    a8 = MetricHistorian()
    a9 = RetrievalSentinel()
    a10 = RemediationJudge()

    with create_sqlite_conn(str(graph_path)) as conn:
        pre_metrics = calculate_metrics(conn)
        a8.record(pre_metrics, history_path)

        # Scans
        a1.scan(conn) # Topology (metrics can be appended to report if needed)
        a3.scan(conn) # Accuracy

        orphaned = a5.scan(conn, policy)
        stale = a6.scan(conn, policy)
        lineage = a7.scan(conn, policy)
        supersession = a4.scan(conn, policy)

        affected_ids = stale.stale_assertion_ids | lineage.assertions_to_quarantine | set(supersession.superseded_mapping.keys())
        sentinel = a9.scan(conn, affected_ids)

        plan = a10.build_transaction(orphaned, stale, lineage, supersession, sentinel)

        # Dry-run mutation to audit deltas
        try:
            conn.execute("BEGIN TRANSACTION;")
            for sql, params in plan.statements:
                conn.execute(sql, params)

            post_metrics = calculate_metrics(conn)

            delta = a2.scan(pre_metrics, post_metrics)
            if delta.unintended_side_effects:
                raise RuntimeError("Unintended side effects detected during dry-run pruning.")
        finally:
            conn.rollback()

        receipt = {
            "policy": policy.__dict__,
            "pre_metrics": pre_metrics,
            "post_metrics_dry_run": post_metrics,
            "delta": delta.metric_diffs,
            "statements_count": len(plan.statements),
            "sql_script": plan.sql_script,
            "status": "PLAN_READY"
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(receipt, f, indent=2)

        return receipt

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=str, required=True, help="Path to sqlite graph")
    parser.add_argument("--output", type=str, required=True, help="Path to output remediation plan JSON")
    parser.add_argument("--history", type=str, default="reports/graph_metrics_history.jsonl", help="Path to metrics history")
    args = parser.parse_args()

    run_orchestrator(Path(args.graph), Path(args.output), Path(args.history))
    logger.info("Graph freshness orchestrator finished.")
