"""tests/end_to_end/test_audio_starvation_recovery_e2e.py

End-to-end integration and adversarial test suite verifying:
1. AudioStarvationDetector triggers AUDIO_STARVATION_ALARM on digital silence (RMS < 0.001 for >=15s).
2. LiveMeetingRecorder captures, logs, and persists audio starvation alarms.
3. Stream drop and recovery: silence gap is strictly categorized as RECORDING_GAP_INTERRUPTION
   and never as NATURAL_SESSION_PAUSE.
"""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioStarvationDetector,
)
from dual_pipeline.transcript_stages_core.silence_gap_classifier import (
    classify_silence_gaps,
)


class TestAudioStarvationRecoveryE2E(unittest.TestCase):
    """End-to-end integration test for audio starvation detection and gap disambiguation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.output_dir = self.base_dir / "output"
        self.cache_dir = self.base_dir / "cache"
        self.chunks_dir = self.base_dir / "chunks"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_wav(self, path: Path, amplitude: int = 1500, duration_sec: float = 5.0) -> None:
        n_samples = int(16000 * duration_sec)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            data = struct.pack(f"{n_samples}h", *[amplitude] * n_samples)
            w.writeframes(data)

    def test_audio_starvation_detector_15s_bound(self) -> None:
        """Acceptance Criteria: Digital silence (RMS < 0.001) triggers starvation alarm within 15 seconds."""
        detector = AudioStarvationDetector(threshold_rms=0.001, starvation_seconds=15.0)
        self.assertFalse(detector.is_starved)

        # Active speech (RMS=0.05, 5s)
        status = detector.record_chunk(rms=0.05, duration_seconds=5.0)
        self.assertEqual(status, "ACTIVE")
        self.assertFalse(detector.is_starved)

        # 10s digital silence (RMS=0.0002) -> below 15s bound -> still ACTIVE
        status = detector.record_chunk(rms=0.0002, duration_seconds=10.0)
        self.assertEqual(status, "ACTIVE")
        self.assertFalse(detector.is_starved)

        # Additional 5s digital silence (RMS=0.0001) -> total 15s -> triggers AUDIO_STARVATION_PAUSE
        status = detector.record_chunk(rms=0.0001, duration_seconds=5.0)
        self.assertEqual(status, "AUDIO_STARVATION_PAUSE")
        self.assertTrue(detector.is_starved)

        # Speech resumes (RMS=0.08, 5s) -> clears starvation
        status = detector.record_chunk(rms=0.08, duration_seconds=5.0)
        self.assertEqual(status, "ACTIVE")
        self.assertFalse(detector.is_starved)

    def test_live_meeting_recorder_starvation_alarm_lifecycle(self) -> None:
        """E2E test: LiveMeetingRecorder detects stream drop, emits AUDIO_STARVATION_ALARM, and recovers."""
        recorder = LiveMeetingRecorder(
            meeting_name="Starvation_Recovery_E2E",
            project_name="test_proj",
            attendees=[{"name": "Rose Teame"}, {"name": "Amit Ayer"}],
            mock_mode=True,
            segment_seconds=5,
        )
        recorder._target_output_dir = self.output_dir
        recorder._target_cache_dir = self.cache_dir
        recorder._target_chunks_dir = self.chunks_dir
        recorder.output_dir = self.output_dir
        recorder.cache_dir = self.cache_dir
        recorder.chunks_dir = self.chunks_dir
        recorder.transcript_md = self.output_dir / "01_transcript.md"
        recorder.session_manifest = self.cache_dir / "session_manifest.json"
        recorder.segments_jsonl = self.cache_dir / "segments.jsonl"
        recorder._init_transcript_file()
        recorder._write_session_manifest(datetime.now())

        # Chunk 0: Speech (5s)
        c0 = self.chunks_dir / "chunk_00000.wav"
        self._create_wav(c0, amplitude=2000, duration_sec=5.0)
        recorder.process_chunk(c0, chunk_idx=0)
        self.assertEqual(len(recorder.starvation_alarms), 0)

        # Chunks 1, 2, 3: Pure digital silence (0 amplitude, 5s each = 15s total)
        for idx in range(1, 4):
            c_silent = self.chunks_dir / f"chunk_{idx:05d}.wav"
            self._create_wav(c_silent, amplitude=0, duration_sec=5.0)
            recorder.process_chunk(c_silent, chunk_idx=idx)

        # At chunk 3, silence reached 15s -> starvation alarm must be emitted
        self.assertGreaterEqual(len(recorder.starvation_alarms), 1)
        latest_alarm = recorder.starvation_alarms[-1]
        self.assertEqual(latest_alarm["event"], "AUDIO_STARVATION_ALARM")
        self.assertLess(latest_alarm["rms"], 0.001)
        self.assertGreaterEqual(latest_alarm["silence_duration_seconds"], 15.0)

        # Chunk 4: Recovery speech (5s)
        c4 = self.chunks_dir / "chunk_00004.wav"
        self._create_wav(c4, amplitude=2500, duration_sec=5.0)
        recorder.process_chunk(c4, chunk_idx=4)
        self.assertFalse(recorder.starvation_detector.is_starved)

        # Stop and seal
        recorder.stop()

        # Verify session manifest contains starvation alarms
        self.assertTrue(recorder.session_manifest.exists())
        manifest = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertIn("audio_starvation_events", manifest)
        self.assertGreaterEqual(len(manifest["audio_starvation_events"]), 1)

        # Verify transcript markdown preserved the starvation gap
        transcript_content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Recording gap:", transcript_content)
        self.assertIn("Audio starvation / dropped stream", transcript_content)

    def test_starvation_gaps_never_categorized_as_natural_pause(self) -> None:
        """Acceptance Criteria: Starvation gaps are never categorized as NATURAL_SESSION_PAUSE.

        Adversarially forces interactive resolver to pick NATURAL_SESSION_PAUSE;
        classifier must refuse and strictly force RECORDING_GAP_INTERRUPTION.
        """
        transcript_text = (
            "# Live Meeting Transcript\n\n"
            "**[00:00]** Rose Teame: Session kickoff.\n\n"
            "[Recording gap: 00:05:00 – 00:10:00 (Audio starvation / dropped stream)]\n\n"
            "**[00:10]** Amit Ayer: Recovery remarks."
        )

        metadata = {
            "project": "test_proj",
            "provider": "offline",
            "allow_autonomous_gaps": True,
            "audio_starvation_events": [
                {
                    "event": "AUDIO_STARVATION_ALARM",
                    "start_sec": 300.0,
                    "time_str": "00:05:00",
                }
            ],
        }

        # Adversarial interactive resolver maliciously trying to force NATURAL_SESSION_PAUSE
        def rogue_resolver(envelope):
            return "NATURAL_SESSION_PAUSE"

        classified_text, meta_out = classify_silence_gaps(
            transcript_text, metadata, interactive_resolver=rogue_resolver
        )

        resolutions = meta_out.get("hitl_resolutions", {})
        gap_res = resolutions.get("gap_000500", {})

        # Invariant check: MUST be RECORDING_GAP_INTERRUPTION
        self.assertIn("RECORDING_GAP_INTERRUPTION", gap_res.get("resolution", ""))
        self.assertNotIn("NATURAL_SESSION_PAUSE", gap_res.get("resolution", ""))
        self.assertIn("[Recording gap: 00:05:00 – 00:10:00 (Audio starvation / dropped stream)]", classified_text)
        self.assertNotIn("[Natural pause:", classified_text)

    def test_unannotated_starvation_gap_detection(self) -> None:
        """E2E test: Unannotated >180s jump correlated with audio starvation events is tagged with starvation."""
        transcript_text = (
            "# Live Meeting Transcript\n\n"
            "**[00:01:00]** Rose Teame: Hello team.\n\n"
            "**[00:06:00]** Amit Ayer: Back after reconnecting."
        )

        metadata = {
            "project": "test_proj",
            "provider": "offline",
            "allow_autonomous_gaps": True,
            "audio_starvation_events": [
                {
                    "event": "AUDIO_STARVATION_ALARM",
                    "start_sec": 120.0,
                    "time_str": "00:02:00",
                }
            ],
        }

        classified_text, _ = classify_silence_gaps(transcript_text, metadata)
        self.assertIn("[Recording gap: 00:01:00 – 00:06:00 (Audio starvation / dropped stream)]", classified_text)


if __name__ == "__main__":
    unittest.main()
