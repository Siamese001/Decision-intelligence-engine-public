#!/usr/bin/env python3
"""Create a pair-bound worksheet for human retrieval-relevance review.

The retrieval-release evaluator proves that the configured retriever executed
against an exact graph/index pair.  It intentionally does not turn
automatically generated expected-record lists into human relevance judgments.
This tool creates a read-only worksheet for that missing review step.

The worksheet is deliberately *not* a release suite and cannot qualify a
release on its own.  A human reviewer must make and approve the recorded
decisions before an explicit, sealed retrieval suite can be produced from it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, sha256_file, utc_now, write_json_exclusive

PAIR_FIELDS = (
    "embedding_backend",
    "generation_id",
    "graph_sha256",
    "graph_snapshot",
    "model_id",
    "model_manifest_digest",
    "model_revision",
    "production_qualified",
    "sidecar_sha256",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a pending human-relevance worksheet bound to one active "
            "BGE-M3 development pair and an existing retrieval evaluation."
        )
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument(
        "--evaluation",
        type=Path,
        required=True,
        help="PASS retrieval-release evaluation for the active pair",
    )
    parser.add_argument(
        "--suite",
        type=Path,
        required=True,
        help="Exact suite whose file hash is bound by the evaluation",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-results-per-case",
        type=int,
        default=10,
        help="Maximum ranked results shown for human review (default: 10)",
    )
    return parser.parse_args()


def _assert_child(root: Path, relative_path: str, *, label: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the runtime root") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"{label} is missing: {candidate}")
    return candidate


def _pair_binding(active_pair: dict[str, Any], evaluation_pair: dict[str, Any]) -> dict[str, Any]:
    mismatches = {
        field: {
            "active": active_pair.get(field),
            "evaluation": evaluation_pair.get(field),
        }
        for field in PAIR_FIELDS
        if active_pair.get(field) != evaluation_pair.get(field)
    }
    if mismatches:
        raise ValueError(
            "evaluation candidate pair does not match active pair: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return {field: active_pair.get(field) for field in PAIR_FIELDS}


def _record_details(connection: sqlite3.Connection, record_id: str) -> dict[str, Any]:
    row = connection.execute(
        """SELECT record_id,lane,record_type,namespace,retrieval_kind,
                  epistemic_status,provenance_kind,answer_use,artifact_json
           FROM retrieval_record WHERE record_id=?""",
        (record_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"evaluation result is absent from active sidecar: {record_id}")
    artifact = json.loads(str(row["artifact_json"]))
    return {
        "record_id": str(row["record_id"]),
        "lane": str(row["lane"]),
        "record_type": str(row["record_type"]),
        "namespace": str(row["namespace"]),
        "retrieval_kind": str(row["retrieval_kind"]),
        "epistemic_status": str(row["epistemic_status"]),
        "provenance_kind": str(row["provenance_kind"]),
        "answer_use": str(row["answer_use"]),
        "classification": artifact.get("classification"),
        "audiences": artifact.get("audiences", []),
        "title": artifact.get("title"),
        "retrieval_text": artifact.get("retrieval_text"),
        "evidence_ids": artifact.get("evidence_ids", []),
    }


def _evaluation_validation_state(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Allow one known historical schema evolution, but never hide other defects."""

    try:
        validate_artifact(evaluation)
    except Exception as exc:  # pragma: no cover - depends on retained evidence bytes
        message = str(exc)
        # E3 receipts issued before the later BM25 diagnostic field are still
        # useful as a review seed when their pair and suite hashes match.  They
        # are not a substitute for a fresh current-schema evaluation.
        if "component_execution.bm25" not in message:
            raise
        return {
            "status": "HISTORICAL_SCHEMA_DRIFT",
            "reason": message,
            "fresh_evaluation_required_before_acceptance": True,
        }
    return {
        "status": "VALID",
        "fresh_evaluation_required_before_acceptance": False,
    }


def _case_review_items(
    connection: sqlite3.Connection,
    case: dict[str, Any],
    result: dict[str, Any],
    maximum: int,
) -> list[dict[str, Any]]:
    expected = set(case["expected_record_ids"])
    forbidden = set(case["forbidden_record_ids"])
    items: list[dict[str, Any]] = []
    for rank, record_id in enumerate(result["candidate_retrieved_record_ids"][:maximum], 1):
        items.append(
            {
                "rank": rank,
                "record": _record_details(connection, record_id),
                "automatic_expectation": (
                    "expected"
                    if record_id in expected
                    else "forbidden"
                    if record_id in forbidden
                    else "unlabelled"
                ),
                "human_relevance_label": None,
                "human_rationale": None,
            }
        )
    return items


