"""Adversarial and boundary tests for Judge Orchestration, Release Policy, and Circuit Breaker (Wave 3)."""
import unittest
from unittest.mock import MagicMock

from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    CriterionApplicability,
    JudgeCriterion,
    validate_output3_scorecard,
)
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.release_policy import ReleaseVerdict
from dual_pipeline.remediation_core.artifact_validator import (
    _check_frozen_content_defects,
    _check_output1_timestamp_order,
)
from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker
from dual_pipeline.transcript_qa_core.evaluator import _eval_output_n, _eval_output1
from dual_pipeline.transcript_stages_core.recap_coherence_gate import (
    parse_recap_sections,
    stitch_recap_sections,
)


class JudgeOrchestrationAdversarialTests(unittest.TestCase):
    """Adversarial and boundary validation for Wave 3 invariants."""

    def test_adversarial_circuit_breaker_empty_or_corrupt_payloads(self):
        """Verify CandidateCircuitBreaker handles malformed or empty payloads gracefully without false collision."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)

        # Non-dict / exception payloads
        exc1 = ValueError("First distinct exception")
        exc2 = RuntimeError("Second distinct exception")

        cb.record_failure(exc1)
        self.assertFalse(cb.tripped)
        self.assertEqual(cb.consecutive_failures, 1)

        cb.record_failure(exc2)
        self.assertFalse(cb.tripped, "Different exceptions must not trip the circuit breaker")
        self.assertEqual(cb.consecutive_failures, 1)

    def test_adversarial_conditional_dimensions_rescaling(self):
        """Verify validate_output3_scorecard rescales composite score when conditional dimensions are absent."""
        # 6 dimensions total in Output 3. If a conditional dimension is omitted/None:
        # dimension_1=15 (max 15), dimension_2=15 (max 15), dimension_3=20 (max 20),
        # dimension_4=15 (max 15), dimension_5=15 (max 15), dimension_6=18 (max 20) -> sum = 98 / 100 = 98.
        # But if dimension_5 (15 pts) is conditional and omitted:
        # sum = 15+15+20+15+18 = 83. max_sum = 15+15+20+15+20 = 85.
        # rescaled = round((83 / 85) * 100, 2) = 97.65.
        scorecard_rescaled = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 18,
            "composite_score": 97,
            "verdict": "PASS",
        }
        res = validate_output3_scorecard(scorecard_rescaled)
        self.assertTrue(res.is_valid, f"Standard Output 3 scorecard must be valid: {res.errors}")
        self.assertEqual(res.validated_composite, 97)

    def test_adversarial_judge_criterion_conditional_applicability(self):
        """Verify JudgeCriterion with CONDITIONAL applicability respects is_conditional_active flag."""
        crit = JudgeCriterion(
            criterion_id="CRIT-COND-01",
            output_num=3,
            title="Conditional Criterion",
            max_points=15,
            applicability=CriterionApplicability.CONDITIONAL,
        )
        self.assertTrue(crit.is_applicable(MeetingArchetype.GENERAL, is_conditional_active=True))
        self.assertFalse(crit.is_applicable(MeetingArchetype.GENERAL, is_conditional_active=False))

    def test_adversarial_hitl_required_cannot_pass_automated_release(self):
        """Verify _eval_output_n and _eval_output1 strictly fail-closed on HITL_REQUIRED and PASS_ADVISORY."""
        # Scorecard with high score (95) but verdict is HITL_REQUIRED
        scorecard_hitl = {
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 35,
                "conditional_extensions": 20,
                "appendix_stakeholder_perspectives": 30,
            },
            "composite_score": 95,
            "verdict": "HITL_REQUIRED",
            "status": "PASS",
        }
        verdict, pass_flag = _eval_output_n(2, scorecard_hitl, score=95, threshold=85)
        self.assertFalse(pass_flag, "HITL_REQUIRED must NEVER set pass_flag to True for automated release")

        # Scorecard with PASS_ADVISORY
        scorecard_advisory = dict(scorecard_hitl)
        scorecard_advisory["verdict"] = "PASS_ADVISORY"
        verdict_adv, pass_flag_adv = _eval_output_n(2, scorecard_advisory, score=95, threshold=85)
        self.assertFalse(pass_flag_adv, "PASS_ADVISORY must NEVER set pass_flag to True for automated release")

        # Output 1 test with HITL_REQUIRED
        o1_hitl = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_speaker_attribution_fidelity": 25,
            "dimension_3_timestamp_and_pause_integrity": 25,
            "dimension_4_dialogue_completeness_and_continuity": 20,
            "composite_score": 95,
            "verdict": "HITL_REQUIRED",
            "status": "PASS",
        }
        v1, pass_1 = _eval_output1(o1_hitl, s1=95)
        self.assertFalse(pass_1, "Output 1 HITL_REQUIRED must NEVER set pass_flag to True")

    def test_adversarial_timestamp_order_detection_across_multiple_lines(self):
        """Verify _check_output1_timestamp_order detects non-monotonic timestamps across distant lines."""
        # Transcript with out-of-order timestamps: [00:05:00] on line 2, [00:02:10] on line 6
        bad_transcript = (
            "## Forensic Transcript\n"
            "[00:01:00] Alice: Welcome everyone.\n"
            "Here is some dialogue that spans multiple lines.\n"
            "[00:05:00] Bob: Let's discuss architecture.\n"
            "More discussion text in between.\n"
            "[00:02:10] Charlie: Wait, I have an earlier question.\n"
        )
        err = _check_output1_timestamp_order(bad_transcript)
        self.assertIsNotNone(err, "Non-monotonic timestamp order across lines MUST be detected")
        self.assertIn("Timestamp out of order", err)

        # Monotonic transcript must pass
        good_transcript = (
            "## Forensic Transcript\n"
            "[00:01:00] Alice: Welcome everyone.\n"
            "[00:02:10] Bob: Let's discuss architecture.\n"
            "[00:05:00] Charlie: Sounds great.\n"
        )
        err_good = _check_output1_timestamp_order(good_transcript)
        self.assertIsNone(err_good, "Monotonic timestamps must pass without error")

    def test_adversarial_recap_coherence_h1_and_h2_stitching(self):
        """Verify stitch_recap_sections handles both H1 and H2 headers cleanly without duplicating titles."""
        narrative_h1 = (
            "# Strategic Overview: Partnership\n\n"
            "This narrative uses H1 headers."
        )
        ledger = "## Action & Governance Ledger\n\n| Item | Owner |\n| --- | --- |\n| Task | Alice |"
        perspectives = "## Detailed Stakeholder Perspectives\n\n### Alice\nNotes."

        stitched = stitch_recap_sections(narrative_h1, ledger, perspectives)
        self.assertIn("# Action & Governance Ledger", stitched)
        self.assertNotIn("## Action & Governance Ledger", stitched)

        # Check slicing with parse_recap_sections
        sections = parse_recap_sections(stitched)
        self.assertTrue(len(sections["2A"]) > 0)
        self.assertTrue(len(sections["2B"]) > 0)
        self.assertTrue(len(sections["2C"]) > 0)


if __name__ == "__main__":
    unittest.main()
