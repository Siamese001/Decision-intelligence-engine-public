"""
tests/adversarial/test_wave2_temporal_and_multimeeting_adversarial.py

Adversarial and boundary test suite for Wave 2 Temporal Resolution & Multi-Meeting Detection:
- Fail-closed behavior on unresolvable temporal windows (no silent "09:00 AM" defaults)
- Malformed, corrupt, or out-of-bounds filename timestamp tokens
- Inverted slice boundaries (end_time <= start_time) and negative timestamps
- Corrupted or truncated audio container inputs rejected before ffmpeg processing
- Inactivity auto-stop startup grace: zero audible chunks never false-positive stop before speech begins
- Multi-meeting segmentation resilience under corrupted transcripts and time regressions
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.audio_ingestion_core.container_validator import (
    AudioContainerValidationError,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.temporal_resolution import (
    calculate_end_time,
    detect_multi_meeting_boundaries,
    parse_filename_timestamp,
    resolve_temporal_window,
)
from tools.slice_and_reingest import parse_timestamp_to_seconds, process_audio_slices


class TestWave2TemporalAndMultiMeetingAdversarial(unittest.TestCase):
    """Adversarial suite verifying Wave 2 edge-case handling and fail-closed security invariants."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_unresolvable_temporal_window_fails_closed(self):
        """Verifies that absence of temporal evidence results in TIME_UNRESOLVED, never silent defaults."""
        dummy_file = self.tmp_path / "recording_no_time.txt"
        dummy_file.write_text("Plain notes without any timestamps or metadata.", encoding="utf-8")

        resolved = resolve_temporal_window(input_path=dummy_file)
        self.assertIsNone(resolved.start_time)
        self.assertIsNone(resolved.end_time)
        self.assertEqual(resolved.display_window, "TIME_UNRESOLVED")
        self.assertEqual(resolved.resolution_source, "UNRESOLVED")
        # Invariant: hardcoded legacy defaults must NEVER be returned
        self.assertNotIn("09:00 AM", resolved.display_window)
        self.assertNotIn("10:18 AM", resolved.display_window)

    def test_malformed_filename_timestamps_rejected(self):
        """Verifies that out-of-bounds or corrupt timestamp tokens return None, not invalid times."""
        # Hour 99, minute 99, second 99
        self.assertIsNone(parse_filename_timestamp("session_999999.wav"))

        # Hour 25, minute 61, second 99
        self.assertIsNone(parse_filename_timestamp("meeting_256199.wav"))

        # Invalid text inside pattern
        self.assertIsNone(parse_filename_timestamp("meeting_abcdef.wav"))

        # Truncated digits
        self.assertIsNone(parse_filename_timestamp("session_12.wav"))

    def test_negative_and_inverted_timestamps_in_slices(self):
        """Verifies that negative timestamps or inverted intervals raise ValueError."""
        # Negative seconds
        with self.assertRaises(ValueError):
            parse_timestamp_to_seconds(-15.0)

        with self.assertRaises(ValueError):
            parse_timestamp_to_seconds("-00:10:00")

        # Inverted slice (end_time <= start_time)
        dummy_wav = self.tmp_path / "dummy.wav"
        dummy_wav.write_bytes(b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

        inverted_slices = [
            {
                "start_time": "00:15:00",
                "end_time": "00:10:00",  # inverted!
                "meeting": "Test",
            }
        ]
        with self.assertRaises(ValueError) as cm:
            process_audio_slices(dummy_wav, inverted_slices)
        self.assertIn("must be greater than start_time", str(cm.exception))

    def test_audio_slicing_corrupted_input_fails_closed(self):
        """Verifies that malformed or truncated audio input is rejected by ContainerValidator."""
        corrupted_wav = self.tmp_path / "corrupted.wav"
        corrupted_wav.write_bytes(b"RIFFcorruptedpayload1234567890")

        slices = [{"start_time": "00:00:00", "end_time": "00:01:00", "meeting": "Test"}]
        with self.assertRaises(AudioContainerValidationError):
            process_audio_slices(corrupted_wav, slices)

    def test_inactivity_auto_stop_startup_grace_guard(self):
        """Verifies that inactivity auto-stop does NOT trigger before any speech is detected."""
        output_dir = self.tmp_path / "output"
        recorder = LiveMeetingRecorder(
            project_name="test_project",
            meeting_name="Test Startup Session",
            output_dir=output_dir,
            mock_mode=True,
            enforce_worktree=False,
            enable_ws=False,
            inactivity_timeout_seconds=0.05,  # 50ms
        )

        # Zero audible chunks processed so far (e.g. session just started, waiting for attendees)
        recorder.total_audible_chunks = 0
        recorder.last_audible_monotonic = time.monotonic() - 10.0  # 10s ago

        with patch.object(recorder, "_is_chunk_audible", return_value=False):
            recorder.run(max_iterations=3)

        # Should NOT trigger inactivity timeout because speech has not begun yet
        self.assertIsNone(recorder.auto_stopped_reason)

    def test_multi_meeting_detection_under_corrupted_transcript(self):
        """Verifies boundary detector resilience when transcript has garbage or time regressions."""
        transcript = (
            "Garbage line without timestamp\n"
            "[invalid_ts] Speaker A: Hello\n"
            "[00:05:00] Speaker A: Moving on\n"
            "[00:01:00] Speaker B: Time regression backwards\n"
            "[00:25:00 - 00:32:00] [Natural conversation pause: 420s]\n"
            "[00:32:05] Speaker C: Second session شروع\n"
        )
        segments = detect_multi_meeting_boundaries(transcript, min_silence_gap_seconds=300.0)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["segment_index"], 1)
        self.assertEqual(segments[1]["segment_index"], 2)
        self.assertEqual(segments[1]["start_timestamp"], "00:32:05")

    def test_calculate_end_time_boundary_rollover(self):
        """Verifies calculate_end_time handles 24-hour midnight boundary rollover safely."""
        start_t = parse_filename_timestamp("session_235500.wav")
        self.assertIsNotNone(start_t)
        # 23:55:00 + 600s (10 min) -> 00:05:00
        end_t = calculate_end_time(start_t, 600.0)
        self.assertEqual(end_t.hour, 0)
        self.assertEqual(end_t.minute, 5)
        self.assertEqual(end_t.second, 0)


if __name__ == "__main__":
    unittest.main()
