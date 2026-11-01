"""
tests/test_acoustic_pause_continuity.py

Unit and integration tests for Acoustic Pause Continuity and Tightened Gate 1.5 (Rule C-11).
Verifies:
1. Gate 1.5 fails closed on unannotated timestamp gaps > 32s.
2. Gate 1.5 passes on natural pause annotations ([Natural pause: HH:MM:SS – HH:MM:SS (no speech)]).
3. Gate 1.5 passes on standard recording gap annotations ([Recording gap: HH:MM:SS – HH:MM:SS]).
4. LiveMeetingRecorder tracks consecutive silent chunks and emits pause markers for pauses >= 16s.
5. LiveMeetingRecorder flushes pending pauses upon receiving speech or on stop().
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    append_live_transcript_pause,
    format_hh_mm_ss,
)
from dual_pipeline.transcript_qa_core.gate1 import _check_timestamp_continuity


class TestAcousticPauseContinuity(unittest.TestCase):
    """Unit tests for acoustic pause annotation and tightened Gate 1.5."""

    def test_format_hh_mm_ss(self) -> None:
        self.assertEqual(format_hh_mm_ss(0), "00:00:00")
        self.assertEqual(format_hh_mm_ss(24), "00:00:24")
        self.assertEqual(format_hh_mm_ss(64), "00:01:04")
        self.assertEqual(format_hh_mm_ss(3665), "01:01:05")

    def test_gate_1_5_fails_on_unannotated_gap_greater_than_32s(self) -> None:
        """A 48-second gap with sparse speech and no pause notation must fail Gate 1.5."""
        transcript_body = """
### [00:00:16]

**Jake Stark (PartnerAdvisory):** Thank you.

### [00:01:04]

**Jake Stark (PartnerAdvisory):** We can get a better picture here.
"""
        passed, gaps, failures = _check_timestamp_continuity(transcript_body)
        self.assertFalse(passed)
        self.assertEqual(len(gaps), 1)
        self.assertIn("[00:00:16] to [00:01:04] (48s gap)", gaps[0])
        self.assertTrue(any("unannotated recording gap(s) > 32s detected" in f for f in failures))

    def test_gate_1_5_passes_with_natural_pause_annotation(self) -> None:
        """A 48-second gap annotated with [Natural pause: ...] must pass Gate 1.5."""
        transcript_body = """
### [00:00:16]

**Jake Stark (PartnerAdvisory):** Thank you.

[Natural pause: 00:00:24 – 00:01:04 (no speech)]

### [00:01:04]

**Jake Stark (PartnerAdvisory):** We can get a better picture here.
"""
        passed, gaps, failures = _check_timestamp_continuity(transcript_body)
        self.assertTrue(passed)
        self.assertEqual(len(gaps), 0)
        self.assertEqual(len(failures), 0)

    def test_gate_1_5_passes_with_recording_gap_annotation(self) -> None:
        """A large gap annotated with [Recording gap: ...] must pass Gate 1.5."""
        transcript_body = """
### [00:01:00]

**Dan:** Starting session.

[Recording gap: 00:01:05 – 00:09:55]

### [00:10:00]

**Sarah:** Resuming session after break.
"""
        passed, gaps, failures = _check_timestamp_continuity(transcript_body)
        self.assertTrue(passed)
        self.assertEqual(len(gaps), 0)

    def test_gate_1_5_passes_continuous_dialogue_without_gaps(self) -> None:
        """Adjacent turns separated by <= 32s pass without requiring pause markers."""
        transcript_body = """
### [00:00:00]

**Dan:** First statement.

### [00:00:16]

**Sarah:** Immediate response within 16 seconds.

### [00:00:32]

**Dan:** Quick follow-up within 16 seconds.
"""
        passed, gaps, failures = _check_timestamp_continuity(transcript_body)
        self.assertTrue(passed)
        self.assertEqual(len(gaps), 0)

    def test_append_live_transcript_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "transcript.md"
            md_path.write_text("# Meeting\n\n", encoding="utf-8")
            append_live_transcript_pause(md_path, "[00:00:24]", "[00:01:04]")
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("[Natural pause: 00:00:24 – 00:01:04 (no speech)]", content)

    def test_live_meeting_recorder_pause_accumulation_and_flush(self) -> None:
        """Test LiveMeetingRecorder silent chunk accumulation and pause flush."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "output"
            rec = LiveMeetingRecorder(
                project_name="test_proj",
                meeting_name="Test Meeting",
                output_dir=out_dir,
                segment_seconds=8,
                mock_mode=True,
                enable_ws=False,
                enable_copilot=False,
            )
            rec.transcript_md.parent.mkdir(parents=True, exist_ok=True)
            rec.transcript_md.write_text("# Test\n\n", encoding="utf-8")

            # Chunk 1: empty (sec=8)
            rec._handle_empty_chunk_pause(chunk_idx=1, sec=8, time_str="[00:08]", rms=5.0)
            self.assertEqual(rec._silent_chunk_count, 1)
            self.assertEqual(rec._silent_pause_start_sec, 8)
            self.assertEqual(rec._silent_pause_end_sec, 16)

            # Chunk 2: empty (sec=16) -> total 16s accumulated
            rec._handle_empty_chunk_pause(chunk_idx=2, sec=16, time_str="[00:16]", rms=4.0)
            self.assertEqual(rec._silent_chunk_count, 2)
            self.assertEqual(rec._silent_pause_start_sec, 8)
            self.assertEqual(rec._silent_pause_end_sec, 24)

            # Chunk 3: empty (sec=24) -> total 24s accumulated
            rec._handle_empty_chunk_pause(chunk_idx=3, sec=24, time_str="[00:24]", rms=6.0)
            self.assertEqual(rec._silent_chunk_count, 3)
            self.assertEqual(rec._silent_pause_start_sec, 8)
            self.assertEqual(rec._silent_pause_end_sec, 32)

            # Flush when speech arrives
            rec._flush_pending_pause()
            content = rec.transcript_md.read_text(encoding="utf-8")
            self.assertIn("[Natural pause: 00:00:08 – 00:00:32 (no speech)]", content)
            self.assertIsNone(rec._silent_pause_start_sec)
            self.assertEqual(rec._silent_chunk_count, 0)

    def test_live_meeting_recorder_single_empty_chunk_does_not_emit_pause(self) -> None:
        """A single 8s empty chunk is normal conversational pause and does NOT emit a marker."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "output"
            rec = LiveMeetingRecorder(
                project_name="test_proj",
                meeting_name="Test Meeting",
                output_dir=out_dir,
                segment_seconds=8,
                mock_mode=True,
                enable_ws=False,
                enable_copilot=False,
            )
            rec.transcript_md.parent.mkdir(parents=True, exist_ok=True)
            rec.transcript_md.write_text("# Test\n\n", encoding="utf-8")

            # Single empty chunk (8s)
            rec._handle_empty_chunk_pause(chunk_idx=1, sec=8, time_str="[00:08]", rms=5.0)
            rec._flush_pending_pause()

            content = rec.transcript_md.read_text(encoding="utf-8")
            self.assertNotIn("[Natural pause:", content)


if __name__ == "__main__":
    unittest.main()
