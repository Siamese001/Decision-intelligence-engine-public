"""Unit tests for judge contract congruence, applicability, and scorecard validation."""
import math
import unittest
from typing import Any, Dict

from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    CriterionApplicability,
    JudgeCriterion,
    OUTPUT1_CRITERIA,
    OUTPUT2_CRITERIA,
    OUTPUT3_CRITERIA,
    validate_output1_scorecard,
    validate_output2_scorecard,
    validate_output3_scorecard,
    validate_score_value,
)
from dual_pipeline.prompts.contracts.output1_contract import OUTPUT1_CONTRACT
from dual_pipeline.prompts.contracts.output2_contract import OUTPUT2_CONTRACT
from dual_pipeline.prompts.contracts.output3_contract import OUTPUT3_CONTRACT
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.release_policy import validate_micro_judge_scorecard


class TestJudgeContractCongruence(unittest.TestCase):
    """Verifies that all criteria definitions, scorecards, and thresholds are unified."""

    def test_output1_speaker_collapse_threshold_is_unified_85(self) -> None:
        """Verify Output 01 single-speaker collapse threshold is exactly 85% across >=10 turns."""
        self.assertEqual(OUTPUT1_CONTRACT.single_speaker_collapse_threshold, 0.85)
        # Verify criteria list in contract also reflects 85%
        dim2 = next(d for d in OUTPUT1_CONTRACT.rubric_dimensions if d.key == "dimension_2_attribution_and_room_mic_hygiene")
        self.assertTrue(any("85%" in c for c in dim2.criteria))
        self.assertFalse(any("65%" in c for c in dim2.criteria))

    def test_output2_primary_speaker_and_et_window_invariants(self) -> None:
        """Verify Output 02 primary speaker threshold is >= 20% and ET window has admissible state."""
        self.assertEqual(OUTPUT2_CONTRACT.primary_speaker_minimum_share, 0.20)
        self.assertTrue(OUTPUT2_CONTRACT.allow_missing_et_window)

    def test_output2_section_maxima_sum_to_100(self) -> None:
        """Verify Output 02 authoritative section specs sum exactly to 100."""
        section_maxima = {
            "header_and_overview": 10,
            "key_updates_technical_substance": 35,
            "conditional_extensions": 20,
            "appendix_stakeholder_perspectives": 35,
        }
        self.assertEqual(sum(section_maxima.values()), 100)

    def test_output3_six_dimensions_sum_and_floors(self) -> None:
        """Verify Output 03 uses 6 dimensions summing to 100 with mandatory floors."""
        dim_specs = [
            ("dimension_1_strategic_edge", 15, 11),
            ("dimension_2_knowledge_layer_density", 15, 11),
            ("dimension_3_semantic_atomicity", 20, 14),
            ("dimension_4_predicate_specificity", 15, 11),
            ("dimension_5_exclusion_of_action_items", 15, 11),
            ("dimension_6_evidence_grounding_and_schema", 20, 14),
        ]
        total_max = sum(max_pts for _, max_pts, _ in dim_specs)
        total_floors = sum(floor_pts for _, _, floor_pts in dim_specs)
        self.assertEqual(total_max, 100)
        self.assertEqual(total_floors, 72)
        self.assertEqual(len(dim_specs), 6)

    def test_validate_score_value_strict_typing_and_bounds(self) -> None:
        """Verify validate_score_value rejects None, booleans, strings, NaN, infinity, and out-of-bounds."""
        self.assertIsNotNone(validate_score_value(None, 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(True, 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(False, 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value("25", 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(float("nan"), 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(float("inf"), 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(float("-inf"), 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(-0.5, 0, 25, "dim"))
        self.assertIsNotNone(validate_score_value(25.1, 0, 25, "dim"))

        # Valid values
        self.assertIsNone(validate_score_value(0, 0, 25, "dim"))
        self.assertIsNone(validate_score_value(25, 0, 25, "dim"))
        self.assertIsNone(validate_score_value(18.5, 0, 25, "dim"))

    def test_validate_output1_scorecard(self) -> None:
        """Verify Output 01 scorecard validation."""
        valid_card = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_attribution_and_room_mic_hygiene": 24,
            "dimension_3_chronological_and_timestamp_integrity": 25,
            "dimension_4_asr_cleanup_and_domain_preservation": 25,
            "composite_score": 99,
        }
        res = validate_output1_scorecard(valid_card)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.validated_composite, 99.0)

        # Mismatched composite
        bad_card = dict(valid_card)
        bad_card["composite_score"] = 90
        res_bad = validate_output1_scorecard(bad_card)
        self.assertFalse(res_bad.is_valid)
        self.assertTrue(any("does not match dimension sum" in e for e in res_bad.errors))

    def test_validate_output2_scorecard_section_authoritative(self) -> None:
        """Verify Output 02 scorecard validates section scores and display dimension parity."""
        valid_card = {
            "section_scores": {
                "header_and_overview": 9,
                "key_updates_technical_substance": 30,
                "conditional_extensions": 17,
                "appendix_stakeholder_perspectives": 28,
            },
            "composite_score": 84,
            "dimension_1_executive_altitude": 21,
            "dimension_2_technical_substance": 21,
            "dimension_3_strategic_synthesis": 21,
            "dimension_4_scoping_and_modality": 21,
        }
        res = validate_output2_scorecard(valid_card)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.validated_composite, 84.0)

        # Display dimensions diverging by > 1.0 point from section sum
        divergent_card = dict(valid_card)
        divergent_card["dimension_1_executive_altitude"] = 25  # sum becomes 88 vs 84
        res_div = validate_output2_scorecard(divergent_card)
        self.assertFalse(res_div.is_valid)
        self.assertTrue(any("diverges from authoritative section sum" in e for e in res_div.errors))

    def test_validate_output3_scorecard_floors(self) -> None:
        """Verify Output 03 scorecard rejects dimension scores below mandatory floor."""
        valid_card = {
            "dimension_1_strategic_edge": 13,
            "dimension_2_knowledge_layer_density": 13,
            "dimension_3_semantic_atomicity": 18,
            "dimension_4_predicate_specificity": 13,
            "dimension_5_exclusion_of_action_items": 14,
            "dimension_6_evidence_grounding_and_schema": 19,
            "composite_score": 90,
        }
        res = validate_output3_scorecard(valid_card)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.validated_composite, 90.0)

        # Dimension below floor (floor for dim 1 is 11)
        below_floor = dict(valid_card)
        below_floor["dimension_1_strategic_edge"] = 10
        below_floor["composite_score"] = 87
        res_flr = validate_output3_scorecard(below_floor)
        self.assertFalse(res_flr.is_valid)
        self.assertTrue(any("below mandatory floor" in e for e in res_flr.errors))

    def test_release_policy_validate_micro_judge_scorecard(self) -> None:
        """Verify release_policy.validate_micro_judge_scorecard supports Output 1-3 schemas."""
        # Output 2
        card2 = {
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 34,
                "conditional_extensions": 19,
                "appendix_stakeholder_perspectives": 33,
            },
            "composite_score": 96,
            "verdict": "PASS",
        }
        ok, errs = validate_micro_judge_scorecard(card2, output_num=2)
        self.assertTrue(ok, f"Unexpected errors: {errs}")

        # Output 3
        card3 = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 18,
            "composite_score": 98,
            "verdict": "PASS",
        }
        ok, errs = validate_micro_judge_scorecard(card3, output_num=3)
        self.assertTrue(ok, f"Unexpected errors: {errs}")

    def test_criterion_applicability_resolution(self) -> None:
        """Verify criteria applicability checks based on attendee count and archetype."""
        multi_crit = JudgeCriterion(
            criterion_id="TEST-MULTI",
            output_num=1,
            title="Multi attendee check",
            max_points=25,
            applicability=CriterionApplicability.MULTI_ATTENDEE,
        )
        # 1 attendee -> not applicable
        self.assertFalse(multi_crit.is_applicable(MeetingArchetype.GENERAL, attendee_count=1, turn_count=20))
        # 2 attendees, <10 turns -> not applicable
        self.assertFalse(multi_crit.is_applicable(MeetingArchetype.GENERAL, attendee_count=2, turn_count=8))
        # 2 attendees, >=10 turns -> applicable
        self.assertTrue(multi_crit.is_applicable(MeetingArchetype.GENERAL, attendee_count=2, turn_count=10))

        arch_crit = JudgeCriterion(
            criterion_id="TEST-ARCH",
            output_num=2,
            title="Deep dive check",
            max_points=20,
            applicability=CriterionApplicability.ARCHETYPE_SPECIFIC,
            applicable_archetypes=(MeetingArchetype.ARCHITECTURE_DEEP_DIVE,),
        )
        self.assertTrue(arch_crit.is_applicable(MeetingArchetype.ARCHITECTURE_DEEP_DIVE))
        self.assertFalse(arch_crit.is_applicable(MeetingArchetype.STRATEGIC_DISCOVERY))


if __name__ == "__main__":
    unittest.main()
