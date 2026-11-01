"""
dual_pipeline/speaker_attribution_core/__init__.py

Public package facade for speaker attribution engine.
"""
from __future__ import annotations

from .attribution_engine import (
    AttributionResult,
    AudioSpeakerAttributionEngine,
    CandidateAttribution,
)
from .candidate_scorer import (
    extract_speaker_turns,
    load_diarization_tracks,
    load_speaker_hints,
    score_candidates_for_speaker,
)
from .confidence_router import (
    DiarizationLane,
    SegmentRoutingDecision,
    WordConfidenceRouter,
    WordConfidenceToken,
)
from .models import SpeakerAttributionCollisionError, TurnAttributionDiscrepancy
from .pattern_engine import (
    SpeakerPatternAnchor,
    SpeakerPatternEngine,
    SpeakerPatternMap,
)
from .review_context import (
    SpeakerReviewContext,
    SpeakerReviewContextError,
    SpeakerReviewTurn,
    build_speaker_review_context,
)
from .review_context_render import (
    build_speaker_review_question,
    format_speaker_review_context,
    parse_speaker_review_turns,
    validate_visible_speaker_context,
)

__all__ = [
    "AudioSpeakerAttributionEngine",
    "AttributionResult",
    "CandidateAttribution",
    "score_candidates_for_speaker",
    "extract_speaker_turns",
    "load_speaker_hints",
    "load_diarization_tracks",
    "SpeakerAttributionCollisionError",
    "TurnAttributionDiscrepancy",
    "DiarizationLane",
    "SegmentRoutingDecision",
    "WordConfidenceRouter",
    "WordConfidenceToken",
    "SpeakerPatternAnchor",
    "SpeakerPatternEngine",
    "SpeakerPatternMap",
    "SpeakerReviewContext",
    "SpeakerReviewContextError",
    "SpeakerReviewTurn",
    "build_speaker_review_context",
    "build_speaker_review_question",
    "format_speaker_review_context",
    "parse_speaker_review_turns",
    "validate_visible_speaker_context",
]


