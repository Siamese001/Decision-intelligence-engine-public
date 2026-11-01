"""
tests/test_chunk_transcriber_repetition.py

Unit tests for Whisper repetition stutter suppression and prompt echo detection
in dual_pipeline.live_meeting_recorder_core.chunk_transcriber:
1. Validates suppression of autoregressive loop hallucinations (e.g. 'and Jen Pan, and Jen Pan...').
2. Validates prompt echo filtering when Whisper reflects the initial_prompt.
3. Tests local phrase deduplication for duplicate consecutive sentences/clauses.
4. Verifies genuine human dialogue passes cleanly without distortion.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _filter_hallucinations,
    _is_prompt_echo,
    _suppress_repetition_loops,
)


class TestChunkTranscriberRepetition(unittest.TestCase):
    def test_runaway_repetition_loop_suppression(self):
        """Verifies that autoregressive repetition loops (e.g., 50x 'and Jen Pan') are completely suppressed."""
        dirty_chunk = (
            "Ayer, Jen Pan, and Jen Pan, and Jen Pan, and Jen Pan, and Jen Pan, "
            "and Jen Pan, and Jen Pan, and Jen Pan, and Jen Pan, and Jen Pan, "
            "and Jen Pan, and Jen Pan, and Jen Pan, and Jen Pan, and Jen Pan."
        )
        filtered = _filter_hallucinations(dirty_chunk)
        self.assertEqual(filtered, "", "Runaway repetition loop must be completely filtered out")

    def test_single_word_runaway_loop_suppression(self):
        """Verifies that single word repeated >= 4 times consecutively is suppressed."""
        loop_text = "okay okay okay okay okay okay okay okay"
        suppressed = _suppress_repetition_loops(loop_text)
        self.assertEqual(suppressed, "")

    def test_prompt_echo_detection_and_filtering(self):
        """Verifies that prompt echo hallucinations are detected and discarded."""
        prompt = "Ayer, Jen Pan discussing Review LENS designs per Mandy feedback scope, technical architecture, and decisions."
        echo_text = "Ayer, Jen Pan discussing Review LENS designs per Mandy feedback scope, technical architecture, and decisions."

        self.assertTrue(_is_prompt_echo(echo_text, prompt))
        filtered = _filter_hallucinations(echo_text, whisper_prompt=prompt)
        self.assertEqual(filtered, "", "Verbatim prompt echo must be filtered out")

    def test_prompt_echo_with_slight_variation(self):
        """Verifies prompt echo detection even with minor punctuation/case variations."""
        prompt = "Amit Ayer, Mandy discussing LENS design review and decisions."
        echo_text = "Amit Ayer, Mandy discussing LENS design review and decisions!"

        self.assertTrue(_is_prompt_echo(echo_text, prompt))
        filtered = _filter_hallucinations(echo_text, whisper_prompt=prompt)
        self.assertEqual(filtered, "")

    def test_local_duplicate_phrase_deduplication(self):
        """Verifies that consecutive duplicate clauses are collapsed to a single instance."""
        stutter_text = "Ayer, Jen Pan, who are both in the same room, are both in the same room."
        collapsed = _suppress_repetition_loops(stutter_text)
        self.assertEqual(collapsed, "Ayer, Jen Pan, who are both in the same room.")

    def test_clean_dialogue_preserved(self):
        """Verifies that authentic human conversation is preserved without alterations."""
        clean_text = "The writeback architecture should be partitioned through an asynchronous event bus to guarantee isolation."
        prompt = "Amit Ayer, Dan discussing NEA project architecture."
        filtered = _filter_hallucinations(clean_text, whisper_prompt=prompt)
        self.assertEqual(filtered, clean_text)

    def test_standard_hallucinations_still_filtered(self):
        """Verifies that classic YouTube/Whisper subtitle hallucinations remain suppressed."""
        self.assertEqual(_filter_hallucinations("Thank you for watching!"), "")
        self.assertEqual(_filter_hallucinations("Please subscribe to our channel."), "")
        self.assertEqual(_filter_hallucinations("Subtitles by the Amara.org community"), "")


if __name__ == "__main__":
    unittest.main()
