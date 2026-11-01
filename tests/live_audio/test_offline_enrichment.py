"""Tests for Wave 5: Fully Local Metal Whisper (MLX) Offline Enrichment."""

from unittest.mock import patch

import pytest

from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _resolve_effective_backend,
    transcribe_meeting_chunk,
)


def test_effective_backend_defaults_to_local_mlx():
    """Verify backend 'auto' resolves to local Apple Silicon Metal Whisper."""
    backend = _resolve_effective_backend("auto")
    assert backend == "mlx"


def test_cloud_backend_explicitly_blocked():
    """Verify any cloud backend is rejected immediately with a descriptive error."""
    for forbidden in ("openai", "cloud", "OPENAI", "Cloud"):
        with pytest.raises(ValueError, match="strictly prohibited by repository governance"):
            _resolve_effective_backend(forbidden)


def test_mock_mode_chunk_transcription(tmp_path):
    """Verify mock transcription returns deterministic text with zero network calls."""
    fake_wav = tmp_path / "chunk_000.wav"
    fake_wav.touch()

    text = transcribe_meeting_chunk(fake_wav, mock_mode=True)
    assert "Mock transcript segment for chunk_000.wav" in text


def test_mlx_failure_fails_closed_without_cloud_fallback(tmp_path):
    """Verify that an error in local MLX Whisper fails closed and never calls a cloud provider."""
    fake_wav = tmp_path / "corrupt_chunk.wav"
    fake_wav.touch()

    with patch("mlx_whisper.transcribe", side_effect=Exception("Corrupt audio frames")):
        with pytest.raises(RuntimeError, match="Cloud fallback is prohibited"):
            transcribe_meeting_chunk(fake_wav, backend="mlx", mock_mode=False)
