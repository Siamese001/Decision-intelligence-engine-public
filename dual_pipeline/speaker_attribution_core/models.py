"""Data models and candidate structures for speaker attribution and audio uncertainty."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dual_pipeline.exceptions import HITLRequiredError


class SpeakerAttributionCollisionError(HITLRequiredError):
    """Raised when speaker attribution assigns duplicate identities in violation of injective mapping."""
    pass


@dataclass
class CandidateAttribution:
    """A scored attendee candidate for a speaker cluster or ambiguous turn."""
    attendee_name: str
    organization: str
    confidence: float
    confidence_tier: str  # HIGH, MEDIUM, LOW
    evidence_rationale: str
    is_recommended: bool = False
    role: Optional[str] = None

    def to_option_string(self) -> str:
        rec_prefix = "(Recommended) " if self.is_recommended else ""
        org_str = f" ({self.organization})" if self.organization else ""
        role_str = f" [{self.role}]" if self.role else ""
        return (
            f"{rec_prefix}{self.attendee_name}{org_str}{role_str} — "
            f"Confidence: {self.confidence_tier} ({int(self.confidence * 100)}%) "
            f"[{self.evidence_rationale}]"
        )


@dataclass
class TurnAttributionDiscrepancy:
    """Detected ambiguity or role discrepancy in an audio transcript turn or cluster."""
    cluster_id: str
    current_speaker: str
    detected_role: str
    start_timestamp: str
    end_timestamp: str
    turn_indices: List[int]
    sample_text: str
    candidates: List[CandidateAttribution]
    margin: float
    is_ambiguous: bool = True


@dataclass
class AttributionResult:
    """Outcome of speaker attribution analysis."""
    speaker_map: Dict[str, str]
    resolved_transcript: str
    unmapped_speakers: List[str]
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    turn_reassignments: Dict[str, str] = field(default_factory=dict)
