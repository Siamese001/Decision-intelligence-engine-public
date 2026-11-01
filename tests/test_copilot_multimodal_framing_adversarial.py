"""tests/test_copilot_multimodal_framing_adversarial.py

Wave 6: Adversarial and Edge-Case Test Suite for Copilot Multimodal Framing,
Response Length Budgeting, Collateral Value Ingestion, and C-29 Firewall.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
from dual_pipeline.attachment_core.value_classifier import (
    CollateralValueTier,
    classify_collateral_value,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    MAX_SPEAKER_SUMMARY_WORDS,
    MAX_SUPPORTING_DETAILS_WORDS,
    MAX_TOTAL_CONTENT_WORDS,
    _count_words,
    validate_copilot_feedback,
)
from dual_pipeline.transcript_qa_core.gate2 import _check_c29_operator_thought_framing_firewall
from dual_pipeline.transcript_qa_core.gate3 import _check_c29_operator_thought_framing_exclusion


class TestCopilotMultimodalFramingAdversarial(unittest.TestCase):
    """Adversarial and boundary test cases for multimodal framing and length budgeting."""

    def setUp(self) -> None:
        self.base_feedback = {
            "type": "COPILOT_FEEDBACK",
            "has_guidance": True,
            "category": "STRATEGIC",
            "speaker_summary_quotes": '"We might need to push API delivery to late November to validate scale."',
            "supporting_details_bullets": [
                "Anchor delivery commitments to an initial grounded-contract spike before locking dates.",
                "Decouple real-time SCADA via PI Historian replication to isolate EMS from risk.",
            ],
            "snap_cue": "Anchor on two-week spike",
            "suggested_script": "Should we phase the charter into an initial grounded-contract spike?",
            "confidence": 0.95,
        }

    def test_copilot_speaker_summary_50_words_boundary(self) -> None:
        """Verifies exact 50-word quote passes and >50-word quote is auto-budgeted to 50 words."""
        # 50 words exactly
        words_50 = " ".join(["word"] * 50)
        fb_50 = dict(self.base_feedback)
        fb_50["speaker_summary_quotes"] = f'"{words_50}"'
        val_50 = validate_copilot_feedback(fb_50)
        self.assertIsNotNone(val_50)
        self.assertEqual(_count_words(val_50["speaker_summary_quotes"]), 50)

        # 80 words overflow
        words_80 = " ".join(["word"] * 80)
        fb_80 = dict(self.base_feedback)
        fb_80["speaker_summary_quotes"] = f'"{words_80}"'
        val_80 = validate_copilot_feedback(fb_80)
        self.assertIsNotNone(val_80)
        summary_count = _count_words(val_80["speaker_summary_quotes"])
        self.assertLessEqual(summary_count, MAX_SPEAKER_SUMMARY_WORDS)
        self.assertTrue(val_80["speaker_summary_quotes"].startswith('"'))
        self.assertTrue(val_80["speaker_summary_quotes"].endswith('"'))

    def test_copilot_supporting_details_200_words_boundary(self) -> None:
        """Verifies exact 200-word bullets pass and >200-word bullets are capped to 200 words."""
        # 4 bullets of 50 words = 200 words
        bullet_words = " ".join(["detail"] * 50)
        fb_200 = dict(self.base_feedback)
        fb_200["supporting_details_bullets"] = [bullet_words] * 4
        val_200 = validate_copilot_feedback(fb_200)
        self.assertIsNotNone(val_200)
        total_200 = sum(_count_words(b) for b in val_200["supporting_details_bullets"])
        self.assertEqual(total_200, 200)

        # 5 bullets of 50 words = 250 words -> capped to 200
        fb_250 = dict(self.base_feedback)
        fb_250["supporting_details_bullets"] = [bullet_words] * 5
        val_250 = validate_copilot_feedback(fb_250)
        self.assertIsNotNone(val_250)
        total_capped = sum(_count_words(b) for b in val_250["supporting_details_bullets"])
        self.assertLessEqual(total_capped, MAX_SUPPORTING_DETAILS_WORDS)

    def test_copilot_total_content_250_words_boundary(self) -> None:
        """Verifies total content ceiling of 250 words across massive overflows."""
        # 80 words quote + 300 words bullets (380 words raw) -> budgeted to <= 250 words total
        fb_overflow = dict(self.base_feedback)
        fb_overflow["speaker_summary_quotes"] = f'"{" ".join(["quote"] * 80)}"'
        fb_overflow["supporting_details_bullets"] = [" ".join(["bullet"] * 75)] * 4
        val = validate_copilot_feedback(fb_overflow)
        self.assertIsNotNone(val)
        self.assertLessEqual(val["total_content_words"], MAX_TOTAL_CONTENT_WORDS)
        quote_words = _count_words(val["speaker_summary_quotes"])
        bullet_words = sum(_count_words(b) for b in val["supporting_details_bullets"])
        self.assertLessEqual(quote_words, 50)
        self.assertLessEqual(bullet_words, 200)
        self.assertEqual(val["total_content_words"], quote_words + bullet_words)

    def test_copilot_speaker_summary_quotes_formatting_adversarial(self) -> None:
        """Verifies unquoted speaker summary is normalized to quotes and non-list bullets are healed."""
        fb_unquoted = dict(self.base_feedback)
        fb_unquoted["speaker_summary_quotes"] = "We might need to push API delivery to late November."
        val = validate_copilot_feedback(fb_unquoted)
        self.assertIsNotNone(val)
        self.assertTrue(val["speaker_summary_quotes"].startswith('"'))
        self.assertTrue(val["speaker_summary_quotes"].endswith('"'))

        # Non-list supporting details healed from alternate fields
        fb_bad_bullets = dict(self.base_feedback)
        fb_bad_bullets["supporting_details_bullets"] = None
        fb_bad_bullets["snap_cue"] = "Healed strategic cue"
        val2 = validate_copilot_feedback(fb_bad_bullets)
        self.assertIsNotNone(val2)
        self.assertTrue(isinstance(val2["supporting_details_bullets"], list))
        self.assertGreaterEqual(len(val2["supporting_details_bullets"]), 1)

    def test_value_classifier_adversarial_inputs(self) -> None:
        """Verifies value classifier handles adversarial, empty, and huge payload inputs safely."""
        att = AttachmentRecord(
            attachment_id="att-adv-001",
            original_path=Path("/tmp/noisy_screenshot.png"),
            staged_path=Path("/tmp/referenced_artifacts/noisy_screenshot.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="noisy_screenshot.png",
            file_size_bytes=1024,
            sha256="noisy123",
            relative_staged_path="referenced_artifacts/noisy_screenshot.png",
        )
        # None content
        res_none = classify_collateral_value(att, None)
        self.assertEqual(res_none.tier, CollateralValueTier.LOW_VALUE)
        self.assertEqual(res_none.score, 0.0)

        # Empty content
        empty_content = ExtractedAttachmentContent(
            attachment_id="att-adv-001",
            filename="noisy_screenshot.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="noisy123",
        )
        res_empty = classify_collateral_value(att, empty_content)
        self.assertEqual(res_empty.tier, CollateralValueTier.LOW_VALUE)

        # Giant non-technical text without components (adversarial text flood)
        flood_text = " ".join(["random noise uninformative log string"] * 5000)
        flood_content = ExtractedAttachmentContent(
            attachment_id="att-adv-001",
            filename="noisy_screenshot.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="noisy123",
            extracted_text=flood_text,
            components=[],
            connections=[],
        )
        res_flood = classify_collateral_value(att, flood_content)
        # Text flood alone without architecture components or connections must not reach HIGH_VALUE
        self.assertNotEqual(res_flood.tier, CollateralValueTier.HIGH_VALUE)

    def test_c29_firewall_adversarial_smuggling_attempts(self) -> None:
        """Verifies attempts to smuggle operator questions past C-29 firewall fail closed."""
        # Attempt 1: Output 02 smuggling query in bullet points under mixed casing
        smuggled_f2 = """# Meeting Objective
