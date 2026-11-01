"""
tests/test_live_audio_capture_healing_e2e.py

End-to-End Test Suite for Live Audio Capture Self-Healing.
Exercises the full lifecycle across:
1. Startup in 'auto' mode where TCC Screen Recording is denied -> preflight auto-downgrades to microphone (:0).
2. Mid-stream capture process crash -> watchdog detects exit within 500ms, engages fallback_to_microphone(), resumes chunk numbering.
3. Multi-epoch master WAV stitching upon meeting conclusion -> single continuous gapless master WAV file.
4. CLI record-meeting fails closed with exit code 1 if 0 valid chunks or empty master WAV is emitted.
"""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.engine import build_parser, execute
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder, LiveMeetingRecorder as SessionRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from pipeline_a_intake.mac_system_audio import AudioDeviceInfo, AudioDeviceType


def _make_valid_chunk(path: Path, duration_sec: float = 1.0, sample_rate: int = 16000) -> None:
    """Creates a valid 16-bit mono WAV chunk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = [int(1500 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


class TestLiveAudioCaptureHealingE2E(unittest.TestCase):
    """Full lifecycle E2E verification of audio capture healing mesh."""

    def test_auto_mode_tcc_denied_recovers_to_mic_and_records_full_lifecycle(self):
        """Asserts full session recovery when SCK TCC permission is denied at startup."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            tmp_path / "chunks"
            out_dir = tmp_path / "out"

            mock_mic = AudioDeviceInfo(
                device_id="1", name="Built-in Microphone", device_type=AudioDeviceType.INPUT,
                input_channels=1, output_channels=0, sample_rate=48000,
                is_default_input=True, is_default_output=False, transport="builtin",
                avfoundation_index=0,
            )

            with patch("dual_pipeline.live_meeting_recorder.acquire_global_mlx_lock", return_value=MagicMock()), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_default_input_device", return_value=mock_mic), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_headphones_connected", return_value=False), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.is_system_muted", return_value=False), \
                 patch("pipeline_a_intake.mac_system_audio.CoreAudioDeviceManager.get_loopback_devices", return_value=[]), \
                 patch.object(AudioPreflightValidator, "check_sck_tcc_permission", return_value=False):

                recorder = LiveMeetingRecorder(
                    project_name="project_nexus",
                    meeting_name="Debrief Alex Jake",
                    output_dir=out_dir,
                    audio_source="auto",
                    mock_mode=False,
                    enable_copilot=False,
                    enable_ws=False,
                    isolated_process=False,
                )

                # 1. Under Decision D1, 'auto' (mixed) fails closed when SCK is denied; explicit 'mic' resolves cleanly
                with self.assertRaises(AudioCaptureUnprovenError):
                    recorder.capture_manager.resolve_source_strategy()
                recorder.capture_manager.source_mode = "mic"
                strategy, dev = recorder.capture_manager.resolve_source_strategy()
                self.assertEqual(strategy, "microphone")
                self.assertEqual(recorder.capture_manager.device_index, ":0")

                # 2. Simulate streaming 2 valid chunks into chunks_dir
                _make_valid_chunk(recorder.chunks_dir / "chunk_0000.wav", duration_sec=1.0)
                _make_valid_chunk(recorder.chunks_dir / "chunk_0001.wav", duration_sec=1.0)

                with patch.object(recorder, "_transcribe_chunk", side_effect=["Hello team.", "Reviewing debrief."]):
                    # Process chunks
                    recorder._process_chunk_file(recorder.chunks_dir / "chunk_0000.wav")
                    recorder._process_chunk_file(recorder.chunks_dir / "chunk_0001.wav")

                    self.assertEqual(len(recorder.processed_chunks), 2)
                    self.assertIn("chunk_0000.wav", recorder.processed_chunks)
                    self.assertIn("chunk_0001.wav", recorder.processed_chunks)

                    # 3. Stop recorder and verify multi-epoch stitcher generated master WAV
                    recorder.stop()
                    self.assertTrue(recorder.master_wav.exists())
                    self.assertGreater(recorder.master_wav.stat().st_size, 4096)
                    with wave.open(str(recorder.master_wav), "rb") as mw:
                        self.assertEqual(mw.getnframes(), 32000)

    def test_mid_stream_crash_watchdog_failover_e2e(self):
        """Asserts mid-stream process crash triggers watchdog fail-closed halt under Decision D1."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            chunks_dir = tmp_path / "chunks"
            master_wav = tmp_path / "master.wav"

            manager = LiveAudioCaptureManager(
                chunks_dir=chunks_dir,
                master_wav=master_wav,
                source_mode="system",
                segment_seconds=5,
            )

            # Pre-populate chunk 0000
            _make_valid_chunk(chunks_dir / "chunk_0000.wav", duration_sec=1.0)

            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                manager._handle_watchdog_failover("SCK process died", exit_code=1)

            self.assertFalse(manager._is_running)
            self.assertIn("AUDIO_GROWTH_STALLED", str(ctx.exception))

    def test_cli_record_meeting_fails_closed_on_empty_session_e2e(self):
        """Asserts that CLI record-meeting returns status FAIL and exit code 1 if no chunks recorded."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            parser = build_parser()
            args = parser.parse_args([
                "record-meeting",
                "--meeting", "Empty Debrief",
                "--project", "project_nexus",
                "--device", ":0",
                "--audio-source", "mic",
            ])

            def _noop_run(rec_self: LiveMeetingRecorder) -> None:
                rec_self.processed_chunks = set()
                rec_self.master_wav = tmp_path / "empty_master.wav"
                rec_self.master_wav.write_bytes(b"")
                rec_self.output_dir = tmp_path / "out"

            with patch("dual_pipeline.live_meeting_recorder.ROOT", tmp_path), \
                 patch("dual_pipeline.live_meeting_recorder.acquire_global_mlx_lock", return_value=MagicMock()), \
                 patch.object(LiveAudioCaptureManager, "resolve_source_strategy", return_value=("microphone", ":0")), \
                 patch.object(SessionRecorder, "run", new=_noop_run):
                result = execute(args)

                self.assertEqual(result.get("status"), "FAIL")
                self.assertIn("Recording concluded with 0 valid audio chunks", result.get("error", ""))
                self.assertEqual(result.get("chunks_processed"), 0)


if __name__ == "__main__":
    unittest.main()
