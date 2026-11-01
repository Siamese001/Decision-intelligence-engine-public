"""Data models for Output 2 Executive Recap Ambiguity Detection & Pre-Synthesis HITL Gating."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold


class ExecutiveAmbiguityDomain(str, enum.Enum):
    """The 4 core ambiguity domains governing Executive Recap (Output 02) synthesis."""
    E1_AFFILIATION_ROSTER = "E1_AFFILIATION_ROSTER"
    E2_VALUE_ALTITUDE = "E2_VALUE_ALTITUDE"
    E3_ACTION_DRI_DEADLINE = "E3_ACTION_DRI_DEADLINE"
    E4_SYSTEMS_GROUNDING = "E4_SYSTEMS_GROUNDING"


@dataclass
class ExecutiveCandidateOption:
    """A bounded, optionated candidate resolution for an executive ambiguity."""
    key: str
    label: str
    description: str
    confidence: float
    rationale: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "metadata": self.metadata,
        }


@dataclass
class ExecutiveAmbiguityRecord:
    """An atomic ambiguity record captured prior to or during Executive Recap synthesis."""
    ambiguity_id: str
    domain: ExecutiveAmbiguityDomain
    title: str
    context_snippet: str
    candidates: List[ExecutiveCandidateOption] = field(default_factory=list)
    margin_delta: float = 0.0
    status: str = "OPEN"  # OPEN, RESOLVED, AUTO_CLEARED
    selected_key: Optional[str] = None
    selected_option: Optional[ExecutiveCandidateOption] = None
    cleared_at: Optional[str] = None
    operator_rationale: Optional[str] = None

    def __post_init__(self) -> None:
        self.calculate_margin()

    def calculate_margin(self) -> float:
        """Calculate margin delta = c_top - c_second across candidate options."""
        if not self.candidates:
            self.margin_delta = 0.0
            return 0.0
        sorted_cands = sorted(self.candidates, key=lambda c: c.confidence, reverse=True)
        top_conf = sorted_cands[0].confidence
        second_conf = sorted_cands[1].confidence if len(sorted_cands) > 1 else 0.0
        self.margin_delta = round(top_conf - second_conf, 4)
        return self.margin_delta

    def is_gate_eligible(self, margin_threshold: Optional[float] = None) -> bool:
        """Determines if ambiguity requires interactive HITL presentation under the 15% margin rule."""
        if self.status != "OPEN":
            return False
        if len(self.candidates) < 2:
            return False
        eff_threshold = margin_threshold if margin_threshold is not None else get_hitl_ambiguity_margin_threshold()
        return self.margin_delta < eff_threshold

    def resolve(self, chosen_key: str, rationale: str = "", timestamp_iso: str = "") -> None:
        """Atomically resolves the ambiguity with operator or autonomous decision."""
        self.selected_key = chosen_key
        for c in self.candidates:
            if c.key == chosen_key:
                self.selected_option = c
                break
        self.status = "RESOLVED"
        self.operator_rationale = rationale
        self.cleared_at = timestamp_iso

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ambiguity_id": self.ambiguity_id,
            "domain": self.domain.value if isinstance(self.domain, enum.Enum) else str(self.domain),
            "title": self.title,
            "context_snippet": self.context_snippet,
            "candidates": [c.to_dict() for c in self.candidates],
            "margin_delta": round(self.margin_delta, 4),
            "status": self.status,
            "selected_key": self.selected_key,
            "selected_option": self.selected_option.to_dict() if self.selected_option else None,
            "cleared_at": self.cleared_at,
            "operator_rationale": self.operator_rationale,
        }
