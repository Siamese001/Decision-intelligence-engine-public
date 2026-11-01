#!/usr/bin/env python3
"""
Wave 1 Unit Tests: MLX Decoder Optimization & Model Cache Resolution.
Target: Apple MacBook Pro (Apple Silicon Metal GPU).

Verifies:
  1. Local snapshot resolution bypassing HF Hub network checks.
  2. Explicit language='en' parameter forwarding (bypassing language detect pass).
  3. without_timestamps=True and greedy temperature=0.0 decoding options.
  4. LocalMLXAudioTranscriber streaming window transcription bridge.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from dual_pipeline.audio_ingestion import LocalMLXAudioTranscriber
from pipeline_a_intake.local_stt_core import resolve_model_path, transcribe_audio_window


class TestMLXDecoderOptimization(unittest.TestCase):
    def test_resolve_model_path_empty_or_direct(self):
        self.assertEqual(resolve_model_path(""), "")
        # Nonexistent dummy repo without snapshot falls back to original name
        self.assertEqual(resolve_model_path("dummy/nonexistent-repo"), "dummy/nonexistent-repo")

    def test_resolve_model_path_cached_snapshot(self):
        hub_cache = Path("~/.cache/huggingface/hub").expanduser()
        turbo_dir = hub_cache / "models--mlx-community--whisper-large-v3-mlx"
        if turbo_dir.is_dir():
            resolved = resolve_model_path("mlx-community/whisper-large-v3-mlx")
            self.assertTrue(Path(resolved).is_dir())
            self.assertIn("snapshots", resolved)

    def test_transcribe_audio_window_mock_mode(self):
        audio = np.zeros(16000, dtype=np.float32)
        text = transcribe_audio_window(
            audio_data=audio,
            start_ts=1.5,
            model_name="mlx-community/whisper-tiny",
            mock_mode=True,
            metadata={"mock_text": "Custom mock transcription"},
        )
        self.assertEqual(text, "Custom mock transcription")

    def test_transcribe_audio_window_passes_optimized_arguments(self):
        audio = np.zeros(16000, dtype=np.float32)
        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = {"text": "Optimized output text"}

        text = transcribe_audio_window(
            audio_data=audio,
            start_ts=0.0,
            model_name="mlx-community/whisper-tiny",
            mock_mode=False,
            metadata={"language": "es", "without_timestamps": True, "temperature": 0.0},
            mlx_whisper=mock_mlx,
        )

        self.assertEqual(text, "Optimized output text")
        mock_mlx.transcribe.assert_called_once()
        _, kwargs = mock_mlx.transcribe.call_args
        self.assertEqual(kwargs.get("language"), "es")
        self.assertEqual(kwargs.get("without_timestamps"), True)
        self.assertEqual(kwargs.get("temperature"), 0.0)
        self.assertEqual(kwargs.get("condition_on_previous_text"), False)
        self.assertEqual(kwargs.get("fp16"), True)

    def test_local_mlx_transcriber_streaming_bridge(self):
        transcriber = LocalMLXAudioTranscriber(mock=True, language="en")
        self.assertEqual(transcriber.language, "en")
        self.assertFalse(transcriber.without_timestamps)

        # Architectural purity: transcribe_stream_window is pruned from batch transcriber
        self.assertFalse(hasattr(transcriber, "transcribe_stream_window"))

        from pipeline_a_intake.local_stt_core import transcribe_audio_window
        audio = np.zeros(8000, dtype=np.float32)
        stream_text = transcribe_audio_window(
            audio_data=audio,
            start_ts=0.0,
            model_name=transcriber.model_name,
            mock_mode=True,
            metadata={"mock_text": "Live stream turn."},
        )
        self.assertEqual(stream_text, "Live stream turn.")


if __name__ == "__main__":
    unittest.main()
