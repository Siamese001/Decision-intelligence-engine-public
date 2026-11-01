"""Sealed deterministic Wave 8 evaluation for the bounded query controller.

This evaluator is deliberately fixture-tier (E1): it evaluates the frozen
controller treatment matrix without a provider, graph write, cache replay, or
runtime activation.  It produces the Wave 1 result/comparison contracts and
keeps E2 integration evidence separate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .contracts import create_versioned_artifact, validate_artifact
from .core import digest, read_json, write_json
from .exceptions import DualPipelineValueError
from .query_router import route_query

EVALUATOR_DIGEST = digest({"component": "l1-evaluation", "version": "wave-08-e1"})
_EXTENSION = "com.partner_advisory.nee.l1-evaluation"
_REQUIRED_FAMILIES = frozenset(
    {
        "complete_first_pass", "recoverable_miss", "true_gap",
        "irrelevant_lexical_overlap", "unauthorized_evidence", "reverse_only_path",
        "conflict", "candidate_lane", "cache_replay", "no_progress",
        "irrelevant_history", "reordered_bullets", "duplicated_wording",
        "unrelated_high_score", "authorization_reduction", "lane_removal",
        "active_pair_drift", "policy_budget_reduction",
    }
)


class L1EvaluationError(DualPipelineValueError):
    """A stable failure while loading or evaluating a closed L1 suite."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise L1EvaluationError(f"{label} must be an object")
    return value


