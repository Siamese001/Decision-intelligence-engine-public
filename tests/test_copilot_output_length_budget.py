"""tests/test_copilot_output_length_budget.py

Wave 4: Unit tests verifying Copilot output length budgeting:
- Quoted speaker summary first (<= 50 words)
- Supporting detail bullets underneath (<= 200 words)
- Total actual content ceiling (<= 250 words)
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    MAX_SPEAKER_SUMMARY_WORDS,
    MAX_SUPPORTING_DETAILS_WORDS,
    MAX_TOTAL_CONTENT_WORDS,
    _count_words,
    validate_copilot_feedback,
)


class TestCopilotOutputLengthBudget(unittest.TestCase):
    """Verifies that Copilot output adheres to the 50w quote + 200w bullets <= 250w total contract."""

    def test_standard_output_structure_and_limits(self) -> None:
        """Asserts standard feedback conforms to the 2-tier structure and length budget."""
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "speaker_summary_quotes": '"Can we complete the API gateway validation before expanding into production?"',
            "supporting_details_bullets": [
                "Anchor commitments to an initial grounded-contract proof-of-value spike.",
                "Decouple real-time SCADA via PI Historian replication to isolate EMS.",
                "Risk trap: unscoped milestone extension without funding.",
            ],
            "snap_cue": "Anchor on spike",
            "suggested_script": "Should we phase the charter into an initial grounded-contract spike before locking commitments?",
            "risk_trap": "Scope creep",
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)

        # 1. Speaker Summary First
        summary = validated.get("speaker_summary_quotes", "")
        self.assertTrue(summary.startswith('"') and summary.endswith('"'))
        self.assertLessEqual(_count_words(summary), MAX_SPEAKER_SUMMARY_WORDS)

        # 2. Supporting details in bullets
        bullets = validated.get("supporting_details_bullets", [])
        self.assertTrue(isinstance(bullets, list) and len(bullets) >= 1)
        total_bullet_words = sum(_count_words(b) for b in bullets)
        self.assertLessEqual(total_bullet_words, MAX_SUPPORTING_DETAILS_WORDS)

        # 3. Total content ceiling <= 250 words
        total_words = validated.get("total_content_words", 0)
        self.assertLessEqual(total_words, MAX_TOTAL_CONTENT_WORDS)
        self.assertEqual(total_words, _count_words(summary) + total_bullet_words)

        # 4. Formatted response contains speaker summary first, then supporting details
        fmt = validated.get("formatted_response", "")
        self.assertIn("### Speaker Summary", fmt)
        self.assertIn("### Supporting Details", fmt)
        idx_summary = fmt.index("### Speaker Summary")
        idx_details = fmt.index("### Supporting Details")
        self.assertLess(idx_summary, idx_details)

    def test_overflow_speaker_summary_truncated_to_50_words(self) -> None:
        """Asserts speaker summary exceeding 50 words is cleanly truncated to 50 words."""
        long_quote = '"' + " ".join([f"word{i}" for i in range(80)]) + '"'
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "speaker_summary_quotes": long_quote,
            "supporting_details_bullets": ["Short bullet point."],
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)
        summary = validated["speaker_summary_quotes"]
        self.assertLessEqual(_count_words(summary), MAX_SPEAKER_SUMMARY_WORDS)
        self.assertTrue(summary.endswith('..."') or summary.endswith('"'))

    def test_overflow_supporting_bullets_budgeted_to_200_words(self) -> None:
        """Asserts bullets exceeding 200 words are capped to 200 words maximum."""
        long_bullets = [
            " ".join([f"alpha{i}" for i in range(50)]),
            " ".join([f"beta{i}" for i in range(80)]),
            " ".join([f"gamma{i}" for i in range(100)]),
        ]  # 230 words total
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "speaker_summary_quotes": '"Concise question from the client regarding delivery."',
            "supporting_details_bullets": long_bullets,
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)
        bullets = validated["supporting_details_bullets"]
        total_bullet_words = sum(_count_words(b) for b in bullets)
        self.assertLessEqual(total_bullet_words, MAX_SUPPORTING_DETAILS_WORDS)
        self.assertLessEqual(validated["total_content_words"], MAX_TOTAL_CONTENT_WORDS)


if __name__ == "__main__":
    unittest.main()
