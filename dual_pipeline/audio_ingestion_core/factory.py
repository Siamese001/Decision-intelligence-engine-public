"""Factory for instantiating default audio transcribers."""
from __future__ import annotations

from typing import Optional

from .acoustic_airgap import is_mlx_available
from .base import BaseAudioTranscriber
from .mlx_transcriber import LocalMLXAudioTranscriber


def get_default_transcriber(
    mock: bool = False,
    backend: str = "auto",
    model_name: Optional[str] = None,
) -> BaseAudioTranscriber:
    """
    Factory returning the preferred 100% on-premise audio transcriber.
    Supports MLX Metal GPU ('mlx') as well as local CPU/MPS non-MLX ('faster-whisper', 'whisperx', 'cpu').
    """
    norm_backend = (backend or "auto").strip().lower().replace("_", "-")
    if norm_backend not in ("auto", "mlx", "faster-whisper", "whisperx", "cpu"):
        raise ValueError(
            f"Invalid transcriber backend '{backend}'. Supported local airgapped backends: "
            "'auto', 'mlx', 'faster-whisper', 'whisperx', 'cpu'."
        )
    if norm_backend in ("faster-whisper", "whisperx", "cpu") or (
        norm_backend == "auto" and not mock and not is_mlx_available()
    ):
        from ..audio_ingestion import (
            LocalFasterWhisperTranscriber,
            WhisperXAudioTranscriber,
        )
        if norm_backend == "whisperx":
            return WhisperXAudioTranscriber(model_size=model_name, mock=mock)
        return LocalFasterWhisperTranscriber(model_size=model_name, mock=mock)
    return LocalMLXAudioTranscriber(model_name=model_name, mock=mock)

