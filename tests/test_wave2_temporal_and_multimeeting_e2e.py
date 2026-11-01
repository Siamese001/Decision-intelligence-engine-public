"""
tests/test_wave2_temporal_and_multimeeting_e2e.py

End-to-end tests exercising Wave 2 Temporal Resolution & Multi-Meeting Detection:
- Filename timestamp parser (_120516.wav -> 12:05:16 PM)
- Session manifest reader for started_at / ended_at / duration
- Derivation of T_end = T_start + audio_duration
- Stage U0 multi-meeting boundary detection on silence gaps and header restarts
- LiveMeetingRecorder inactivity auto-stop and max duration ceiling
- Governed audio slicer tool (tools/slice_and_reingest.py) with container validation
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
import wave
from pathlib import Path

from dual_pipeline.audio_ingestion_core.container_validator import validate_audio_container_headers
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.temporal_resolution import (
    calculate_end_time,
    detect_multi_meeting_boundaries,
    format_time_window,
    parse_filename_timestamp,
    read_adjacent_session_manifest,
    resolve_temporal_window,
)
from tools.slice_and_reingest import process_audio_slices


def create_dummy_wav(path: Path, duration_sec: float = 2.0, sample_rate: int = 16000) -> Path:
    """Generates a valid 16kHz mono PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # 16-bit silence
        wf.writeframes(b"\x00\x00" * n_samples)
    return path


