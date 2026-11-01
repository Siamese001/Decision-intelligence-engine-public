"""Transcript stages in the dual pipeline.

This module is a backward-compatible facade re-exporting all symbols from
dual_pipeline.transcript_stages_core, preserving 100% symbol parity,
signatures, and type definitions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
    require_audio_certificate,
)
from dual_pipeline.transcript_models import CanonicalSignalProjectionV57

from .transcript_stages_core import (
    _BASE_DIR,
    _GENERIC_SPEAKER_PATTERN,
    _NATIVE_DIARIZATION_PATTERN,
    _PASSIVE_VERBS,
    _STAGE_PATHS,
    _STAGES_DIR,
    AGENTIC_EXTRACTION_PROMPT,
    AGENTIC_LEVERAGE_NAMESPACE,
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    AgenticLeverageNode,
    BipartiteSignalProjection,
    C0ConformanceGate,
    C0ContextEnvelopeGate,
    C0RosterContextGate,
    HITLRequiredError,
    L0DeterministicRouter,
    L1ForensicCognition,
    L1PlanContract,
    L2ExecutiveRecapExecutor,
    L2ForensicNormalizerExecutor,
    L2GraphExtractorExecutor,
    L2SynthesisExecutor,
    L2SynthesisProvider,
    LLMTranscriptSynthesizer,
    OfflineTranscriptSynthesizer,
    PAPacketAssembler,
    RestrictedAdvisoryProjection,
    U0IngestionBoundary,
    XExitGatesEvaluator,
    _load_stage_prompt,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AudioCompletenessCircuitBreaker",
    "AudioIncompleteFailFastError",
    "require_audio_certificate",
    "AGENTIC_EXTRACTION_PROMPT",
    "AGENTIC_LEVERAGE_NAMESPACE",
    "AgenticLeverageNode",
    "Any",
    "BaseModel",
    "BipartiteSignalProjection",
    "C0ConformanceGate",
    "C0ContextEnvelopeGate",
    "C0RosterContextGate",
    "CLIENT_SAFE_NAMESPACE",
    "INTERNAL_ADVISORY_NAMESPACE",
    "CanonicalSignalProjectionV57",
    "Dict",
    "Field",
    "HITLRequiredError",
    "L0DeterministicRouter",
    "L1ForensicCognition",
    "L1PlanContract",
    "L2ExecutiveRecapExecutor",
    "L2ForensicNormalizerExecutor",
    "L2GraphExtractorExecutor",
    "L2SynthesisExecutor",
] + [
    "L2SynthesisProvider",
    "LLMTranscriptSynthesizer",
    "List",
    "OfflineTranscriptSynthesizer",
    "Optional",
    "PAPacketAssembler",
    "RestrictedAdvisoryProjection",
    "Path",
    "Protocol",
    "U0IngestionBoundary",
    "Union",
    "XExitGatesEvaluator",
    "_BASE_DIR",
    "_GENERIC_SPEAKER_PATTERN",
    "_NATIVE_DIARIZATION_PATTERN",
    "_PASSIVE_VERBS",
    "_STAGES_DIR",
    "_STAGE_PATHS",
    "_load_stage_prompt",
    "hashlib",
    "json",
    "load_dotenv",
    "logger",
    "logging",
    "os",
    "re",
]
