"""dual_pipeline/audio

Source-agnostic audio format detection, circuit breaking,
and hardware resilience layer for the Decision Intelligence Engine.
"""

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.audio.source_detector import AudioSourceDetector, AudioSourceProfile

__all__ = [
    "AudioCompletenessCircuitBreaker",
    "AudioIncompleteFailFastError",
    "AudioSourceDetector",
    "AudioSourceProfile",
]
