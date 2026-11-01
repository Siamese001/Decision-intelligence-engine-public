#!/usr/bin/env python3
"""Unit tests for Wave 5: GraphExtractionJudge & ExecutiveRecapJudge HITL Clarification.

Verifies:
1. Borderline judge evaluations (85 <= composite < 90) surface HitlRequirementEnvelope.
2. Operator affirmation upgrades verdict to PASS (90/100) and authorizes release.
3. Hard errors (composite < 85) cannot be bypassed; remain FAIL.
4. assign_clarified_graph_verdict correctly classifies PASS, FAIL, and HITL_REQUIRED.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest
from dual_pipeline.llm_judge_core.graph import DIMENSION_FLOORS, DIMENSION_KEYS

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import (
    assign_clarified_graph_verdict,
    evaluate_judge_clarification,
)


@pytest.mark.unit
class GraphExtractionJudgeHitlTests(unittest.TestCase):
    """Test suite verifying Wave 5 LLM judge HITL clarification mechanics."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_assign_clarified_graph_verdict_thresholds(self) -> None:
        """assign_clarified_graph_verdict assigns HITL_REQUIRED for 85-89, PASS for >=90, FAIL for <85."""
        # 1. PASS (>= 90)
        pass_eval = {
            "dimension_1_strategic_edge": 14,
            "dimension_2_knowledge_layer_density": 14,
            "dimension_3_semantic_atomicity": 19,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 14,
            "dimension_6_evidence_grounding_and_schema": 19,
        }
        res_pass = assign_clarified_graph_verdict(pass_eval, DIMENSION_KEYS, DIMENSION_FLOORS)
        self.assertEqual(res_pass["verdict"], "PASS")
        self.assertEqual(res_pass["composite_score"], 94)

        # 2. Borderline HITL_REQUIRED (85-89)
        border_eval = {
            "dimension_1_strategic_edge": 13,
            "dimension_2_knowledge_layer_density": 13,
            "dimension_3_semantic_atomicity": 18,
            "dimension_4_predicate_specificity": 13,
            "dimension_5_exclusion_of_action_items": 13,
            "dimension_6_evidence_grounding_and_schema": 17,
        }
        res_border = assign_clarified_graph_verdict(border_eval, DIMENSION_KEYS, DIMENSION_FLOORS)
        self.assertEqual(res_border["verdict"], "HITL_REQUIRED")
        self.assertEqual(res_border["composite_score"], 87)

        # 3. Hard FAIL (< 85)
        fail_eval = {
            "dimension_1_strategic_edge": 10,
            "dimension_2_knowledge_layer_density": 10,
            "dimension_3_semantic_atomicity": 14,
            "dimension_4_predicate_specificity": 10,
            "dimension_5_exclusion_of_action_items": 10,
            "dimension_6_evidence_grounding_and_schema": 14,
        }
        res_fail = assign_clarified_graph_verdict(fail_eval, DIMENSION_KEYS, DIMENSION_FLOORS)
        self.assertEqual(res_fail["verdict"], "FAIL")
        self.assertLess(res_fail["composite_score"], 85)

    def test_borderline_judge_deduction_clarification_upgrades_to_pass(self) -> None:
        """When operator affirms candidate validity for borderline score, judge upgrades to PASS."""
        judge_report = {
            "output_3_evaluation": {
                "composite_score": 87,
                "verdict": "HITL_REQUIRED",
                "forensic_rationale": "Nuanced predicate classification between DEPENDS_ON and INTEGRATES_WITH (-3 pts).",
            }
        }
        final_decision = {
            "status": "FAIL",
            "output_3_pass": False,
            "output_3_llm_score_pass": False,
            "authoritative_write_authorized": False,
        }
        metadata = {
            "hitl_judge_resolver": lambda env: "AFFIRM_VALID",
        }

        upgraded = evaluate_judge_clarification(
            judge_report=judge_report,
            final_decision=final_decision,
            metadata=metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_judge_resolver"],
        )

        self.assertTrue(upgraded)
        self.assertEqual(judge_report["output_3_evaluation"]["verdict"], "PASS")
        self.assertEqual(judge_report["output_3_evaluation"]["composite_score"], 90.0)
        self.assertEqual(final_decision["status"], "PASS")
        self.assertTrue(final_decision["authoritative_write_authorized"])
        self.assertIn("judge_clarification_output_3", metadata["hitl_resolutions"])

        # Check ledger persistence
        records = self.store.get_history(category="judge_deduction_clarification")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["selected_option"]["key"], "AFFIRM_VALID")

    def test_hard_judge_failure_cannot_be_clarified_away(self) -> None:
        """Hard failure (composite < 85) is not eligible for clarification and remains FAIL."""
        judge_report = {
            "output_3_evaluation": {
                "composite_score": 72,
                "verdict": "FAIL",
                "forensic_rationale": "Major hallucinations and schema violations across multiple sections.",
            }
        }
        final_decision = {
            "status": "FAIL",
            "output_3_pass": False,
            "authoritative_write_authorized": False,
        }
        metadata: dict = {}

        upgraded = evaluate_judge_clarification(
            judge_report=judge_report,
            final_decision=final_decision,
            metadata=metadata,
            store=self.store,
        )

        self.assertFalse(upgraded)
        self.assertEqual(judge_report["output_3_evaluation"]["verdict"], "FAIL")
        self.assertEqual(final_decision["status"], "FAIL")
        self.assertFalse(final_decision["authoritative_write_authorized"])


if __name__ == "__main__":
    unittest.main()