Review architecture.

# Key Discussion Points & Findings
- **General Topics**: The team met to align on deliverables.

# Recommendations & Decisions Required
- **Decision**: No decisions ratified.

# Action & Governance Ledger
| Item | Owner | Target Date | Status |
| --- | --- | --- | --- |
| What if we bypass the Zone 4 validation gate to accelerate throughput? | Alex Sanchez | 10/10/2026 | Open |
"""
        metadata = {
            "operator_framing_questions": [
                {"query": "WHAT IF WE BYPASS THE ZONE 4 VALIDATION GATE TO ACCELERATE THROUGHPUT?", "ratified": False}
            ]
        }
        passed2, failures2 = _check_c29_operator_thought_framing_firewall(smuggled_f2, metadata=metadata)
        self.assertFalse(passed2)
        self.assertTrue(any("C29_VIOLATION" in f for f in failures2))

        # Attempt 2: Output 03 smuggling query into risk_updates
        bad_f3: Dict[str, Any] = {
            "schema_version": "5.7",
            "metadata": {"meeting_name": "Test", "date": "09/25/2026"},
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [
                    {
                        "risk_id": "risk:zone4_bypass_inquiry",
                        "description": "What if we bypass the Zone 4 validation gate to accelerate throughput?",
                        "rationale": "Amit's exploratory inquiry during the meeting",
                        "observed_at": "09/25/2026",
                    }
                ],
                "action_updates": [],
            },
        }
        passed3, failures3, _ = _check_c29_operator_thought_framing_exclusion(bad_f3, metadata=metadata)
        self.assertFalse(passed3)
        self.assertTrue(any("C29_VIOLATION" in f for f in failures3))


if __name__ == "__main__":
    unittest.main()
