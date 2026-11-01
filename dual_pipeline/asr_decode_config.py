"""
dual_pipeline/asr_decode_config.py

Single Source of Truth (SSOT) ASR Decoding Configuration.
Shared across:
1. Live chunk ASR (chunk_transcriber.py / LiveMeetingRecorder)
2. Batch master WAV ASR (mlx_transcriber.py / LocalMLXAudioTranscriber)

Enforces deterministic decoding parameters, temperature fallback ladder,
anti-hallucination thresholds, and format conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.parameters import get_asr_decode_parameters

_ASR_DECODE_DEFAULTS = get_asr_decode_parameters()


@dataclass(frozen=True)
class ASRDecodeConfig:
    """Canonical decoding parameters for MLX Whisper transcription."""

    temperature: float = float(_ASR_DECODE_DEFAULTS["temperature"])
    fallback_temperatures: Tuple[float, ...] = (0.2, 0.4)
    condition_on_previous_text: bool = False
    no_speech_threshold: float = float(_ASR_DECODE_DEFAULTS["no_speech_threshold"])
    compression_ratio_threshold: float = float(_ASR_DECODE_DEFAULTS["compression_ratio_threshold"])
    logprob_threshold: float = float(_ASR_DECODE_DEFAULTS["logprob_threshold"])
    hallucination_silence_threshold: Optional[float] = None
    word_timestamps: bool = True
    language: str = str(_ASR_DECODE_DEFAULTS["language"])
    fp16: bool = bool(_ASR_DECODE_DEFAULTS["fp16"])
    verbose: bool = False

    def to_live_kwargs(
        self,
        model_path: str,
        initial_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Kwargs for live chunk transcription."""
        kwargs: Dict[str, Any] = {
            "path_or_hf_repo": model_path,
            "language": self.language,
            "temperature": (self.temperature, *self.fallback_temperatures),
            "condition_on_previous_text": self.condition_on_previous_text,
            "without_timestamps": False,
            "word_timestamps": self.word_timestamps,
            "verbose": self.verbose,
            "fp16": self.fp16,
            "no_speech_threshold": self.no_speech_threshold,
            "compression_ratio_threshold": self.compression_ratio_threshold,
            "logprob_threshold": self.logprob_threshold,
        }
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        return kwargs

    def to_batch_kwargs(
        self,
        model_path: str,
        initial_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Kwargs for batch master WAV transcription with word-level alignment."""
        kwargs: Dict[str, Any] = {
            "path_or_hf_repo": model_path,
            "word_timestamps": self.word_timestamps,
            "condition_on_previous_text": self.condition_on_previous_text,
            "temperature": (self.temperature, *self.fallback_temperatures),
            "compression_ratio_threshold": self.compression_ratio_threshold,
            "no_speech_threshold": self.no_speech_threshold,
            "verbose": self.verbose,
        }
        if self.hallucination_silence_threshold is not None:
            kwargs["hallucination_silence_threshold"] = self.hallucination_silence_threshold
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        if self.language:
            kwargs["language"] = self.language
        return kwargs

    def compute_config_digest(self) -> str:
        """Computes a deterministic hash of decoding parameters for cache key binding."""
        import hashlib
        payload = (
            f"{self.temperature}:{self.fallback_temperatures}:{self.condition_on_previous_text}:"
            f"{self.no_speech_threshold}:{self.compression_ratio_threshold}:{self.logprob_threshold}:"
            f"{self.hallucination_silence_threshold}:{self.word_timestamps}:{self.language}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


DEFAULT_ASR_DECODE_CONFIG = ASRDecodeConfig()
