#!/usr/bin/env python3
"""Build a delta-complete BGE-M3 release suite for Sherpa strategic records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.retrieval_release import (
    _load_policy,
    _policy_binding,
    _records_by_id,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _record(value: str) -> dict:
    return json.loads(value)["retrieval_record"]


def _assertion_ids(records: list[dict]) -> list[str]:
    values: set[str] = set()
    for record in records:
        extensions = record["extensions"]
        values.update(extensions.get("com.partner_advisory.nee.graph-assertion-ids", []))
        object_id = extensions.get("com.partner_advisory.nee.graph-object-id")
        if object_id:
            values.add(object_id)
    if not values:
        raise RuntimeError("delta records have no graph assertion bindings")
    return sorted(values)


def _case(
    *,
    case_id: str,
    query_kind: str,
    query: str,
    lane: str,
    retrieval_mode: str,
    expected: list[str],
    forbidden: list[str],
    allowed_assertion_ids: list[str] | None = None,
    scope: str = "delta",
    critical: bool = False,
    classification: str = "internal",
) -> dict:
    value = {
        "case_id": case_id,
        "scope": scope,
        "query_kind": query_kind,
        "critical": critical,
        "query": query,
        "classification": classification,
        "audiences": ["internal_consulting"],
        "lane": lane,
        "retrieval_mode": retrieval_mode,
        "top_k": 100 if scope == "delta" else 1,
        "expected_record_ids": expected,
        "forbidden_record_ids": forbidden,
    }
    if allowed_assertion_ids is not None:
        value["allowed_assertion_ids"] = allowed_assertion_ids
    return value


def _delta_cases(
    *,
    prefix: str,
    record_ids: list[str],
    assertion_ids: list[str],
    positive_mode: str,
    lane_isolation_mode: str,
    queries: list[str],
) -> tuple[list[dict], list[str]]:
    semantic_ids = [f"{prefix}-semantic-{index}" for index in range(1, 4)]
    alias_id = f"{prefix}-alias"
    hard_negative_id = f"{prefix}-hard-negative"
    lane_id = f"{prefix}-lane-isolation"
    acl_id = f"{prefix}-acl-boundary"
    cases = [
        *[
            _case(
                case_id=case_id,
                query_kind="semantic_paraphrase",
                query=query,
                lane="candidate",
                retrieval_mode=positive_mode,
                expected=record_ids,
                forbidden=[],
                allowed_assertion_ids=assertion_ids,
            )
            for case_id, query in zip(semantic_ids, queries[:3], strict=True)
        ],
        _case(
            case_id=alias_id,
            query_kind="alias",
            query=queries[3],
            lane="candidate",
            retrieval_mode=positive_mode,
            expected=record_ids,
            forbidden=[],
            allowed_assertion_ids=assertion_ids,
        ),
        _case(
            case_id=hard_negative_id,
            query_kind="hard_negative",
            query="Unrelated payroll cafeteria parking policy",
            lane="candidate",
            retrieval_mode=positive_mode,
            expected=[],
            forbidden=record_ids,
            allowed_assertion_ids=["assertion-intentionally-absent-release-negative"],
        ),
        _case(
            case_id=lane_id,
            query_kind="lane_isolation",
            query=queries[0],
            lane="approved",
            retrieval_mode=lane_isolation_mode,
            expected=[],
            forbidden=record_ids,
            allowed_assertion_ids=assertion_ids,
        ),
        _case(
            case_id=acl_id,
            query_kind="acl_boundary",
            query=queries[0],
            lane="candidate",
            retrieval_mode=positive_mode,
            expected=[],
            forbidden=record_ids,
            classification="public",
        ),
    ]
    return cases, [*semantic_ids, alias_id, hard_negative_id, lane_id, acl_id]


def main() -> int:
    args = _arguments()
    runtime_root = args.runtime_root.resolve()
    receipt = read_json(args.receipt)
    baseline = _records_by_id(runtime_root / receipt["previous_pair"]["sidecar_path"])
    candidate = _records_by_id(runtime_root / receipt["next_pair"]["sidecar_path"])
    added_ids = sorted(
        record_id
        for record_id, value in candidate.items()
        if baseline.get(record_id) != value
    )
    removed_ids = sorted(set(baseline) - set(candidate))
    added = [_record(candidate[record_id]) for record_id in added_ids]
    answer_records = [
        record
        for record in added
        if record["answer_use"] in {"candidate_hypothesis", "guidance"}
    ]
    facet_records = [
        record for record in added if record["answer_use"] == "navigation_only"
    ]
    unsupported = sorted(
        record["record_id"]
        for record in added
        if record not in answer_records and record not in facet_records
    )
    if unsupported:
        raise RuntimeError(
            f"added records are not retrievable through governed modes: {unsupported}"
        )
    if len(answer_records) != 28 or len(facet_records) != 1 or len(removed_ids) != 11:
        raise RuntimeError(
            "unexpected Sherpa retrieval delta: "
            f"answer={len(answer_records)} facet={len(facet_records)} "
            f"removed={len(removed_ids)}"
        )

    answer_ids = sorted(record["record_id"] for record in answer_records)
    facet_ids = sorted(record["record_id"] for record in facet_records)
    answer_cases, answer_case_ids = _delta_cases(
        prefix="sherpa-strategic",
        record_ids=answer_ids,
        assertion_ids=_assertion_ids(answer_records),
        positive_mode="strategic",
        lane_isolation_mode="strategic",
        queries=[
            "Which Sherpa business goals, constraints, A2A patterns, and outcome measures guide the target architecture?",
            "Retrieve the reusable AWS-native agent-to-agent governance, lifecycle, identity, evidence, isolation, and assurance patterns for Sherpa.",
            "What decision principles and agentic-mesh precursors support Sherpa self-service while protecting enterprise architecture capacity?",
            "Project Beacon A2A reusable agent architecture controls",
        ],
    )
    facet_cases, facet_case_ids = _delta_cases(
        prefix="sherpa-entity-facet",
        record_ids=facet_ids,
        assertion_ids=_assertion_ids(facet_records),
        positive_mode="candidate_entity_navigation",
        lane_isolation_mode="entity_navigation",
        queries=[
            "Navigate to the Project Beacon strategic architecture context.",
            "Find the entity collecting Sherpa business outcomes and agent architecture controls.",
            "Show the graph navigation facet for the Sherpa self-service initiative.",
            "Project Beacon",
        ],
    )

    removed_case_id = "sherpa-a2a-superseded-records"
    removed_case = _case(
        case_id=removed_case_id,
        query_kind="stale_or_superseded",
        query="Earlier unprofiled Sherpa A2A architecture candidates",
        lane="candidate",
        retrieval_mode="strategic",
        expected=[],
        forbidden=removed_ids,
    )

    baseline_records = [_record(value) for value in baseline.values()]
    core = next(
        record
        for record in sorted(baseline_records, key=lambda value: value["record_id"])
        if record["lane"] == "approved"
        and record["answer_use"] == "factual_answer"
        and record["classification"] == "internal"
        and "internal_consulting" in record["audiences"]
        and record["record_type"] == "assertion"
    )
    core_case = _case(
        case_id="core-approved-factual-regression",
        query_kind="semantic_paraphrase",
        query=core["retrieval_text"],
        lane="approved",
        retrieval_mode="factual",
        expected=[core["record_id"]],
        forbidden=[],
        allowed_assertion_ids=_assertion_ids([core]),
        scope="core",
        critical=True,
    )

    coverage = [
        *[
            {
                "record_id": record_id,
                "change_kind": "added_or_changed",
                "case_ids": answer_case_ids,
                "relationship_applicable": False,
                "stale_or_superseded_applicable": False,
            }
            for record_id in answer_ids
        ],
        *[
            {
                "record_id": record_id,
                "change_kind": "added_or_changed",
                "case_ids": facet_case_ids,
                "relationship_applicable": False,
                "stale_or_superseded_applicable": False,
            }
            for record_id in facet_ids
        ],
        *[
            {
                "record_id": record_id,
                "change_kind": "removed",
                "case_ids": [removed_case_id],
                "relationship_applicable": False,
                "stale_or_superseded_applicable": True,
            }
            for record_id in removed_ids
        ],
    ]
    policy = _load_policy()
    suite = create_artifact(
        "retrieval-release-suite",
        "retrieval-release-suite-sherpa-strategic-20260729",
        suite_id="retrieval-release-suite-sherpa-strategic-20260729",
        execution_tier="E3",
        acceptance_policy=_policy_binding(policy),
        cases=[core_case, *answer_cases, *facet_cases, removed_case],
        coverage=coverage,
        created_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.prepared-receipt-digest": receipt["receipt_digest"]
        },
    )
    validate_artifact(suite)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, suite)
    print(
        {
            "status": "PASS",
            "case_count": len(suite["cases"]),
            "added_or_changed": len(added_ids),
            "removed": len(removed_ids),
            "output": str(args.output.resolve()),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
