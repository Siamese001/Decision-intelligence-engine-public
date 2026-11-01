#!/usr/bin/env python3
"""Build the exact 12 HITL decisions for the Sherpa semantic correction run."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, seal_artifact, stable_id, utc_now, write_json

CORRECTION_PACKAGE_KEY = "com.partner_advisory.nee.semantic-correction"
NEW_INTERNAL_ENTITY_KEY = "com.partner_advisory.nee.new-entity-proposal"
SUPERSEDES_ASSERTION_KEY = "com.partner_advisory.nee.supersedes-assertion-id"
RETIRE_ASSERTION_KEY = "com.partner_advisory.nee.retire-assertion-id"
EXISTING_PATTERN_IDS = {"pattern_agent_to_agent_protocol"}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    summary = read_json(args.run_dir / "intake_summary.json")
    batches = read_json(args.run_dir / "hitl_batches.json")
    package = read_json(args.package)
    controls = package["extensions"][CORRECTION_PACKAGE_KEY]["controls"]
    decided_at = utc_now()
    decisions: list[dict] = []

    items = [
        item
        for batch in batches["review_batches"]
        for item in batch["items"]
    ]
    for item in items:
        correction = item["semantic_correction"]
        control = controls[correction["chunk_id"]]
        anchor = control["anchor_entity"]
        alternatives = [
            alternative["entity_id"]
            for reported in item["reported_entities"]
            for alternative in reported["alternatives"]
        ]
        extensions: dict[str, object]
        if control["retire_only"]:
            decision = "REJECT"
            extensions = {
                RETIRE_ASSERTION_KEY: control["supersedes_assertion_id"]
            }
            rationale = (
                "Retire the prior provenance-summary assertion without replacement; "
                "the source map remains provenance rather than graph-eligible guidance."
            )
        elif anchor["stable_identifier"] in EXISTING_PATTERN_IDS:
            decision = "SUPERSEDE"
            extensions = {
                SUPERSEDES_ASSERTION_KEY: control["supersedes_assertion_id"]
            }
            rationale = (
                "Replace the prior candidate wording while retaining the exact, "
                "already-governed A2A protocol pattern identity."
            )
        else:
            decision = "CREATE_NEW"
            extensions = {
                SUPERSEDES_ASSERTION_KEY: control["supersedes_assertion_id"],
                NEW_INTERNAL_ENTITY_KEY: {
                    "canonical_name": anchor["reported_text"],
                    "description": anchor["context"],
                    "entity_id": anchor["stable_identifier"],
                    "node_type": "pattern",
                    "status": "candidate",
                },
            }
            rationale = (
                "Create the source-bound internal pattern candidate and replace the "
                "prior candidate assertion with the corrected, narrower semantics."
            )

        candidate_id = item["candidate_ids"][0]
        value = seal_artifact(
            {
                "contract_id": "urn:nee:dual-pipeline:hitl-decision:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": stable_id(
                    "hitl-decision", summary["run_id"], candidate_id
                ),
                "decision_id": stable_id(
                    "decision", summary["run_id"], candidate_id
                ),
                "review_item_id": item["review_item_id"],
                "run_id": summary["run_id"],
                "trace_id": summary["trace_id"],
                "tier": item["tier"],
                "decision": decision,
                "reviewer": "amit.ayer",
                "decided_at": decided_at,
                "rationale": rationale,
                "evidence_reviewed": item["evidence_ids"],
                "alternatives": [decision, *alternatives],
                "before_state_digest": item["before_state_digest"],
                "proposed_after_state_digest": item[
                    "proposed_after_state_digest"
                ],
                "affected_candidate_ids": item["candidate_ids"],
                "challenger_required": False,
                "challenger_id": None,
                "extensions": extensions,
            }
        )
        validate_artifact(value)
        decisions.append(value)

    if len(decisions) != 12:
        raise RuntimeError(f"expected 12 Sherpa correction decisions, got {len(decisions)}")
    write_json(args.output, decisions)
    print(
        {
            "decision_count": len(decisions),
            "create_new": sum(item["decision"] == "CREATE_NEW" for item in decisions),
            "supersede": sum(item["decision"] == "SUPERSEDE" for item in decisions),
            "reject": sum(item["decision"] == "REJECT" for item in decisions),
            "output": str(args.output.resolve()),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