class TestWave2TemporalAndMultiMeetingE2E(unittest.TestCase):
    """E2E suite verifying Wave 2 temporal resolution and multi-meeting capabilities."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_filename_timestamp_parsing_e2e(self):
        """Verifies parsing timestamps from real meeting filenames."""
        # 6-digit HHMMSS
        t1 = parse_filename_timestamp("00_09-25-2026_Google_Alex_Shreyak_Amit_Anya_master_120516.wav")
        self.assertIsNotNone(t1)
        self.assertEqual(t1.hour, 12)
        self.assertEqual(t1.minute, 5)
        self.assertEqual(t1.second, 16)

        # 6-digit with underscores HH_MM_SS
        t2 = parse_filename_timestamp("recording_14_30_00.wav")
        self.assertIsNotNone(t2)
        self.assertEqual(t2.hour, 14)
        self.assertEqual(t2.minute, 30)

        # 4-digit with AM/PM
        t3 = parse_filename_timestamp("session_0915AM.mp3")
        self.assertIsNotNone(t3)
        self.assertEqual(t3.hour, 9)
        self.assertEqual(t3.minute, 15)

        # Derivation of T_end = T_start + duration
        # 12:05:16 + 1407 seconds (23m 27s) -> 12:28:43
        end_t = calculate_end_time(t1, 1407.0)
        self.assertEqual(end_t.hour, 12)
        self.assertEqual(end_t.minute, 28)
        self.assertEqual(end_t.second, 43)

        # Time window formatting
        disp, slug = format_time_window(t1, end_t, tz_name="ET")
        self.assertEqual(disp, "12:05 PM - 12:28 PM ET")
        self.assertEqual(slug, "1205PM_1228PM_ET")

    def test_manifest_temporal_resolution_e2e(self):
        """Verifies session manifest discovery and start/end time extraction."""
        cache_dir = self.tmp_path / "transcript_cache" / "project_nexus" / "09-25-2026"
        cache_dir.mkdir(parents=True, exist_ok=True)

        audio_file = cache_dir / "00_09-25-2026_Google_Alex_master_120516.wav"
        create_dummy_wav(audio_file, duration_sec=600.0)  # 10 minutes

        manifest_file = cache_dir / "00_09-25-2026_Google_Alex_120516_manifest.json"
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump({
                "project": "project_nexus",
                "started_at": "2026-09-25T12:05:16",
                "master_wav": str(audio_file),
            }, f)

        # Test manifest reader
        manifest_meta = read_adjacent_session_manifest(audio_file)
        self.assertIsNotNone(manifest_meta)
        self.assertEqual(manifest_meta["started_at"], "2026-09-25T12:05:16")

        # Test full temporal window resolution
        resolved = resolve_temporal_window(input_path=audio_file)
        self.assertIsNotNone(resolved.start_time)
        self.assertEqual(resolved.start_time.hour, 12)
        self.assertEqual(resolved.start_time.minute, 5)
        self.assertEqual(resolved.start_time.second, 16)
        self.assertEqual(resolved.duration_seconds, 600.0)
        self.assertEqual(resolved.display_window, "12:05 PM - 12:15 PM ET")

    def test_u0_multi_meeting_boundary_detection_e2e(self):
        """Verifies Stage U0 splitting of multi-meeting transcripts based on silence gaps and header restarts."""
        transcript_content = (
            "# Meeting Transcript: Google Discussion\n"
            "Date: 09/25/2026\n\n"
            "[00:00:15] Alex Brennan: Welcome everyone to the Nexus discussion.\n"
            "[00:10:00] Amit Ayer: The architecture is looking solid.\n"
            "[00:23:27] Shreyak Gupta: Let's wrap up this first half.\n\n"
            "[00:23:27 - 00:29:00] [Natural conversation pause: 333s]\n\n"
            "# Meeting Transcript: PartnerAdvisory Partner Review\n"
            "[00:29:05] Mandy: Hello team, starting the SOW discussion now.\n"
            "[00:45:00] Amit Ayer: Let's review the deliverable timeline.\n"
        )

        segments = detect_multi_meeting_boundaries(transcript_content, min_silence_gap_seconds=300.0)
        self.assertEqual(len(segments), 2)

        # Segment 1
        self.assertEqual(segments[0]["segment_index"], 1)
        self.assertEqual(segments[0]["start_timestamp"], "00:00:15")
        self.assertEqual(segments[0]["end_timestamp"], "00:23:27")
        self.assertIn("Alex Brennan", segments[0]["text"])

        # Segment 2
        self.assertEqual(segments[1]["segment_index"], 2)
        self.assertEqual(segments[1]["start_timestamp"], "00:29:05")
        self.assertIn("Mandy", segments[1]["text"])
        self.assertEqual(segments[1]["boundary_trigger"], "SILENCE_GAP_AND_TITLE_RESTART")

    def test_u0_single_meeting_with_subheadings_and_preamble_no_false_positive(self):
        """Verifies that markdown preambles and intra-meeting H2 subheadings do not trigger false multi-meeting boundaries."""
        transcript_content = (
            "# Apex Global Enterprises × PartnerAdvisory — Infrastructure Automation Meeting\n\n"
            "**Meeting date:** October 2, 2026\n"
            "**Recorded transcript range:** 00:00–01:29:06\n"
            "**Source:** Microsoft Teams transcript, corrected against the participant screenshot.\n\n"
            "## Attendee roster (from the screenshot)\n\n"
            "| Display name in Teams | Standardized speaker name | Affiliation |\n"
            "|---|---|---|\n"
            "| Drew Oakley | Drew Oakley | PartnerAdvisory |\n\n"
            "## Full transcript (chronological; no omissions)\n\n"
            "**[00:00] Drew Oakley — PartnerAdvisory**\n\n"
            "Good afternoon.\n\n"
            "**[01:00] Nathan Sutton — NEE**\n\n"
            "Welcome team.\n"
        )
        segments = detect_multi_meeting_boundaries(transcript_content, min_silence_gap_seconds=300.0)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["segment_index"], 1)

    def test_live_meeting_recorder_inactivity_auto_stop_e2e(self):
        """Verifies LiveMeetingRecorder stops when continuous silence exceeds inactivity timeout."""
        output_dir = self.tmp_path / "output"
        recorder = LiveMeetingRecorder(
            project_name="test_project",
            meeting_name="Test Inactivity Session",
            output_dir=output_dir,
            mock_mode=True,
            enforce_worktree=False,
            enable_ws=False,
            inactivity_timeout_seconds=0.1,  # 100ms
        )

        # Simulate at least one audible chunk was processed
        recorder.total_audible_chunks = 1
        recorder.last_audible_monotonic = time.monotonic() - 0.2  # 200ms ago

        recorder.run(max_iterations=5)
        self.assertIsNotNone(recorder.auto_stopped_reason)
        self.assertIn("INACTIVITY_TIMEOUT", recorder.auto_stopped_reason)

    def test_live_meeting_recorder_max_duration_ceiling_e2e(self):
        """Verifies LiveMeetingRecorder stops when elapsed duration exceeds max duration ceiling."""
        output_dir = self.tmp_path / "output"
        recorder = LiveMeetingRecorder(
            project_name="test_project",
            meeting_name="Test Max Duration Session",
            output_dir=output_dir,
            mock_mode=True,
            enforce_worktree=False,
            enable_ws=False,
            max_duration_minutes=0.0001,  # immediate ceiling
        )

        recorder.run(max_iterations=5)
        self.assertIsNotNone(recorder.auto_stopped_reason)
        self.assertIn("MAX_DURATION_EXCEEDED", recorder.auto_stopped_reason)

    def test_audio_slicing_tool_e2e(self):
        """Verifies tools/slice_and_reingest.py accurately slices master audio with container checks."""
        master_wav = self.tmp_path / "00_09-25-2026_Google_Alex_master_120516.wav"
        create_dummy_wav(master_wav, duration_sec=10.0, sample_rate=16000)

        # Pre-validate master container
        spec = validate_audio_container_headers(master_wav)
        self.assertEqual(spec.sample_rate, 16000)

        slices_spec = [
            {
                "start_time": "00:00:00",
                "end_time": "00:00:04",
                "project": "project_nexus",
                "meeting": "Google_Alex_Part1",
                "attendees": "Alex Brennan, Amit Ayer",
            },
            {
                "start_time": "00:00:04",
                "end_time": "00:00:09",
                "project": "project_nexus",
                "meeting": "Google_Alex_Part2",
                "attendees": "Alex Brennan, Amit Ayer",
            },
        ]

        # Dry run preview
        dry_results = process_audio_slices(master_wav, slices_spec, dry_run=True, reingest=True)
        self.assertEqual(len(dry_results), 2)
        self.assertEqual(dry_results[0]["status"], "PLANNED")
        self.assertEqual(dry_results[0]["start_seconds"], 0.0)
        self.assertEqual(dry_results[0]["end_seconds"], 4.0)

        # Actual execution
        out_dir = self.tmp_path / "slices"
        results = process_audio_slices(master_wav, slices_spec, output_dir=out_dir, dry_run=False, reingest=True)
        self.assertEqual(len(results), 2)

        for r in results:
            self.assertEqual(r["status"], "SLICED_AND_READY_FOR_REINGEST")
            out_file = Path(r["output_path"])
            self.assertTrue(out_file.exists())
            # Output container header verification
            out_spec = validate_audio_container_headers(out_file)
            self.assertEqual(out_spec.sample_rate, 16000)
            self.assertEqual(out_spec.channels, 1)
            self.assertIn("nee ingest-meeting", r["reingest_command"])


if __name__ == "__main__":
    unittest.main()
