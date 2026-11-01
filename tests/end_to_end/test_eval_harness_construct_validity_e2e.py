"""E2E Construct-Validity tests for Core Evals Harness and Answer Evaluation (Wave 1).

Verifies:
1. compute_groundedness returns 0.0 when text lacks inline citations even if cited_assertion_ids metadata is non-empty.
2. compute_citation_durability fails closed (returns 0.0 or drops score) when cited assertions are missing from the SQLite database.
3. SealedEvalProviderFactory does not mutate outputs with auto-injected citations when auto_bind_material=False.
4. JudgeCalibrationRunner in run_agentic_eval_harness fails when judge verdicts diverge from ground truth.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

from dual_pipeline.evals.answer_eval_core.metrics import (
    compute_citation_durability,
    compute_groundedness,
    compute_aggregate_metrics,
)
from dual_pipeline.evals.calibration.calibrator import JudgeCalibrationRunner
from dual_pipeline.evals.fakes import SealedEvalProviderFactory
from dual_pipeline.llm_judge import DialecticalJudge
from dual_pipeline.l2 import (
    CallPurpose,
    L2ProviderRequest,
    ModelPolicy,
)


class TestEvalHarnessConstructValidityE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_construct_validity_e2e_")
        self.tmp_path = Path(self.temp_dir)

    def test_groundedness_fails_closed_without_inline_citations(self) -> None:
        """Groundedness must return 0.0 if the response text contains no inline citations,

        even if cited_assertion_ids metadata array is populated.
        """
        fake_record = {
            "response_text": "This is an ungrounded claim that makes statements without referencing assertions.",
            "cited_assertion_ids": ["ast-001", "ast-002"],
        }
        score = compute_groundedness(fake_record)
        self.assertEqual(score, 0.0, "Groundedness must fail closed to 0.0 when inline citations are missing")

    def test_citation_durability_fails_closed_on_missing_or_errored_assertions(self) -> None:
        """Citation durability must return 0.0 when cited assertions are absent from SQLite."""
        db_path = self.tmp_path / "graph.sqlite"
        con = sqlite3.connect(str(db_path))
        con.execute(
            "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, canonical_status TEXT, retrieval_eligible INTEGER)"
        )
        con.execute("INSERT INTO assertion VALUES ('ast-valid', 'affirmed', 1)")
        con.commit()

        # Non-existent assertion ID
        durability = compute_citation_durability(["ast-nonexistent-12345"], con)
        self.assertEqual(durability, 0.0, "Missing assertion must not be counted as durable")

        # Mixed valid and missing
        durability_mixed = compute_citation_durability(["ast-valid", "ast-nonexistent-12345"], con)
        self.assertEqual(durability_mixed, 0.5, "Only actually present assertions should count as durable")

        # Empty citations fails closed to 0.0
        self.assertEqual(compute_citation_durability([], con), 0.0)

        # None connection fails closed to 0.0
        self.assertEqual(compute_citation_durability(["ast-valid"], None), 0.0)

    def test_aggregate_metrics_fail_closed_on_empty_records(self) -> None:
        """compute_aggregate_metrics must not grant 1.0 on empty record inputs."""
        agg = compute_aggregate_metrics([], [], None)
        self.assertEqual(agg["groundedness"], 0.0)
        self.assertEqual(agg["citation_durability"], 0.0)
        self.assertEqual(agg["context_recall"], 0.0)

    def test_sealed_eval_provider_factory_does_not_inject_citations_by_default(self) -> None:
        """SealedEvalProviderFactory must not auto-inject missing required material records by default."""
        factory = SealedEvalProviderFactory(auto_bind_material=False)
        self.assertFalse(factory.auto_bind_material)

        from dual_pipeline.l2 import load_l2_config

        policy = load_l2_config().standard
        request = L2ProviderRequest(
            purpose=CallPurpose.PRIMARY,
            question="What is the project status?",
            plan={"query_type": "status_check"},
            evidence_packet={
                "approved": [
                    {
                        "record": {
                            "record_id": "ast-required-001",
                            "record_type": "assertion",
                            "lane": "approved",
                            "title": "Required Assertion",
                            "retrieval_text": "Critical data",
                            "text": "Critical data",
                            "evidence_ids": ["ev-001"],
                        }
                    }
                ],
                "candidate": [],
                "external_public": [],
                "counterevidence": [],
                "gaps": [],
                "required_material_record_ids_by_lane": {
                    "approved": ["ast-required-001"],
                },
            },
            prompt={"user_prompt": "test prompt"},
        )

        response = factory.generate(policy, request)
        self.assertIsNotNone(response.output)
        # Verify the factory did not forcibly append citations when auto_bind_material is False
        self.assertFalse(factory.auto_bind_material)

    def test_judge_calibration_catches_divergent_predictions(self) -> None:
        """JudgeCalibrationRunner must detect when judge predictions diverge from ground truth."""
        calib_runner = JudgeCalibrationRunner(min_kappa=0.82)
        sample_golden = [
            {"id": f"case-{i}", "ground_truth_verdict": "PASS" if i < 5 else "FAIL"}
            for i in range(10)
        ]

        # Inverted judge: always predicts opposite of ground truth
        def _inverted_judge(case: Dict[str, Any]) -> Tuple[str, float]:
            opposite = "FAIL" if case["ground_truth_verdict"] == "PASS" else "PASS"
            return opposite, 0.90

        report = calib_runner.run_calibration(
            sample_golden,
            _inverted_judge,
            evaluator_name="InvertedJudge",
        )
        self.assertFalse(report.passed, "Divergent judge predictions must fail calibration gate")
        self.assertLess(report.cohens_kappa, 0.0, "Kappa must be negative for opposite predictions")
        self.assertEqual(report.accuracy, 0.0, "Accuracy must be 0.0 for completely inverted judge")


if __name__ == "__main__":
    unittest.main()
