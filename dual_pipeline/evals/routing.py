"""Provider-free destination confusion evaluation.

This evaluator runs before retrieval. It deliberately has no embedding or L2
dependency, so its zero-call counters are enforced by the module boundary
rather than inferred from a later query run.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..contracts import create_artifact, validate_file
from ..core import sha256_file, stable_id, utc_now, write_json_exclusive
from ..drift_guard import DriftError, assert_file_unchanged
from ..query_router import QueryDestination, load_query_routing_policy, route_query

GRAPH_DESTINATIONS = {
    QueryDestination.GRAPH_EVIDENCE_UTILITY.value,
    QueryDestination.NEE_DECISION_INTELLIGENCE.value,
}


def load_route_suite(path: Path) -> dict[str, Any]:
    suite_path = Path(path).expanduser().resolve()
    suite = validate_file(suite_path)
    if suite["contract_id"] != "urn:nee:dual-pipeline:query-route-eval-suite:1.0.0":
        raise ValueError("route evaluation suite has the wrong contract")
    current_policy_digest = load_query_routing_policy().policy_digest
    if suite["policy_digest"] != current_policy_digest:
        raise DriftError(
            "ROUTE_POLICY_DRIFT",
            "route evaluation suite is not bound to the active routing policy",
            expected=current_policy_digest,
            observed=suite["policy_digest"],
        )
    case_ids = [str(case["case_id"]) for case in suite["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("route evaluation suite has duplicate case IDs")
    return suite


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def run_route_suite(
    *,
    suite_path: Path,
    output_root: Path,
    evaluation_id: str | None = None,
    tags: Iterable[str] = (),
) -> tuple[dict[str, Any], Path]:
    """Evaluate destination routing without initializing retrieval or providers."""

    suite_file = Path(suite_path).expanduser().resolve()
    suite_file_sha256 = sha256_file(suite_file)
    suite = load_route_suite(suite_file)
    assert_file_unchanged(
        suite_file,
        suite_file_sha256,
        label="route evaluation suite source",
    )
    selection = set(tags)
    cases = [
        case for case in suite["cases"]
        if not selection or selection.intersection(case["tags"])
    ]
    if not cases:
        raise ValueError("route evaluation case selection is empty")
    policy_digest = suite["policy_digest"]

    rows: list[dict[str, Any]] = []
    confusion: Counter[tuple[str, str]] = Counter()
    for case in cases:
        expected = case["expected"]
        cause_codes: list[str] = []
        try:
            decision = route_query(
                case["question"],
                authorized=bool(case["authorized"]),
                delivery_profile=case["delivery_profile"],
                artifact_type=case["artifact_type"],
            )
            predicted_destination = decision.destination.value
            predicted_query_type = decision.query_type
            predicted_materiality = decision.materiality
            predicted_eligibility = decision.eligibility_status
            predicted_enters = decision.enters_query_runtime
        except Exception as exc:  # a route exception is a measured case failure
            predicted_destination = "ERROR"
            predicted_query_type = None
            predicted_materiality = None
            predicted_eligibility = None
            predicted_enters = False
            cause_codes.append(type(exc).__name__)

        comparisons = {
            "DESTINATION_MISMATCH": predicted_destination == expected["destination"],
            "QUERY_TYPE_MISMATCH": predicted_query_type == expected["query_type"],
            "MATERIALITY_MISMATCH": predicted_materiality == expected["materiality"],
            "ELIGIBILITY_MISMATCH": predicted_eligibility == expected["eligibility_status"],
            "RUNTIME_ENTRY_MISMATCH": predicted_enters == expected["enters_query_runtime"],
        }
        cause_codes.extend(code for code, passed in comparisons.items() if not passed)
        status = "PASS" if not cause_codes else "FAIL"
        confusion[(expected["destination"], predicted_destination)] += 1
        rows.append(
            {
                "case_id": case["case_id"],
                "critical": bool(case["critical"]),
                "expected_destination": expected["destination"],
                "predicted_destination": predicted_destination,
                "expected_query_type": expected["query_type"],
                "predicted_query_type": predicted_query_type,
                "expected_materiality": expected["materiality"],
                "predicted_materiality": predicted_materiality,
                "expected_eligibility_status": expected["eligibility_status"],
                "predicted_eligibility_status": predicted_eligibility,
                "expected_enters_query_runtime": expected["enters_query_runtime"],
                "predicted_enters_query_runtime": predicted_enters,
                "status": status,
                "cause_codes": list(dict.fromkeys(cause_codes)),
            }
        )

    assert_file_unchanged(
        suite_file,
        suite_file_sha256,
        label="route evaluation suite source",
    )
    current_policy_digest = load_query_routing_policy().policy_digest
    if current_policy_digest != policy_digest:
        raise DriftError(
            "ROUTE_POLICY_DRIFT",
            "routing policy changed while route evaluation was running",
            expected=policy_digest,
            observed=current_policy_digest,
        )

    correct = sum(row["status"] == "PASS" for row in rows)
    graph_tp = sum(
        row["expected_enters_query_runtime"] and row["predicted_enters_query_runtime"]
        for row in rows
    )
    graph_fp = sum(
        not row["expected_enters_query_runtime"] and row["predicted_enters_query_runtime"]
        for row in rows
    )
    graph_fn = sum(
        row["expected_enters_query_runtime"] and not row["predicted_enters_query_runtime"]
        for row in rows
    )
    nee_expected = sum(
        row["expected_destination"] == QueryDestination.NEE_DECISION_INTELLIGENCE.value
        for row in rows
    )
    nee_tp = sum(
        row["expected_destination"] == QueryDestination.NEE_DECISION_INTELLIGENCE.value
        and row["predicted_destination"] == QueryDestination.NEE_DECISION_INTELLIGENCE.value
        for row in rows
    )
    nee_fn = nee_expected - nee_tp
    critical_fn = sum(
        row["critical"]
        and row["expected_enters_query_runtime"]
        and not row["predicted_enters_query_runtime"]
        for row in rows
    )
    metrics = {
        "case_count": len(rows),
        "correct_case_count": correct,
        "destination_accuracy": _ratio(correct, len(rows)),
        "graph_entry_true_positive_count": graph_tp,
        "graph_entry_false_positive_count": graph_fp,
        "graph_entry_false_negative_count": graph_fn,
        "graph_entry_precision": _ratio(graph_tp, graph_tp + graph_fp),
        "graph_entry_recall": _ratio(graph_tp, graph_tp + graph_fn),
        "nee_decision_expected_count": nee_expected,
        "nee_decision_true_positive_count": nee_tp,
        "nee_decision_false_negative_count": nee_fn,
        "nee_decision_recall": _ratio(nee_tp, nee_expected),
        "critical_false_negative_count": critical_fn,
    }
    thresholds = suite["thresholds"]
    gates = {
        "destination_accuracy": metrics["destination_accuracy"] >= thresholds["minimum_destination_accuracy"],
        "graph_entry_precision": metrics["graph_entry_precision"] >= thresholds["minimum_graph_entry_precision"],
        "graph_entry_recall": metrics["graph_entry_recall"] >= thresholds["minimum_graph_entry_recall"],
        "nee_decision_recall": metrics["nee_decision_recall"] >= thresholds["minimum_nee_decision_recall"],
        "critical_false_negatives": metrics["critical_false_negative_count"] <= thresholds["maximum_critical_false_negative_count"],
        "all_case_oracles": correct == len(rows),
        "provider_free": True,
        "embedding_free": True,
        "l2_free": True,
        "graph_read_write_free": True,
    }
    status = "PASS" if all(gates.values()) else "FAIL"
    evaluation_id = evaluation_id or stable_id(
        "route-eval", suite["suite_id"], suite["canonical_digest"]["value"], utc_now(), length=28
    )
    result = create_artifact(
        "query-route-eval-result",
        evaluation_id,
        evaluation_id=evaluation_id,
        suite_id=suite["suite_id"],
        suite_digest=suite["canonical_digest"]["value"],
        suite_file_sha256=suite_file_sha256,
        policy_digest=suite["policy_digest"],
        runtime_boundary="PRE_RETRIEVAL_PROVIDER_FREE_ROUTING",
        case_results=rows,
        confusion_cells=[
            {
                "expected_destination": expected,
                "predicted_destination": predicted,
                "count": count,
            }
            for (expected, predicted), count in sorted(confusion.items())
        ],
        metrics=metrics,
        gates=gates,
        provider_initialization_count=0,
        provider_call_count=0,
        embedding_call_count=0,
        l2_call_count=0,
        graph_mutation_count=0,
        status=status,
        evaluated_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.selected-tags": sorted(selection),
            "com.partner_advisory.nee.drift-binding": {
                "suite_file_sha256": suite_file_sha256,
                "policy_digest": policy_digest,
            },
        },
    )
    root = Path(output_root).expanduser().resolve()
    result_path = root / "route_evaluations" / evaluation_id / "route-evaluation-result.json"
    write_json_exclusive(result_path, result)
    return result, result_path
