"""tests/end_to_end/test_live_single_capture_e2e.py

End-to-End lifecycle test suite for Wave 5: Capture-Layer Correctness.
Verifies that:
1. Capture-layer uses a single capture process (M4 = 1); secondary InstantAudioSpooler is eliminated from CLI execution.
2. Duplicate master-WAV output is removed from FFmpeg capture commands (AudioMasterStitcher is sole SSOT).
3. Chunk naming format is strictly unified across all strategies to chunk_%05d.wav.
4. Stable directory architecture: recording chunks and active directories remain stable during capture;
   _maybe_materialize_for_chunk returns stable paths without mid-session directory moves.
5. Deferred worktree materialization executes cleanly post-stop (Stage 1).
6. CoreAudio and AVFoundation device discovery caching eliminates redundant CLI/system_profiler spawns.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.engine import execute
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
    CoreAudioDeviceManager,
)


class TestLiveSingleCaptureE2E(unittest.TestCase):
    """End-to-end lifecycle verification for single capture architecture and directory stability."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="single_capture_e2e_"))
        self.output_dir = self.temp_dir / "out"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        CoreAudioDeviceManager.clear_device_cache()
        AudioPreflightValidator.clear_cache()

    def tearDown(self):
        CoreAudioDeviceManager.clear_device_cache()
        AudioPreflightValidator.clear_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_record_meeting_single_capture_no_instant_spooler(self):
        """Invariant: CLI record-meeting does NOT spawn InstantAudioSpooler (M4 = 1)."""
        args = argparse.Namespace(
            command="record-meeting",
            meeting="Wave 5 Single Capture Invariant",
            project="test_proj",
            date="10/04/2026",
            mock=True,
            dry_run=True,
            device=":0",
            segment_seconds=15,
            copilot=False,
            no_copilot=True,
            attendees=None,
            immediate_worktree=False,
            fast_start=True,
            hud=False,
            raw_only=True,
            skip_preflight=True,
            audio_source="microphone",
            whisper_model=None,
            whisper_prompt=None,
            enable_isolated_transcription=False,
            language="en",
            resume=False,
            stereo_isolation=False,
            debug_vad=False,
            stream_transcribe=False,
            use_real_vad=False,
        )

        with patch("dual_pipeline.live_meeting_recorder_core.instant_spooler.InstantAudioSpooler.spawn_instant") as mock_spooler_spawn:
            res = execute(args)
            self.assertEqual(res.get("status"), "PASS")
            self.assertTrue(res.get("dry_run"))
            mock_spooler_spawn.assert_not_called()

    def test_ffmpeg_commands_omit_secondary_master_wav_output(self):
        """Invariant: FFmpeg capture commands do NOT include duplicate master_wav output."""
        chunks_dir = self.temp_dir / "chunks"
        master_wav = self.temp_dir / "master.wav"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        mgr = LiveAudioCaptureManager(
            chunks_dir=chunks_dir,
            master_wav=master_wav,
            source_mode="microphone",
            device_index=":0",
            segment_seconds=15,
            start_chunk_idx=0,
            mock_mode=True,
        )

        with patch("subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture.AudioSubprocessWatchdog"):
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_popen.return_value = mock_proc

            mgr._start_avf_capture(":0")
            self.assertTrue(mock_popen.called)
            call_cmd = mock_popen.call_args[0][0]
            # Ensure master_wav is NOT an output in the ffmpeg command
            self.assertNotIn(str(master_wav), call_cmd)
            # Ensure segment pattern uses %05d
            self.assertTrue(any("chunk_%05d.wav" in arg for arg in call_cmd))

    def test_stable_directory_architecture_during_recording(self):
        """Invariant: Recording directories remain stable; _maybe_materialize_for_chunk never moves files mid-capture."""
        rec = LiveMeetingRecorder(
            meeting_name="Wave 5 Stable Directory Architecture",
            project_name="project_stability",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
            immediate_worktree=False,
        )

        # Staged chunks directory
        original_chunks_dir = rec.chunks_dir
        self.assertTrue(original_chunks_dir.exists())

        # Simulate arrival of audio chunk
        chunk_file = original_chunks_dir / "chunk_00000.wav"
        chunk_file.write_bytes(b"RIFF" + b"\x00" * 40)

        # _maybe_materialize_for_chunk must return the exact stable path without moving directory
        returned_path = rec._maybe_materialize_for_chunk(chunk_file)
        self.assertEqual(returned_path, chunk_file)
        self.assertEqual(rec.chunks_dir, original_chunks_dir)
        self.assertTrue(chunk_file.exists())

    def test_post_stop_stage1_worktree_materialization(self):
        """Invariant: If deferred, worktree materializes cleanly in stop() Stage 1 after capture terminates."""
        rec = LiveMeetingRecorder(
            meeting_name="Wave 5 Post Stop Materialization",
            project_name="project_stability",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
            immediate_worktree=False,
        )

        # Mark 1 chunk as processed
        chunk_name = "chunk_00000.wav"
        chunk_file = rec.chunks_dir / chunk_name
        chunk_file.write_bytes(b"RIFF" + b"\x00" * 100)
        rec.processed_chunks.add(chunk_name)

        with patch.object(rec, "materialize_worktree") as mock_mat, \
             patch.object(rec, "_validate_final_chunk"), \
             patch.object(rec, "_stitch_master_audio"), \
             patch.object(rec, "_cleanup_empty_session_or_seal"):
            rec.worktree_dir = self.temp_dir / ".worktrees" / "dummy_wt"
            rec.worktree_branch = "feat/live-dummy-branch"
            rec._worktree_materialized = False

            rec.stop()

            # Verify materialize_worktree was invoked during stop() Stage 1
            mock_mat.assert_called_once()
            self.assertEqual(rec.drain_receipt["stage"], "drain")
            self.assertEqual(rec.drain_receipt["status"], "COMPLETED")

    def test_coreaudio_and_avfoundation_caching_eliminates_startup_storm(self):
        """Invariant: CoreAudioDeviceManager and AudioPreflightValidator cache device lines with 15s TTL."""
        # 1. CoreAudioDeviceManager caching
        mock_devices = [
            AudioDeviceInfo(
                device_id="builtin_mic",
                name="MacBook Pro Microphone",
                device_type=AudioDeviceType.INPUT,
                avfoundation_index=0,
            )
        ]
        with patch.object(CoreAudioDeviceManager, "_list_devices_via_hal", return_value=mock_devices) as mock_hal, \
             patch.object(CoreAudioDeviceManager, "_enrich_avfoundation_indices"):
            # First call queries HAL and enriches
            devs1 = CoreAudioDeviceManager.list_devices()
            self.assertEqual(len(devs1), 1)
            self.assertEqual(mock_hal.call_count, 1)

            # Second call within 15s TTL returns cached list immediately
            t0 = time.perf_counter()
            devs2 = CoreAudioDeviceManager.list_devices()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertEqual(len(devs2), 1)
            self.assertEqual(mock_hal.call_count, 1)  # No additional HAL calls
            self.assertLess(elapsed_ms, 5.0, f"Cached device lookup took {elapsed_ms:.2f}ms, expected < 5ms")

            # Force refresh bypasses cache
            devs3 = CoreAudioDeviceManager.list_devices(force_refresh=True)
            self.assertEqual(len(devs3), 1)
            self.assertEqual(mock_hal.call_count, 2)

        # 2. AudioPreflightValidator ffmpeg caching
        with patch("subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stderr = (
                "[AVFoundation indev @ 0x123] AVFoundation audio devices:\n"
                "[AVFoundation indev @ 0x123] [0] Built-in Microphone\n"
                "[AVFoundation indev @ 0x123] [1] External Mic"
            )
            mock_run.return_value = mock_res

            val1 = AudioPreflightValidator.validate_avfoundation_index(
                ffmpeg_bin="ffmpeg", index_str=":0"
            )
            self.assertTrue(val1)
            self.assertEqual(mock_run.call_count, 1)

            # Repeated call uses cached lines
            val2 = AudioPreflightValidator.validate_avfoundation_index(
                ffmpeg_bin="ffmpeg", index_str=":1"
            )
            self.assertTrue(val2)
            self.assertEqual(mock_run.call_count, 1)  # No repeated subprocess spawn


if __name__ == "__main__":
    unittest.main()
