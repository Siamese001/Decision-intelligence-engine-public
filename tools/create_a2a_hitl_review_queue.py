#!/usr/bin/env python3
"""Render a source-bound, human-readable JSON queue for A2A span selection.

The queue is deliberately not a ``hitl-decision`` artifact.  It precedes a
governed intake run and lets a reviewer select exact frozen passages or hold
and reject unsupported claims.  Formal candidate-disposition decisions remain
bound to the later intake run and its review item IDs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import (  # noqa: E402
    seal_artifact,
    sha256_file,
    stable_id,
    write_json_exclusive,
)
from dual_pipeline.source_spans import (  # noqa: E402
    HUMAN_ATTESTATION,
    SELECTION_SCHEMA_VERSION,
)
from tools.prepare_a2a_fact_vector_packet_intake import read_and_verify_bundle  # noqa: E402


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def create_queue(
    *,
    bundle_path: Path,
    span_ledger_path: Path,
    output_path: Path,
    selection_output_path: Path | None = None,
) -> dict[str, Any]:
    verified = read_and_verify_bundle(bundle_path)
    ledger_path = span_ledger_path.resolve()
    ledger = _read_object(ledger_path, label="source span ledger")
    if (
        ledger.get("schema_version") != "1.0.0"
        or ledger.get("status") != "PASS"
        or ledger.get("source_bundle", {}).get("sha256") != verified["bundle_sha256"]
        or ledger.get("source_bundle", {}).get("bundle_manifest_sha256")
        != verified["bundle_manifest_sha256"]
        or not isinstance(ledger.get("proposals"), list)
    ):
        raise ValueError("source span ledger is not bound to the supplied verified bundle")
    proposals_by_fact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in ledger["proposals"]:
        if (
            not isinstance(proposal, dict)
            or proposal.get("status") != "PROPOSED_FOR_HITL"
            or proposal.get("review_state") != "PROPOSED_FOR_HITL"
            or not isinstance(proposal.get("fact_id"), str)
        ):
            raise ValueError("source span ledger contains an invalid proposal")
        proposals_by_fact[proposal["fact_id"]].append(proposal)
    facts = verified["data"]["fact_candidates"]
    fact_ids = [fact.get("fact_id") for fact in facts]
    if any(not isinstance(item, str) for item in fact_ids) or len(set(fact_ids)) != len(fact_ids):
        raise ValueError("verified bundle has invalid fact IDs")
    if set(proposals_by_fact) != set(fact_ids):
        raise ValueError("source span ledger does not cover every factual claim")

    fact_items: list[dict[str, Any]] = []
    for fact in sorted(facts, key=lambda item: item["fact_id"]):
        fact_id = fact["fact_id"]
        options = sorted(
            proposals_by_fact[fact_id],
            key=lambda item: (
                int(item.get("selection", {}).get("rank", 999)),
                item["span_proposal_id"],
            ),
        )
        fact_items.append(
            {
                "review_key": stable_id("a2a-source-span-review", fact_id, length=28),
                "fact_id": fact_id,
                "source_ids": fact["source_ids_json"],
                "prior_evidence_ids": fact["evidence_ids_json"],
                "proposed_claim": fact["embedding_text"],
                "subject": fact.get("subject"),
                "required_decision": True,
                "allowed_decisions": ["CONFIRM", "REWRITE", "HOLD", "REJECT"],
                "decision_rule": (
                    "CONFIRM only if one selected exact quote directly entails every clause. "
                    "REWRITE creates a narrower claim. HOLD and REJECT create no candidate package."
                ),
                "span_options": [
                    {
                        "span_proposal_id": option["span_proposal_id"],
                        "rank": option["selection"]["rank"],
                        "score": option["selection"]["score"],
                        "source_url": option["source_version"]["final_url"],
                        "captured_at": option["source_version"]["captured_at"],
                        "raw_response_sha256": option["source_version"]["raw_response_sha256"],
                        "visible_text_sha256": option["source_version"]["visible_text_sha256"],
                        "heading_path": option["heading_path"],
                        "visible_offsets": {
                            "start": option["visible_start_offset"],
                            "end": option["visible_end_offset"],
                        },
                        "quote": option["quote"],
                        "quote_sha256": option["quote_sha256"],
                        "before_context_sha256": option["context_before_sha256"],
                        "after_context_sha256": option["context_after_sha256"],
                    }
                    for option in options
                ],
            }
        )

    guidance_items = [
        {
            "review_key": stable_id("a2a-guidance-review", row["practice_id"], length=28),
            "source_item_id": row["practice_id"],
            "content_kind": "guidance",
            "retrieval_kind": "guidance",
            "proposed_text": row["embedding_text"],
            "required_decision": True,
            "allowed_decisions": ["KEEP_GUIDANCE", "HOLD", "REJECT"],
            "decision_rule": (
                "May be retrievable as guidance only; never a vendor fact or fact-promotion input."
            ),
        }
        for row in sorted(
            verified["data"]["guidance_practices"],
            key=lambda item: item["practice_id"],
        )
    ] + [
        {
            "review_key": stable_id("a2a-pattern-review", row["pattern_id"], length=28),
            "source_item_id": row["pattern_id"],
            "content_kind": "pattern",
            "retrieval_kind": "guidance",
            "proposed_text": row["embedding_text"],
            "required_decision": True,
            "allowed_decisions": ["KEEP_GUIDANCE", "HOLD", "REJECT"],
            "decision_rule": (
                "May be retrievable as a labeled pattern only; never a vendor "
                "fact or fact-promotion input."
            ),
        }
        for row in sorted(verified["data"]["pattern_guidance"], key=lambda item: item["pattern_id"])
    ]
    selection_template = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "span_ledger_sha256": sha256_file(ledger_path),
        "reviewer": None,
        "decided_at": None,
        "human_attestation": HUMAN_ATTESTATION,
        "reviewer_authorization_reference": None,
        "decisions": [
            {
                "fact_id": item["fact_id"],
                "decision": None,
                "span_proposal_id": None,
                "rewritten_claim": None,
                "rationale": None,
            }
            for item in fact_items
        ],
        "guidance_pattern_decisions": [
            {
                "source_item_id": item["source_item_id"],
                "content_kind": item["content_kind"],
                "decision": None,
                "rationale": None,
            }
            for item in guidance_items
        ],
    }
    queue = seal_artifact(
        {
            "schema_version": "1.0.0",
            "queue_kind": "a2a_first_party_source_span_hitl",
            "status": "PENDING_HITL",
            "source_bundle": {
                "path": str(verified["bundle_path"]),
                "sha256": verified["bundle_sha256"],
                "bundle_manifest_sha256": verified["bundle_manifest_sha256"],
            },
            "source_span_ledger": {
                "path": str(ledger_path),
                "sha256": sha256_file(ledger_path),
                "capture_id": ledger["capture_manifest"]["capture_id"],
            },
            "fact_review_items": fact_items,
            "guidance_pattern_review_items": guidance_items,
            "selection_instructions": (
                "Keep this sealed review queue unchanged. Fill a separate selection template with "
                "reviewer and decided_at, retain the attestation exactly, and provide one factual and "
                "one guidance/pattern decision with a nonempty rationale. This is a pre-intake "
                "source-span selection ledger; it does not promote or formally ingest any claim."
            ),
            "selection_template": selection_template,
            "summary": {
                "factual_claim_count": len(fact_items),
                "guidance_pattern_count": len(guidance_items),
                "automatic_fact_promotion": False,
                "graph_mutation_count": 0,
                "embedding_generation_count": 0,
            },
        }
    )
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError("--output must not overwrite an existing review queue")
    if selection_output_path is not None:
        selection_output_path = selection_output_path.resolve()
        if selection_output_path.exists():
            raise FileExistsError("--selection-output must not overwrite an existing selection template")
        write_json_exclusive(selection_output_path, selection_template)
    write_json_exclusive(output_path, queue)
    return queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--span-ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--selection-output",
        type=Path,
        help="optional editable v1.1 selection template; created without decisions",
    )
    args = parser.parse_args()
    try:
        queue = create_queue(
            bundle_path=args.bundle,
            span_ledger_path=args.span_ledger,
            output_path=args.output,
            selection_output_path=args.selection_output,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(queue["summary"] | {"status": queue["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
