#!/usr/bin/env python3
"""Seal decision-routing fixtures and refresh the closed contract inventory."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, read_json, sha256_file, write_json  # noqa: E402
from dual_pipeline.query_assurance import select_assurance_profile  # noqa: E402
from dual_pipeline.query_router import route_query  # noqa: E402

CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
FIXTURE_PATHS = (
    CONTRACT_ROOT / "fixtures" / "valid" / "query-destination-decision.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "delivery-quality-decision.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "query-plan-v2.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "l2-execution-decision-v2.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "query-graph-fit-decision.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "retrieval-evidence-report.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "query-route-eval-suite.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "query-route-eval-result.json",
    CONTRACT_ROOT / "fixtures" / "valid" / "eval-suite-v3.json",
    CONTRACT_ROOT / "fixtures" / "invalid" / "query-destination-acl-as-delivery.json",
    CONTRACT_ROOT / "fixtures" / "invalid" / "delivery-quality-fail-allows-release.json",
    CONTRACT_ROOT / "fixtures" / "invalid" / "query-plan-v2-nee-without-assurance.json",
    CONTRACT_ROOT / "fixtures" / "invalid" / "query-plan-v2-graph-with-assurance.json",
    CONTRACT_ROOT / "fixtures" / "invalid" / "query-graph-fit-fail-allows-l2.json",
    CONTRACT_ROOT / "fixtures" / "invalid" / "retrieval-evidence-report-unbound-record.json",
)

EVAL_SUITE_PATHS = (
    ROOT / "evals" / "suites" / "query-route-adversarial-e2.json",
)

POST_RUNTIME_SUITES = {
    ROOT / "evals" / "suites" / "dual-deterministic-e1.json": {
        "scope": "runtime_control_verification",
        "records": {
            "q-exact-lookup-offline": "retrieval-record-0541924ff7fa090b517951c9a8fd",
            "q-synthesis-fake-provider": "retrieval-record-0541924ff7fa090b517951c9a8fd",
        },
    },
    ROOT / "evals" / "suites" / "runtime-x1-quality-e1.json": {
        "scope": "runtime_control_verification",
        "records": {
            "q-high-shadow-quality": "retrieval-record-8ac8849c55628f811605c966bd08",
            "q-most-complex-repair-rejudge": "retrieval-record-2f35d35fd515ed6ac7a968c9ebce",
            "q-high-enforced-escalate": "retrieval-record-8ac8849c55628f811605c966bd08",
        },
    },
    ROOT / "evals" / "suites" / "graph-retrieval-e1.json": {
        "scope": "graph_retrieval_qualification",
        "records": {
            "q-connected-cross-workstream": {
                "record_id": "retrieval-record-c71ffbc63e7d5acd8dc4aa0bab4f",
                "lane": "approved",
                "maximum_fused_rank": 3,
            },
            "q-specific-relationship-no-hub-crowding": {
                "record_id": "retrieval-record-6dc8ec1dbe52acc5cb76afd0b9d1",
                "lane": "approved",
                "maximum_fused_rank": 10,
            },
            "q-opportunity-connected-evidence": {
                "record_id": "retrieval-record-0f8edc087a10eef6fea487d93f7b",
                "lane": "candidate",
                "maximum_fused_rank": 3,
            },
            "q-exact-bm25-no-traversal": {
                "record_id": "retrieval-record-6dc8ec1dbe52acc5cb76afd0b9d1",
                "lane": "approved",
                "maximum_fused_rank": 15,
            },
        },
    },
    ROOT / "evals" / "suites" / "query-retrieval-evidence-e1.json": {
        "scope": "graph_retrieval_qualification",
        "records": {
            "qre-exact-lookup": {
                "record_id": "retrieval-record-bc7c51c11b5f5087e4a110fb6cf8",
                "lane": "approved",
                "maximum_fused_rank": 5,
            },
            "qre-current-state": {
                "record_id": "retrieval-record-bc7c51c11b5f5087e4a110fb6cf8",
                "lane": "approved",
                "maximum_fused_rank": 5,
            },
            "qre-current-corroboration": {
                "record_id": "retrieval-record-bc7c51c11b5f5087e4a110fb6cf8",
                "lane": "approved",
                "maximum_fused_rank": 5,
            },
            "qre-relationship": {
                "record_id": "retrieval-record-1bee09c7693bc234e940d79a72e6",
                "lane": "approved",
                "maximum_fused_rank": 5,
            },
            "qre-cross-workstream": {
                "record_id": "retrieval-record-bb7bea6f43fc85a92bc6d99a9c1c",
                "lane": "approved",
                "maximum_fused_rank": 5,
            },
            "qre-gap-risk": {
                "record_id": "retrieval-record-051efd99dd762351fca2c6d52033",
                "lane": "approved",
                "maximum_fused_rank": 10,
            },
            "qre-opportunity": {
                "record_id": "retrieval-record-298e29f02ab2da2719267277c496",
                "lane": "approved",
                "maximum_fused_rank": 5,
            },
            "qre-candidate-review": {
                "record_id": "retrieval-record-4a7565a08d5ad235bc086f3891b1",
                "lane": "approved",
                "maximum_fused_rank": 10,
            },
        },
    },
}

FACET_BY_QUERY_TYPE = {
    "exact_lookup": "Exact governed facts and source locators",
    "current_state": "Current governed state and effective dates",
    "current_state_corroboration": "Current governed state and effective dates",
    "relationship_explanation": "Governed relationship paths and direction",
    "cross_workstream_synthesis": "Cross-workstream recurring patterns",
    "gap_risk_inconsistency": "Observed gaps, risks, and conflicts",
    "opportunity_nba": "Business needs, opportunities, and expected outcomes",
    "candidate_promotion_review": "Candidate assertions and proposal-only posture",
}

VACUOUS_EVAL_FIXTURE = (
    CONTRACT_ROOT / "fixtures" / "invalid" / "eval-suite-v3-vacuous.json"
)


def seal(path: Path) -> None:
    value = read_json(path)
    value.pop("canonical_digest", None)
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(value),
    }
    write_json(path, value)


def update_inventory() -> None:
    rows = []
    for path in sorted((CONTRACT_ROOT / "schemas").glob("*.schema.json")):
        value = read_json(path)
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "schema_id": value["$id"],
                "schema_version": (
                    "1.0.0"
                    if value["$id"] == "urn:nee:dual-pipeline:common:1.0.0"
                    else value["properties"]["schema_version"]["const"]
                ),
                "sha256": sha256_file(path),
            }
        )
    previous = read_json(CONTRACT_ROOT / "contract_inventory.json")
    inventory = {
        "compatibility_policy": previous["compatibility_policy"],
        "initiative_id": previous["initiative_id"],
        "inventory_version": previous["inventory_version"],
        "schemas": rows,
    }
    inventory["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(inventory),
    }
    write_json(CONTRACT_ROOT / "contract_inventory.json", inventory)


def migrate_post_runtime_suites() -> None:
    for path, config in POST_RUNTIME_SUITES.items():
        suite = read_json(path)
        if path.name == "query-retrieval-evidence-e1.json":
            for case in suite["cases"]:
                if case["case_id"] == "qre-current-corroboration":
                    case["question"] = (
                        "Which approved records corroborate the current Project Beacon status?"
                    )
                    case["expected"]["provider_call_count"] = 0
                    case["expected"]["required_stages"] = [
                        stage
                        for stage in case["expected"]["required_stages"]
                        if stage != "PA"
                    ]
                if case["case_id"] == "qre-opportunity":
                    case["question"] = (
                        "What are the strongest connected opportunities across NEA and "
                        "Project Beacon, which pain points and dependencies do they address, "
                        "and what evidence supports them?"
                    )
                    # The current graph has no governed support for the
                    # dependencies/prioritization requirement.  This case
                    # deliberately proves a BGE retrieval coverage block,
                    # rather than pretending that an L2 response can bridge
                    # the evidence gap.
                    case["expected"].update(
                        {
                            "status": "BLOCKED",
                            "x3_outcome": "X3B_ESCALATE_HITL",
                            "answer_present": False,
                            "provider_call_count": 0,
                            "runtime_judge_receipt_required": False,
                            "required_stages": [
                                stage
                                for stage in case["expected"]["required_stages"]
                                if stage not in {"PA", "L2"}
                            ],
                        }
                    )
                if case["case_id"] == "qre-candidate-review":
                    # The current graph retains Project Beacon candidates as
                    # unparsed legacy text.  Retrieval clearance must exclude
                    # them until source-bound atomic migration is approved;
                    # this case proves the resulting pre-L2 HITL block.
                    case["expected"].update(
                        {
                            "status": "BLOCKED",
                            "x3_outcome": "X3B_ESCALATE_HITL",
                            "answer_present": False,
                            "provider_call_count": 0,
                            "runtime_judge_receipt_required": False,
                            "required_stages": [
                                stage
                                for stage in case["expected"]["required_stages"]
                                if stage not in {"PA", "L2"}
                            ],
                        }
                    )
            suite["thresholds"]["maximum_provider_call_count"] = 5
        suite["contract_id"] = "urn:nee:dual-pipeline:eval-suite:3.0.0"
        suite["schema_version"] = "3.0.0"
        suite["runtime_boundary"] = "POST_RUNTIME_VERIFICATION"
        suite["thresholds"]["maximum_unqualified_retrieval_case_count"] = 0
        for case in suite["cases"]:
            if case["pipeline"] != "query":
                continue
            case_id = case["case_id"]
            query_type = case["query_type"]
            scope = config["scope"]
            decision = route_query(
                case["question"],
                query_type_override=query_type,
            )
            assurance = select_assurance_profile(
                destination=decision.destination.value,
                query_type=decision.query_type or query_type,
                materiality=decision.materiality,
                delivery_profile=decision.delivery_profile,
                artifact_type=decision.artifact_type,
                question=case["question"],
            )
            subject = (
                "PDLC" if "PDLC" in case["question"]
                else "Project Beacon" if "Sherpa" in case["question"]
                else "NEE"
            )
            case["critical"] = True
            case["evaluation_scope"] = scope
            case["embedding_qualification"] = (
                "real_bge_m3"
                if scope == "graph_retrieval_qualification"
                else "not_qualified"
            )
            expected = case["expected"]
            record_expectation = config["records"].get(case_id)
            if isinstance(record_expectation, dict):
                record_id = record_expectation["record_id"]
            elif isinstance(record_expectation, str):
                record_id = record_expectation
            else:
                record_id = None
            expected["required_record_ids"] = [record_id] if record_id else []
            if scope == "graph_retrieval_qualification" and record_id:
                if not isinstance(record_expectation, dict):
                    raise RuntimeError(
                        f"graph retrieval case {case_id} lacks a lane and rank oracle"
                    )
                expected["required_record_lanes"] = {
                    record_id: record_expectation["lane"],
                }
                expected["maximum_fused_rank_by_record"] = {
                    record_id: record_expectation["maximum_fused_rank"],
                }
            expected["expected_destination"] = decision.destination.value
            expected["expected_assurance_profile"] = assurance
            expected["expected_delivery_profile"] = decision.delivery_profile
            expected["expected_artifact_type"] = decision.artifact_type
            expected["semantic_retrieval_qualified"] = (
                scope == "graph_retrieval_qualification"
            )
            expected["runtime_judge_receipt_required"] = True
            expected["expected_runtime_quality_mode"] = (
                case["quality_control"]["mode"]
                if "quality_control" in case
                else "off"
            )
            expected["required_lanes"] = (
                ["approved", "candidate"]
                if query_type in {"candidate_promotion_review", "opportunity_nba"}
                else ["approved"]
            )
            expected["required_requirement_assertions"] = [
                {
                    "label_contains": subject,
                    "expected_outcomes": ["covered"],
                    "minimum_record_count": 1,
                },
                {
                    "label_contains": FACET_BY_QUERY_TYPE[query_type],
                    "expected_outcomes": ["covered"],
                    "minimum_record_count": 1,
                },
            ]
            expected["minimum_supported_requirement_count"] = 2
            expected["minimum_safe_direction_requirement_count"] = 1
            expected["minimum_connected_path_count"] = max(
                1, expected.get("minimum_connected_path_count", 0)
            )
            if case_id in {"qre-opportunity", "qre-candidate-review"}:
                # Neither case may bind an excluded legacy candidate as a
                # required result.  Approved context remains useful for the
                # coverage audit, while the candidate-specific requirement
                # must terminate as an explicit gap.
                expected["required_lanes"] = ["approved"]
                expected["runtime_judge_receipt_required"] = False
                if case_id == "qre-candidate-review":
                    expected["required_requirement_assertions"][1].update(
                        {"expected_outcomes": ["explicit_gap"], "minimum_record_count": 0}
                    )
                    expected["required_stages"] = [
                        stage
                        for stage in expected["required_stages"]
                        if stage not in {"PA", "L2"}
                    ]
        write_json(path, suite)
        seal(path)


def main() -> int:
    migrate_post_runtime_suites()
    for path in FIXTURE_PATHS + EVAL_SUITE_PATHS:
        seal(path)
    vacuous = copy.deepcopy(
        read_json(CONTRACT_ROOT / "fixtures" / "valid" / "eval-suite-v3.json")
    )
    vacuous["artifact_id"] = "eval-suite-v3-vacuous"
    vacuous["suite_id"] = "eval-suite-v3-vacuous"
    vacuous["cases"][0]["expected"]["required_record_ids"] = []
    write_json(VACUOUS_EVAL_FIXTURE, vacuous)
    seal(VACUOUS_EVAL_FIXTURE)
    update_inventory()
    print(f"sealed {len(FIXTURE_PATHS)} decision-routing fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
