"""
dual_pipeline/live_meeting_recorder_core/inflection_detector.py

Real-Time Inflection, Cadence, and Natural Stopping Point Detector.
Identifies when a speaker has reached a conversational stopping point, question inflection,
or periodic cadence boundary, signaling the Live Copilot Advisor to deliver instant guidance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class InflectionTriggerType(str, Enum):
    """Classification of live conversational inflection triggers."""
    STOPPING_POINT_QUESTION = "STOPPING_POINT_QUESTION"
    STOPPING_POINT_PAUSE = "STOPPING_POINT_PAUSE"
    STOPPING_POINT_COMPLETION = "STOPPING_POINT_COMPLETION"
    CADENCE_INTERVAL = "CADENCE_INTERVAL"


# Backward-compatible alias
InflectionTrigger = InflectionTriggerType


@dataclass(frozen=True)
class InflectionEvent:
    """Represents a detected inflection or stopping point event."""
    trigger_type: InflectionTriggerType
    cue_text: str
    timestamp_sec: float
    is_priority: bool
    confidence: float
    reason: str
    immediate_dispatch_eligible: bool = False


# Interrogative hand-offs, rising tone markers, and conversational pass-backs
_INTERROGATIVE_PATTERNS = re.compile(
    r"(?:"
    r"\?\s*$"
    r"|\b(?:right|correct|agreed|fair|no)\?\s*$"
    r"|\b(?:what do you think|what are your thoughts|what's your take|how do you view|how do you see)\b"
    r"|\b(?:does that make sense|is that fair|would you agree|sound good|sound right)\b"
    r"|\b(?:who owns|who should own|which decisions|where do we draw the line|where should we)\b"
    r"|\b(?:what would be helpful|tell me what your thoughts are|how do we handle|how are we handling)\b"
    r"|\b(?:what is the timeline|what's the timeline|when can we expect|what is the status|what's the status)\b"
    r"|\b(?:can you clarify|could you explain|could you elaborate|can you speak to|how do you handle)\b"
    r"|\b(?:why wouldn't we|why shouldn't we|is that really necessary|do we really need)\b"
    r"|^(?:can|could|should|would|will|is|are|do|does|have|has)\s+(?:you|we|there|they|it)\b"
    r")",
    re.IGNORECASE,
)

# Thought completions and conversational hand-off transitions
_COMPLETION_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:anyway|so yeah|so that's that|that's where it ends|I guess we'll see)\b"
    r"|\b(?:if we agree on that|let's figure that out|that(?:'s|\s+is) my take)\b"
    r"|\b(?:what I feel like we should do|that(?:'s|\s+is)\s+something\s+(?:I|we)\s+can\s+take\s+off\s+your\s+plate)\b"
    r"|\b(?:that(?:'s|\s+is)\s+(?:how|why|where)\s+(?:we|I)|moving\s+forward\s+with\s+that)\b"
    r")",
    re.IGNORECASE,
)


class InflectionDetector:
    """Evaluates incremental transcript chunks and audio energy to detect stopping points and cadence."""

    def __init__(
        self,
        cadence_seconds: float = 15.0,
        silence_rms_threshold: float = 0.015,
        min_speech_chars_for_cadence: int = 40,
    ) -> None:
        self.cadence_seconds = max(5.0, float(cadence_seconds))
        self.silence_rms_threshold = float(silence_rms_threshold)
        self.min_speech_chars_for_cadence = int(min_speech_chars_for_cadence)

        self._last_evaluation_time: float = 0.0
        self._cadence_accumulated_chars: int = 0
        self._last_chunk_sec: float = 0.0
        self._prior_had_speech: bool = False

    def reset_cadence(self, current_time: float) -> None:
        """Resets cadence timer after a successful copilot evaluation."""
        self._last_evaluation_time = current_time
        self._cadence_accumulated_chars = 0

    def evaluate_chunk(
        self,
        chunk_idx: int,
        sec: float,
        text: str,
        rms: float,
        current_time: float,
    ) -> Optional[InflectionEvent]:
        """Inspects chunk text and audio energy to detect stopping points, questions, or cadence ticks."""
        clean = (text or "").strip()
        has_speech = bool(clean)
        self._last_chunk_sec = sec

        # Initialize baseline evaluation time on first speech chunk
        if self._last_evaluation_time <= 0.0:
            self._last_evaluation_time = current_time

        if has_speech:
            self._cadence_accumulated_chars += len(clean)

        # 1. High-Priority: Interrogative / Question Hand-off
        if has_speech and _INTERROGATIVE_PATTERNS.search(clean):
            self._prior_had_speech = True
            return InflectionEvent(
                trigger_type=InflectionTriggerType.STOPPING_POINT_QUESTION,
                cue_text=clean,
                timestamp_sec=sec,
                is_priority=True,
                confidence=0.98,
                reason="Detected interrogative inflection or conversational hand-off query",
                immediate_dispatch_eligible=True,
            )

        # 2. High-Priority: Acoustic Silence Drop (Speaker paused after active speech)
        if self._prior_had_speech and (not has_speech or rms < self.silence_rms_threshold):
            self._prior_had_speech = False
            return InflectionEvent(
                trigger_type=InflectionTriggerType.STOPPING_POINT_PAUSE,
                cue_text=clean or "[Pause]",
                timestamp_sec=sec,
                is_priority=True,
                confidence=0.92,
                reason=f"Detected natural speech pause / energy drop (RMS={rms:.4f})",
            )

        # 3. Medium-Priority: Conversational Thought Completion
        if has_speech and _COMPLETION_PATTERNS.search(clean) and clean[-1] in ".!?":
            self._prior_had_speech = True
            return InflectionEvent(
                trigger_type=InflectionTriggerType.STOPPING_POINT_COMPLETION,
                cue_text=clean,
                timestamp_sec=sec,
                is_priority=False,
                confidence=0.88,
                reason="Detected sentence completion marker at clause boundary",
            )

        self._prior_had_speech = has_speech

        # 4. Periodic Cadence Fallback: Monologue running without pauses
        time_since_last_eval = current_time - self._last_evaluation_time
        if (
            time_since_last_eval >= self.cadence_seconds
            and self._cadence_accumulated_chars >= self.min_speech_chars_for_cadence
        ):
            return InflectionEvent(
                trigger_type=InflectionTriggerType.CADENCE_INTERVAL,
                cue_text=clean,
                timestamp_sec=sec,
                is_priority=False,
                confidence=0.85,
                reason=f"Periodic cadence timer elapsed ({time_since_last_eval:.1f}s >= {self.cadence_seconds:.1f}s)",
            )

        return None
