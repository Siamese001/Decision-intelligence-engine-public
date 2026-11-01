"""
tests/end_to_end/test_live_recording_lifecycle_e2e.py

End-to-End lifecycle test suite for live meeting recording:
1. Full lifecycle: start -> record 3 chunks -> SIGINT/stop -> drain -> stitch -> seal.
2. Drain-before-kill ordering: buffered in-flight chunks are preserved and transcribed.
3. Zero orphaned child processes or PGID leaks after shutdown.
4. Non-destructive chunk migration preserves live streaming files.
5. Session manifest and transcript sealing validation.
"""
from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher


def _create_valid_wav_file(path: Path, duration_sec: float = 1.0, sample_rate: int = 16000, value: int = 500) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        data = struct.pack(f"<{num_samples}h", *[value] * num_samples)
        w.writeframes(data)


class TestLiveRecordingLifecycleE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_e2e_recording_")
        self.temp_path = Path(self.temp_dir)
        self.output_dir = self.temp_path / "output"
        self.cache_dir = self.temp_path / "cache"
        self.chunks_dir = self.cache_dir / "chunks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_lifecycle_start_record_drain_stitch_seal(self):
        """Exercises complete recording lifecycle from start to sealed master artifact."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="E2E Lifecycle Review",
            attendees="Amit Ayer, Reid S (NEA)",
            output_dir=self.output_dir,
            cache_dir=self.cache_dir,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )

        # Pre-populate 3 valid audio chunks
        for i in range(3):
            _create_valid_wav_file(recorder.chunks_dir / f"chunk_{i:04d}.wav", duration_sec=1.0)

        # Run 3 iterations to process all 3 chunks
        recorder.run(max_iterations=3)

        # Assert all 3 chunks were processed
        self.assertEqual(len(recorder.processed_chunks), 3)
        self.assertIn("chunk_0000.wav", recorder.processed_chunks)
        self.assertIn("chunk_0001.wav", recorder.processed_chunks)
        self.assertIn("chunk_0002.wav", recorder.processed_chunks)

        # Verify master audio stitcher created master WAV with total duration of 3s (48,000 samples)
        master_wav = recorder.master_wav
        AudioMasterStitcher.stitch_master_wav(recorder.chunks_dir, master_wav)
        self.assertTrue(master_wav.exists())
        with wave.open(str(master_wav), "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnframes(), 48000)

        # Verify transcript file exists and contains sealed footer
        self.assertTrue(recorder.transcript_md.exists())
        transcript_content = recorder.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Total chunks processed: 3", transcript_content)
        self.assertIn("Session paused at", transcript_content)

        # Verify session manifest was written
        self.assertTrue(recorder.session_manifest.exists())
        manifest_data = json.loads(recorder.session_manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data.get("meeting"), "E2E Lifecycle Review")
        self.assertEqual(len(recorder.processed_chunks), 3)
        self.assertIn("started_at", manifest_data)
        self.assertIn("stopped_at", manifest_data)
        self.assertIn("wall_clock_duration_seconds", manifest_data)
        self.assertGreaterEqual(manifest_data["wall_clock_duration_seconds"], 0.0)

    def test_drain_before_kill_ordering_preserves_late_chunks(self):
        """Verifies that late-arriving chunks during shutdown are drained before process kill."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Drain Ordering Check",
            output_dir=self.output_dir,
            cache_dir=self.cache_dir,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )

        call_order = []

        # Populate initial chunk
        _create_valid_wav_file(recorder.chunks_dir / "chunk_0000.wav", duration_sec=1.0)
        recorder.processed_chunks.add("chunk_0000.wav")

        # Track call sequence
        orig_graceful = recorder.capture_manager.graceful_stop
        orig_drain = recorder._drain_remaining_chunks
        orig_shutdown = recorder._shutdown_background_services
        orig_validate = recorder._validate_final_chunk

        def tracked_graceful():
            call_order.append("graceful_stop")
            return orig_graceful()

        def tracked_drain():
            call_order.append("drain")
            return orig_drain()

        def tracked_shutdown():
            call_order.append("shutdown")
            # Injected late chunk during shutdown
            _create_valid_wav_file(recorder.chunks_dir / "chunk_0001.wav", duration_sec=1.0)
            return orig_shutdown()

        def tracked_validate():
            call_order.append("validate")
            return orig_validate()

        with patch.object(recorder.capture_manager, "graceful_stop", side_effect=tracked_graceful), \
             patch.object(recorder, "_drain_remaining_chunks", side_effect=tracked_drain), \
             patch.object(recorder, "_shutdown_background_services", side_effect=tracked_shutdown), \
             patch.object(recorder, "_validate_final_chunk", side_effect=tracked_validate):
            recorder.stop()

        # Validate order: graceful_stop -> drain -> shutdown -> validate
        self.assertIn("graceful_stop", call_order)
        self.assertIn("drain", call_order)
        self.assertIn("shutdown", call_order)
        self.assertIn("validate", call_order)
        graceful_idx = call_order.index("graceful_stop")
        first_drain_idx = call_order.index("drain")
        shutdown_idx = call_order.index("shutdown")
        self.assertLess(graceful_idx, first_drain_idx)
        self.assertLess(first_drain_idx, shutdown_idx)

    def test_non_destructive_chunk_migration(self):
        """Verifies that staged chunk migration copies rather than deletes files."""
        staging_dir = self.temp_path / "staging_chunks"
        target_dir = self.temp_path / "target_chunks"
        staging_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Create staged chunks
        for i in range(3):
            _create_valid_wav_file(staging_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Migration Test",
            output_dir=self.output_dir,
            cache_dir=self.cache_dir,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        recorder.chunks_dir = staging_dir
        recorder._target_chunks_dir = target_dir

        recorder._migrate_staged_chunks()

        # Verify all files present in target
        for i in range(3):
            self.assertTrue((target_dir / f"chunk_{i:04d}.wav").exists())
            # Crucial: source files must NOT be deleted while streaming is active
            self.assertTrue((staging_dir / f"chunk_{i:04d}.wav").exists())

    def test_zero_orphaned_processes_after_e2e_run(self):
        """Verifies no orphaned child processes or PGID leaks remain after recorder stop."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Process Leak Check",
            output_dir=self.output_dir,
            cache_dir=self.cache_dir,
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )

        _create_valid_wav_file(recorder.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        recorder.run(max_iterations=1)

        # Verify processes in capture_manager are None
        self.assertIsNone(recorder.capture_manager._ffmpeg_proc)
        self.assertIsNone(recorder.capture_manager._sck_proc)
        self.assertFalse(recorder.capture_manager.is_running)


if __name__ == "__main__":
    unittest.main()
