"""tests/test_judge_borderline_floor_reconciliation.py

Wave 1 Verification Suite: Reconciles scorecard validation invariants and borderline gating.
Verifies that:
1. Scorecards with verdict == 'HITL_REQUIRED' and dimension score == floor - 1 pass validation.
2. Scorecards with dimension score < floor - 1 cannot have verdict 'HITL_REQUIRED' and are rejected.
3. Multi-point floor breaches (e.g. 8/14) strictly suppress HITL clarification prompts.
4. Borderline floor breaches trigger HITL clarification with accurate needed_pts calculation.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import (
    evaluate_judge_clarification,
)
from dual_pipeline.release_policy import (
    validate_micro_judge_scorecard,
)


def _make_out3_scorecard(
    composite: float,
    d6_score: float,
    verdict: str = "HITL_REQUIRED",
    status: str = "PASS",
) -> Dict[str, Any]:
    rem = round(composite - d6_score, 2)
    # Max points: d1=15, d2=15, d3=20, d4=15, d5=15 (sum=80)
    ratio = rem / 80.0
    d1 = round(15.0 * ratio, 2)
    d2 = round(15.0 * ratio, 2)
    d3 = round(20.0 * ratio, 2)
    d4 = round(15.0 * ratio, 2)
    d5 = round(rem - (d1 + d2 + d3 + d4), 2)

    return {
        "status": status,
        "verdict": verdict,
        "composite_score": round(composite, 2),
        "dimension_1_strategic_edge": d1,
        "dimension_2_knowledge_layer_density": d2,
        "dimension_3_semantic_atomicity": d3,
        "dimension_4_predicate_specificity": d4,
        "dimension_5_exclusion_of_action_items": d5,
        "dimension_6_evidence_grounding_and_schema": round(d6_score, 2),
        "evidence_id_references": ["stmt-1", "stmt-2"],
        "graph_rationale": "Evaluated graph signal projection.",
        "judge_model": "test-judge-graph",
        "prompt_digest": "digest_test",
        "rubric_version": "2.0",
        "sample_count": 1,
        "is_fallback": False,
    }


class TestJudgeBorderlineFloorReconciliation(unittest.TestCase):
    """Test suite for Wave 1 borderline floor reconciliation and gate safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "test_hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_borderline_hitl_required_scorecard_validates_cleanly(self) -> None:
        """An Output 3 scorecard with D6=13.0 (floor=14.0) and HITL_REQUIRED validates cleanly."""
        card = _make_out3_scorecard(composite=88.0, d6_score=13.0, verdict="HITL_REQUIRED")
        is_valid, errors = validate_micro_judge_scorecard(card, output_num=3)
        self.assertTrue(is_valid, f"Validation failed unexpectedly: {errors}")
        self.assertEqual(errors, [])

    def test_catastrophic_floor_failure_with_hitl_required_rejected(self) -> None:
        """An Output 3 scorecard with D6=8.0 (floor=14.0) and HITL_REQUIRED is rejected."""
        card = _make_out3_scorecard(composite=88.0, d6_score=8.0, verdict="HITL_REQUIRED")
        is_valid, errors = validate_micro_judge_scorecard(card, output_num=3)
        self.assertFalse(is_valid)
        self.assertTrue(
            any("more than 1 point below mandatory floor" in e for e in errors),
            f"Expected multi-point floor error, got: {errors}",
        )

    def test_catastrophic_floor_failure_with_verdict_fail_is_valid_structure(self) -> None:
        """An Output 3 scorecard with D6=8.0 and verdict FAIL is structurally valid."""
        card = _make_out3_scorecard(composite=88.0, d6_score=8.0, verdict="FAIL", status="FAIL")
        is_valid, errors = validate_micro_judge_scorecard(card, output_num=3)
        self.assertTrue(is_valid, f"Validation failed: {errors}")
        self.assertEqual(errors, [])

    def test_multi_point_floor_breach_suppresses_clarification_prompt(self) -> None:
        """Scorecard with D6=8.0 suppresses HITL clarification prompt and fails closed."""
        card = _make_out3_scorecard(composite=88.0, d6_score=8.0, verdict="FAIL", status="FAIL")
        judge_report = {"output_3_evaluation": card}
        final_decision = {"status": "FAIL", "output_3_pass": False, "authoritative_write_authorized": False}
        resolver = MagicMock(return_value="AFFIRM_VALID")

        upgraded = evaluate_judge_clarification(
            judge_report=judge_report,
            final_decision=final_decision,
            metadata={"hitl_ledger_path": str(self.ledger_path)},
            store=self.store,
            interactive_resolver=resolver,
        )

        self.assertFalse(upgraded)
        # Resolver should NEVER be called for multi-point floor breach
        resolver.assert_not_called()
        self.assertEqual(final_decision["status"], "FAIL")
        self.assertFalse(final_decision["authoritative_write_authorized"])

    def test_borderline_floor_breach_triggers_clarification_with_accurate_deficit(self) -> None:
        """Scorecard with D6=13.0 triggers HITL clarification and calculates accurate needed_pts."""
        card = _make_out3_scorecard(composite=88.0, d6_score=13.0, verdict="HITL_REQUIRED", status="PASS")
        judge_report = {"output_3_evaluation": card}
        final_decision = {"status": "FAIL", "output_3_pass": False, "authoritative_write_authorized": False}

        captured_envelope: list[Any] = []

        def mock_resolver(envelope: Any) -> str:
            captured_envelope.append(envelope)
            return "UPHOLD_DEDUCTION"

        metadata = {
            "reviewer": "operator_amit",
            "operator_id": "operator_amit",
            "approval_source": "operator_amit",
            "hitl_ledger_path": str(self.ledger_path),
        }

        upgraded = evaluate_judge_clarification(
            judge_report=judge_report,
            final_decision=final_decision,
            metadata=metadata,
            store=self.store,
            interactive_resolver=mock_resolver,
        )

        self.assertFalse(upgraded)
        self.assertEqual(len(captured_envelope), 1)
        envelope = captured_envelope[0]
        affirm_opt = [o for o in envelope.raw_options if o["key"] == "AFFIRM_VALID"][0]
        self.assertIn("+2.0 pts", affirm_opt["label"])

    def test_output1_borderline_hitl_required_validates(self) -> None:
        """Output 1 scorecard with D1=13.0 (floor=14.0) and HITL_REQUIRED validates cleanly."""
        card = {
            "status": "PASS",
            "verdict": "HITL_REQUIRED",
            "composite_score": 88.0,
            "dimension_1_forensic_grounding_and_fidelity": 13.0,
            "dimension_2_attribution_and_room_mic_hygiene": 25.0,
            "dimension_3_chronological_and_timestamp_integrity": 25.0,
            "dimension_4_asr_cleanup_and_domain_preservation": 25.0,
            "judge_model": "test-judge-forensic",
            "prompt_digest": "digest_o1_test",
            "rubric_version": "2.0",
            "sample_count": 1,
            "is_fallback": False,
        }
        is_valid, errors = validate_micro_judge_scorecard(card, output_num=1)
        self.assertTrue(is_valid, f"Validation failed: {errors}")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
