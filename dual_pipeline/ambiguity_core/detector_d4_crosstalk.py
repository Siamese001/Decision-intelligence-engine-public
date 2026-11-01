"""
dual_pipeline/ambiguity_core/detector_d4_crosstalk.py

Domain 4: Turn Boundaries & Crosstalk Collisions Detector.
Identifies overlapping speech, interleaved turns, and boundary handoff ambiguities.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)

logger = logging.getLogger(__name__)


class CrosstalkCollisionDetector:
    """Detects Domain 4 (Turn Boundaries & Crosstalk Collisions) ambiguities."""

    def __init__(
        self,
        margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
        crosstalk_overlap_threshold_ms: int = 500,
    ) -> None:
        self.margin_threshold = margin_threshold
        self.overlap_threshold_ms = crosstalk_overlap_threshold_ms

    def detect(
        self,
        turns: List[Dict[str, Any]],
    ) -> List[AmbiguityRecord]:
        """Scans sequential turns for temporal overlaps or explicit crosstalk tokens."""
        records: List[AmbiguityRecord] = []

        for idx in range(len(turns) - 1):
            t_curr = turns[idx]
            t_next = turns[idx + 1]

            end_curr = t_curr.get("end_ms", 0)
            start_next = t_next.get("start_ms", 0)
            overlap_ms = end_curr - start_next if (end_curr > 0 and start_next > 0) else 0

            text_curr = t_curr.get("text", "")
            text_next = t_next.get("text", "")
            spk_curr = t_curr.get("speaker", "Speaker A")
            spk_next = t_next.get("speaker", "Speaker B")

            has_crosstalk_token = any(
                marker in (text_curr + " " + text_next).lower()
                for marker in ("[crosstalk]", "[simultaneous]", "[interruption]", "--", "...")
            )

            if overlap_ms >= self.overlap_threshold_ms or has_crosstalk_token:
                c_seq = CandidateOption(
                    option_id=f"opt_d4_{idx}_sequential",
                    text=f"Sequential Turns: {spk_curr} finishes before {spk_next}",
                    confidence=0.51,
                    rationale=f"Clean turn separation with reconstructed boundary at +{overlap_ms}ms",
                    evidence_refs=[f"turn_{idx}", f"turn_{idx+1}"],
                )
                c_ovl = CandidateOption(
                    option_id=f"opt_d4_{idx}_simultaneous",
                    text=f"Simultaneous Crosstalk: Both speakers active between {start_next}ms and {end_curr}ms",
                    confidence=0.49,
                    rationale=f"Dual active voice activity detected ({overlap_ms}ms overlap)",
                    evidence_refs=[f"turn_{idx}", f"turn_{idx+1}"],
                )

                rec = AmbiguityRecord.create(
                    ambiguity_id=f"amb_d4_{idx:04d}",
                    domain=AmbiguityDomain.D4_TURN_CROSSTALK,
                    segment_id=f"turn_{idx}_{idx+1}",
                    source_excerpt=f"{spk_curr}: '{text_curr[-30:]}' || {spk_next}: '{text_next[:30]}'",
                    candidates=[c_seq, c_ovl],
                    time_span_ms=(start_next, end_curr),
                    is_material=True,
                    threshold=self.margin_threshold,
                )
                records.append(rec)

        return records
