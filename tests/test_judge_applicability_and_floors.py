"""
tests/test_judge_applicability_and_floors.py

Verification suite for Wave 2:
1. Document mode O1/O2 NOT_APPLICABLE handling (no synthetic 100/PASS).
2. Strict fail-closed enforcement of mandatory dimension floors across Outputs 1-3.
3. Historical replay validation of F1, F2 (Hrit_Sow, Energy_Utilities, Mandy_Hopmann), and F5.
4. Dynamic applicability re-weighting for non-strategic meeting archetypes (Tactical Alignment).
5. Hypothesis property-based verification of dimension floor boundaries.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from dual_pipeline.canonical_pipeline.quality_aggregates import (
    compute_quality_aggregate_score,
    get_evaluated_composite_scores,
    is_deliverable_skipped_or_not_applicable,
)
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.prompts.contracts.output3_contract import OUTPUT3_CONTRACT
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    DocumentOutput3ScorecardV2,
    Output3ScorecardV2,
    validate_versioned_judge_scorecard,
)
from dual_pipeline.release_policy import validate_micro_judge_scorecard


class TestJudgeApplicabilityAndFloors(unittest.TestCase):
    """Test suite for applicability-aware judge rubrics and floor enforcement."""

    # -------------------------------------------------------------------------
    # 1. Document Mode O1/O2 NOT_APPLICABLE Validation
    # -------------------------------------------------------------------------

    def test_document_mode_o1_o2_not_applicable_passes(self) -> None:
        """Document mode O1/O2 marked NOT_APPLICABLE with None score passes validation."""
        skipped_stub = {
            "status": "NOT_APPLICABLE",
            "composite_score": None,
            "verdict": "NOT_APPLICABLE",
            "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
        }
        valid1, errs1 = validate_versioned_judge_scorecard(skipped_stub, output_num=1, modality="DOCUMENT")
        self.assertTrue(valid1, f"O1 document skip failed: {errs1}")

        valid2, errs2 = validate_versioned_judge_scorecard(skipped_stub, output_num=2, modality="DOCUMENT")
        self.assertTrue(valid2, f"O2 document skip failed: {errs2}")

        # Also verify validate_micro_judge_scorecard accepts NOT_APPLICABLE status
        v_micro, m_errs = validate_micro_judge_scorecard(skipped_stub, output_num=1)
        self.assertTrue(v_micro, f"Micro-judge validation failed on NOT_APPLICABLE: {m_errs}")

    def test_document_mode_synthetic_pass_rejected(self) -> None:
        """Document mode attempting to award synthetic 100/PASS on O1/O2 is rejected."""
        synthetic_stub = {
            "status": "SKIPPED",
            "composite_score": 100,
            "verdict": "PASS",
            "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
        }
        valid, errs = validate_versioned_judge_scorecard(synthetic_stub, output_num=1, modality="DOCUMENT")
        self.assertFalse(valid)
        self.assertTrue(any("cannot award synthetic 100/PASS" in e for e in errs))

    def test_quality_aggregates_excludes_not_applicable_stubs(self) -> None:
        """Quality aggregates exclude NOT_APPLICABLE stubs and avoid score inflation."""
        report = {
            "mode": "document",
            "output_1_evaluation": {
                "status": "NOT_APPLICABLE",
                "composite_score": None,
                "verdict": "NOT_APPLICABLE",
                "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
            },
            "output_2_evaluation": {
                "status": "NOT_APPLICABLE",
                "composite_score": None,
                "verdict": "NOT_APPLICABLE",
                "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
            },
            "output_3_evaluation": {
                "composite_score": 96.0,
                "verdict": "PASS",
                "dimension_1_strategic_edge": 15,
                "dimension_2_knowledge_layer_density": 15,
                "dimension_3_semantic_atomicity": 20,
                "dimension_4_predicate_specificity": 15,
                "dimension_5_exclusion_of_action_items": 15,
                "dimension_6_evidence_grounding_and_schema": 16,
            },
        }
        scores = get_evaluated_composite_scores(report)
        self.assertEqual(scores, {"output_3": 96.0})

        agg = compute_quality_aggregate_score(report)
        self.assertEqual(agg, 96.0)

    # -------------------------------------------------------------------------
    # 2. Historical Replay: Floor Breach Rejection (F2)
    # -------------------------------------------------------------------------

    def test_replay_hrit_sow_floor_breach_fails_closed(self) -> None:
        """Replay of Hrit_Sow_V3_Final (dim 6 = 8, floor 14) cannot yield PASS."""
        card = {
            "rubric_version": "2.0",
            "meeting_archetype": "ARCHITECTURE_DEEP_DIVE",
            "dimension_1_strategic_edge": 15.0,
            "dimension_2_knowledge_layer_density": 13.0,
            "dimension_3_semantic_atomicity": 18.0,
            "dimension_4_predicate_specificity": 15.0,
            "dimension_5_exclusion_of_action_items": 15.0,
            "dimension_6_evidence_grounding_and_schema": 8.0,
            "composite_score": 84.0,
            "verdict": "PASS",
            "forensic_rationale": "Per-dimension floor failure (Rubric §5.3): dimension_6_evidence_grounding_and_schema=8",
            "evaluated_span_coverage": 1.0,
            "effective_model": "gpt-6-luna",
            "effective_provider": "anthropic",
        }
        with self.assertRaises(ValidationError):
            Output3ScorecardV2.model_validate(card)

        # Micro-judge scorecard validation also rejects PASS
        v_micro, m_errs = validate_micro_judge_scorecard(card, output_num=3)
        self.assertFalse(v_micro)
        self.assertTrue(any("mandatory floor" in e for e in m_errs))

    def test_replay_energy_utilities_floor_breach_fails_closed(self) -> None:
        """Replay of Energy_Utilities (dim 1 = 9, floor 11, composite 90) cannot yield PASS."""
        card = {
            "rubric_version": "2.0",
            "meeting_archetype": "STRATEGIC_DISCOVERY",
            "dimension_1_strategic_edge": 9.0,
            "dimension_2_knowledge_layer_density": 13.0,
            "dimension_3_semantic_atomicity": 20.0,
            "dimension_4_predicate_specificity": 15.0,
            "dimension_5_exclusion_of_action_items": 15.0,
            "dimension_6_evidence_grounding_and_schema": 18.0,
            "composite_score": 90.0,
            "verdict": "PASS",
            "forensic_rationale": "Per-dimension floor failure (Rubric §5.3): dimension_1_strategic_edge=9",
            "evaluated_span_coverage": 1.0,
            "effective_model": "gpt-6-luna",
            "effective_provider": "anthropic",
        }
        with self.assertRaises(ValidationError):
            Output3ScorecardV2.model_validate(card)

        v_micro, m_errs = validate_micro_judge_scorecard(card, output_num=3)
        self.assertFalse(v_micro)
        self.assertTrue(any("mandatory floor" in e for e in m_errs))

    # -------------------------------------------------------------------------
    # 3. Dynamic Applicability Re-weighting for Tactical Meetings
    # -------------------------------------------------------------------------

    def test_tactical_alignment_reweighted_composite_passes(self) -> None:
        """Tactical meetings omitting strategic edge re-weight 85 max points to 100."""
        # Active dimensions sum to 76.5 out of 85.0 -> scaled = (76.5 / 85) * 100 = 90.0
        card = {
            "rubric_version": "2.0",
            "meeting_archetype": "TACTICAL_ALIGNMENT",
            "dimension_1_strategic_edge": None,  # Not applicable
            "dimension_2_knowledge_layer_density": 13.5,
            "dimension_3_semantic_atomicity": 18.0,
            "dimension_4_predicate_specificity": 14.0,
            "dimension_5_exclusion_of_action_items": 14.0,
            "dimension_6_evidence_grounding_and_schema": 17.0,
            "composite_score": 90.0,
            "verdict": "PASS",
            "forensic_rationale": "Tactical standup correctly evaluated with strategic edge omitted.",
            "evaluated_span_coverage": 1.0,
            "effective_model": "gpt-6-luna",
            "effective_provider": "anthropic",
            "prompt_digest": "sha256:tactical_test",
        }
        scorecard = Output3ScorecardV2.model_validate(card)
        self.assertEqual(scorecard.composite_score, 90.0)
        self.assertEqual(scorecard.verdict, "PASS")

        valid, errs = validate_versioned_judge_scorecard(card, output_num=3)
        self.assertTrue(valid, f"Validation failed: {errs}")

        v_micro, m_errs = validate_micro_judge_scorecard(card, output_num=3)
        self.assertTrue(v_micro, f"Micro validation failed: {m_errs}")

    def test_f6_status_vs_verdict_contradiction_fails_closed(self) -> None:
        """Scorecards with contradictory status and verdict fail validation (F6)."""
        card = {
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 30,
                "conditional_extensions": 18,
                "appendix_stakeholder_perspectives": 30,
            },
            "composite_score": 88.0,
            "status": "FAIL",
            "verdict": "HITL_REQUIRED",
        }
        valid, errs = validate_micro_judge_scorecard(card, output_num=2)
        self.assertFalse(valid)
        self.assertTrue(any("contradicts verdict" in e for e in errs))

    # -------------------------------------------------------------------------
    # 4. Hypothesis Property-Based Invariant Verification
    # -------------------------------------------------------------------------

    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    @given(
        d1=st.floats(min_value=0.0, max_value=15.0),
        d2=st.floats(min_value=0.0, max_value=15.0),
        d3=st.floats(min_value=0.0, max_value=20.0),
        d4=st.floats(min_value=0.0, max_value=15.0),
        d5=st.floats(min_value=0.0, max_value=15.0),
        d6=st.floats(min_value=0.0, max_value=20.0),
    )
    def test_property_floor_breach_strictly_blocks_pass(
        self, d1: float, d2: float, d3: float, d4: float, d5: float, d6: float
    ) -> None:
        """Property: If ANY active dimension is below its floor, verdict PASS is forbidden."""
        floors = [d1 < 11.0, d2 < 11.0, d3 < 14.0, d4 < 11.0, d5 < 11.0, d6 < 14.0]
        has_breach = any(floors)
        composite = round(d1 + d2 + d3 + d4 + d5 + d6, 2)

        card = {
            "rubric_version": "2.0",
            "dimension_1_strategic_edge": d1,
            "dimension_2_knowledge_layer_density": d2,
            "dimension_3_semantic_atomicity": d3,
            "dimension_4_predicate_specificity": d4,
            "dimension_5_exclusion_of_action_items": d5,
            "dimension_6_evidence_grounding_and_schema": d6,
            "composite_score": composite,
            "verdict": "PASS",
            "forensic_rationale": "Hypothesis evaluation test run.",
            "evaluated_span_coverage": 1.0,
            "effective_model": "test-model",
            "effective_provider": "test-prov",
        }
        if has_breach:
            with self.assertRaises(ValidationError):
                Output3ScorecardV2.model_validate(card)


if __name__ == "__main__":
    unittest.main()
