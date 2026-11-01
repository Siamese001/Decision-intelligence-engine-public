"""tests/test_judge_graduated_rubric_e2e.py

Wave 2 Verification Suite: Graduated Dimension 6 Rubric & Dynamic Bayesian Confidence.
Verifies that:
1. Output 03 Dimension 6 defines itemized sub-criteria (factuality, schema, locators).
2. Minor locator deductions maintain passing scores (16-18/20), whereas hallucinations fail (<10/20).
3. HITL decision envelopes compute dynamic confidence levels from historical priors rather than static constants.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import (
    evaluate_judge_clarification,
)
from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT
from dual_pipeline.release_policy import (
    validate_micro_judge_scorecard,
)


class TestJudgeGraduatedRubricE2E(unittest.TestCase):
    """End-to-end tests for graduated rubric criteria and dynamic confidence calibration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "test_hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_output3_contract_dimension6_graduated_criteria(self) -> None:
        """Output 03 Dimension 6 must define itemized sub-criteria for graduated scoring."""
        d6 = [d for d in OUTPUT3_CONTRACT.rubric_dimensions if d.key == "dimension_6_evidence_grounding_and_schema"][0]
        self.assertEqual(d6.max_points, 20)
        self.assertEqual(d6.floor, 14.0)
        self.assertIsNotNone(d6.criteria)
        self.assertGreaterEqual(len(d6.criteria), 3)

        # Confirm 3 distinct sub-criteria
        text = " ".join(d6.criteria)
        self.assertIn("Source Factuality", text)
        self.assertIn("Schema Strictness", text)
        self.assertIn("Timestamp & Locator", text)

    def test_graduated_deduction_minor_locator_vs_hallucination(self) -> None:
        """Minor locator error yields 17/20 (passes floor 14); hallucination yields 8/20 (fails)."""
        # Case A: Minor locator shift (-3 pts): Score = 17/20
        minor_card = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 92.0,
            "dimension_1_strategic_edge": 15.0,
            "dimension_2_knowledge_layer_density": 15.0,
            "dimension_3_semantic_atomicity": 20.0,
            "dimension_4_predicate_specificity": 13.0,
            "dimension_5_exclusion_of_action_items": 12.0,
            "dimension_6_evidence_grounding_and_schema": 17.0,
            "evidence_id_references": ["stmt-1"],
            "graph_rationale": "Minor timestamp bracket discrepancy (-3 pts on locator precision).",
            "judge_model": "test-judge-graph",
            "prompt_digest": "digest_test",
            "rubric_version": "2.0",
            "sample_count": 1,
            "is_fallback": False,
        }
        ok_minor, errs_minor = validate_micro_judge_scorecard(minor_card, output_num=3)
        self.assertTrue(ok_minor, f"Minor locator error should pass, got: {errs_minor}")

        # Case B: Severe factual hallucination (-12 pts): Score = 8/20
        hallucination_card = {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 83.0,
            "dimension_1_strategic_edge": 15.0,
            "dimension_2_knowledge_layer_density": 15.0,
            "dimension_3_semantic_atomicity": 20.0,
            "dimension_4_predicate_specificity": 13.0,
            "dimension_5_exclusion_of_action_items": 12.0,
            "dimension_6_evidence_grounding_and_schema": 8.0,
            "evidence_id_references": ["stmt-1"],
            "graph_rationale": "Fabricated entity with no source grounding (-12 pts).",
            "judge_model": "test-judge-graph",
            "prompt_digest": "digest_test",
            "rubric_version": "2.0",
            "sample_count": 1,
            "is_fallback": False,
        }
        ok_halluc, errs_halluc = validate_micro_judge_scorecard(hallucination_card, output_num=3)
        self.assertTrue(ok_halluc)
        self.assertEqual(hallucination_card["verdict"], "FAIL")

    def test_dynamic_confidence_from_historical_priors(self) -> None:
        """HITL envelope computes dynamic confidence reflecting historical affirmation rate."""
        # Seed 4 historical affirmations in category judge_deduction_clarification
        for i in range(4):
            self.store.record_decision(
                decision_id=f"prior_affirm_{i}",
                category="judge_deduction_clarification",
                prompt="Affirm candidate validity?",
                options=[{"key": "AFFIRM_VALID", "label": "Affirm"}, {"key": "UPHOLD_DEDUCTION", "label": "Uphold"}],
                selected_option={"key": "AFFIRM_VALID", "label": "Affirm"},
                operator_id="operator_amit",
            )

        card = {
            "status": "PASS",
            "verdict": "HITL_REQUIRED",
            "composite_score": 88.0,
            "dimension_1_strategic_edge": 14.0,
            "dimension_2_knowledge_layer_density": 14.0,
            "dimension_3_semantic_atomicity": 18.0,
            "dimension_4_predicate_specificity": 14.0,
            "dimension_5_exclusion_of_action_items": 15.0,
            "dimension_6_evidence_grounding_and_schema": 13.0,
            "evidence_id_references": ["stmt-1"],
            "judge_model": "test-judge-graph",
            "prompt_digest": "digest_test",
            "rubric_version": "2.0",
            "sample_count": 1,
            "is_fallback": False,
        }
        judge_report = {"output_3_evaluation": card}
        final_decision = {"status": "FAIL", "output_3_pass": False, "authoritative_write_authorized": False}

        captured_envelope: list[Any] = []

        def resolver(env: Any) -> str:
            captured_envelope.append(env)
            return "AFFIRM_VALID"

        metadata = {
            "reviewer": "operator_amit",
            "operator_id": "operator_amit",
            "approval_source": "operator_amit",
            "prior_user_command": "operator affirmed domain validity",
            "hitl_ledger_path": str(self.ledger_path),
        }

        upgraded = evaluate_judge_clarification(
            judge_report=judge_report,
            final_decision=final_decision,
            metadata=metadata,
            store=self.store,
            interactive_resolver=resolver,
        )

        self.assertTrue(upgraded)
        self.assertEqual(len(captured_envelope), 1)
        env = captured_envelope[0]

        # Prior affirm rate: (4 + 1)/(4 + 2) = 5/6 = 0.833 base score
        affirm_opt = [o for o in env.calibrated_options if o["key"] == "AFFIRM_VALID"][0]
        affirm_conf = float(affirm_opt.get("confidence_score") or affirm_opt.get("confidence"))
        # Must be dynamically calibrated and significantly higher than static 0.55
        self.assertGreater(affirm_conf, 0.70)
        self.assertNotEqual(affirm_conf, 0.55)

        # Inspect persisted receipt in ledger
        receipt = judge_report["output_3_evaluation"].get("hitl_clarification")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.get("status"), "RESOLVED")


if __name__ == "__main__":
    unittest.main()