def main() -> int:
    args = _arguments()
    if args.max_results_per_case < 1:
        raise ValueError("--max-results-per-case must be at least one")

    runtime_root = args.runtime_root.resolve()
    active_pair = read_json(runtime_root / "active_pair.json")
    evaluation_path = args.evaluation.resolve()
    suite_path = args.suite.resolve()
    evaluation = read_json(evaluation_path)
    suite = read_json(suite_path)
    evaluation_validation = _evaluation_validation_state(evaluation)
    validate_artifact(suite)

    if evaluation["status"] != "PASS":
        raise ValueError("human review packet requires a PASS retrieval evaluation")
    if evaluation["suite_file_sha256"] != sha256_file(suite_path):
        raise ValueError("evaluation is not bound to the supplied suite bytes")
    if evaluation["suite_digest"] != suite["canonical_digest"]["value"]:
        raise ValueError("evaluation is not bound to the supplied suite digest")
    if evaluation["suite_id"] != suite["suite_id"]:
        raise ValueError("evaluation is not bound to the supplied suite id")

    binding = _pair_binding(active_pair, evaluation["candidate_pair"])
    sidecar = _assert_child(
        runtime_root,
        str(active_pair["sidecar_path"]),
        label="active sidecar",
    )
    suite_by_id = {case["case_id"]: case for case in suite["cases"]}
    results_by_id = {
        result["case_id"]: result for result in evaluation["case_results"]
    }
    if set(suite_by_id) != set(results_by_id):
        raise ValueError("suite cases do not exactly match evaluation results")

    connection = create_sqlite_conn(sidecar, readonly=True)
    try:
        cases = []
        for case_id in sorted(suite_by_id):
            case = suite_by_id[case_id]
            result = results_by_id[case_id]
            cases.append(
                {
                    "case": case,
                    "automatic_observation": {
                        "candidate_retrieved_record_ids": result[
                            "candidate_retrieved_record_ids"
                        ],
                        "candidate_repeat_retrieved_record_ids": result[
                            "candidate_repeat_retrieved_record_ids"
                        ],
                        "candidate_recall_at_k": result["candidate_recall_at_k"],
                        "candidate_reciprocal_rank": result[
                            "candidate_reciprocal_rank"
                        ],
                        "candidate_ndcg_at_k": result["candidate_ndcg_at_k"],
                        "forbidden_leakage": result["forbidden_leakage"],
                    },
                    "review_items": _case_review_items(
                        connection,
                        case,
                        result,
                        args.max_results_per_case,
                    ),
                    "human_case_decision": {
                        "status": "PENDING_HUMAN_REVIEW",
                        "reviewer": None,
                        "reviewed_at": None,
                        "accepted_relevant_record_ids": [],
                        "confirmed_forbidden_record_ids": [],
                        "rationale": None,
                        "approval_owner": None,
                        "approved_at": None,
                    },
                }
            )
    finally:
        connection.close()

    packet = {
        "packet_type": "nee_retrieval_human_relevance_review",
        "schema_version": "0.1.0-draft",
        "status": "PENDING_HUMAN_REVIEW",
        "created_at": utc_now(),
        "purpose": (
            "Collect human relevance, hard-negative, lane, ACL, and stale-result "
            "judgments before creating an explicit sealed retrieval-release suite."
        ),
        "pair_binding": binding,
        "evidence_binding": {
            "runtime_root": str(runtime_root),
            "active_pair_path": str((runtime_root / "active_pair.json").resolve()),
            "evaluation_path": str(evaluation_path),
            "evaluation_digest": evaluation["canonical_digest"]["value"],
            "evaluation_id": evaluation["evaluation_id"],
            "evaluation_contract_validation": evaluation_validation,
            "suite_path": str(suite_path),
            "suite_file_sha256": sha256_file(suite_path),
            "suite_digest": suite["canonical_digest"]["value"],
            "sidecar_path": str(sidecar),
        },
        "review_protocol": {
            "automatic_labels_are_not_human_labels": True,
            "allowed_human_relevance_labels": [
                "RELEVANT",
                "PARTIALLY_RELEVANT",
                "NOT_RELEVANT",
                "FORBIDDEN",
                "UNRESOLVED",
            ],
            "minimum_completion_criteria": [
                "Every case has a named human reviewer, timestamp, decision, and rationale.",
                "Every positive case identifies the reviewed records judged RELEVANT.",
                "Every hard-negative, lane-isolation, ACL-boundary, and stale case confirms forbidden records or records a failure.",
                "A named approval owner accepts the completed decisions; unresolved disagreements are not accepted.",
                "The accepted decisions are converted into an explicit sealed suite and rerun against this exact pair or a newly bound pair.",
            ],
            "recommended_independent_reviewer_count": 2,
            "review_window": {
                "results_per_case": args.max_results_per_case,
                "note": (
                    "The full automatic result list remains preserved in the bound "
                    "evaluation artifact; this worksheet presents the highest-ranked "
                    "results for practical human review."
                ),
            },
        },
        "cases": cases,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_exclusive(output, packet)
    print(
        json.dumps(
            {
                "status": packet["status"],
                "case_count": len(cases),
                "pair_generation": binding["generation_id"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
