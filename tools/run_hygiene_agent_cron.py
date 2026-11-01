#!/usr/bin/env python3
"""
CLI entry point for running the Knowledge Graph Hygiene Agent autonomously.
Intended to be scheduled via Antigravity Cron or `/schedule` slash commands.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) != sys.path[0]:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.graph_maintenance_core.posture_ledger import snapshot_graph_vs_archive_posture
from dual_pipeline.storage.governed_context import GovernedStorageContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run the 8-Agent Adversarial Hygiene Council & Panel")
    parser.add_argument(
        "--graph", required=True, type=Path, help="Path to the graph SQLite database"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Path to save the Remediation Package JSON"
    )
    args = parser.parse_args()

    if not args.graph.exists():
        logger.error(f"Graph database not found at {args.graph}")
        sys.exit(1)

    logger.info("Initializing 8-Agent Adversarial Graph Hygiene Council...")
    panel = GraphHygienePanel()

    logger.info(f"Running panel against {args.graph}")
    pkg = panel.run_panel(args.graph)
    with GovernedStorageContext.read_context(args.graph) as ro_conn:
        posture_snap = snapshot_graph_vs_archive_posture(ro_conn)

    # Convert the package to a dict for JSON serialization
    pkg_dict = {
        "schema": "learning-change-proposal:1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": pkg.status,
        "model_tier": pkg.model_tier,
        "council_size": pkg.council_size,
        "approval_required": pkg.approval_required,
        "auto_apply": pkg.auto_apply,
        "strict_package_gating": True,
        "hitl_receipt_bound": pkg.approval_required,
        "graph_vs_archive_posture": posture_snap,
        "summary": (
            f"8-Agent Hygiene Council detected {len(pkg.findings)} findings partitioned into "
            f"{len(pkg.hitl_clusters)} atomic HITL decision clusters."
        ),
        "findings": [
            {
                "detective": f.detective,
                "severity": f.severity,
                "description": f.description,
                "entity_references": f.entity_references,
                "proposed_fix": f.proposed_fix,
                "raw_data": f.raw_data,
            }
            for f in pkg.findings
        ],
        "remediation_directives": pkg.remediation_directives,
        "council_debates": pkg.council_debates,
        "hitl_clusters": pkg.hitl_clusters,
    }

    # Ensure output dir exists
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(pkg_dict, f, indent=2)

    logger.info(f"Remediation package saved to {args.output}")

    if pkg.findings:
        logger.warning(
            f"Hygiene panel completed with {len(pkg.findings)} findings requiring review."
        )
    else:
        logger.info("Hygiene panel completed successfully. Graph is clean.")


if __name__ == "__main__":
    main()
