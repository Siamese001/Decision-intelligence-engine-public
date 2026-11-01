"""
dual_pipeline/ambiguity_core/detector_d3_polarity.py

Domain 3: Critical Semantic Polarities & Intent Detector.
Detects ambiguous negations, inverted business logic, and commitment polarities.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from .models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)

logger = logging.getLogger(__name__)

POLARITY_PAIRS = [
    (r"\bcan\b", r"\bcannot\b|\bcan't\b", "can", "cannot"),
    (r"\bwill\b", r"\bwon't\b|\bwill not\b", "will", "will not"),
    (r"\bshould\b", r"\bshouldn't\b|\bshould not\b", "should", "should not"),
    (r"\bapprove\b|\bapproved\b", r"\breject\b|\brejected\b", "approve", "reject"),
    (r"\bcommit\b|\bcommitted\b", r"\bpropose\b|\bproposed\b", "commit", "propose"),
    (r"\bmust\b", r"\bmay\b|\bmight\b", "mandatory (must)", "optional (may)"),
]


class SemanticPolarityDetector:
    """Detects Domain 3 (Semantic Polarity & Intent) ambiguities."""

    def __init__(
        self,
        margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
    ) -> None:
        self.margin_threshold = margin_threshold

    def detect(
        self,
        turns: List[Dict[str, Any]],
    ) -> List[AmbiguityRecord]:
        """Scans transcript turns for polarity inversion and modal ambiguity."""
        records: List[AmbiguityRecord] = []

        for idx, turn in enumerate(turns):
            text = turn.get("text", "")
            speaker = turn.get("speaker", "Speaker")
            if not text:
                continue

            text_lower = text.lower()
            for pos_pat, neg_pat, pos_term, neg_term in POLARITY_PAIRS:
                has_pos = bool(re.search(pos_pat, text_lower))
                has_neg = bool(re.search(neg_pat, text_lower))

                if (has_pos and has_neg) or ("?" in text and (has_pos or has_neg)):
                    c_pos = CandidateOption(
                        option_id=f"opt_d3_{idx}_{pos_term}",
                        text=f"Affirmative: '{pos_term}'",
                        confidence=0.52,
                        rationale=f"Affirmative polarity detected in context '{text[:40]}...'",
                        evidence_refs=[f"turn_{idx}"],
                    )
                    c_neg = CandidateOption(
                        option_id=f"opt_d3_{idx}_{neg_term}",
                        text=f"Negative/Qualifying: '{neg_term}'",
                        confidence=0.48,
                        rationale=f"Negative/qualifying polarity detected in context '{text[:40]}...'",
                        evidence_refs=[f"turn_{idx}"],
                    )

                    rec = AmbiguityRecord.create(
                        ambiguity_id=f"amb_d3_{idx:04d}_{pos_term}",
                        domain=AmbiguityDomain.D3_SEMANTIC_POLARITY,
                        segment_id=f"turn_{idx}",
                        source_excerpt=f"{speaker}: {text}",
                        candidates=[c_pos, c_neg],
                        time_span_ms=(turn.get("start_ms", 0), turn.get("end_ms", 0)),
                        is_material=True,
                        threshold=self.margin_threshold,
                    )
                    records.append(rec)
                    break

        return records
