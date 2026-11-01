"""Governed Audio Ingestion Core Package."""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "BaseAudioTranscriber",
    "is_mlx_available",
    "LocalMLXAudioTranscriber",
    "clear_mlx_whisper_metal_cache",
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

_SYMBOL_TO_MODULE = {
    "BaseAudioTranscriber": "dual_pipeline.audio_ingestion_core.base",
    "get_default_transcriber": "dual_pipeline.audio_ingestion_core.factory",
    "LocalMLXAudioTranscriber": "dual_pipeline.audio_ingestion_core.mlx_transcriber",
    "clear_mlx_whisper_metal_cache": "dual_pipeline.audio_ingestion_core.mlx_transcriber",
    "is_mlx_available": "dual_pipeline.audio_ingestion_core.mlx_transcriber",
    "ensure_ffmpeg": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "clean_speaker_label": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "detect_silence_cutpoints": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "stitch_and_align_segments": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "get_audio_duration": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "slice_audio_chunk": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "_CHUNK_DURATION_SECONDS": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "_OVERLAP_SECONDS": "dual_pipeline.audio_ingestion_core.ffmpeg",
    "_MAX_FILE_SIZE_BYTES": "dual_pipeline.audio_ingestion_core.ffmpeg",
}


def __getattr__(name: str) -> Any:
    mod_name = _SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        mod = importlib.import_module(mod_name)
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
