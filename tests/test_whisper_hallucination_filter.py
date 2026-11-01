"""Unit tests for Whisper broadcast caption hallucination filtering
and audio starvation detection.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.audio_capture import AudioStarvationDetector
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import _filter_hallucinations


class WhisperHallucinationFilterTests(unittest.TestCase):
    """Test suite for broadcast caption filtering and audio starvation."""

    def test_scrub_closed_captioning_hallucination(self) -> None:
        """Whisper hallucination 'Closed Captioning provided by the Amit' must be scrubbed to empty."""
        text = "Closed Captioning provided by the Amit"
        filtered = _filter_hallucinations(text)
        self.assertEqual(filtered, "", "Broadcast closed caption hallucination was not scrubbed")

    def test_scrub_subtitles_and_caption_variants(self) -> None:
        """Verify scrubbing across caption variants."""
        cases = [
            "Subtitles by MEMORY_ON_DESK",
            "captions provided by google",
            "closed captions sponsored by",
            "captions by PartnerAdvisory",
            "thank you for watching",
            "Please subscribe to the channel",
        ]
        for c in cases:
            self.assertEqual(_filter_hallucinations(c), "", f"Failed to filter '{c}'")

    def test_preserve_legitimate_speech(self) -> None:
        """Legitimate meeting dialogue must be preserved intact."""
        legit = "We need to align on the statement of work for Gemini Enterprise licenses."
        self.assertEqual(_filter_hallucinations(legit), legit)

    def test_audio_starvation_detector_triggers_after_10s(self) -> None:
        """AudioStarvationDetector triggers AUDIO_STARVATION_PAUSE when low RMS exceeds 10s."""
        detector = AudioStarvationDetector(threshold_rms=0.005, starvation_seconds=10.0)

        # 5 seconds of low RMS -> ACTIVE
        res1 = detector.record_chunk(rms=0.002, duration_seconds=5.0)
        self.assertEqual(res1, "ACTIVE")
        self.assertFalse(detector.is_starved)

        # Another 6 seconds of low RMS (total 11s > 10s) -> AUDIO_STARVATION_PAUSE
        res2 = detector.record_chunk(rms=0.001, duration_seconds=6.0)
        self.assertEqual(res2, "AUDIO_STARVATION_PAUSE")
        self.assertTrue(detector.is_starved)

        # Speech resumes with normal RMS -> resets to ACTIVE
        res3 = detector.record_chunk(rms=0.045, duration_seconds=5.0)
        self.assertEqual(res3, "ACTIVE")
        self.assertFalse(detector.is_starved)


if __name__ == "__main__":
    unittest.main()
