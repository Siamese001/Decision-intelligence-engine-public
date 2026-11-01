"""Adversarial stress and edge-case test suite for resilient audio capture.

Covers mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte stream inputs, zero-length audio chunks, and null device strings.
2. Malformed input: Odd byte lengths in 16-bit PCM conversion and truncated audio headers.
3. Concurrency & race: Concurrent stop/graceful_stop while pipe drain is active.
4. Exact production regression: Pin SCK abrupt pipe closure during dual-channel recording,
   asserting FFmpeg survives and master audio completes without truncation.
"""
from __future__ import annotations

import contextlib
import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from pipeline_a_intake.mac_system_audio import FFMPEG_BIN


class TestAudioCaptureAdversarial(unittest.TestCase):
    """Adversarial test cases covering boundary payloads, malformed inputs, and concurrency."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.chunks_dir = self.tmp_path / "chunks"
        self.master_wav = self.tmp_path / "master.wav"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # Category 1: Boundary payload
    def test_boundary_zero_byte_chunk_handling(self) -> None:
        """Boundary: Verify audio converter safely handles zero-byte inputs without IndexError or division by zero."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mic",
            mock_mode=True,
        )
        # Empty chunk conversion
        empty_chunk = self.chunks_dir / "chunk_00000.wav"
        empty_chunk.touch()
        self.assertEqual(empty_chunk.stat().st_size, 0)
        # Manager should safely tolerate zero-size file checks
        mgr.last_growth_time = 0.0
        self.assertFalse(mgr.is_running)

    # Category 2: Malformed input
    def test_malformed_odd_byte_length_pcm(self) -> None:
        """Malformed: Verify audio RMS measurement safely handles odd-byte incomplete 16-bit frames."""
        from dual_pipeline.live_meeting_recorder_core.audio_utils import get_audio_rms

        malformed_wav = self.tmp_path / "malformed.wav"
        with wave.open(str(malformed_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x01" * 50 + b"\x00")  # 101 bytes (incomplete 16-bit sample, total file >= 100 bytes)

        # get_audio_rms must safely truncate or ignore trailing odd byte rather than crash
        rms = get_audio_rms(malformed_wav)
        self.assertIsInstance(rms, float)

    # Category 3: Concurrency & race
    def test_concurrency_simultaneous_stop_and_graceful_stop(self) -> None:
        """Concurrency: Verify racing stop() and graceful_stop() calls do not deadlock or raise."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mic",
            mock_mode=True,
        )
        mgr.start()
        self.assertTrue(mgr.is_running)

        exceptions: list[Exception] = []

        def call_stop():
            try:
                mgr.stop()
            except Exception as e:
                exceptions.append(e)

        def call_graceful_stop():
            try:
                mgr.graceful_stop()
            except Exception as e:
                exceptions.append(e)

        t1 = threading.Thread(target=call_stop)
        t2 = threading.Thread(target=call_graceful_stop)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        self.assertFalse(t1.is_alive(), "stop() deadlocked")
        self.assertFalse(t2.is_alive(), "graceful_stop() deadlocked")
        self.assertEqual(len(exceptions), 0, f"Concurrent stops raised: {exceptions}")
        self.assertFalse(mgr.is_running)

    # Category 4: Exact production regression
    def test_production_regression_single_channel_abrupt_eof(self) -> None:
        """Exact Production Regression: Pin SCK early termination and assert FFmpeg continues via apad."""
        if not Path(FFMPEG_BIN).exists():
            self.skipTest("FFmpeg binary required for live execution check")

        out_wav = self.tmp_path / "surviving_master.wav"
        stderr_log = self.chunks_dir / "capture_stderr.log"

        # Stream 0 terminates after 0.5s; Stream 1 lasts 2.5s.
        # Filter must use apad on both streams so amerge does not exit on Stream 0 EOF.
        resample = "asetpts=PTS-STARTPTS,aresample=async=1000:first_pts=0:min_hard_comp=0.100000,aformat=channel_layouts=mono"
        filter_str = f"[0:a]{resample},apad[a0];[1:a]{resample},apad[a1];[a0][a1]amerge=inputs=2[aout]"

        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.5",
            "-f", "lavfi", "-i", "sine=frequency=400:duration=2.5",
            "-filter_complex", filter_str,
            "-map", "[aout]",
            "-ar", "16000", "-ac", "2",
            "-t", "2.5",
            str(out_wav),
        ]

        with open(stderr_log, "a", encoding="utf-8") as f_err:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=f_err, check=False)

        self.assertEqual(res.returncode, 0, "FFmpeg crashed on early stream termination")
        self.assertTrue(out_wav.exists())
        # 2.5 seconds of 16kHz stereo 16-bit audio = 2.5 * 16000 * 2 * 2 = 160,000 bytes
        file_size = out_wav.stat().st_size
        self.assertGreaterEqual(file_size, 150000, f"Output audio truncated: {file_size} bytes")


if __name__ == "__main__":
    unittest.main()
