"""
dual_pipeline/ambiguity_core/models.py

Data models for the Full-Spectrum 5-Domain Ambiguity Engine.
Governs standardized candidate representation, confidence vectorization,
margin delta calculation, and audit state tracking.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_min_autonomous_confidence_floor,
    needs_operator_decision,
)

# Governed Ambiguity Gating Rule threshold mandated by repository governance
AMBIGUITY_MARGIN_THRESHOLD: float = get_hitl_ambiguity_margin_threshold()


class AmbiguityDomain(str, Enum):
    """The governed ambiguity domains for Output 1 hardening."""

    D1_SPEAKER_ATTRIBUTION = "D1_SPEAKER_ATTRIBUTION"
    D2_DOMAIN_VOCABULARY = "D2_DOMAIN_VOCABULARY"
    D3_SEMANTIC_POLARITY = "D3_SEMANTIC_POLARITY"
    D4_TURN_CROSSTALK = "D4_TURN_CROSSTALK"
    D5_DROPS_AND_GAPS = "D5_DROPS_AND_GAPS"
    D6_ENTITY_CLARIFICATION = "D6_ENTITY_CLARIFICATION"


class AmbiguityStatus(str, Enum):
    """Lifecycle disposition of an ambiguity record."""

    OPEN = "OPEN"
    RESOLVED_AUTONOMOUS = "RESOLVED_AUTONOMOUS"
    RESOLVED_HITL = "RESOLVED_HITL"
    UNRESOLVED_EXPLICIT = "UNRESOLVED_EXPLICIT"
    UNRESOLVED_PENDING_OPERATOR = "UNRESOLVED_PENDING_OPERATOR"


class CandidateOption(BaseModel):
    """Single discrete candidate interpretation."""

    option_id: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence_refs: List[str] = Field(default_factory=list)
    is_recommended: bool = False
    attributes: Dict[str, Any] = Field(default_factory=dict)


class AmbiguityRecord(BaseModel):
    """Standardized record for any detected ambiguity across domains D1–D5."""

    ambiguity_id: str
    domain: AmbiguityDomain
    segment_id: str
    time_span_ms: Tuple[int, int] = (0, 0)
    source_excerpt: str
    candidates: List[CandidateOption] = Field(default_factory=list)
    delta_margin: float = 0.0
    requires_hitl: bool = False
    is_material: bool = True
    status: AmbiguityStatus = AmbiguityStatus.OPEN
    selected_option_id: Optional[str] = None
    resolution_rationale: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None

    @classmethod
    def create(
        cls,
        ambiguity_id: str,
        domain: AmbiguityDomain,
        segment_id: str,
        source_excerpt: str,
        candidates: List[CandidateOption],
        time_span_ms: Tuple[int, int] = (0, 0),
        is_material: bool = True,
        threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
    ) -> AmbiguityRecord:
        """Constructs an AmbiguityRecord and computes delta margin and HITL requirement."""
        sorted_opts = sorted(candidates, key=lambda c: c.confidence, reverse=True)
        if len(sorted_opts) >= 2:
            delta = round(sorted_opts[0].confidence - sorted_opts[1].confidence, 4)
            is_ambiguous = needs_operator_decision(
                sorted_opts[0].confidence, sorted_opts[1].confidence, threshold=threshold
            )
        elif len(sorted_opts) == 1:
            delta = 1.0
            is_ambiguous = False
        else:
            delta = 0.0
            is_ambiguous = True

        if sorted_opts:
            sorted_opts[0].is_recommended = True

        top_conf = sorted_opts[0].confidence if sorted_opts else 0.0
        confidence_floor = get_hitl_min_autonomous_confidence_floor()
        requires_hitl = is_material and (is_ambiguous or top_conf < confidence_floor)

        return cls(
            ambiguity_id=ambiguity_id,
            domain=domain,
            segment_id=segment_id,
            time_span_ms=time_span_ms,
            source_excerpt=source_excerpt,
            candidates=sorted_opts,
            delta_margin=delta,
            requires_hitl=requires_hitl,
            is_material=is_material,
            status=AmbiguityStatus.OPEN,
        )
