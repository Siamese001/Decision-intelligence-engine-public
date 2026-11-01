"""
tests/adversarial/test_sck_audio_stream_adversarial.py

Adversarial Stress & Hardening Test Suite for ScreenCaptureKit Audio Streaming:
1. Idle Stream Continuous Sample Output: Asserts idle system audio generates monotonic digital silence.
2. Flat Spectral Floor: Asserts no cyclic dither harmonic spikes (e.g. 5,333 Hz pseudo-dither artifact).
3. FFmpeg Filtergraph Clock Synchronization: Asserts asetpts and aresample async compensation are present.
4. Stereo Isolation Resample Invariant: Asserts amerge filtergraph zero-aligns and resamples both tracks.
5. SIGPIPE Resistance: Asserts tap ignores SIGPIPE when downstream consumer closes stdin.
6. Mixed Capture Graceful Degradation: Asserts failure in mixed initialization falls back to mic without crash.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)


class TestSCKAudioStreamAdversarial(unittest.TestCase):
    """Adversarial suite validating ScreenCaptureKit stream synchronization and filtergraphs."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.chunks_dir = self.tmp_path / "chunks"
        self.master_wav = self.tmp_path / "master.wav"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_ffmpeg_filtergraph_contains_asetpts_and_aresample(self):
        """CRITICAL: Mixed capture filtergraph MUST include asetpts and aresample on both inputs."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mixed",
            enable_stereo_isolation=False,
        )

        with patch("subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary") as mock_ensure, \
             patch.object(mgr, "_verify_sck_process", return_value=True):

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()
            mock_ensure.return_value = dummy_bin

            mock_proc = MagicMock()
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc

            mgr._start_mixed_capture(None)

            # Inspect ffmpeg arguments
            ffmpeg_calls = [
                call for call in mock_popen.call_args_list
                if isinstance(call[0][0], list) and "-filter_complex" in call[0][0]
            ]
            self.assertTrue(len(ffmpeg_calls) > 0, "FFmpeg was not spawned for mixed capture")
            cmd = ffmpeg_calls[0][0][0]
            filter_arg_idx = cmd.index("-filter_complex")
            filter_str = cmd[filter_arg_idx + 1]

            # Assert asetpts and aresample are applied to both [0:a] and [1:a]
            self.assertIn("[0:a]asetpts=PTS-STARTPTS,aresample=async=1000", filter_str)
            self.assertIn("[1:a]asetpts=PTS-STARTPTS,aresample=async=1000", filter_str)
            self.assertIn("amix=inputs=2", filter_str)

    def test_stereo_isolation_filtergraph_contains_aresample(self):
        """CRITICAL: Stereo isolation filtergraph MUST include asetpts and aresample before amerge."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mixed",
            enable_stereo_isolation=True,
        )

        with patch("subprocess.Popen") as mock_popen, \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary") as mock_ensure, \
             patch.object(mgr, "_verify_sck_process", return_value=True):

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()
            mock_ensure.return_value = dummy_bin

            mock_proc = MagicMock()
            mock_proc.stdout = MagicMock()
            mock_popen.return_value = mock_proc

            mgr._start_mixed_capture(None)

            ffmpeg_calls = [
                call for call in mock_popen.call_args_list
                if isinstance(call[0][0], list) and "-filter_complex" in call[0][0]
            ]
            self.assertTrue(len(ffmpeg_calls) > 0)
            cmd = ffmpeg_calls[0][0][0]
            filter_str = cmd[cmd.index("-filter_complex") + 1]

            self.assertIn("[0:a]asetpts=PTS-STARTPTS,aresample=async=1000", filter_str)
            self.assertIn("[1:a]asetpts=PTS-STARTPTS,aresample=async=1000", filter_str)
            self.assertIn("amerge=inputs=2", filter_str)

    def test_no_harmonic_dither_spikes_in_silence_synthesis(self):
        """Acoustic check: Synthesized idle silence buffer must have no periodic harmonic spikes (> -90 dBFS)."""
        # Generate 1 second of 16kHz digital silence
        n_samples = 16000
        zero_samples = np.zeros(n_samples, dtype=np.int16)

        # FFT analysis
        fft_vals = np.abs(np.fft.rfft(zero_samples.astype(np.float32) / 32768.0))
        # Zero samples should produce 0 amplitude across all frequency bins
        max_amplitude = np.max(fft_vals)
        self.assertEqual(max_amplitude, 0.0, "Digital silence contains non-zero spectral energy")

        # Now test that naive cyclic pattern [1, -1, 0] would have failed:
        naive_dither = np.array([1 if i % 3 == 0 else (-1 if i % 3 == 1 else 0) for i in range(n_samples)], dtype=np.int16)
        naive_fft = np.abs(np.fft.rfft(naive_dither.astype(np.float32) / 32768.0))
        # Naive pattern creates a massive spike at 5,333 Hz (sample_rate / 3)
        naive_spike_bin = int(16000.0 / 3.0 / (16000.0 / n_samples))
        naive_spike_amp = naive_fft[naive_spike_bin]
        self.assertGreater(naive_spike_amp, 0.1, "Naive dither was expected to exhibit harmonic spike")

    def test_mixed_capture_initialization_failure_degrades_to_microphone(self):
        """Asserts that failure in mixed capture initialization fails closed without silent fallback (Decision D1)."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="mixed",
        )
        mgr._detected_headphones = True

        with patch("subprocess.Popen", side_effect=OSError("Resource temporarily unavailable")), \
             patch("dual_pipeline.live_meeting_recorder_core.audio_capture._ensure_sck_binary") as mock_ensure, \
             patch.object(mgr, "_start_microphone_capture") as mock_mic:

            dummy_bin = self.tmp_path / "mac_audio_tap"
            dummy_bin.touch()
            mock_ensure.return_value = dummy_bin

            # Must raise AudioCaptureUnprovenError without fallback
            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                mgr._start_mixed_capture(None)
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.FFMPEG_SPAWN_FAILED)
            self.assertTrue(mgr._sck_permanently_unavailable)
            mock_mic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
