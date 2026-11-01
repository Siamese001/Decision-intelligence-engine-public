"""Wave 8 sealed E1 controller evaluation and route-regression coverage."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, seal_artifact, write_json
from dual_pipeline.l1_evaluation import (
    L1EvaluationError,
    load_l1_evaluation_suite,
    run_l1_evaluation_suite,
    run_l1_route_integration,
)
from dual_pipeline.query_router import route_query

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals" / "suites" / "query-l1-agentic-control-e1.json"
BASELINE = ROOT / "evals" / "suites" / "query-l1-agentic-baseline-e1.json"


class L1QueryEvaluationTests(unittest.TestCase):
    def test_e1_matrix_is_sealed_comparable_and_passes_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = run_l1_evaluation_suite(
                suite_path=SUITE,
                output_root=Path(temp),
                evaluation_id="test-e1",
            )
            self.assertEqual("PASS", result["status"])
            self.assertEqual("E1_DETERMINISTIC_FIXTURE", result["tier"])
            self.assertEqual(0, result["bounded_metrics"]["false_coverage_count"])
            self.assertEqual(12, result["bounded_metrics"]["action_count"])
            bounded = read_json(Path(result["results"]["bounded"]))
            comparison = read_json(Path(result["comparison_path"]))
            validate_artifact(bounded)
            validate_artifact(comparison)
            self.assertEqual(18, bounded["aggregate"]["case_count"])
            self.assertEqual(1.0, bounded["aggregate"]["recovery_rate"])
            self.assertEqual("COMPARABLE", comparison["status"])

    def test_missing_or_weakened_critical_coverage_fails_suite_loading(self) -> None:
        suite = read_json(SUITE)
        detail = suite["extensions"]["com.partner_advisory.nee.l1-evaluation"]
        detail["cases"] = detail["cases"][1:]
        changed = seal_artifact(suite)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing-case.json"
            write_json(path, changed)
            with self.assertRaisesRegex(L1EvaluationError, "coverage"):
                load_l1_evaluation_suite(path)

    def test_false_coverage_threshold_cannot_be_weakened(self) -> None:
        suite = read_json(SUITE)
        suite["extensions"]["com.partner_advisory.nee.l1-evaluation"]["hard_gates"]["maximum_false_coverage_count"] = 1
        changed = seal_artifact(suite)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "weakened-threshold.json"
            write_json(path, changed)
            with self.assertRaisesRegex(L1EvaluationError, "false coverage"):
                load_l1_evaluation_suite(path)

    def test_e2_route_regression_covers_every_frozen_query_type(self) -> None:
        suite = load_l1_evaluation_suite(SUITE)
        baseline = read_json(BASELINE)
        by_id = {case["case_id"]: case for case in baseline["query_type_cases"]}
        ids = suite["extensions"]["com.partner_advisory.nee.l1-evaluation"]["query_type_case_ids"]
        self.assertEqual(9, len(ids))
        for case_id in ids:
            case = by_id[case_id]
            with self.subTest(case_id=case_id):
                route = route_query(case["question"], query_type_override=case.get("query_type_override"))
                self.assertEqual(case["expected_destination"], route.destination.value)
                self.assertEqual(case["expected_query_type"], route.query_type)

    def test_e2_route_integration_is_sealed_and_explicitly_boundary_limited(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = run_l1_route_integration(
                suite_path=SUITE,
                baseline_path=BASELINE,
                output_root=Path(temp),
                evaluation_id="test-e2",
            )
            self.assertEqual("PASS", result["status"])
            self.assertEqual("routing_only_no_retrieval_or_controller_action", result["boundary"])
            bounded = read_json(Path(result["results"]["bounded"]))
            self.assertEqual("E2_ROUTE_INTEGRATION", bounded["extensions"]["com.partner_advisory.nee.l1-evaluation"]["tier"])
            validate_artifact(bounded)


if __name__ == "__main__":
    unittest.main()
