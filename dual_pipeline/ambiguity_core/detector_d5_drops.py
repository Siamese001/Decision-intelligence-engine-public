"""
dual_pipeline/ambiguity_core/detector_d5_drops.py

Domain 5: Recording Drops & Packet Loss Detector.
Identifies ungrounded gaps, packet loss dropouts, clipped audio bursts, and enforces Rule C-11.
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


def _format_ms_to_timestamp(ms: int) -> str:
    """Formats milliseconds to HH:MM:SS."""
    total_sec = max(0, ms // 1000)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class RecordingDropsDetector:
    """Detects Domain 5 (Recording Drops & Packet Loss) ambiguities."""

    def __init__(
        self,
        margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
        gap_silence_threshold_ms: int = 5000,
    ) -> None:
        self.margin_threshold = margin_threshold
        self.gap_threshold_ms = gap_silence_threshold_ms

    def detect(
        self,
        turns: List[Dict[str, Any]],
    ) -> List[AmbiguityRecord]:
        """Scans turns for suspicious temporal silence gaps or missing audio packets."""
        records: List[AmbiguityRecord] = []

        for idx in range(len(turns) - 1):
            t_curr = turns[idx]
            t_next = turns[idx + 1]

            end_curr = t_curr.get("end_ms", 0)
            start_next = t_next.get("start_ms", 0)
            gap_ms = start_next - end_curr if (end_curr > 0 and start_next > 0) else 0

            text_curr = t_curr.get("text", "")
            has_gap_token = any(
                marker in text_curr.lower()
                for marker in ("[gap", "[audio dropped", "[recording gap", "[inaudible]")
            )

            if gap_ms >= self.gap_threshold_ms or has_gap_token:
                ts_start = _format_ms_to_timestamp(end_curr)
                ts_end = _format_ms_to_timestamp(start_next)

                c_loss = CandidateOption(
                    option_id=f"opt_d5_{idx}_packet_loss",
                    text=f"Rule C-11 Explicit Recording Gap: [Recording gap: {ts_start} – {ts_end}]",
                    confidence=0.55,
                    rationale=f"True media dropout/packet loss of {gap_ms}ms between utterances",
                    evidence_refs=[f"turn_{idx}", f"turn_{idx+1}"],
                )
                c_silence = CandidateOption(
                    option_id=f"opt_d5_{idx}_natural_silence",
                    text="Natural Meeting Pause (no spoken content dropped)",
                    confidence=0.45,
                    rationale="Inter-turn reflection or presentation slide advance pause",
                    evidence_refs=[f"turn_{idx}", f"turn_{idx+1}"],
                )

                rec = AmbiguityRecord.create(
                    ambiguity_id=f"amb_d5_{idx:04d}",
                    domain=AmbiguityDomain.D5_DROPS_AND_GAPS,
                    segment_id=f"turn_{idx}_{idx+1}",
                    source_excerpt=f"Silence gap of {gap_ms}ms ({ts_start} - {ts_end})",
                    candidates=[c_loss, c_silence],
                    time_span_ms=(end_curr, start_next),
                    is_material=True,
                    threshold=self.margin_threshold,
                )
                records.append(rec)

        return records
