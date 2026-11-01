"""Unit tests for Whisper repetition suppression, prompt hallucination defense, and cross-chunk loop filtering."""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.audio_utils import is_cross_chunk_repetition
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _filter_hallucinations,
    _has_runaway_loop,
    _is_prompt_echo,
    _suppress_repetition_loops,
)


class TestWhisperRepetitionSuppression(unittest.TestCase):
    """Test suite verifying robust defense against Whisper decoding loops and prompt echoes."""

    def test_prompt_echo_exact_and_fuzzy(self):
        prompt = "Amit Ayer discussing Project Beacon Sprint Demo scope, technical architecture, and decisions."

        # Exact or near-exact echo
        self.assertTrue(_is_prompt_echo("Amit Ayer discussing Project Beacon Sprint Demo scope, technical architecture, and decisions.", prompt))
        self.assertTrue(_is_prompt_echo("Ayer discussing Project Beacon Sprint Demo scope, technical architecture, and decisions", prompt))

        # Prompt-derived hallucination on low audio
        self.assertTrue(_is_prompt_echo("Ayer discussing Digital Shredder, and decisions.", prompt))
        self.assertTrue(_is_prompt_echo("Ayer discussing Digital Shrink, and decisions.", prompt))
        self.assertTrue(_is_prompt_echo("Ayer discussing Digital Shredder", prompt))

        # Genuine speech should not be flagged as echo
        self.assertFalse(_is_prompt_echo("We need to complete the migration of the database tables by Friday.", prompt))
        self.assertFalse(_is_prompt_echo("Let's review the API architecture and discuss integration points.", prompt))

    def test_hallucination_phrases_filtered(self):
        self.assertEqual(_filter_hallucinations("Thanks for watching!"), "")
        self.assertEqual(_filter_hallucinations("Thank you for watching."), "")
        self.assertEqual(_filter_hallucinations("Please subscribe to our channel."), "")
        self.assertEqual(_filter_hallucinations("Subtitles by the team."), "")
        self.assertEqual(_filter_hallucinations("Thank you for listening."), "")

    def test_runaway_loop_detection(self):
        # Repeated phrase within single utterance
        loop_text = "Ayer's presentation on the future of digital media and the future of digital media in the future."
        words = loop_text.split()
        self.assertTrue(_has_runaway_loop(words))
        self.assertEqual(_suppress_repetition_loops(loop_text), "")

        # Normal dialogue without runaway loops
        normal_text = "The team reviewed the architecture document and verified that all tests passed successfully."
        self.assertFalse(_has_runaway_loop(normal_text.split()))
        self.assertEqual(_suppress_repetition_loops(normal_text), normal_text)

    def test_cross_chunk_repetition_suppression(self):
        history = [
            "We are reviewing the Project Beacon sprint demo today.",
            "David walked through the architecture diagrams.",
        ]

        # First appearance of a turn is allowed
        self.assertFalse(is_cross_chunk_repetition("Thank you.", history))

        # Immediate consecutive repetition of short phrase is suppressed
        history.append("Thank you.")
        self.assertTrue(is_cross_chunk_repetition("Thank you.", history))

        # Hallucinatory prompt phrase repeating
        history2 = [
            "Ayer discussing Digital Shredder, and decisions.",
        ]
        self.assertTrue(is_cross_chunk_repetition("Ayer discussing Digital Shrink, and decisions.", history2))

    def test_conversational_backchannel_protection(self):
        """Conversational affirmations ('Yeah.', 'Right.') must not be dropped on a single repeat."""
        history = ["Yeah."]
        self.assertFalse(is_cross_chunk_repetition("Yeah.", history))
        self.assertFalse(is_cross_chunk_repetition("Right.", ["Right."]))
        self.assertFalse(is_cross_chunk_repetition("Okay.", ["Okay."]))

        # But runaway backchannel looping (>= 3 consecutive identical) must be suppressed
        runaway_history = ["Yeah.", "Yeah."]
        self.assertTrue(is_cross_chunk_repetition("Yeah.", runaway_history))

    def test_long_verbatim_loop_and_vocabulary_drift(self):
        """Loops > 20 words and near-verbatim drift (>85% Jaccard similarity) must be caught."""
        long_loop = "We are performing a comprehensive architectural audit of all microservice endpoints to confirm zero regressions before promotion."
        self.assertTrue(is_cross_chunk_repetition(long_loop, [long_loop]))

        # Near verbatim drift with 1 word appended
        drift_loop = long_loop + " today."
        self.assertTrue(is_cross_chunk_repetition(drift_loop, [long_loop]))

        # Distinct long utterances should not be flagged
        different_utterance = "David presented the updated timeline for user acceptance testing and team remediation efforts."
        self.assertFalse(is_cross_chunk_repetition(different_utterance, [long_loop]))


if __name__ == "__main__":
    unittest.main()
