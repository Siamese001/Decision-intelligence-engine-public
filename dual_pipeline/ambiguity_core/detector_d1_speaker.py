"""
dual_pipeline/ambiguity_core/detector_d1_speaker.py

Domain 1: Speaker Attribution & Room-Mic Hygiene Detector.
Identifies ambiguous speaker labels, acoustic cluster collisions, and shared room-mic bleed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)

logger = logging.getLogger(__name__)


class SpeakerAttributionDetector:
    """Detects Domain 1 (Speaker Attribution) ambiguities in transcript turns."""

    def __init__(
        self,
        margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.margin_threshold = margin_threshold
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e or "xhigh"

    def _score_attendee_posteriors(
        self,
        idx: int,
        speaker: str,
        text: str,
        turn: Dict[str, Any],
        attendees: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[CandidateOption]:
        meta = dict(metadata or turn.get("metadata") or {})
        if turn.get("embedding") and "cluster_embeddings" not in meta:
            meta["cluster_embeddings"] = {speaker or f"Speaker {idx+1}": turn["embedding"]}
        try:
            from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
            roster_tuples = [(a, "") for a in attendees]
            synth_tx = f"{speaker or 'Speaker 1'}: {text}"
            scored = score_candidates_for_speaker(
                spk_id=speaker or "Speaker 1",
                transcript=synth_tx,
                roster=roster_tuples,
                metadata=meta,
                total_clusters=max(1, len(attendees)),
            )
            if scored:
                total_raw = sum(max(0.05, c.confidence) for c in scored[:3]) or 1.0
                return [
                    CandidateOption(
                        option_id=f"opt_d1_{idx}_{c_idx}",
                        text=c.attendee_name,
                        confidence=round(max(0.05, min(0.99, c.confidence / total_raw if len(scored[:3]) > 1 and max(x.confidence for x in scored[:3]) <= 0.55 else c.confidence)), 2),
                        rationale=c.evidence_rationale or f"Roster posterior for '{text[:30]}...'",
                        evidence_refs=[f"turn_{idx}"],
                    )
                    for c_idx, c in enumerate(scored[:3])
                ]
        except Exception as exc:
            logger.debug("CandidateScorer posterior fallback in D1 detector: %s", exc)
        base_conf = round(1.0 / len(attendees[:3]), 2)
        return [
            CandidateOption(
                option_id=f"opt_d1_{idx}_{a_idx}",
                text=att,
                confidence=base_conf,
                rationale=f"Roster match for shared microphone utterance '{text[:30]}...'",
                evidence_refs=[f"turn_{idx}"],
            )
            for a_idx, att in enumerate(attendees[:3])
        ]

    def detect(
        self,
        turns: List[Dict[str, Any]],
        known_attendees: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[AmbiguityRecord]:
        """Scans transcript turns for speaker ambiguities."""
        attendees = known_attendees or []
        records: List[AmbiguityRecord] = []

        for idx, turn in enumerate(turns):
            speaker = turn.get("speaker", "").strip()
            text = turn.get("text", "").strip()
            if not text:
                continue

            is_room_mic = any(
                marker in speaker.lower()
                for marker in ("room mic", "speaker", "unidentified", "unknown", "conference room", "mic")
            )
            candidates_raw = turn.get("candidates") or []

            if is_room_mic or len(candidates_raw) >= 2:
                candidates: List[CandidateOption] = []
                if candidates_raw:
                    for c_idx, c in enumerate(candidates_raw):
                        candidates.append(
                            CandidateOption(
                                option_id=f"opt_d1_{idx}_{c_idx}",
                                text=c.get("speaker", f"Speaker {c_idx+1}"),
                                confidence=float(c.get("confidence", 0.5)),
                                rationale=c.get("rationale", "Acoustic and semantic similarity score"),
                                evidence_refs=c.get("evidence", [f"turn_{idx}"]),
                            )
                        )
                else:
                    if attendees:
                        candidates = self._score_attendee_posteriors(
                            idx, speaker, text, turn, attendees, metadata=metadata
                        )
                    else:
                        candidates.append(
                            CandidateOption(
                                option_id=f"opt_d1_{idx}_0",
                                text=speaker or "Unidentified Speaker",
                                confidence=0.50,
                                rationale="No roster provided for room mic resolution",
                            )
                        )

                rec = AmbiguityRecord.create(
                    ambiguity_id=f"amb_d1_{idx:04d}",
                    domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
                    segment_id=f"turn_{idx}",
                    source_excerpt=f"{speaker}: {text}",
                    candidates=candidates,
                    time_span_ms=(turn.get("start_ms", 0), turn.get("end_ms", 0)),
                    is_material=True,
                    threshold=self.margin_threshold,
                )
                records.append(rec)

        return records
