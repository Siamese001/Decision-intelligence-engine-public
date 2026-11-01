"""Adversarial and property-based boundary tests for judge contracts and scorecard validation.

Covers mandatory edge-case categories:
1. Boundary payload (empty scorecard, exact boundary extremes 0/100, float tolerances)
2. Malformed input (booleans, strings, NaN, infinity, missing keys)
3. Invariant bypass attempt (forged composite score, below-floor bypass, verdict forgery)
4. Exact production regression (historical Output 02 at 84, Output 03 at 90, rounding drift)
"""
import math
import unittest
from typing import Any, Dict

from hypothesis import given, strategies as st

from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    validate_output1_scorecard,
    validate_output2_scorecard,
    validate_output3_scorecard,
    validate_score_value,
)
from dual_pipeline.llm_judge_core.judges.recap import _populate_dimension_scores
from dual_pipeline.release_policy import CANONICAL_RELEASE_POLICY, validate_micro_judge_scorecard


class TestJudgeContractAdversarial(unittest.TestCase):
    """Adversarial tests against judge contract and scorecard integrity invariants."""

    # -------------------------------------------------------------------------
    # 1. Boundary Payloads
    # -------------------------------------------------------------------------

    def test_boundary_empty_scorecard_fails_closed(self) -> None:
        """Verify empty scorecards fail validation immediately with clear diagnostics."""
        for out_num in (1, 2, 3):
            valid, errors = validate_micro_judge_scorecard({}, output_num=out_num)
            self.assertFalse(valid)
            self.assertTrue(len(errors) > 0)

    def test_boundary_zero_and_max_exact_extremes(self) -> None:
        """Verify exact boundary extremes (0 and 100) are valid when dimension sums match."""
        # Exact 0 scorecard for Output 1
        zero_card = {
            "dimension_1": 0,
            "dimension_2": 0,
            "dimension_3": 0,
            "dimension_4": 0,
            "composite_score": 0,
            "verdict": "FAIL",
        }
        ok, errs = validate_micro_judge_scorecard(zero_card, output_num=1)
        self.assertTrue(ok, f"Zero card failed: {errs}")

        # Exact 100 scorecard for Output 1
        max_card = {
            "dimension_1": 25,
            "dimension_2": 25,
            "dimension_3": 25,
            "dimension_4": 25,
            "composite_score": 100,
            "verdict": "PASS",
        }
        ok, errs = validate_micro_judge_scorecard(max_card, output_num=1)
        self.assertTrue(ok, f"Max card failed: {errs}")

    def test_boundary_micro_overflow_fails_closed(self) -> None:
        """Verify values slightly above or below boundaries fail closed."""
        self.assertIsNotNone(validate_score_value(-0.0001, 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(25.0001, 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(100.001, 0, 100, "composite_score"))

    @given(
        val=st.floats(min_value=-1000.0, max_value=-0.001) | st.floats(min_value=25.001, max_value=1000.0)
    )
    def test_property_out_of_bounds_scores_rejected(self, val: float) -> None:
        """Hypothesis property: any score outside [0, 25] must be rejected."""
        err = validate_score_value(val, 0, 25, "test_field")
        self.assertIsNotNone(err)

    # -------------------------------------------------------------------------
    # 2. Malformed Input
    # -------------------------------------------------------------------------

    def test_malformed_booleans_strictly_rejected(self) -> None:
        """Verify boolean values are never coerced to ints (isinstance(True, int) trap)."""
        card_with_bool = {
            "dimension_1": True,
            "dimension_2": 24,
            "dimension_3": 25,
            "dimension_4": 24,
            "composite_score": 74,
        }
        ok, errs = validate_micro_judge_scorecard(card_with_bool, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("must be a number" in e for e in errs))

    def test_malformed_nan_and_inf_rejected(self) -> None:
        """Verify NaN, inf, and -inf are rejected in scorecards."""
        for bad_val in (float("nan"), float("inf"), float("-inf")):
            card = {
                "dimension_1": bad_val,
                "dimension_2": 20,
                "dimension_3": 20,
                "dimension_4": 20,
                "composite_score": 60,
            }
            ok, errs = validate_micro_judge_scorecard(card, output_num=1)
            self.assertFalse(ok)
            self.assertTrue(any("must be finite" in e or "must be a number" in e for e in errs))

    def test_malformed_strings_rejected(self) -> None:
        """Verify string representations of numbers are rejected without coercion."""
        card_with_str = {
            "dimension_1": "25",
            "dimension_2": 25,
            "dimension_3": 25,
            "dimension_4": 25,
            "composite_score": 100,
        }
        ok, errs = validate_micro_judge_scorecard(card_with_str, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("must be a number" in e for e in errs))

    # -------------------------------------------------------------------------
    # 3. Invariant Bypass Attempts
    # -------------------------------------------------------------------------

    def test_bypass_forged_composite_score_rejected(self) -> None:
        """Verify an evaluation cannot claim composite=98 when dimension sum is 75."""
        forged_card = {
            "dimension_1": 20,
            "dimension_2": 15,
            "dimension_3": 20,
            "dimension_4": 20,
            "composite_score": 98,  # sum is 75
            "verdict": "PASS",
        }
        ok, errs = validate_micro_judge_scorecard(forged_card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("does not match dimension sum" in e for e in errs))

    def test_bypass_output3_floor_failure_blocks_release(self) -> None:
        """Verify Output 03 fails validation if any dimension is below its floor, despite high composite."""
        # Total is 92, but dimension_1 is 10 (floor is 11)
        below_floor_card = {
            "dimension_1_strategic_edge": 10,  # below floor 11
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 14,
            "dimension_6_evidence_grounding_and_schema": 19,
            "composite_score": 92,
        }
        res = validate_output3_scorecard(below_floor_card)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("below mandatory floor" in e for e in res.errors))

    def test_bypass_forged_verdict_rejected(self) -> None:
        """Verify providing a PASS verdict for a failing score fails validation."""
        failing_card = {
            "dimension_1": 15,
            "dimension_2": 15,
            "dimension_3": 15,
            "dimension_4": 15,
            "composite_score": 60,
            "verdict": "PASS",  # 60 must be FAIL
        }
        ok, errs = validate_micro_judge_scorecard(failing_card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("does not match policy expected" in e for e in errs))

    # -------------------------------------------------------------------------
    # 4. Exact Production Regressions (Historical 84 and 90 Cases)
    # -------------------------------------------------------------------------

    def test_regression_historical_output2_score_84_and_display_parity(self) -> None:
        """Verify the historical 84 case for Output 02 does not suffer display rounding drift (84 != 85)."""
        sec_scores = {
            "header_and_overview": 8.0,
            "key_updates_technical_substance": 30.0,
            "conditional_extensions": 17.0,
            "appendix_stakeholder_perspectives": 29.0,
        }
        # Sum of sections: 8 + 30 + 17 + 29 = 84.0
        sec_sum = sum(sec_scores.values())
        self.assertEqual(sec_sum, 84.0)

        raw_dict = {"section_scores": sec_scores}
        _populate_dimension_scores(raw_dict, sec_scores)

        # Display dimensions sum MUST equal authoritative section sum (84, never 85)
        d_sum = sum(
            raw_dict[f"dimension_{i}_{k}"]
            for i, k in (
                (1, "executive_altitude"),
                (2, "technical_substance"),
                (3, "strategic_synthesis"),
                (4, "scoping_and_modality"),
            )
        )
        self.assertEqual(d_sum, 84.0)

        # Under canonical release policy, 84 is FAIL (production pass is 95)
        verdict = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, 84.0).value
        self.assertEqual(verdict, "FAIL")

    def test_regression_historical_output3_score_90_floors(self) -> None:
        """Verify historical Output 03 at 90 with all dimensions above floor passes advisory."""
        card_90 = {
            "dimension_1_strategic_edge": 13,  # >= 11
            "dimension_2_knowledge_layer_density": 13,  # >= 11
            "dimension_3_semantic_atomicity": 18,  # >= 14
            "dimension_4_predicate_specificity": 13,  # >= 11
            "dimension_5_exclusion_of_action_items": 14,  # >= 11
            "dimension_6_evidence_grounding_and_schema": 19,  # >= 14
            "composite_score": 90,
        }
        res = validate_output3_scorecard(card_90)
        self.assertTrue(res.is_valid, f"Output 3 score 90 failed: {res.errors}")
        self.assertEqual(res.validated_composite, 90.0)


if __name__ == "__main__":
    unittest.main()
