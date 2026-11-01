from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli import build_parser, execute
from dual_pipeline.core import read_json, seal_artifact, write_json
from dual_pipeline.evals.assurance import (
    AssuranceFailure,
    compare_workflow_results,
    evaluate_workflow_scenario,
    generate_attempts,
    load_workflow_suite,
    reduce_attempts,
    run_workflow_suite,
    validate_attempts,
)
from tools.run_automatic_evaluation_gates import run_gate

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "evals" / "suites" / "agentic-workflow-assurance-e1.json"


class WorkflowAssuranceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite, _, cls.workload = load_workflow_suite(SUITE_PATH)
        cls.candidate = next(
            value
            for value in cls.suite["treatments"]
            if value["treatment_id"] == "candidate-bounded"
        )

    def _scenario(self, scenario_id: str) -> dict:
        return copy.deepcopy(
            next(
                value
                for value in self.workload["scenarios"]
                if value["scenario_id"] == scenario_id
            )
        )

    def _case(self, case_id: str) -> dict:
        return copy.deepcopy(
            next(
                value
                for value in self.suite["cases"]
                if value["case_id"] == case_id
            )
        )

    def _evaluate(self, case_id: str, mutations: dict | None = None):
        return evaluate_workflow_scenario(
            evaluation_id="evaluation-mutation-test",
            suite=self.suite,
            scenario=self._scenario(case_id),
            case=self._case(case_id),
            treatment=self.candidate,
            mutations=mutations,
        )

    def test_candidate_suite_passes_and_legacy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            candidate, candidate_path = run_workflow_suite(
                suite_path=SUITE_PATH,
                output_root=output,
                treatment_id="candidate-bounded",
                evaluation_id="candidate-evaluation",
            )
            legacy, legacy_path = run_workflow_suite(
                suite_path=SUITE_PATH,
                output_root=output,
                treatment_id="legacy-replay",
                evaluation_id="legacy-evaluation",
            )
            self.assertEqual(candidate["status"], "PASS")
            self.assertEqual(candidate["aggregate"]["max_completed_history_bytes"], 0)
            self.assertEqual(candidate["aggregate"]["max_raw_tool_output_bytes"], 0)
            self.assertEqual(
                candidate["aggregate"]["max_previous_compiled_prompt_bytes"], 0
            )
            self.assertEqual(
                candidate["extensions"]["com.partner_advisory.nee.content-emitted"],
                False,
            )
            self.assertEqual(legacy["status"], "FAIL")

            comparison = compare_workflow_results(
                baseline_path=legacy_path,
                candidate_path=candidate_path,
            )
            self.assertEqual(comparison["status"], "PASS")
            self.assertGreaterEqual(
                comparison["credit_improvement_fraction"], 0.5
            )

    def test_invariant_drift_is_not_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            _, baseline_path = run_workflow_suite(
                suite_path=SUITE_PATH,
                output_root=output,
                treatment_id="legacy-replay",
                evaluation_id="legacy-invariant",
            )
            _, candidate_path = run_workflow_suite(
                suite_path=SUITE_PATH,
                output_root=output,
                treatment_id="candidate-bounded",
                evaluation_id="candidate-invariant",
            )
            candidate = read_json(candidate_path)
            candidate["workload_sha256"] = "f" * 64
            candidate = seal_artifact(candidate)
            changed_path = output / "candidate-invariant-changed.json"
            write_json(changed_path, candidate)
            comparison = compare_workflow_results(
                baseline_path=baseline_path,
                candidate_path=changed_path,
            )
            self.assertEqual(comparison["status"], "NOT_COMPARABLE")
            self.assertIn(
                "INVARIANT_BINDING_MISMATCH", comparison["cause_codes"]
            )

    def test_suite_rejects_evaluator_digest_drift(self) -> None:
        suite = read_json(SUITE_PATH)
        suite["evaluator"]["digest"] = "f" * 64
        suite = seal_artifact(suite)
        with tempfile.TemporaryDirectory() as temp:
            changed_path = Path(temp) / "changed-suite.json"
            write_json(changed_path, suite)
            with self.assertRaises(AssuranceFailure) as raised:
                load_workflow_suite(changed_path)
            self.assertEqual(raised.exception.code, "EVALUATOR_DIGEST_MISMATCH")

    def test_incident_request_plateaus_after_warmup(self) -> None:
        measurement, result = self._evaluate("incident-34-step")
        self.assertEqual(result["status"], "PASS")
        self.assertLessEqual(
            measurement["aggregate"]["post_warmup_growth_bytes_per_node"],
            self.suite["thresholds"]["maximum_growth_bytes_per_node"],
        )
        self.assertLessEqual(
            measurement["aggregate"]["post_warmup_growth_percent_per_node"],
            self.suite["thresholds"]["maximum_growth_percent_per_node"],
        )
        attempts = measurement["attempts"]
        self.assertLessEqual(
            attempts[-1]["request_serialized_bytes"]
            / attempts[9]["request_serialized_bytes"],
            1.1,
        )

    def test_tool_output_is_externalized_and_e5_return_is_bounded(self) -> None:
        measurement, result = self._evaluate("tool-output-one-megabyte")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            measurement["aggregate"]["max_tool_result_bytes"], 1048576
        )
        self.assertEqual(
            measurement["aggregate"]["max_raw_tool_output_bytes"], 0
        )
        self.assertLessEqual(
            measurement["aggregate"]["max_e5_return_bytes"], 4096
        )

    def test_hard_budget_blocks_before_provider_initialization(self) -> None:
        measurement, result = self._evaluate("hard-budget-overflow")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(measurement["aggregate"]["provider_call_count"], 0)
        self.assertEqual(
            measurement["aggregate"]["provider_initialization_count"], 0
        )

    def test_no_progress_stops_after_declared_limit(self) -> None:
        measurement, result = self._evaluate("no-progress-strike-limit")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            measurement["aggregate"]["no_progress_provider_call_count"], 3
        )
        self.assertEqual(measurement["aggregate"]["provider_call_count"], 6)

    def test_mutations_fail_with_stable_cause_codes(self) -> None:
        mutations = [
            (
                "incident-34-step",
                {"history_policy": "append_completed"},
                "COMPLETED_HISTORY_ABSENT",
            ),
            (
                "tool-output-one-megabyte",
                {"tool_output_policy": "inline"},
                "RAW_TOOL_OUTPUT_ABSENT",
            ),
            (
                "incident-34-step",
                {"carry_previous_compiled_prompt": True},
                "PREVIOUS_COMPILED_PROMPT_ABSENT",
            ),
            (
                "incident-34-step",
                {"reuse_prompt_across_steps": True},
                "REQUEST_DIGEST_REUSED_ACROSS_STEPS",
            ),
            (
                "incident-34-step",
                {"missing_usage_nodes": [2]},
                "CALLED_PROVIDER_USAGE_MISSING",
            ),
            (
                "incident-34-step",
                {"omit_cached_nodes": [2]},
                "CACHED_INPUT_MISSING",
            ),
            (
                "incident-34-step",
                {"unbound_call_nodes": [2]},
                "UNATTRIBUTED_PROVIDER_CALL",
            ),
            (
                "incident-34-step",
                {"drop_required_evidence": True},
                "REQUIRED_EVIDENCE_PRESERVED",
            ),
            (
                "incident-34-step",
                {"drop_required_contradiction": True},
                "REQUIRED_CONTRADICTIONS_PRESERVED",
            ),
            (
                "hard-budget-overflow",
                {"budget_enforced": False},
                "REQUESTS_WITHIN_HARD_LIMIT",
            ),
            (
                "no-progress-strike-limit",
                {"no_progress_limit": 20},
                "NO_PROGRESS_CALLS_BOUNDED",
            ),
            (
                "incident-34-step",
                {"feature_path": "legacy"},
                "FEATURE_PATH_EXECUTED",
            ),
        ]
        for case_id, mutation, expected_code in mutations:
            with self.subTest(case_id=case_id, mutation=mutation):
                _, result = self._evaluate(case_id, mutation)
                self.assertNotEqual(result["status"], "PASS")
                self.assertIn(expected_code, result["cause_codes"])

    def test_reducer_rejects_invalid_accounting(self) -> None:
        attempts = generate_attempts(
            scenario=self._scenario("incident-34-step"),
            treatment=self.candidate,
        )
        cached_too_large = copy.deepcopy(attempts)
        cached_too_large[0]["cached_input_tokens"] = (
            cached_too_large[0]["input_tokens"] + 1
        )
        self.assertIn(
            "CACHED_INPUT_EXCEEDS_INPUT",
            validate_attempts(cached_too_large),
        )

        total_mismatch = copy.deepcopy(attempts)
        total_mismatch[0]["total_tokens"] += 1
        self.assertIn("TOTAL_TOKEN_MISMATCH", validate_attempts(total_mismatch))

        duplicate = copy.deepcopy(attempts)
        duplicate.append(copy.deepcopy(duplicate[0]))
        self.assertIn(
            "DUPLICATE_ATTEMPT_ORDINAL", validate_attempts(duplicate)
        )

    def test_zero_output_usage_has_no_division_error(self) -> None:
        scenario = self._scenario("incident-34-step")
        scenario["output_tokens_per_call"] = 0
        attempts = generate_attempts(
            scenario=scenario,
            treatment=self.candidate,
        )
        aggregate = reduce_attempts(attempts, warmup_nodes=3)
        self.assertGreater(aggregate["read_write_ratio"], 0)
        self.assertEqual(aggregate["output_tokens"], 0)

    def test_measurement_is_content_silent(self) -> None:
        measurement, _ = self._evaluate("incident-34-step")
        encoded = json.dumps(measurement, sort_keys=True)
        self.assertNotIn("fixed_controls:", encoded)
        self.assertNotIn("current_step:", encoded)
        self.assertNotIn("raw prompt", encoded.lower())

    def test_cli_runs_and_compares_workflow_treatments(self) -> None:
        parser = build_parser()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            candidate = execute(
                parser.parse_args(
                    [
                        "eval-workflow-run",
                        "--suite",
                        str(SUITE_PATH),
                        "--output-root",
                        str(output),
                        "--treatment-id",
                        "candidate-bounded",
                        "--evaluation-id",
                        "candidate-cli",
                    ]
                )
            )
            legacy = execute(
                parser.parse_args(
                    [
                        "eval-workflow-run",
                        "--suite",
                        str(SUITE_PATH),
                        "--output-root",
                        str(output),
                        "--treatment-id",
                        "legacy-replay",
                        "--evaluation-id",
                        "legacy-cli",
                    ]
                )
            )
            comparison_path = output / "comparison.json"
            comparison = execute(
                parser.parse_args(
                    [
                        "eval-workflow-compare",
                        "--baseline",
                        legacy["result_path"],
                        "--candidate",
                        candidate["result_path"],
                        "--output",
                        str(comparison_path),
                    ]
                )
            )
            self.assertEqual(candidate["status"], "PASS")
            self.assertEqual(legacy["status"], "FAIL")
            self.assertEqual(comparison["status"], "PASS")
            self.assertTrue(comparison_path.is_file())

    def test_automatic_gate_persists_a_valid_pass_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = run_gate(
                repository_root=ROOT,
                output_root=Path(temp),
                evaluation_id="automatic-gate-test",
            )
            self.assertEqual("PASS", report["status"])
            self.assertEqual(6, report["case_count"])
            self.assertEqual(6, report["passed_case_count"])
            self.assertEqual(0, report["failed_case_count"])
            self.assertEqual(0, report["invalid_case_count"])
            self.assertEqual(0, report["external_provider_calls"])
            self.assertEqual(0, report["graph_mutations"])
            self.assertTrue(Path(str(report["result_path"])).is_file())


if __name__ == "__main__":
    unittest.main()
