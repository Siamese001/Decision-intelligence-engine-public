"""
dual_pipeline/speaker_attribution_core/confidence_router.py

Word-Level Diarization Confidence Router & 3-Lane Triage Engine.
Implements industry-validated 3-lane HITL routing (AssemblyAI / Deepgram cpWER standard):
  - Straight-Through: word confidence >= 0.90 (autonomous progression)
  - Field-Level Review: 0.70 <= word confidence < 0.90 (flag critical tokens for HITL)
  - Full Manual Exception: word confidence < 0.70 (escalate entire segment to HITL)
Also includes cpWER proxy calculation and confidence drift detection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.speaker_attribution_core.confidence_router")


class DiarizationLane(str, Enum):
    """The 3 enterprise HITL review lanes."""
    STRAIGHT_THROUGH = "STRAIGHT_THROUGH"
    FIELD_LEVEL_REVIEW = "FIELD_LEVEL_REVIEW"
    FULL_MANUAL_EXCEPTION = "FULL_MANUAL_EXCEPTION"


@dataclass
class WordConfidenceToken:
    """Individual word token with timestamp and speaker confidence."""
    word: str
    speaker: str
    confidence: float
    start: float = 0.0
    end: float = 0.0
    is_critical_entity: bool = False


@dataclass
class SegmentRoutingDecision:
    """Routing decision for a transcript segment."""
    segment_id: str
    lane: DiarizationLane
    mean_confidence: float
    min_confidence: float
    word_count: int
    flagged_tokens: List[WordConfidenceToken] = field(default_factory=list)
    reason: str = ""
    speaker: str = "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "lane": self.lane.value,
            "mean_confidence": round(self.mean_confidence, 4),
            "min_confidence": round(self.min_confidence, 4),
            "word_count": self.word_count,
            "flagged_tokens_count": len(self.flagged_tokens),
            "flagged_words": [t.word for t in self.flagged_tokens],
            "reason": self.reason,
            "speaker": self.speaker,
        }


class WordConfidenceRouter:
    """
    Evaluates word-level speaker confidence and routes transcript segments into
    appropriate triage lanes.
    """

    STRAIGHT_THROUGH_FLOOR: float = 0.90
    FIELD_LEVEL_FLOOR: float = 0.70

    def __init__(
        self,
        straight_through_floor: float = STRAIGHT_THROUGH_FLOOR,
        field_level_floor: float = FIELD_LEVEL_FLOOR,
    ):
        self.straight_through_floor = straight_through_floor
        self.field_level_floor = field_level_floor

    def route_segment(
        self,
        words: List[Dict[str, Any]],
        segment_id: str = "seg-0",
        speaker: str = "Unknown",
    ) -> SegmentRoutingDecision:
        """
        Routes a segment based on word-level confidence tokens.
        """
        if not words:
            return SegmentRoutingDecision(
                segment_id=segment_id,
                lane=DiarizationLane.FULL_MANUAL_EXCEPTION,
                mean_confidence=0.0,
                min_confidence=0.0,
                word_count=0,
                reason="Empty word segment requires manual inspection.",
                speaker=speaker,
            )

        tokens: List[WordConfidenceToken] = []
        confidences: List[float] = []

        for w in words:
            word_text = str(w.get("word") or w.get("text", "")).strip()
            conf = float(w.get("confidence", w.get("speaker_confidence", 1.0)))
            conf = max(0.0, min(1.0, conf))
            confidences.append(conf)
            is_crit = self._is_critical_token(word_text)
            tokens.append(
                WordConfidenceToken(
                    word=word_text,
                    speaker=str(w.get("speaker", speaker)),
                    confidence=conf,
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    is_critical_entity=is_crit,
                )
            )

        mean_conf = sum(confidences) / len(confidences)
        min_conf = min(confidences)

        # 1. Full manual exception check
        if mean_conf < self.field_level_floor or min_conf < 0.50:
            flagged = [t for t in tokens if t.confidence < self.field_level_floor]
            return SegmentRoutingDecision(
                segment_id=segment_id,
                lane=DiarizationLane.FULL_MANUAL_EXCEPTION,
                mean_confidence=mean_conf,
                min_confidence=min_conf,
                word_count=len(tokens),
                flagged_tokens=flagged,
                reason=f"Mean confidence {mean_conf:.2f} or min confidence {min_conf:.2f} below field-level floor ({self.field_level_floor:.2f}).",
                speaker=speaker,
            )

        # 2. Field-level review check
        field_flagged = [
            t for t in tokens
            if t.confidence < self.straight_through_floor or (t.is_critical_entity and t.confidence < 0.95)
        ]
        if mean_conf < self.straight_through_floor or field_flagged:
            return SegmentRoutingDecision(
                segment_id=segment_id,
                lane=DiarizationLane.FIELD_LEVEL_REVIEW,
                mean_confidence=mean_conf,
                min_confidence=min_conf,
                word_count=len(tokens),
                flagged_tokens=field_flagged,
                reason=f"Found {len(field_flagged)} token(s) below high-confidence threshold ({self.straight_through_floor:.2f}).",
                speaker=speaker,
            )

        # 3. Straight-through
        return SegmentRoutingDecision(
            segment_id=segment_id,
            lane=DiarizationLane.STRAIGHT_THROUGH,
            mean_confidence=mean_conf,
            min_confidence=min_conf,
            word_count=len(tokens),
            flagged_tokens=[],
            reason="All word tokens meet or exceed high-confidence autonomous floor.",
            speaker=speaker,
        )

    def route_transcript_segments(
        self, segments: List[Dict[str, Any]]
    ) -> List[SegmentRoutingDecision]:
        """Routes a sequence of transcript segments."""
        decisions: List[SegmentRoutingDecision] = []
        for idx, seg in enumerate(segments):
            seg_id = str(seg.get("segment_id", f"seg-{idx}"))
            spk = str(seg.get("speaker", f"Speaker {idx % 2 + 1}"))
            words = seg.get("words")
            if not words and "text" in seg:
                # Synthesize simulated words from text if not provided
                text_words = str(seg["text"]).split()
                default_conf = float(seg.get("confidence", 0.95))
                words = [{"word": w, "confidence": default_conf, "speaker": spk} for w in text_words]
            dec = self.route_segment(words or [], segment_id=seg_id, speaker=spk)
            decisions.append(dec)
        return decisions

    @staticmethod
    def _is_critical_token(token: str) -> bool:
        """Determines if a token is a high-risk entity (Proper noun, acronym, number)."""
        clean = token.strip(".,;:?!'\"()[]{}")
        if not clean:
            return False
        if clean[0].isupper() and len(clean) > 1:
            return True
        if clean.isupper() and len(clean) >= 2:
            return True
        if any(c.isdigit() for c in clean):
            return True
        return False

    @staticmethod
    def calculate_cpwer_proxy(
        hypotheses: List[Dict[str, Any]], references: List[Dict[str, Any]]
    ) -> float:
        """
        Concatenated minimum-permutation Word Error Rate (cpWER) proxy.
        Compares word and speaker match between hypothesis and reference segments.
        """
        if not references:
            return 0.0
        total_words = 0
        error_count = 0
        for hyp, ref in zip(hypotheses, references):
            ref_words = str(ref.get("text", "")).split()
            hyp_words = str(hyp.get("text", "")).split()
            total_words += max(len(ref_words), 1)

            # Speaker mismatch penalty
            if hyp.get("speaker") != ref.get("speaker"):
                error_count += len(ref_words)
            else:
                # Word mismatch count
                diff = abs(len(ref_words) - len(hyp_words))
                mismatches = sum(1 for w1, w2 in zip(ref_words, hyp_words) if w1.lower() != w2.lower())
                error_count += mismatches + diff

        return min(1.0, round(error_count / max(1, total_words), 4))

    @staticmethod
    def detect_confidence_drift(
        recent_decisions: List[SegmentRoutingDecision],
        baseline_distribution: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Detects if current model confidence distribution drifts significantly from baseline.
        Alerts when straight-through lane drops by > 20% or exceptions jump > 15%.
        """
        if not recent_decisions:
            return {"drift_detected": False, "reason": "No decisions to audit"}

        total = len(recent_decisions)
        counts = {
            DiarizationLane.STRAIGHT_THROUGH.value: sum(1 for d in recent_decisions if d.lane == DiarizationLane.STRAIGHT_THROUGH),
            DiarizationLane.FIELD_LEVEL_REVIEW.value: sum(1 for d in recent_decisions if d.lane == DiarizationLane.FIELD_LEVEL_REVIEW),
            DiarizationLane.FULL_MANUAL_EXCEPTION.value: sum(1 for d in recent_decisions if d.lane == DiarizationLane.FULL_MANUAL_EXCEPTION),
        }
        current_dist = {k: round(v / total, 4) for k, v in counts.items()}

        base = baseline_distribution or {
            DiarizationLane.STRAIGHT_THROUGH.value: 0.70,
            DiarizationLane.FIELD_LEVEL_REVIEW.value: 0.20,
            DiarizationLane.FULL_MANUAL_EXCEPTION.value: 0.10,
        }

        straight_drop = base.get(DiarizationLane.STRAIGHT_THROUGH.value, 0.70) - current_dist.get(DiarizationLane.STRAIGHT_THROUGH.value, 0.0)
        exception_jump = current_dist.get(DiarizationLane.FULL_MANUAL_EXCEPTION.value, 0.0) - base.get(DiarizationLane.FULL_MANUAL_EXCEPTION.value, 0.10)

        drift = straight_drop > 0.20 or exception_jump > 0.15
        reason = "Distribution within expected variance"
        if drift:
            reason = f"Confidence drift detected: straight-through dropped by {straight_drop * 100:.1f}%, exceptions shifted by {exception_jump * 100:.1f}%."

        return {
            "drift_detected": drift,
            "current_distribution": current_dist,
            "baseline_distribution": base,
            "reason": reason,
            "live_integrator_signal": "RECALIBRATE_DIARIZATION_THRESHOLDS" if drift else "NOMINAL",
        }

    def calibrate_from_hitl_outcomes(self, outcomes: List[Dict[str, Any]]) -> Dict[str, float]:
        """Adapts straight-through and field-level floors using HITL AFFIRMED/OVERRIDDEN feedback."""
        if not outcomes:
            return {"straight_through_floor": self.straight_through_floor, "field_level_floor": self.field_level_floor}
        overrides = sum(1 for o in outcomes if str(o.get("outcome_type", "")).upper() == "OVERRIDDEN")
        affirmed = sum(1 for o in outcomes if str(o.get("outcome_type", "")).upper() == "AFFIRMED")
        total = max(1, overrides + affirmed)
        override_rate = overrides / total
        if override_rate > 0.25:
            self.straight_through_floor = round(min(0.96, self.straight_through_floor + 0.03), 3)
            self.field_level_floor = round(min(0.82, self.field_level_floor + 0.03), 3)
        elif override_rate < 0.05 and affirmed >= 5:
            self.straight_through_floor = round(max(0.85, self.straight_through_floor - 0.02), 3)
            self.field_level_floor = round(max(0.65, self.field_level_floor - 0.02), 3)
        return {"straight_through_floor": self.straight_through_floor, "field_level_floor": self.field_level_floor}
