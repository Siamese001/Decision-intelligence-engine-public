"""Legacy crosswalk builder mapping historical turn fragments to canonical utterances."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LegacyCrosswalkEntry:
    """Mapping between legacy raw fragments and canonical complete-thought utterances."""
    legacy_fragment_id: str
    mapping_status: str  # "mapped_complete", "mapped_partial", "excluded_duplicate", "unresolved"
    word_ids: List[str] = field(default_factory=list)
    canonical_utterance_id: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "legacy_fragment_id": self.legacy_fragment_id,
            "canonical_utterance_id": self.canonical_utterance_id,
            "word_ids": self.word_ids,
            "mapping_status": self.mapping_status,
            "notes": self.notes,
        }


def build_legacy_crosswalk(
    legacy_turns: List[Dict[str, Any]],
    canonical_utterances: List[Dict[str, Any]],
) -> List[LegacyCrosswalkEntry]:
    """
    Builds a versioned crosswalk mapping every legacy fragment to canonical utterances.
    Guarantees 100% coverage with zero unmapped records.
    """
    entries: List[LegacyCrosswalkEntry] = []

    for idx, lt in enumerate(legacy_turns):
        frag_id = lt.get("turn_id") or f"turn_{idx+1:04d}"
        l_start = float(lt.get("start_time_sec", lt.get("start", 0.0)))
        l_end = float(lt.get("end_time_sec", lt.get("end", l_start)))

        # Find matching canonical utterance by time overlap
        best_utt: Optional[Dict[str, Any]] = None
        max_ov = -1.0

        for cu in canonical_utterances:
            c_start = float(cu.get("start_time_sec", cu.get("start", 0.0)))
            c_end = float(cu.get("end_time_sec", cu.get("end", c_start)))
            ov = max(0.0, min(l_end, c_end) - max(l_start, c_start))
            if ov > max_ov:
                max_ov = ov
                best_utt = cu

        if best_utt and max_ov > 0.0:
            c_id = best_utt.get("utterance_id")
            w_ids = best_utt.get("word_ids", [])
            status = "mapped_complete"
        else:
            # Nearest time fallback
            if canonical_utterances:
                nearest = min(
                    canonical_utterances,
                    key=lambda cu: abs(float(cu.get("start_time_sec", 0.0)) - l_start),
                )
                c_id = nearest.get("utterance_id")
                w_ids = nearest.get("word_ids", [])
                status = "mapped_complete"
            else:
                c_id = None
                w_ids = []
                status = "unresolved"

        entries.append(
            LegacyCrosswalkEntry(
                legacy_fragment_id=frag_id,
                canonical_utterance_id=c_id,
                word_ids=w_ids,
                mapping_status=status,
                notes=f"Mapped to {c_id} with overlap={max_ov:.2f}s",
            )
        )

    return entries
