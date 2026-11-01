"""Unit test suite for FFmpeg live audio capture resilience and stderr logging.

Validates:
1. Resilient mixed filter graph generation:
   - amix non-dropping filter when enable_stereo_isolation=False
   - apad-resilient amerge filter when enable_stereo_isolation=True
2. FFmpeg stderr logging to chunks_dir / capture_stderr.log across all capture strategies.
3. Proper lifecycle cleanup (file handle flush and closure) on stop_processes.
4. Non-dropping stream execution where one audio input terminates early.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from pipeline_a_intake.mac_system_audio import FFMPEG_BIN


class TestFFmpegCaptureResilience(unittest.TestCase):
    """Unit tests for resilient FFmpeg audio mixing and stderr diagnostics."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.chunks_dir = self.tmp_path / "chunks"
        self.master_wav = self.tmp_path / "master.wav"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_mixed_capture_amix_filter_graph_and_stderr(self) -> None:
        """Assert amix filter graph is selected when enable_stereo_isolation=False and stderr logged."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mixed",
            enable_stereo_isolation=False,
        )

        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture.subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary") as mock_ensure, \
             patch.object(mgr, "_verify_sck_process", return_value=True):

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()
            mock_ensure.return_value = dummy_bin

            mock_sck = MagicMock()
            mock_sck.stdout = MagicMock()
            mock_ffmpeg = MagicMock()
            mock_popen.side_effect = [mock_sck, mock_ffmpeg]

            mgr._start_mixed_capture(None)

            # Inspect ffmpeg call
            self.assertEqual(mock_popen.call_count, 2)
            ffmpeg_call = mock_popen.call_args_list[1]
            cmd = ffmpeg_call[0][0]
            kwargs = ffmpeg_call[1]

            # Verify amix filtergraph
            filter_arg_idx = cmd.index("-filter_complex")
            filter_str = cmd[filter_arg_idx + 1]
            self.assertIn("amix=inputs=2:duration=longest:dropout_transition=0:normalize=0:weights=1 1[aout]", filter_str)

            # Verify stderr file handle
            stderr_arg = kwargs.get("stderr")
            self.assertIsNotNone(stderr_arg)
            self.assertFalse(stderr_arg.closed)
            self.assertEqual(Path(stderr_arg.name).name, "capture_stderr.log")

            mgr.stop_processes()
            self.assertTrue(stderr_arg.closed)
            self.assertIsNone(mgr._ffmpeg_stderr_file)

    def test_mixed_capture_stereo_isolation_apad_filter_graph(self) -> None:
        """Assert apad is applied to both streams before amerge when enable_stereo_isolation=True."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mixed",
            enable_stereo_isolation=True,
        )

        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture.subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary") as mock_ensure, \
             patch.object(mgr, "_verify_sck_process", return_value=True):

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()
            mock_ensure.return_value = dummy_bin

            mock_sck = MagicMock()
            mock_sck.stdout = MagicMock()
            mock_ffmpeg = MagicMock()
            mock_popen.side_effect = [mock_sck, mock_ffmpeg]

            mgr._start_mixed_capture(None)

            ffmpeg_call = mock_popen.call_args_list[1]
            cmd = ffmpeg_call[0][0]
            filter_str = cmd[cmd.index("-filter_complex") + 1]

            # Verify both inputs are padded with apad before amerge to prevent early termination
            self.assertIn("[0:a]asetpts=PTS-STARTPTS,aresample=async=1000:first_pts=0:min_hard_comp=0.100000,aformat=channel_layouts=mono,apad[a0]", filter_str)
            self.assertIn("[1:a]asetpts=PTS-STARTPTS,aresample=async=1000:first_pts=0:min_hard_comp=0.100000,aformat=channel_layouts=mono,apad[a1]", filter_str)
            self.assertIn("[a0][a1]amerge=inputs=2[aout]", filter_str)

            mgr.stop_processes()

    def test_sck_capture_stderr_logging(self) -> None:
        """Assert SCK-only capture routes FFmpeg stderr to capture_stderr.log."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="system",
        )

        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture.subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary") as mock_ensure, \
             patch.object(mgr, "_verify_sck_process", return_value=True):

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()
            mock_ensure.return_value = dummy_bin

            mock_sck = MagicMock()
            mock_sck.stdout = MagicMock()
            mock_ffmpeg = MagicMock()
            mock_popen.side_effect = [mock_sck, mock_ffmpeg]

            mgr._start_sck_capture()

            ffmpeg_call = mock_popen.call_args_list[1]
            kwargs = ffmpeg_call[1]
            stderr_arg = kwargs.get("stderr")
            self.assertIsNotNone(stderr_arg)
            self.assertEqual(Path(stderr_arg.name).name, "capture_stderr.log")

            mgr.stop_processes()
            self.assertTrue(stderr_arg.closed)

    def test_avf_capture_stderr_logging(self) -> None:
        """Assert AVFoundation capture routes FFmpeg stderr to capture_stderr.log."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mic",
        )

        with patch("dual_pipeline.live_meeting_recorder_core.audio_capture.subprocess.Popen") as mock_popen:
            mock_ffmpeg = MagicMock()
            mock_popen.return_value = mock_ffmpeg

            mgr._start_avf_capture(":0")

            ffmpeg_call = mock_popen.call_args_list[0]
            kwargs = ffmpeg_call[1]
            stderr_arg = kwargs.get("stderr")
            self.assertIsNotNone(stderr_arg)
            self.assertEqual(Path(stderr_arg.name).name, "capture_stderr.log")

            mgr.stop_processes()
            self.assertTrue(stderr_arg.closed)

    def test_ffmpeg_execution_resilience_with_early_drop(self) -> None:
        """Functional execution test: Run actual FFmpeg with simulated single-stream dropout."""
        if not Path(FFMPEG_BIN).exists():
            self.skipTest(f"FFmpeg binary not found at {FFMPEG_BIN}")

        log_path = self.chunks_dir / "capture_stderr.log"
        out_wav = self.tmp_path / "test_out.wav"

        # Input 0 lasts 1 second; Input 1 lasts 3 seconds.
        # With apad or amix duration=longest, the output must survive for the full 3 seconds.
        resample = "asetpts=PTS-STARTPTS,aresample=async=1000:first_pts=0:min_hard_comp=0.100000,aformat=channel_layouts=mono"
        filter_str = f"[0:a]{resample},apad[a0];[1:a]{resample},apad[a1];[a0][a1]amerge=inputs=2[aout]"

        with open(log_path, "a", encoding="utf-8") as stderr_file:
            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=500:duration=3",
                "-filter_complex", filter_str,
                "-map", "[aout]",
                "-ar", "16000", "-ac", "2",
                "-t", "3",
                str(out_wav),
            ]
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=stderr_file, check=False)

        self.assertEqual(res.returncode, 0, "FFmpeg failed despite apad resilience")
        self.assertTrue(out_wav.exists(), "Output WAV was not created")
        self.assertGreater(out_wav.stat().st_size, 10000, "Output WAV is truncated")
        self.assertTrue(log_path.exists(), "Stderr log was not written")
        self.assertGreater(log_path.stat().st_size, 0, "Stderr log is empty")


if __name__ == "__main__":
    unittest.main()
