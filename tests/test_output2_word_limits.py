"""tests/test_output2_word_limits.py

Acceptance tests for Wave 3: Output 02 Executive Recap, C-21 Appendix, and Live Copilot word limits.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    _MAX_SENTENCE_WORDS,
    _check_word_and_sentence_budget,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    MAX_SPEAKER_SUMMARY_WORDS,
    MAX_SUPPORTING_DETAILS_WORDS,
    MAX_TOTAL_CONTENT_WORDS,
)
from dual_pipeline.parameters import (
    compute_output2_duration_word_bounds,
    get_copilot_speech_limits,
    get_output2_c21_speaker_tier_floors,
    get_output2_dag_word_floors,
)
from dual_pipeline.prompts.contracts.output2_contract import (
    validate_c21_depth_floors_and_anchors,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    DEFAULT_MIN_WORDS_NARRATIVE,
    DEFAULT_MIN_WORDS_PERSPECTIVES,
    PASS_2A_SYSTEM_PROMPT,
    PASS_2C_SYSTEM_PROMPT,
)


class TestOutput2WordLimits(unittest.TestCase):
    def test_output2_duration_bounds_and_diagram_bonus(self) -> None:
        """Verify continuous duration bounds formula and diagram bonus."""
        # 30 minutes without diagrams: floor = max(100, min(600, 240)) = 240; ceiling = max(1800, min(5000, 1200)) = 1800
        min_30, max_30, max_e_30, max_t_30 = compute_output2_duration_word_bounds(30, is_tier3=False, has_diagrams=False)
        self.assertEqual(min_30, 240)
        self.assertEqual(max_30, 1800)
        self.assertEqual(max_e_30, 600)
        self.assertEqual(max_t_30, 8000)

        # 30 minutes with diagrams: ceiling receives +600w bonus, exhibit cap is 1200
        min_30_diag, max_30_diag, max_e_30_diag, _ = compute_output2_duration_word_bounds(30, is_tier3=False, has_diagrams=True)
        self.assertEqual(min_30_diag, 240)
        self.assertEqual(max_30_diag, 1800 + 600)
        self.assertEqual(max_e_30_diag, 1200)

        # 60 minutes: floor = 480, ceiling = 2400 (or 3000 with diagrams)
        min_60, max_60, _, _ = compute_output2_duration_word_bounds(60, is_tier3=False, has_diagrams=False)
        self.assertEqual(min_60, 480)
        self.assertEqual(max_60, 2400)

        min_60_diag, max_60_diag, _, _ = compute_output2_duration_word_bounds(60, is_tier3=False, has_diagrams=True)
        self.assertEqual(max_60_diag, 3000)

    def test_c21_speaker_tier_floors_ssot(self) -> None:
        """Verify C-21 speaker tier floors resolve from SSOT."""
        floors = get_output2_c21_speaker_tier_floors()
        self.assertEqual(floors["primary"], (150, 3))
        self.assertEqual(floors["co_lead"], (80, 2))
        self.assertEqual(floors["participant"], (40, 2))
        self.assertEqual(floors["contributor"], (15, 1))

    def test_c21_contract_depth_floors_and_anchors(self) -> None:
        """Verify output2_contract validate_c21_depth_floors_and_anchors enforces SSOT floors."""
        # Valid recap text with actor-action anchor and deep appendix
        valid_recap = (
            "## Key Discussion Points & Strategic Findings\n\n"
            "- **Nathan Sutton defines developer velocity friction:** Detailed technical findings on 50-step workflows.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "### Nathan Sutton - PartnerAdvisory, Solution Principal\n"
            "- **Developer Ergonomics**: Nathan Sutton emphasized workflow ergonomics and IDE integration across platforms.\n"
            "- **Platform Scaling**: Nathan Sutton highlighted horizontal scalability requirements for the data pipeline.\n"
            "- **Security Review**: Nathan Sutton pushed for early security sign-off before pilot deployment phase.\n\n"
            "### Evidence Locks\n"
        )
        meta = {"attendees": ["Nathan Sutton"]}
        is_valid, failures = validate_c21_depth_floors_and_anchors(valid_recap, meta=meta)
        self.assertTrue(is_valid, f"Expected valid recap to pass, got failures: {failures}")
        self.assertEqual(failures, [])

        # Recap with bare persona anchor in Section 3
        bare_anchor_recap = (
            "## Key Discussion Points & Strategic Findings\n\n"
            "- **Nathan Sutton:** Discussion on workflows.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "### Nathan Sutton - PartnerAdvisory, Solution Principal\n"
            "- **Developer Ergonomics**: Nathan Sutton emphasized workflow ergonomics and IDE integration across platforms.\n\n"
            "### Evidence Locks\n"
        )
        is_valid, failures = validate_c21_depth_floors_and_anchors(bare_anchor_recap, meta=meta)
        self.assertFalse(is_valid)
        self.assertTrue(any("SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f for f in failures))

    def test_recap_modular_dag_prompts_interpolate_floors(self) -> None:
        """Verify modular recap DAG system prompts interpolate dynamic word floors."""
        narrative_min, perspectives_min = get_output2_dag_word_floors()
        self.assertEqual(narrative_min, DEFAULT_MIN_WORDS_NARRATIVE)
        self.assertEqual(perspectives_min, DEFAULT_MIN_WORDS_PERSPECTIVES)

        self.assertIn(f"Minimum length is {narrative_min} words.", PASS_2A_SYSTEM_PROMPT)
        self.assertIn(f"Minimum length is {perspectives_min} words.", PASS_2C_SYSTEM_PROMPT)

    def test_copilot_validator_constants_bound_to_ssot(self) -> None:
        """Verify copilot_validator length budgets match SSOT speech limits."""
        speech_limits = get_copilot_speech_limits()
        self.assertEqual(MAX_SPEAKER_SUMMARY_WORDS, speech_limits["speaker_summary_max_words"])
        self.assertEqual(MAX_SUPPORTING_DETAILS_WORDS, speech_limits["supporting_details_max_words"])
        self.assertEqual(MAX_TOTAL_CONTENT_WORDS, speech_limits["total_content_max_words"])
        self.assertEqual(MAX_SPEAKER_SUMMARY_WORDS, 130)
        self.assertEqual(MAX_SUPPORTING_DETAILS_WORDS, 220)
        self.assertEqual(MAX_TOTAL_CONTENT_WORDS, 350)

    def test_copilot_speak_gate_sentence_and_word_budget(self) -> None:
        """Verify copilot_speak_gate enforces SSOT sentence limit (28 words)."""
        self.assertEqual(_MAX_SENTENCE_WORDS, 28)

        # 28-word sentence within 35-word total budget -> passes
        words_28 = ("word " * 28).strip() + "."
        dec_pass = _check_word_and_sentence_budget(words_28, max_words=35, speaker_confidence=0.9)
        self.assertIsNone(dec_pass)

        # 29-word sentence -> blocked
        words_29 = ("word " * 29).strip() + "."
        dec_fail = _check_word_and_sentence_budget(words_29, max_words=35, speaker_confidence=0.9)
        self.assertIsNotNone(dec_fail)
        self.assertFalse(dec_fail.should_speak)


if __name__ == "__main__":
    unittest.main()
