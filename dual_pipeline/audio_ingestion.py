"""Governed Audio Ingestion and Diarization Pipeline.

Backward-compatibility facade delegating to `dual_pipeline.audio_ingestion_core`.
"""
from __future__ import annotations

from .audio_ingestion_core import (
    _CHUNK_DURATION_SECONDS,
    _MAX_FILE_SIZE_BYTES,
    _OVERLAP_SECONDS,
    BaseAudioTranscriber,
    LocalMLXAudioTranscriber,
    clean_speaker_label,
    detect_silence_cutpoints,
    ensure_ffmpeg,
    get_audio_duration,
    get_default_transcriber,
    is_mlx_available,
    slice_audio_chunk,
    stitch_and_align_segments,
)
from .model_registry import ModelTier, resolve_tier_model_name


class LocalFasterWhisperTranscriber(LocalMLXAudioTranscriber):
    """Local CPU/MPS Faster-Whisper / WhisperX audio transcriber for non-MLX execution."""

    def __init__(
        self,
        model_size: str | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
        mock: bool = False,
    ):
        resolved_model = model_size or resolve_tier_model_name(ModelTier.ACOUSTIC_FASTER_WHISPER)
        super().__init__(model_name=resolved_model, mock=mock)
        self.model_size = resolved_model
        self.device = device
        self.compute_type = compute_type
        self.backend = "faster-whisper"


class WhisperXAudioTranscriber(LocalMLXAudioTranscriber):
    """Local WhisperX audio transcriber for non-MLX execution."""

    def __init__(
        self,
        api_key: str | None = None,
        model_size: str | None = None,
        mock: bool = False,
    ):
        resolved_model = model_size or resolve_tier_model_name(ModelTier.ACOUSTIC_WHISPERX)
        super().__init__(api_key=api_key, model_name=resolved_model, mock=mock)
        self.api_key = api_key
        self.model_size = resolved_model
        self.backend = "whisperx"


class OpenAIAudioTranscriber(LocalMLXAudioTranscriber):
    """Backward compatibility alias delegating to LocalMLXAudioTranscriber."""

    def __init__(self, api_key: str | None = None, model: str | None = None, mock: bool = False):
        resolved_model = model or resolve_tier_model_name(ModelTier.CLOUD_AUDIO_TRANSCRIBE)
        super().__init__(api_key=api_key, model_name=resolved_model, mock=mock)
        self.api_key = api_key
        self.model = resolved_model

__all__ = [
    "BaseAudioTranscriber",
    "LocalFasterWhisperTranscriber",
    "OpenAIAudioTranscriber",
    "is_mlx_available",
    "LocalMLXAudioTranscriber",
    "WhisperXAudioTranscriber",
    "get_default_transcriber",
    "ensure_ffmpeg",
    "clean_speaker_label",
    "detect_silence_cutpoints",
    "stitch_and_align_segments",
    "get_audio_duration",
    "slice_audio_chunk",
    "_CHUNK_DURATION_SECONDS",
    "_OVERLAP_SECONDS",
    "_MAX_FILE_SIZE_BYTES",
]
