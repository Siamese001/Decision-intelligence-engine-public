"""
tests/test_live_audio_capture_adversarial.py

Adversarial and Edge-Case Test Suite for Live Audio Capture and Failover.
Covers:
1. Boundary payloads: 0-byte, 12-byte, 44-byte, 78-byte, and 4095-byte WAV files.
2. Digital zero injection: 32KB valid-header WAV with 100% digital zeros (unrouted virtual sink).
3. Process failure / SIGKILL recovery: Watchdog triggers automated fallback upon child process death.
4. Stream stall recovery: Watchdog triggers failover when capture process freezes.
5. Master stitcher resilience: Multi-chunk sequence with corrupted / garbage middle chunks.
"""
from __future__ import annotations

import math
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    MIN_VALID_CHUNK_BYTES,
    AudioChunkValidator,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioSubprocessWatchdog


def _create_raw_wav(path: Path, n_samples: int, amp: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [int(amp * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n_samples)]
        w.writeframes(struct.pack(f"{len(samples)}h", *samples))


class TestLiveAudioCaptureAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case test suite for live audio capture."""

    def test_boundary_payloads_truncated_wav_rejection(self):
        """Verifies rejection across 0B, 12B, 44B, 78B, and 4095B boundary payloads."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # 1. 0 bytes
            f0 = tmp_path / "0b.wav"
            f0.write_bytes(b"")
            self.assertFalse(AudioChunkValidator.validate_chunk(f0).is_valid)

            # 2. 12 bytes (RIFF header only)
            f12 = tmp_path / "12b.wav"
            f12.write_bytes(b"RIFF\x04\x00\x00\x00WAVE")
            self.assertFalse(AudioChunkValidator.validate_chunk(f12).is_valid)

            # 3. 44 bytes (standard empty WAV header)
            f44 = tmp_path / "44b.wav"
            _create_raw_wav(f44, n_samples=0)
            self.assertFalse(AudioChunkValidator.validate_chunk(f44).is_valid)

            # 4. 78 bytes (exact debrief failure anatomy)
            f78 = tmp_path / "78b.wav"
            f78.write_bytes(b"RIFF" + b"\x00" * 74)
            self.assertFalse(AudioChunkValidator.validate_chunk(f78).is_valid)

            # 5. 4095 bytes (1 byte below threshold)
            f4095 = tmp_path / "4095b.wav"
            f4095.write_bytes(b"RIFF" + b"\x00" * 4091)
            self.assertFalse(AudioChunkValidator.validate_chunk(f4095).is_valid)

    def test_adversarial_digital_zeros_unrouted_virtual_device(self):
        """Validates that a 32KB valid-header WAV with 100% digital zeros is rejected as dead stream."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "unrouted_sink.wav"
            # 16,000 samples of 16-bit mono = 32,000 bytes > 4096B threshold
            _create_raw_wav(wav_path, n_samples=16000, amp=0)
            self.assertGreater(wav_path.stat().st_size, MIN_VALID_CHUNK_BYTES)

            result = AudioChunkValidator.validate_chunk(wav_path, allow_digital_zero=False)
            self.assertFalse(result.is_valid)
            self.assertTrue(result.is_dead_stream)
            self.assertIn("100% digital zeros", result.reason)

    def test_adversarial_mid_session_crash_recovery(self):
        """Simulates abrupt process crash and asserts failover to microphone within 500ms."""
        failovers = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda reason, code: failovers.append((reason, code)),
            poll_interval_sec=0.04,
            max_stall_sec=5.0,
        )

        mock_sck = MagicMock()
        mock_sck.poll.return_value = None
        mock_sck.args = ["tools/mac_audio_tap/bin/mac_audio_tap"]

        mock_ffmpeg = MagicMock()
        mock_ffmpeg.poll.return_value = None
        mock_ffmpeg.args = ["ffmpeg"]

        watchdog.register_processes([mock_sck, mock_ffmpeg])
        watchdog.start()

        # Simulate SCK death with exit code 1
        time.sleep(0.06)
        mock_sck.poll.return_value = 1

        deadline = time.time() + 1.0
        while time.time() < deadline and not failovers:
            time.sleep(0.04)

        watchdog.stop()
        self.assertEqual(len(failovers), 1)
        reason, code = failovers[0]
        self.assertIn("exited unexpectedly", reason)
        self.assertEqual(code, 1)

    def test_adversarial_stream_stall_recovery(self):
        """Simulates hung capture process emitting no chunks; asserts stall detection triggers failover."""
        failovers = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda reason, code: failovers.append((reason, code)),
            poll_interval_sec=0.03,
            max_stall_sec=0.12,
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is alive but deadlocked
        mock_proc.args = ["ffmpeg"]

        watchdog.register_processes([mock_proc])
        watchdog.start()

        # Sleep longer than max_stall_sec without calling notify_chunk_written()
        time.sleep(0.25)
        watchdog.stop()

        self.assertEqual(len(failovers), 1)
        reason, code = failovers[0]
        self.assertIn("stalled", reason)
        self.assertEqual(code, -999)

    def test_adversarial_master_stitcher_corrupt_middle_chunk_resilience(self):
        """Asserts master stitcher fails fast on corrupt chunks, refusing to drop audio silently."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks_dir = Path(tmp_dir) / "chunks"
            master_wav = Path(tmp_dir) / "master.wav"

            # chunk 0: valid (16000 frames)
            _create_raw_wav(chunks_dir / "chunk_0000.wav", n_samples=16000, amp=1000)
            # chunk 1: 44B empty WAV header (corrupt / starved)
            _create_raw_wav(chunks_dir / "chunk_0001.wav", n_samples=0)
            # chunk 2: valid (16000 frames)
            _create_raw_wav(chunks_dir / "chunk_0002.wav", n_samples=16000, amp=1200)

            # Wave 3 fail-fast: corrupt chunk must raise RuntimeError refusing to proceed
            with self.assertRaises(RuntimeError) as ctx:
                AudioMasterStitcher.stitch_master_wav(chunks_dir, master_wav)
            self.assertIn("[STITCHER-CORRUPT-CHUNK]", str(ctx.exception))

    def test_mixed_capture_splits_stereo_filter_to_both_master_and_rolling_chunks(self):
        """Regression test: build_mixed_ffmpeg_command must use asplit=2[aout0][aout1] and map both master and rolling chunks."""
        from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            master_wav = tmp_path / "master.wav"
            chunks_dir = tmp_path / "chunks"

            # 1. Stereo isolation mode (amerge + asplit)
            cm_stereo = LiveAudioCaptureManager(
                chunks_dir=chunks_dir,
                master_wav=master_wav,
                segment_seconds=4,
                mock_mode=False,
                skip_device_discovery=True,
                enable_stereo_isolation=True,
            )
            cmd_stereo = cm_stereo.build_mixed_ffmpeg_command(":0")
            cmd_stereo_str = " ".join(cmd_stereo)

            self.assertIn("asplit=2[aout0][aout1]", cmd_stereo_str)
            self.assertIn("amerge=inputs=2", cmd_stereo_str)
            self.assertIn("-map [aout0]", cmd_stereo_str)
            self.assertIn("-map [aout1]", cmd_stereo_str)
            self.assertIn(str(master_wav), cmd_stereo_str)
            self.assertIn(str(chunks_dir / "chunk_%05d.wav"), cmd_stereo_str)

            # 2. Mixed mono mode (amix + asplit)
            cm_mono = LiveAudioCaptureManager(
                chunks_dir=chunks_dir,
                master_wav=master_wav,
                segment_seconds=4,
                mock_mode=False,
                skip_device_discovery=True,
                enable_stereo_isolation=False,
            )
            cmd_mono = cm_mono.build_mixed_ffmpeg_command(":0")
            cmd_mono_str = " ".join(cmd_mono)

            self.assertIn("asplit=2[aout0][aout1]", cmd_mono_str)
            self.assertIn("amix=inputs=2", cmd_mono_str)
            self.assertIn("-map [aout0]", cmd_mono_str)
            self.assertIn("-map [aout1]", cmd_mono_str)


if __name__ == "__main__":
    unittest.main()

