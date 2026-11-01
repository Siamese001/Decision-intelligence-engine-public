"""Focused tests for release-assurance bridge, stress, and portfolio controls."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.core import digest
from dual_pipeline.evals.bridge import BridgeEvaluationFailure, run_bridge_suite
from dual_pipeline.evals.portfolio import REQUIRED_TRACKS, validate_portfolio
from dual_pipeline.evals.runner import _selected_evidence_authorization
from dual_pipeline.evals.stress import run_stress_suite
from tests.embedding_fixture import BgeM3TestBackend, MetadataOnlyBgeM3Backend

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class ExpandedEvaluationAssuranceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-eval-assurance-")
        self.runtime = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_portfolio_covers_every_required_track(self) -> None:
        report = validate_portfolio(
            ROOT / "evals" / "suites" / "pipeline-assurance-portfolio-e2.json"
        )
        self.assertEqual("PASS", report["status"])
        self.assertEqual(REQUIRED_TRACKS, {row["track_id"] for row in report["tracks"]})
        self.assertFalse(report["runtime_judge_substitution"])

    def test_operator_bridge_and_stress_reject_the_nonproduction_fixture(self) -> None:
        backend = MetadataOnlyBgeM3Backend()
        with self.assertRaisesRegex(RuntimeError, "real BGE-M3"):
            run_bridge_suite(
                suite_path=ROOT / "evals" / "suites" / "ingestion-query-bridge-e2.json",
                runtime_root=self.runtime,
                seed_graph=GRAPH,
                backend=backend,
                evaluation_id="bridge-reject-fixture",
            )
        with self.assertRaisesRegex(RuntimeError, "real BGE-M3"):
            run_stress_suite(
                suite_path=ROOT / "evals" / "suites" / "dual-deterministic-e1.json",
                runtime_root=self.runtime,
                seed_graph=GRAPH,
                backend=backend,
                repetitions=2,
                stress_id="stress-reject-fixture",
            )

    def test_bridge_refuses_the_source_checkout_as_runtime_root(self) -> None:
        with (
            mock.patch("dual_pipeline.evals.bridge._assert_operator_backend"),
            self.assertRaisesRegex(
                BridgeEvaluationFailure, "outside the source checkout"
            ),
        ):
            run_bridge_suite(
                suite_path=ROOT / "evals" / "suites" / "ingestion-query-bridge-e2.json",
                runtime_root=ROOT,
                seed_graph=GRAPH,
                backend=BgeM3TestBackend(),
                evaluation_id="bridge-source-root-refusal",
            )

    def test_same_runtime_bridge_makes_new_candidate_evidence_retrievable(self) -> None:
        with mock.patch("dual_pipeline.evals.bridge._assert_operator_backend"):
            result, path = run_bridge_suite(
                suite_path=ROOT / "evals" / "suites" / "ingestion-query-bridge-e2.json",
                runtime_root=self.runtime,
                seed_graph=GRAPH,
                backend=BgeM3TestBackend(),
                evaluation_id="bridge-fixture",
            )
        self.assertEqual("PASS", result["status"])
        self.assertTrue(path.is_file())
        row = result["case_results"][0]
        self.assertTrue(row["hard_gates"]["new_source_terms_absent_before_ingestion"])
        self.assertTrue(row["hard_gates"]["new_source_terms_retrieved_after_activation"])
        self.assertEqual("flagembedding-bge-m3-dev", result["embedding_backend"]["backend_id"])

    def test_stress_rejects_behavioral_drift(self) -> None:
        calls = 0

        def fake_run_suite(**kwargs):
            nonlocal calls
            calls += 1
            result = {
                "evaluation_id": kwargs["evaluation_id"],
                "canonical_digest": {"value": "a" * 64},
                "status": "PASS",
                "case_results": [
                    {
                        "case_id": "q",
                        "pipeline": "query",
                        "status": "PASS",
                        "cause_code": None,
                        "provider_call_count": 0,
                        "graph_mutation_count": 0,
                        "hard_gates": {"stable": calls == 1},
                    }
                ],
                "aggregate": {
                    "case_count": 1,
                    "provider_call_count": 0,
                    "graph_mutation_count": 0,
                    "hard_gate_failure_count": 0 if calls == 1 else 1,
                },
            }
            return result, self.runtime / f"{kwargs['evaluation_id']}.json"

        with (
            mock.patch("dual_pipeline.evals.stress._assert_operator_backend"),
            mock.patch("dual_pipeline.evals.stress.run_suite", side_effect=fake_run_suite),
        ):
            result, _path = run_stress_suite(
                suite_path=ROOT / "evals" / "suites" / "dual-deterministic-e1.json",
                runtime_root=self.runtime,
                seed_graph=GRAPH,
                backend=BgeM3TestBackend(),
                repetitions=2,
                stress_id="stress-drift-fixture",
                tags=("offline",),
            )
        self.assertEqual("FAIL", result["status"])
        self.assertFalse(result["aggregate"]["behavior_stable"])

    def test_stress_repeats_sealed_query_behavior(self) -> None:
        with (
            mock.patch("dual_pipeline.evals.stress._assert_operator_backend"),
            mock.patch("dual_pipeline.evals.runner._assert_operator_backend"),
        ):
            result, path = run_stress_suite(
                suite_path=ROOT / "evals" / "suites" / "dual-deterministic-e1.json",
                runtime_root=self.runtime,
                seed_graph=GRAPH,
                backend=BgeM3TestBackend(),
                repetitions=2,
                stress_id="stress-fixture",
                tags=("offline",),
            )
        self.assertEqual("PASS", result["status"])
        self.assertTrue(path.is_file())
        self.assertTrue(result["aggregate"]["behavior_stable"])
        self.assertTrue(result["aggregate"]["seed_graph_unchanged"])
        self.assertEqual(0, result["aggregate"]["hard_gate_failure_count"])
        self.assertEqual("flagembedding-bge-m3-dev", result["embedding_backend"]["backend_id"])

    def test_cache_context_digest_changes_when_access_context_changes(self) -> None:
        baseline = {
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "allowed_lanes": ["approved"],
            "generation": "a",
        }
        variants = (
            {"classification": "confidential"},
            {"audiences": ["client-team"]},
            {"allowed_lanes": ["approved", "candidate"]},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(digest(baseline), digest(baseline | variant))

    def test_evidence_authorization_and_candidate_labeling_are_independent(self) -> None:
        def record(
            record_id: str,
            *,
            classification: str,
            audience: str,
        ) -> dict:
            return {
                "record_id": record_id,
                "classification": classification,
                "audiences": [audience],
                "evidence_ids": [f"evidence-{record_id}"],
            }

        packet = {
            "approved": [
                {"record": record("internal-record", classification="internal", audience="nee-engineering")},
                {"record": record("restricted-record", classification="restricted", audience="nee-engineering")},
            ],
            "candidate": [
                {"record": record("candidate-record", classification="internal", audience="nee-engineering")},
            ],
            "external_public": [],
        }
        claim_map = {
            "claims": [
                {
                    "claim_id": "candidate-presented-as-fact",
                    "claim_kind": "factual",
                    "evidence": [{"record_id": "candidate-record"}],
                }
            ]
        }
        report = _selected_evidence_authorization(
            packet,
            claim_map,
            requested_classification="internal",
            requested_audiences=["nee-engineering"],
        )
        self.assertEqual(
            ["restricted-record"], report["authorization_violation_record_ids"]
        )
        self.assertEqual(
            ["candidate-presented-as-fact"],
            report["candidate_claim_violation_ids"],
        )
        self.assertEqual(
            {
                "evidence-internal-record",
                "evidence-restricted-record",
                "evidence-candidate-record",
            },
            set(report["distinct_evidence_ids"]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
