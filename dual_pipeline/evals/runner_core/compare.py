"""Result aggregation and comparison for evaluation suites."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...contracts import create_artifact
from ...core import stable_id, utc_now
from ..contracts import load_result
from .constants import EvaluationFailure


def _aggregate(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(result["status"] == "PASS" for result in case_results)
    failures = len(case_results) - passed
    return {
        "case_count": len(case_results),
        "passed_case_count": passed,
        "failed_case_count": failures,
        "case_pass_rate": passed / len(case_results),
        "provider_call_count": sum(result["provider_call_count"] for result in case_results),
        "graph_mutation_count": sum(result["graph_mutation_count"] for result in case_results),
        "hard_gate_failure_count": sum(
            not passed for result in case_results for passed in result["hard_gates"].values()
        ),
    }


def compare_results(*, baseline_path: Path, candidate_path: Path) -> dict[str, Any]:
    """Compare two exactly compatible sealed results without rewriting either input."""

    baseline = load_result(baseline_path)
    candidate = load_result(candidate_path)
    for key in ("suite_id", "suite_digest", "graph_sha256", "pipeline"):
        if baseline[key] != candidate[key]:
            raise EvaluationFailure(f"evaluation comparison binding mismatch: {key}")
    metric_names = (
        "case_pass_rate",
        "provider_call_count",
        "graph_mutation_count",
        "hard_gate_failure_count",
    )
    deltas = []
    for name in metric_names:
        before = baseline["aggregate"][name]
        after = candidate["aggregate"][name]
        lower_is_better = name in {
            "provider_call_count",
            "graph_mutation_count",
            "hard_gate_failure_count",
        }
        # A failed evaluation cannot be a trusted zero-mutation reference for
        # a passing ingestion recovery: the missing graph change may be the
        # very defect under test.  Retain mutation-growth regression detection
        # for like-for-like successful baselines.
        mutation_baseline_trusted = (
            name != "graph_mutation_count" or baseline["status"] == "PASS"
        )
        deltas.append(
            {
                "metric": name,
                "baseline": before,
                "candidate": after,
                "delta": after - before,
                "regression": (
                    mutation_baseline_trusted
                    and (after > before if lower_is_better else after < before)
                ),
            }
        )
    status = "PASS" if candidate["status"] == "PASS" and not any(
        item["regression"] for item in deltas
    ) else "FAIL"
    comparison_id = stable_id(
        "eval-compare",
        baseline["evaluation_id"],
        candidate["evaluation_id"],
        length=28,
    )
    return create_artifact(
        "eval-comparison",
        comparison_id,
        comparison_id=comparison_id,
        baseline_evaluation_id=baseline["evaluation_id"],
        baseline_digest=baseline["canonical_digest"]["value"],
        candidate_evaluation_id=candidate["evaluation_id"],
        candidate_digest=candidate["canonical_digest"]["value"],
        suite_id=baseline["suite_id"],
        graph_sha256=baseline["graph_sha256"],
        metric_deltas=deltas,
        status=status,
        compared_at=utc_now(),
        extensions={},
    )