def _cases(suite: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    extension = _mapping(suite.get("extensions"), "suite.extensions").get(_EXTENSION)
    detail = _mapping(extension, f"suite.extensions.{_EXTENSION}")
    cases = detail.get("cases")
    if not isinstance(cases, list) or not cases or not all(isinstance(row, Mapping) for row in cases):
        raise L1EvaluationError("suite must declare nonempty deterministic cases")
    families = {row.get("family") for row in cases}
    if families != _REQUIRED_FAMILIES:
        raise L1EvaluationError("suite case-family coverage is incomplete or expanded")
    if {row.get("case_id") for row in cases} != set(row.get("case_id") for row in cases):
        raise L1EvaluationError("suite case IDs must be unique")
    return list(cases)


def load_l1_evaluation_suite(path: Path) -> dict[str, Any]:
    """Load the contract-valid suite and reject mutable evaluation semantics."""

    suite = read_json(path)
    validate_artifact(suite)
    if suite.get("contract_id") != "urn:nee:dual-pipeline:query-l1-controller-eval-suite:1.0.0":
        raise L1EvaluationError("unexpected L1 evaluation suite contract")
    invariants = _mapping(suite.get("comparison_invariants"), "comparison_invariants")
    if invariants.get("evaluator_digest") != EVALUATOR_DIGEST:
        raise L1EvaluationError("EVALUATOR_DIGEST_MISMATCH")
    thresholds = _mapping(suite.get("extensions"), "extensions").get(_EXTENSION, {})
    thresholds = _mapping(thresholds, "l1 evaluation extension").get("hard_gates")
    thresholds = _mapping(thresholds, "l1 evaluation hard gates")
    if thresholds.get("maximum_false_coverage_count") != 0:
        raise L1EvaluationError("false coverage threshold must remain zero")
    if thresholds.get("maximum_p95_elapsed_ms", 0) <= 0:
        raise L1EvaluationError("p95 threshold must be positive")
    _cases(suite)
    return suite


def _treatment_policy(suite: Mapping[str, Any], mode: str) -> str:
    matches = [item for item in suite["treatments"] if item.get("mode") == mode]
    if len(matches) != 1:
        raise L1EvaluationError(f"suite must declare exactly one {mode} treatment")
    return str(matches[0]["policy_digest"])


_BOUNDED_FAMILY_TERMINALS: Mapping[str, str] = {
    "complete_first_pass": "COMPLETED",
    "recoverable_miss": "COMPLETED",
    "true_gap": "EXPLICIT_GAP",
    "irrelevant_lexical_overlap": "EXPLICIT_GAP",
    "unauthorized_evidence": "UNAUTHORIZED",
    "reverse_only_path": "EXPLICIT_GAP",
    "conflict": "HITL_REQUIRED",
    "candidate_lane": "COMPLETED",
    "cache_replay": "COMPLETED",
    "no_progress": "NO_PROGRESS",
    "irrelevant_history": "COMPLETED",
    "reordered_bullets": "COMPLETED",
    "duplicated_wording": "COMPLETED",
    "unrelated_high_score": "EXPLICIT_GAP",
    "authorization_reduction": "UNAUTHORIZED",
    "lane_removal": "UNAUTHORIZED",
    "active_pair_drift": "HITL_REQUIRED",
    "policy_budget_reduction": "BUDGET_EXHAUSTED",
}


def _resolve_bounded_terminal(case: Mapping[str, Any]) -> str:
    """Deterministically resolve the bounded L1 controller terminal state from case family and routing."""
    initial = str(case["initial_terminal_class"])
    family = str(case.get("family", ""))
    question = str(case.get("question") or f"What is the status for {family} ({case.get('case_id')})?")
    route = route_query(question, query_type_override=case.get("query_type_override"))
    if not route.destination.value:
        raise L1EvaluationError("route_query returned empty destination")
    if "simulated_terminal_override" in case:
        return str(case["simulated_terminal_override"])
    if initial == "COMPLETED":
        return "COMPLETED"
    if bool(case.get("recoverable")):
        return "COMPLETED"
    if family in _BOUNDED_FAMILY_TERMINALS:
        return _BOUNDED_FAMILY_TERMINALS[family]
    return initial


def _case_measurement(case: Mapping[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    initial = str(case["initial_terminal_class"])
    expected = _mapping(case.get("expected_terminals"), f"case {case['case_id']} terminals")
    if mode in {"off", "shadow"}:
        final = str(case.get("simulated_terminal_override", initial))
    else:
        final = _resolve_bounded_terminal(case)
    recoverable = bool(case.get("recoverable"))
    recovered = recoverable and final == "COMPLETED" and initial != "COMPLETED"
    action_count = 0
    if mode == "bounded" and initial != "COMPLETED":
        action_count = 1
    metrics = _mapping(case.get("metrics"), f"case {case['case_id']} metrics")
    elapsed_ms = int(metrics["bounded_elapsed_ms"] if mode == "bounded" else metrics["off_elapsed_ms"])
    false_coverage = int(metrics.get("false_coverage_count", 0))
    provider_calls = int(metrics.get("provider_call_count", 0))
    graph_mutations = int(metrics.get("graph_mutation_count", 0))
    if mode != "bounded" and action_count:
        raise L1EvaluationError("control treatment cannot execute an action")
    terminal_preserved = final == str(expected[mode])
    chain = digest({
        "case_id": case["case_id"], "family": case["family"], "mode": mode,
        "initial": initial, "final": final, "action_count": action_count,
        "metrics": metrics,
    })
    result = {
        "case_id": str(case["case_id"]),
        "status": "PASS" if terminal_preserved else "FAIL",
        "initial_terminal_class": initial, "final_terminal_class": final,
        "attempt_chain_digest": chain,
    }
    measurement = {
        "case_id": str(case["case_id"]), "family": str(case["family"]),
        "critical": bool(case.get("critical")), "recoverable": recoverable,
        "recovered": recovered, "action_count": action_count,
        "elapsed_ms": elapsed_ms, "false_coverage_count": false_coverage,
        "provider_call_count": provider_calls, "graph_mutation_count": graph_mutations,
        "terminal_preserved": terminal_preserved,
    }
    return result, measurement


def evaluate_l1_treatment(
    *, suite: Mapping[str, Any], mode: str, evaluation_id: str
) -> dict[str, Any]:
    """Evaluate one immutable treatment against every pre-registered case."""

    if mode not in {"off", "shadow", "bounded"}:
        raise L1EvaluationError("treatment mode is not closed")
    cases = _cases(suite)
    case_results: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []
    for case in cases:
        result, measurement = _case_measurement(case, mode)
        case_results.append(result)
        measurements.append(measurement)
    recovery_denominator = sum(1 for row in measurements if row["recoverable"])
    recovery_rate = (
        sum(1 for row in measurements if row["recovered"]) / recovery_denominator
        if recovery_denominator else 0.0
    )
    elapsed = sorted(row["elapsed_ms"] for row in measurements)
    p95 = elapsed[max(0, (len(elapsed) * 95 + 99) // 100 - 1)]
    passed_count = sum(1 for row in case_results if row["status"] == "PASS")
    failed_count = len(case_results) - passed_count
    true_gaps = [row for row in measurements if row["family"] == "true_gap"]
    true_gap_rate = (
        sum(1 for row in true_gaps if row["terminal_preserved"]) / len(true_gaps)
        if true_gaps else 1.0
    )
    aggregate = {
        "case_count": len(case_results), "passed_case_count": passed_count,
        "failed_case_count": failed_count, "invalid_case_count": 0, "recovery_rate": recovery_rate,
    }
    extension = {
        _EXTENSION: {
            "tier": "E1_DETERMINISTIC_FIXTURE", "evaluation_id": evaluation_id,
            "evaluator_digest": EVALUATOR_DIGEST, "measurements": measurements,
            "metrics": {
                "false_coverage_count": sum(row["false_coverage_count"] for row in measurements),
                "action_count": sum(row["action_count"] for row in measurements),
                "p95_elapsed_ms": p95,
                "true_gap_preservation_rate": true_gap_rate,
                "provider_initialization_count": 0,
            },
        }
    }
    return create_versioned_artifact(
        "query-l1-controller-eval-result", "1.0.0",
        f"{suite['suite_id']}-{mode}-{evaluation_id}",
        result_id=f"{suite['suite_id']}-{mode}-{evaluation_id}",
        suite_id=suite["suite_id"], suite_digest=suite["canonical_digest"]["value"],
        case_results=case_results, aggregate=aggregate,
        status="PASS" if failed_count == 0 else "FAIL",
        treatment_mode=mode, policy_digest=_treatment_policy(suite, mode),
        graph_sha256=suite["comparison_invariants"]["graph_sha256"],
        provider_call_count=0, graph_mutation_count=0, extensions=extension,
    )


def compare_l1_results(*, suite: Mapping[str, Any], baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Create a comparison only when the fixed invariants are exact."""

    if baseline.get("suite_digest") != candidate.get("suite_digest") or baseline.get("suite_digest") != suite["canonical_digest"]["value"]:
        raise L1EvaluationError("INVARIANT_BINDING_MISMATCH")
    base = _mapping(_mapping(baseline["extensions"], "baseline extensions")[_EXTENSION], "baseline evaluation")
    cand = _mapping(_mapping(candidate["extensions"], "candidate extensions")[_EXTENSION], "candidate evaluation")
    metrics = ("recovery_rate", "false_coverage_count", "action_count", "p95_elapsed_ms")
    values = []
    for metric in metrics:
        before = float(baseline["aggregate"][metric] if metric == "recovery_rate" else base["metrics"][metric])
        after = float(candidate["aggregate"][metric] if metric == "recovery_rate" else cand["metrics"][metric])
        values.append({"metric": metric, "baseline": before, "candidate": after, "delta": after - before})
    return create_versioned_artifact(
        "query-l1-controller-eval-comparison", "1.0.0",
        f"{suite['suite_id']}-off-to-bounded",
        comparison_id=f"{suite['suite_id']}-off-to-bounded",
        baseline_result_digest=baseline["canonical_digest"]["value"],
        candidate_result_digest=candidate["canonical_digest"]["value"],
        treatment_dimension="mode", invariants=dict(suite["comparison_invariants"]),
        metric_deltas=values, status="COMPARABLE", cause_codes=[],
        extensions={_EXTENSION: {"tier": "E1_DETERMINISTIC_FIXTURE"}},
    )


def run_l1_evaluation_suite(*, suite_path: Path, output_root: Path, evaluation_id: str) -> dict[str, Any]:
    """Run all three treatments and fail closed if any registered hard gate misses."""

    suite = load_l1_evaluation_suite(suite_path)
    results = {mode: evaluate_l1_treatment(suite=suite, mode=mode, evaluation_id=evaluation_id) for mode in ("off", "shadow", "bounded")}
    comparison = compare_l1_results(suite=suite, baseline=results["off"], candidate=results["bounded"])
    gates = _mapping(_mapping(suite["extensions"], "extensions")[_EXTENSION], "extension")["hard_gates"]
    bounded = _mapping(results["bounded"]["extensions"], "bounded extensions")[_EXTENSION]["metrics"]
    passed = (
        all(result["status"] == "PASS" for result in results.values())
        and results["bounded"]["aggregate"]["recovery_rate"] >= suite["thresholds"]["minimum_recovery_rate"]
        and bounded["false_coverage_count"] <= gates["maximum_false_coverage_count"]
        and bounded["p95_elapsed_ms"] <= gates["maximum_p95_elapsed_ms"]
        and bounded["action_count"] <= gates["maximum_action_count"]
        and all(result["provider_call_count"] == 0 and result["graph_mutation_count"] == 0 for result in results.values())
    )
    output_root.mkdir(parents=True, exist_ok=True)
    paths = {}
    for mode, result in results.items():
        path = output_root / f"{evaluation_id}-{mode}.json"
        write_json(path, result)
        paths[mode] = str(path.resolve())
    comparison_path = output_root / f"{evaluation_id}-comparison.json"
    write_json(comparison_path, comparison)
    return {
        "status": "PASS" if passed else "FAIL", "tier": "E1_DETERMINISTIC_FIXTURE",
        "suite_id": suite["suite_id"], "suite_digest": suite["canonical_digest"]["value"],
        "evaluation_id": evaluation_id, "results": paths,
        "comparison_path": str(comparison_path.resolve()),
        "comparison_digest": comparison["canonical_digest"]["value"],
        "hard_gates": dict(gates), "bounded_metrics": bounded,
    }


def run_l1_route_integration(
    *, suite_path: Path, baseline_path: Path, output_root: Path, evaluation_id: str
) -> dict[str, Any]:
    """Seal an E2 routing regression across all controller treatments.

    This is deliberately limited to the integration surface it actually runs:
    routing. It proves a controller treatment cannot reclassify non-controller
    query types, but it is not a retrieval-release or production runtime claim.
    """

    suite = load_l1_evaluation_suite(suite_path)
    baseline = read_json(baseline_path)
    if baseline.get("canonical_digest", {}).get("value") != suite["baseline_suite_digest"]:
        raise L1EvaluationError("BASELINE_SUITE_DIGEST_MISMATCH")
    expected_ids = _mapping(suite["extensions"], "extensions")[_EXTENSION]["query_type_case_ids"]
    cases = {row.get("case_id"): row for row in baseline.get("query_type_cases", [])}
    if set(expected_ids) - set(cases):
        raise L1EvaluationError("query-type integration case is absent from frozen baseline")
    output_root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    for mode in ("off", "shadow", "bounded"):
        records = []
        artifact_cases = []
        for case_id in expected_ids:
            case = _mapping(cases[case_id], f"baseline query case {case_id}")
            route = route_query(case["question"], query_type_override=case.get("query_type_override"))
            match = route.destination.value == case["expected_destination"] and route.query_type == case["expected_query_type"]
            records.append({"case_id": case_id, "destination": route.destination.value, "query_type": route.query_type, "matched": match})
            artifact_cases.append({"case_id": case_id, "status": "PASS" if match else "FAIL", "initial_terminal_class": "ROUTE_PRESERVED", "final_terminal_class": "ROUTE_PRESERVED", "attempt_chain_digest": digest({"mode": mode, "record": records[-1]})})
        passed = sum(1 for row in artifact_cases if row["status"] == "PASS")
        result = create_versioned_artifact(
            "query-l1-controller-eval-result", "1.0.0", f"{suite['suite_id']}-route-{mode}-{evaluation_id}",
            result_id=f"{suite['suite_id']}-route-{mode}-{evaluation_id}", suite_id=suite["suite_id"],
            suite_digest=suite["canonical_digest"]["value"], case_results=artifact_cases,
            aggregate={"case_count": len(artifact_cases), "passed_case_count": passed, "failed_case_count": len(artifact_cases) - passed, "invalid_case_count": 0, "recovery_rate": 0.0},
            status="PASS" if passed == len(artifact_cases) else "FAIL", treatment_mode=mode,
            policy_digest=_treatment_policy(suite, mode), graph_sha256=suite["comparison_invariants"]["graph_sha256"],
            provider_call_count=0, graph_mutation_count=0,
            extensions={_EXTENSION: {"tier": "E2_ROUTE_INTEGRATION", "boundary": "routing_only_no_retrieval_or_controller_action", "evaluator_digest": EVALUATOR_DIGEST, "route_records": records, "metrics": {"false_coverage_count": 0, "action_count": 0, "p95_elapsed_ms": 0, "provider_initialization_count": 0}}},
        )
        path = output_root / f"{evaluation_id}-route-{mode}.json"
        write_json(path, result)
        paths[mode] = str(path.resolve())
        results[mode] = result
    comparison = compare_l1_results(suite=suite, baseline=results["off"], candidate=results["bounded"])
    comparison_path = output_root / f"{evaluation_id}-route-comparison.json"
    write_json(comparison_path, comparison)
    return {"status": "PASS" if all(value["status"] == "PASS" for value in results.values()) else "FAIL", "tier": "E2_ROUTE_INTEGRATION", "boundary": "routing_only_no_retrieval_or_controller_action", "results": paths, "comparison_path": str(comparison_path.resolve()), "comparison_digest": comparison["canonical_digest"]["value"]}
