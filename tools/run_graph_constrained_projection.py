#!/usr/bin/env python3
"""CLI runner for Graph-Constrained Hybrid Projection Architecture.

Executes end-to-end extraction of Layer 1 graph boundaries, Layer 2 forensic
evidence spans, and Layer 3 fail-closed validation, producing governed executive
briefings and immutable runtime receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_constrained_projection.engine import GraphConstrainedProjectionEngine
from dual_pipeline.graph_constrained_projection.evidence_envelope import EvidenceEnvelopeResolver
from dual_pipeline.graph_constrained_projection.graph_spine import GraphSpineResolver
from dual_pipeline.graph_constrained_projection.validator import EntityAffiliationValidator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tools.run_graph_constrained_projection")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Graph-Constrained Hybrid Projection")
    parser.add_argument("--project", default="project_lens", help="Project identifier context")
    parser.add_argument("--db-path", default="graph/nee_graph.sqlite", help="Path to SQLite graph DB")
    parser.add_argument(
        "--output-md",
        default="reports/project_lens/governed_delivery_lead_gap_analysis.md",
        help="Path for generated Markdown deliverable",
    )
    parser.add_argument(
        "--output-json",
        default="reports/project_lens/governed_delivery_lead_gap_analysis.json",
        help="Path for generated JSON deliverable",
    )
    parser.add_argument(
        "--receipt",
        default="reports/wave4_projection_e2e_receipt.json",
        help="Path for runtime verification receipt",
    )
    parser.add_argument("--validate", action="store_true", default=True, help="Enforce fail-closed validation")
    return parser.parse_args()


def compute_sha256(content_bytes: bytes) -> str:
    return hashlib.sha256(content_bytes).hexdigest()


def main() -> int:
    args = parse_args()
    logger.info("Initializing Graph-Constrained Hybrid Projection for project: %s", args.project)

    spine = GraphSpineResolver(db_path=args.db_path, project_context=args.project)
    evidence = EvidenceEnvelopeResolver(project_dir=f"output/{args.project}")
    validator = EntityAffiliationValidator(spine=spine)

    engine = GraphConstrainedProjectionEngine(
        spine=spine,
        evidence=evidence,
        validator=validator,
        project_context=args.project,
    )

    logger.info("Executing projection generation and running Layer 3 validation...")
    try:
        rows, report, md_table = engine.execute_and_validate()
    except AssertionError as exc:
        logger.error("Fail-closed validation failed: %s", exc)
        return 1

    logger.info("Validation passed: %d entities checked, 0 violations.", report.checked_entities_count)

    # 1. Write Markdown deliverable
    out_md = Path(args.output_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    md_header = (
        f"# Project LENS: Governed Delivery Lead & Pod Capability Gap Analysis\n"
        f"**Generated:** {report.timestamp_utc}\n"
        f"**Governance Engine:** Graph-Constrained Hybrid Projection Architecture (v1.0.0)\n"
        f"**Validation Status:** PASSED (Fail-Closed Entity & SOW Boundary Verification)\n\n"
    )
    full_md = md_header + md_table
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(full_md)
    md_sha256 = compute_sha256(full_md.encode("utf-8"))
    logger.info("Markdown deliverable written to: %s (sha256: %s)", out_md, md_sha256[:12])

    # 2. Write JSON deliverable
    out_json = Path(args.output_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    json_payload = {
        "project": args.project,
        "schema_version": "1.0.0",
        "timestamp_utc": report.timestamp_utc,
        "validation_report": report.to_dict(),
        "rows": [r.to_dict() for r in rows],
        "markdown_artifact_sha256": md_sha256,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2)
    json_sha256 = compute_sha256(json.dumps(json_payload, sort_keys=True).encode("utf-8"))
    logger.info("JSON deliverable written to: %s (sha256: %s)", out_json, json_sha256[:12])

    # 3. Emit Wave 4 Runtime Receipt
    receipt_path = Path(args.receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "wave": "Wave 4: Governed Projection Engine, CLI Tool & End-to-End Delivery Lead Evaluation",
        "status": "COMPLETE",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "command": f"python tools/run_graph_constrained_projection.py --project {args.project} --validate",
        "deliverables": {
            "markdown_path": str(out_md),
            "markdown_sha256": md_sha256,
            "json_path": str(out_json),
            "json_sha256": json_sha256,
        },
        "validation": report.to_dict(),
        "row_count": len(rows),
        "terminal_outcome": "SUCCESS",
    }
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
    logger.info("Wave 4 runtime receipt written to: %s", receipt_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
