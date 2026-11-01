#!/usr/bin/env python3
"""Build exact-scope HITL decisions for the Sherpa strategic incorporation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, seal_artifact, stable_id, utc_now, write_json

CORRECTION_KEY = "com.partner_advisory.nee.semantic-correction"
SUPERSEDES_ASSERTION_KEY = "com.partner_advisory.nee.supersedes-assertion-id"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-run-dir", type=Path, required=True)
    parser.add_argument("--plan-run-dir", type=Path, required=True)
    parser.add_argument("--a2a-run-dir", type=Path, required=True)
    parser.add_argument("--a2a-package", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _review_items(run_dir: Path) -> tuple[dict, list[dict]]:
    summary = read_json(run_dir / "intake_summary.json")
    batches = read_json(run_dir / "hitl_batches.json")
    items = [
        item
        for batch in batches["review_batches"]
        for item in batch["items"]
    ]
    expected = summary["hitl"]["tier_1_count"] + summary["hitl"]["tier_2_count"]
    if len(items) != expected:
        raise RuntimeError(
            f"{summary['run_id']} emitted {len(items)} review items, expected {expected}"
        )
    return summary, items


def _decision(
    *,
    summary: dict,
    item: dict,
    decision: str,
    reviewer: str,
    decided_at: str,
    rationale: str,
    extensions: dict,
) -> dict:
    candidate_id = item["candidate_ids"][0]
    alternatives = [decision]
    for reported in item["reported_entities"]:
        for alternative in reported["alternatives"]:
            entity_id = alternative["entity_id"]
            if entity_id not in alternatives:
                alternatives.append(entity_id)
    value = seal_artifact(
        {
            "contract_id": "urn:nee:dual-pipeline:hitl-decision:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": stable_id(
                "hitl-decision", summary["run_id"], candidate_id
            ),
            "decision_id": stable_id("decision", summary["run_id"], candidate_id),
            "review_item_id": item["review_item_id"],
            "run_id": summary["run_id"],
            "trace_id": summary["trace_id"],
            "tier": item["tier"],
            "decision": decision,
            "reviewer": reviewer,
            "decided_at": decided_at,
            "rationale": rationale,
            "evidence_reviewed": item["evidence_ids"],
            "alternatives": alternatives,
            "before_state_digest": item["before_state_digest"],
            "proposed_after_state_digest": item["proposed_after_state_digest"],
            "affected_candidate_ids": item["candidate_ids"],
            "challenger_required": False,
            "challenger_id": None,
            "extensions": extensions,
        }
    )
    validate_artifact(value)
    return value


def _candidate_approval_decisions(
    run_dir: Path,
    *,
    reviewer: str,
    decided_at: str,
    rationale: str,
) -> tuple[dict, list[dict]]:
    summary, items = _review_items(run_dir)
    decisions = [
        _decision(
            summary=summary,
            item=item,
            decision="APPROVE_CANDIDATE",
            reviewer=reviewer,
            decided_at=decided_at,
            rationale=rationale,
            extensions={},
        )
        for item in items
    ]
    return summary, decisions


def main() -> int:
    args = _arguments()
    reviewer = "amit.ayer"
    decided_at = utc_now()
    official_summary, official = _candidate_approval_decisions(
        args.official_run_dir,
        reviewer=reviewer,
        decided_at=decided_at,
        rationale=(
            "Approve the exact source-bound Sherpa business problem, official intent, "
            "and target outcome for governed candidate-lane incorporation. This "
            "approval does not assert a separate canonical-fact promotion."
        ),
    )
    plan_summary, plan = _candidate_approval_decisions(
        args.plan_run_dir,
        reviewer=reviewer,
        decided_at=decided_at,
        rationale=(
            "Approve this exact operator-supplied strategic control for governed "
            "candidate-lane constraint, principle, criterion, option, or metric "
            "retrieval without promoting it as a canonical current-state fact."
        ),
    )

    a2a_summary, a2a_items = _review_items(args.a2a_run_dir)
    package = read_json(args.a2a_package)
    controls = package["extensions"][CORRECTION_KEY]["controls"]
    a2a: list[dict] = []
    for item in a2a_items:
        correction = item["semantic_correction"]
        control = controls[correction["chunk_id"]]
        a2a.append(
            _decision(
                summary=a2a_summary,
                item=item,
                decision="SUPERSEDE",
                reviewer=reviewer,
                decided_at=decided_at,
                rationale=(
                    "Replace the exact prior candidate assertion with the "
                    "text-distinct, source-bound strategic-role migration while "
                    "retaining candidate posture and the governed pattern identity."
                ),
                extensions={
                    SUPERSEDES_ASSERTION_KEY: control["supersedes_assertion_id"]
                },
            )
        )

    expected = {
        official_summary["run_id"]: 3,
        plan_summary["run_id"]: 14,
        a2a_summary["run_id"]: 11,
    }
    actual = {
        official_summary["run_id"]: len(official),
        plan_summary["run_id"]: len(plan),
        a2a_summary["run_id"]: len(a2a),
    }
    if actual != expected:
        raise RuntimeError(f"unexpected decision counts: {actual}, expected {expected}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "official": args.output_dir / "official-business-case-hitl-decisions.json",
        "plan": args.output_dir / "strategic-plan-hitl-decisions.json",
        "a2a": args.output_dir / "a2a-profile-migration-hitl-decisions.json",
    }
    write_json(outputs["official"], official)
    write_json(outputs["plan"], plan)
    write_json(outputs["a2a"], a2a)
    print(
        {
            "counts": {
                "APPROVE_CANDIDATE": len(official) + len(plan),
                "SUPERSEDE": len(a2a),
            },
            "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
