#!/usr/bin/env python3
"""Focused contracts and vertical-slice tests for the dual-pipeline eval harness."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.contracts import create_artifact, validate_artifact  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    canonical_bytes,
    read_json,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    write_json,
)
from dual_pipeline.evals import compare_results, run_route_suite, run_suite  # noqa: E402
from dual_pipeline.evals.contracts import (  # noqa: E402
    load_suite,
    resolve_repository_file,
)
from dual_pipeline.evals.runner import (  # noqa: E402
    EvaluationFailure,
    _finalize_evaluation_workspace,
    prune_expired_failed_workspaces,
)
from tests.embedding_fixture import (  # noqa: E402
    BgeM3TestBackend,
    MetadataOnlyBgeM3Backend,
)
from tools.rebind_query_route_eval_suite import rebind_route_suite  # noqa: E402
from tools.validate_dual_pipeline_contracts import ValidationFailure  # noqa: E402

_RUN_SLOW_EVAL_HARNESS = os.environ.get("NEE_RUN_SLOW_EVAL_HARNESS") == "1"


class DualPipelineEvaluationHarnessTests(unittest.TestCase):
    _WAVE8_OUTER_WRAPPER_CASE_IDS = (
        "route-meeting-summary",
        "route-client-rewrite",
        "route-long-non-nee",
        "route-staffing-workbook",
        "route-repository-publish",
        "route-exact-record",
        "route-list-governed-records",
        "route-short-graph-diagnostic",
        "route-a2a-architecture",
        "route-candidate-approved",
        "route-reverse-edge-trap",
        "route-sparse-evidence-risk",
        "route-a2a-meeting-summary",
        "route-client-slide-no-graph",
        "route-ambiguous",
        "route-unauthorized-nee",
        "route-natural-funding-decision",
        "route-natural-best-option",
        "route-logo-false-positive",
        "route-restaurant-false-positive",
        "route-grammar-false-positive",
        "route-restart-operational",
        "route-compare-recommend-decision",
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-eval-harness-")
        self.root = Path(self.temp.name)
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        self.suite = ROOT / "evals" / "suites" / "dual-deterministic-e1.json"
        self.x1_quality_suite = ROOT / "evals" / "suites" / "runtime-x1-quality-e1.json"
        self.route_suite = ROOT / "evals" / "suites" / "query-route-adversarial-e3.json"
        self.route_robustness_suite = ROOT / "evals" / "suites" / "query-route-robustness-e3.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _result(
        self,
        evaluation_id: str,
        *,
        case_pass_rate: float = 1.0,
        graph_mutation_count: int = 0,
    ) -> dict:
        passed = 1 if case_pass_rate == 1.0 else 0
        return create_artifact(
            "eval-run-result",
            evaluation_id,
            evaluation_id=evaluation_id,
            suite_id="eval-test-suite",
            suite_digest="a" * 64,
            suite_file_sha256="b" * 64,
            pipeline="dual",
            graph_sha256="c" * 64,
            case_results=[
                {
                    "case_id": "case-one",
                    "pipeline": "query",
                    "status": "PASS" if passed else "FAIL",
                    "failure_category": None if passed else "ORACLE_MISMATCH",
                    "cause_code": None if passed else "TEST_FAILURE",
                    "stage": "X3",
                    "provider_initialization_count": 0,
                    "provider_call_count": 0,
                    "answer_present": bool(passed),
                    "graph_mutation_count": graph_mutation_count,
                    "directive_status": None,
                    "terminal_receipt_digest": None,
                    "hard_gates": {"contract_valid": bool(passed)},
                    "artifact_path": "in-memory",
                }
            ],
            aggregate={
                "case_count": 1,
                "passed_case_count": passed,
                "failed_case_count": 1 - passed,
                "case_pass_rate": case_pass_rate,
                "provider_call_count": 0,
                "graph_mutation_count": graph_mutation_count,
                "hard_gate_failure_count": 1 - passed,
            },
            status="PASS" if passed else "FAIL",
            evaluated_at="2026-07-27T12:00:00Z",
            extensions={},
        )

    def test_suite_loads_and_binds_repository_packages(self) -> None:
        suite = load_suite(self.suite)
        self.assertEqual("dual-deterministic-e1", suite["suite_id"])
        self.assertEqual("dual", suite["pipeline"])
        self.assertEqual(4, len(suite["cases"]))
        self.assertTrue(resolve_repository_file("evals/suites/dual-deterministic-e1.json").is_file())
        with self.assertRaisesRegex(ValueError, "escapes"):
            resolve_repository_file("../outside.json")

    def test_query_retrieval_evidence_suite_covers_all_query_families(self) -> None:
        suite = load_suite(
            ROOT / "evals" / "suites" / "query-retrieval-evidence-e1.json"
        )
        self.assertEqual("query", suite["pipeline"])
        self.assertEqual(
            {
                "exact_lookup",
                "current_state",
                "current_state_corroboration",
                "relationship_explanation",
                "cross_workstream_synthesis",
                "gap_risk_inconsistency",
                "opportunity_nba",
                "candidate_promotion_review",
            },
            {case["query_type"] for case in suite["cases"]},
        )
        self.assertEqual(8, len(suite["cases"]))
        self.assertEqual("POST_RUNTIME_VERIFICATION", suite["runtime_boundary"])
        for case in suite["cases"]:
            self.assertTrue(case["expected"]["required_record_ids"])
            self.assertTrue(case["expected"]["required_requirement_assertions"])
            self.assertTrue(case["expected"]["required_lanes"])
            self.assertGreaterEqual(
                case["expected"]["minimum_connected_path_count"],
                1,
            )
            pre_l2_block = (
                case["expected"]["status"] == "BLOCKED"
                and case["expected"]["x3_outcome"] == "X3B_ESCALATE_HITL"
                and "PA" not in case["expected"]["required_stages"]
                and "L2" not in case["expected"]["required_stages"]
            )
            self.assertTrue(
                case["expected"]["runtime_judge_receipt_required"] or pre_l2_block
            )
        self.assertEqual(
            0,
            suite["thresholds"]["maximum_graph_mutation_count"],
        )

    def test_route_confusion_suite_is_provider_embedding_and_l2_free(self) -> None:
        result, path = run_route_suite(
            suite_path=self.route_suite,
            output_root=self.root,
            evaluation_id="route-confusion-test",
        )
        self.assertEqual("PASS", result["status"])
        self.assertTrue(path.is_file())
        self.assertEqual(23, result["metrics"]["case_count"])
        self.assertEqual(1, result["metrics"]["destination_accuracy"])
        self.assertEqual(1, result["metrics"]["graph_entry_precision"])
        self.assertEqual(1, result["metrics"]["graph_entry_recall"])
        self.assertEqual(1, result["metrics"]["nee_decision_recall"])
        self.assertEqual(0, result["metrics"]["critical_false_negative_count"])
        self.assertEqual(0, result["provider_initialization_count"])
        self.assertEqual(0, result["provider_call_count"])
        self.assertEqual(0, result["embedding_call_count"])
        self.assertEqual(0, result["l2_call_count"])
        self.assertEqual(0, result["graph_mutation_count"])

    def test_wave8_outer_quote_contract_unwraps_the_known_blocker(self) -> None:
        """Prove one whole outer wrapper preserves all active destinations."""

        source_suite = read_json(self.route_suite)
        self.assertEqual(
            "29ca0d2f63b1b6acf8a957fd9cfa734a10f3ff88661ae29544d40ee8e1394956",
            source_suite["canonical_digest"]["value"],
        )
        source_cases = {case["case_id"]: case for case in source_suite["cases"]}
        self.assertEqual(set(self._WAVE8_OUTER_WRAPPER_CASE_IDS), set(source_cases))

        quoted_cases = []
        for case_id in self._WAVE8_OUTER_WRAPPER_CASE_IDS:
            case = dict(source_cases[case_id])
            case["case_id"] = f"{case_id}-w8-outer-double-quote"
            case["question"] = f'"{case["question"]}"'
            case["tags"] = [*case["tags"], "wave8", "outer-wrapper-contract"]
            quoted_cases.append(case)
        suite = {
            **source_suite,
            "artifact_id": "query-route-outer-wrapper-wave8-contract",
            "suite_id": "query-route-outer-wrapper-wave8-contract",
            "created_at": "2026-08-03T00:00:00Z",
            "cases": quoted_cases,
        }
        suite.pop("canonical_digest")
        suite["canonical_digest"] = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": sha256_bytes(canonical_bytes(suite)),
        }
        suite_path = self.root / "query-route-outer-wrapper-wave8-contract.json"
        write_json(suite_path, suite)

        result, path = run_route_suite(
            suite_path=suite_path,
            output_root=self.root,
            evaluation_id="route-outer-wrapper-wave8-contract",
        )
        self.assertTrue(path.is_file())
        self.assertEqual("PASS", result["status"])
        self.assertEqual(23, result["metrics"]["case_count"])
        self.assertEqual(23, result["metrics"]["correct_case_count"])
        self.assertEqual(1, result["metrics"]["destination_accuracy"])
        self.assertEqual(0, result["metrics"]["graph_entry_false_negative_count"])
        self.assertEqual(0, result["metrics"]["critical_false_negative_count"])
        self.assertTrue(all(row["status"] == "PASS" for row in result["case_results"]))
        self.assertEqual(0, result["provider_initialization_count"])
        self.assertEqual(0, result["provider_call_count"])
        self.assertEqual(0, result["embedding_call_count"])
        self.assertEqual(0, result["l2_call_count"])
        self.assertEqual(0, result["graph_mutation_count"])

    def test_wave9_outer_wrapper_delimiter_matrix_preserves_all_routes(self) -> None:
        """Exercise every canonical route through each supported delimiter."""

        source_suite = read_json(self.route_suite)
        source_cases = {case["case_id"]: case for case in source_suite["cases"]}
        self.assertEqual(set(self._WAVE8_OUTER_WRAPPER_CASE_IDS), set(source_cases))

        wrapped_cases = []
        for delimiter, delimiter_id in (("\"", "double-quote"), ("'", "single-quote"), ("`", "code")):
            for case_id in self._WAVE8_OUTER_WRAPPER_CASE_IDS:
                case = dict(source_cases[case_id])
                case["case_id"] = f"{case_id}-w9-outer-{delimiter_id}"
                case["question"] = f"{delimiter}{case['question']}{delimiter}"
                case["tags"] = [*case["tags"], "wave9", "outer-wrapper-delimiter-matrix"]
                wrapped_cases.append(case)
        suite = {
            **source_suite,
            "artifact_id": "query-route-outer-wrapper-wave9-matrix",
            "suite_id": "query-route-outer-wrapper-wave9-matrix",
            "created_at": "2026-08-03T00:00:00Z",
            "cases": wrapped_cases,
        }
        suite.pop("canonical_digest")
        suite["canonical_digest"] = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": sha256_bytes(canonical_bytes(suite)),
        }
        suite_path = self.root / "query-route-outer-wrapper-wave9-matrix.json"
        write_json(suite_path, suite)

        result, path = run_route_suite(
            suite_path=suite_path,
            output_root=self.root,
            evaluation_id="route-outer-wrapper-wave9-matrix",
        )
        self.assertTrue(path.is_file())
        self.assertEqual("PASS", result["status"])
        self.assertEqual(69, result["metrics"]["case_count"])
        self.assertEqual(69, result["metrics"]["correct_case_count"])
        self.assertEqual(1, result["metrics"]["destination_accuracy"])
        self.assertEqual(0, result["metrics"]["graph_entry_false_negative_count"])
        self.assertEqual(0, result["metrics"]["critical_false_negative_count"])
        self.assertTrue(all(row["status"] == "PASS" for row in result["case_results"]))
        self.assertEqual(0, result["provider_initialization_count"])
        self.assertEqual(0, result["provider_call_count"])
        self.assertEqual(0, result["embedding_call_count"])
        self.assertEqual(0, result["l2_call_count"])
        self.assertEqual(0, result["graph_mutation_count"])

    def test_route_robustness_suite_passes_all_256_cases(self) -> None:
        suite = read_json(self.route_robustness_suite)
        self.assertEqual("query-route-robustness-e3", suite["suite_id"])
        self.assertEqual(256, len(suite["cases"]))
        self.assertEqual(
            "query-route-robustness-e2",
            suite["extensions"]["com.partner_advisory.nee.rebound-from"]["suite_id"],
        )
        result, path = run_route_suite(
            suite_path=self.route_robustness_suite,
            output_root=self.root,
            evaluation_id="route-robustness-wave10-test",
        )
        self.assertTrue(path.is_file())
        self.assertEqual("PASS", result["status"])
        self.assertEqual(256, result["metrics"]["case_count"])
        self.assertEqual(256, result["metrics"]["correct_case_count"])
        self.assertEqual(1, result["metrics"]["destination_accuracy"])
        self.assertEqual(0, result["metrics"]["graph_entry_false_negative_count"])
        self.assertEqual(0, result["metrics"]["critical_false_negative_count"])
        self.assertEqual(0, result["provider_initialization_count"])
        self.assertEqual(0, result["provider_call_count"])
        self.assertEqual(0, result["embedding_call_count"])
        self.assertEqual(0, result["l2_call_count"])
        self.assertEqual(0, result["graph_mutation_count"])

    def test_route_suite_rebind_is_explicit_and_non_overwriting(self) -> None:
        output = self.root / "rebound-route-suite.json"
        result = rebind_route_suite(
            source_path=self.route_robustness_suite,
            output_path=output,
            suite_id="route-robustness-rebind-test",
            artifact_id="route-robustness-rebind-test",
            created_at="2026-08-03T00:00:00Z",
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual(256, result["case_count"])
        rebound = read_json(output)
        self.assertEqual("query-route-robustness-e3", rebound["extensions"]["com.partner_advisory.nee.rebound-from"]["suite_id"])
        with self.assertRaises(FileExistsError):
            rebind_route_suite(
                source_path=self.route_robustness_suite,
                output_path=output,
                suite_id="route-robustness-rebind-test",
                artifact_id="route-robustness-rebind-test",
                created_at="2026-08-03T00:00:00Z",
            )

    def test_compare_accepts_compatible_pass_results(self) -> None:
        baseline_path = self.root / "baseline.json"
        candidate_path = self.root / "candidate.json"
        write_json(baseline_path, self._result("baseline"))
        write_json(candidate_path, self._result("candidate"))
        comparison = compare_results(
            baseline_path=baseline_path,
            candidate_path=candidate_path,
        )
        self.assertEqual("PASS", comparison["status"])
        self.assertFalse(any(delta["regression"] for delta in comparison["metric_deltas"]))

    def test_compare_allows_expected_graph_change_after_failed_baseline(self) -> None:
        baseline_path = self.root / "failed-baseline.json"
        candidate_path = self.root / "recovered-candidate.json"
        write_json(baseline_path, self._result("failed-baseline", case_pass_rate=0.0))
        write_json(
            candidate_path,
            self._result("recovered-candidate", graph_mutation_count=2),
        )
        comparison = compare_results(
            baseline_path=baseline_path,
            candidate_path=candidate_path,
        )
        self.assertEqual("PASS", comparison["status"])
        mutations = next(
            delta
            for delta in comparison["metric_deltas"]
            if delta["metric"] == "graph_mutation_count"
        )
        self.assertEqual(2, mutations["delta"])
        self.assertFalse(mutations["regression"])

    @unittest.skipUnless(
        _RUN_SLOW_EVAL_HARNESS
        and os.environ.get("NEE_RUN_BGE_TESTS") == "1",
        "real-BGE evaluation harness disabled; set NEE_RUN_SLOW_EVAL_HARNESS=1 and NEE_RUN_BGE_TESTS=1",
    )
    def test_synthesis_case_runs_against_fake_provider_without_seed_mutation(self) -> None:
        before = sha256_file(self.graph)
        result, path = run_suite(
            suite_path=self.suite,
            runtime_root=self.root,
            seed_graph=self.graph,
            backend=BgeM3TestBackend(),
            evaluation_id="synthesis-test",
            tags=("synthesis",),
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual(1, result["aggregate"]["provider_call_count"])
        self.assertTrue(path.is_file())
        self.assertEqual(before, sha256_file(self.graph))
        self.assertEqual(
            "DELETED_ON_PASS",
            result["extensions"]["workspace-lifecycle"]["status"],
        )
        self.assertEqual(
            "post_runtime_verification",
            result["extensions"]["com.partner_advisory.nee.evaluation-boundary"]["role"],
        )
        self.assertFalse(
            result["extensions"]["com.partner_advisory.nee.evaluation-boundary"][
                "runtime_judge_substitution"
            ]
        )
        retrieval_binding = result["extensions"][
            "com.partner_advisory.nee.evaluation-retrieval-binding"
        ]
        self.assertTrue(retrieval_binding["case_pair_binding_complete"])
        self.assertTrue(retrieval_binding["case_pair_binding_matches_backend"])
        self.assertTrue(retrieval_binding["semantic_cache_evidence_complete"])
        cache_evidence = retrieval_binding["semantic_cache_evidence"]
        self.assertEqual(1, len(cache_evidence))
        self.assertFalse(cache_evidence[0]["lookup_hit"])
        self.assertEqual("BOUND", cache_evidence[0]["embedding_binding"]["status"])
        self.assertTrue(
            result["case_results"][0]["hard_gates"][
                "fake_l2_consumes_sealed_retrieval_evidence"
            ]
        )
        self.assertFalse((self.root / ".eval-work").exists())

    @unittest.skipUnless(
        _RUN_SLOW_EVAL_HARNESS
        and os.environ.get("NEE_RUN_BGE_TESTS") == "1",
        "real-BGE evaluation harness disabled; set NEE_RUN_SLOW_EVAL_HARNESS=1 and NEE_RUN_BGE_TESTS=1",
    )
    def test_ingestion_cases_evaluate_before_bge_activation(self) -> None:
        before = sha256_file(self.graph)
        result, path = run_suite(
            suite_path=self.suite,
            runtime_root=self.root,
            seed_graph=self.graph,
            backend=BgeM3TestBackend(),
            evaluation_id="ingestion-completion-test",
            tags=("ingestion",),
        )
        self.assertEqual("PASS", result["status"])
        self.assertTrue(path.is_file())
        self.assertEqual(2, result["aggregate"]["case_count"])
        self.assertEqual(2, result["aggregate"]["passed_case_count"])
        self.assertEqual(0, result["aggregate"]["provider_call_count"])
        self.assertGreater(result["aggregate"]["graph_mutation_count"], 0)
        self.assertEqual(before, sha256_file(self.graph))
        cases = {case["case_id"]: case for case in result["case_results"]}
        self.assertTrue(
            cases["i-copilot-graph-change"]["hard_gates"][
                "retrieval_release_evaluation_posture"
            ]
        )
        self.assertTrue(
            cases["i-architecture-noop"]["hard_gates"][
                "retrieval_release_evaluation_posture"
            ]
        )
        changed = read_json(Path(cases["i-copilot-graph-change"]["artifact_path"]))
        evaluation = changed["retrieval_release_evaluation"]
        self.assertEqual("PASS", evaluation["status"])
        self.assertEqual("automatic", evaluation["suite_resolution"]["source"])
        self.assertTrue(evaluation["evaluation_digest"])
        noop = read_json(Path(cases["i-architecture-noop"]["artifact_path"]))
        self.assertEqual("IDEMPOTENT_NOOP", noop["mutation_outcome"])
        self.assertIsNone(noop["retrieval_release_evaluation"])
        self.assertFalse((self.root / ".eval-work").exists())

    @unittest.skipUnless(
        _RUN_SLOW_EVAL_HARNESS
        and os.environ.get("NEE_RUN_BGE_TESTS") == "1",
        "real-BGE evaluation harness disabled; set NEE_RUN_SLOW_EVAL_HARNESS=1 and NEE_RUN_BGE_TESTS=1",
    )
    def test_x1_quality_suite_evaluates_receipts_and_enforced_routing(self) -> None:
        result, path = run_suite(
            suite_path=self.x1_quality_suite,
            runtime_root=self.root,
            seed_graph=self.graph,
            backend=BgeM3TestBackend(),
            evaluation_id="runtime-x1-quality-eval-test",
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual(3, result["aggregate"]["case_count"])
        self.assertEqual(11, result["aggregate"]["provider_call_count"])
        self.assertTrue(path.is_file())
        for case in result["case_results"]:
            self.assertTrue(case["hard_gates"]["x1_quality_receipts_present"])
            self.assertTrue(
                case["hard_gates"]["x1_quality_receipts_bind_sealed_evidence"]
            )
            self.assertTrue(
                case["hard_gates"]["x1_quality_release_routing_respected"]
            )
            self.assertTrue(
                case["hard_gates"]["runtime_judge_receipt_verified_post_run"]
            )

    def test_vacuous_query_retrieval_suite_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValidationFailure,
            "SEMANTIC_VACUOUS_RETRIEVAL_ORACLE",
        ):
            load_suite(
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "invalid"
                / "eval-suite-v3-vacuous.json"
            )

    def test_project_status_v3_suite_requires_graph_evidence_utility(self) -> None:
        """Weekly status is a citation-only graph-evidence route, never L2."""

        suite = read_json(ROOT / "evals" / "suites" / "edo-june-candidate-source-evidence-e2.json")
        case = suite["cases"][0]
        case["query_type"] = "project_status_current"
        case["provider_posture"] = "none"
        suite["provider_posture"] = "none"
        expected = case["expected"]
        expected["expected_destination"] = "GRAPH_EVIDENCE_UTILITY"
        expected["expected_assurance_profile"] = None
        expected["required_lanes"] = ["candidate"]
        suite = seal_artifact(suite)
        validate_artifact(suite)

        suite["cases"][0]["expected"]["expected_destination"] = (
            "NEE_DECISION_INTELLIGENCE"
        )
        with self.assertRaisesRegex(
            ValidationFailure,
            "SEMANTIC_QUERY_DESTINATION_ORACLE",
        ):
            validate_artifact(seal_artifact(suite))

    def test_legacy_query_suite_cannot_execute(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy query evaluation suites"):
            load_suite(
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "valid"
                / "eval-suite.json"
            )

    def test_operator_eval_rejects_metadata_only_encoder(self) -> None:
        with self.assertRaisesRegex(
            EvaluationFailure,
            "requires a real BGE-M3 retrieval encoder",
        ):
            run_suite(
                suite_path=self.suite,
                runtime_root=self.root,
                seed_graph=self.graph,
                backend=MetadataOnlyBgeM3Backend(),
                evaluation_id="metadata-only-operator-run",
                tags=("offline",),
            )
        self.assertFalse((self.root / "evaluations").exists())

    def test_failed_workspace_is_retained_then_pruned_after_expiry(self) -> None:
        workspace = self.root / ".eval-work" / "ew-failed-case"
        workspace.mkdir(parents=True)
        (workspace / "diagnostic.txt").write_text("failure evidence", encoding="utf-8")
        unmanaged = self.root / ".eval-work" / "manual-diagnostics"
        unmanaged.mkdir()
        (unmanaged / "keep.txt").write_text("unmanaged", encoding="utf-8")
        lifecycle = _finalize_evaluation_workspace(
            workspace,
            evaluation_id="failed-evaluation",
            status="FAIL",
            evaluated_at="2026-07-29T12:00:00Z",
        )
        self.assertEqual("RETAINED_FAILURE", lifecycle["status"])
        self.assertTrue((workspace / ".retention.json").is_file())
        self.assertEqual(
            [],
            prune_expired_failed_workspaces(
                self.root,
                now="2026-08-05T11:59:59Z",
            ),
        )
        self.assertEqual(
            ["ew-failed-case"],
            prune_expired_failed_workspaces(
                self.root,
                now="2026-08-05T12:00:00Z",
            ),
        )
        self.assertFalse(workspace.exists())
        self.assertTrue(unmanaged.is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
