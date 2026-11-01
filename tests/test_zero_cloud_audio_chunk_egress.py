"""Unit tests enforcing zero-cloud-audio-egress and strict fail-closed governance for chunk transcription.

Guarantees:
  1. Passing backend="openai" or backend="cloud" raises ValueError immediately.
  2. Local MLX transcription failure raises RuntimeError (no silent cloud fallback).
  3. Missing FFmpeg executable raises RuntimeError (no silent cloud fallback).
  4. LiveMeetingRecorder does not initialize an audio transcription cloud client.
  5. Zero socket/network egress occurs during chunk transcription.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _resolve_effective_backend,
    transcribe_meeting_chunk,
)


def _create_synthetic_wav(
    path: Path,
    duration_sec: float = 0.5,
    sample_rate: int = 16000,
    amplitude: int = 1000,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    num_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_frames):
            val = int(amplitude if (i % 2 == 0) else -amplitude)
            frames.extend(struct.pack("<h", val))
        w.writeframes(frames)


class TestZeroCloudAudioChunkEgress(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.wav_path = self.temp_path / "test_chunk_001.wav"
        _create_synthetic_wav(self.wav_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_openai_backend_rejected_immediately(self) -> None:
        """Verifies that requesting 'openai' or 'cloud' fails closed with ValueError."""
        with self.assertRaises(ValueError) as ctx:
            transcribe_meeting_chunk(self.wav_path, backend="openai")
        self.assertIn("strictly prohibited", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            transcribe_meeting_chunk(self.wav_path, backend="cloud")
        self.assertIn("strictly prohibited", str(ctx.exception).lower())

    def test_mlx_failure_fails_closed_without_fallback(self) -> None:
        """Verifies that MLX transcription error raises RuntimeError and does not swallow or fall back."""
        with mock.patch("mlx_whisper.transcribe", side_effect=RuntimeError("Metal GPU out of memory")):
            with mock.patch("pipeline_a_intake.local_stt_core.resolve_model_path", return_value="/mock/path"):
                with self.assertRaises(RuntimeError) as ctx:
                    transcribe_meeting_chunk(self.wav_path, backend="mlx")
                self.assertIn("Metal GPU out of memory", str(ctx.exception))
                self.assertIn("Cloud fallback is prohibited", str(ctx.exception))

    def test_missing_ffmpeg_fails_closed(self) -> None:
        """Verifies that missing FFmpeg raises RuntimeError rather than falling back to cloud."""
        with mock.patch("shutil.which", return_value=None):
            with mock.patch.object(Path, "exists", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    _resolve_effective_backend("auto")
                self.assertIn("FFmpeg", str(ctx.exception))
                self.assertIn("Cloud fallback is strictly prohibited", str(ctx.exception))

    def test_zero_network_egress_during_transcription(self) -> None:
        """Asserts that socket.socket.connect is never called during chunk transcription."""
        with mock.patch("socket.socket.connect") as mock_connect:
            with mock.patch("mlx_whisper.transcribe", return_value={"text": "Local offline audio."}):
                with mock.patch("pipeline_a_intake.local_stt_core.resolve_model_path", return_value="/mock/path"):
                    result = transcribe_meeting_chunk(self.wav_path, backend="mlx")
                    self.assertEqual(result, "Local offline audio.")
                    mock_connect.assert_not_called()

    def test_live_meeting_recorder_has_no_audio_client(self) -> None:
        """Verifies that LiveMeetingRecorder initializes without an OpenAI audio transcription client."""
        recorder = LiveMeetingRecorder(
            project_name="project_lens",
            meeting_name="Zero Egress Audit",
            meeting_date="09/15/2026",
            attendees="Amit Ayer, Jen Pan",
            output_dir=self.temp_path,
            backend="mlx",
            mock_mode=False,
            enable_ws=False,
            enable_copilot=False,
        )
        self.assertFalse(hasattr(recorder, "client") and recorder.client is not None)

    def test_mock_mode_zero_side_effects(self) -> None:
        """Verifies that mock mode returns deterministic text without invoking hardware or network."""
        result = transcribe_meeting_chunk(self.wav_path, backend="auto", mock_mode=True)
        self.assertEqual(result, f"Mock transcript segment for {self.wav_path.name}.")


if __name__ == "__main__":
    unittest.main()
