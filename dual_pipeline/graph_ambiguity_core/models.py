"""Data models for Output 3 Knowledge Graph Ambiguity Detection & Pre-Synthesis HITL Gating."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class GraphAmbiguityDomain(str, enum.Enum):
    """The 4 core ambiguity domains governing Knowledge Graph (Output 03) synthesis."""

    G1_ENTITY_IDENTITY_NAMESPACE = "G1_ENTITY_IDENTITY_NAMESPACE"
    G2_PREDICATE_DIRECTIONALITY = "G2_PREDICATE_DIRECTIONALITY"
    G3_EPISTEMIC_CLAIM_STATUS = "G3_EPISTEMIC_CLAIM_STATUS"
    G4_BIPARTITE_C19_PARTITION = "G4_BIPARTITE_C19_PARTITION"


@dataclass
class GraphCandidateOption:
    """A bounded, optionated candidate resolution for a graph ambiguity."""

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
class GraphAmbiguityRecord:
    """An atomic ambiguity record captured prior to or during Knowledge Graph synthesis."""

    ambiguity_id: str
    domain: GraphAmbiguityDomain
    title: str
    context_snippet: str
    candidates: List[GraphCandidateOption] = field(default_factory=list)
    margin_delta: float = 0.0
    status: str = "OPEN"  # OPEN, RESOLVED, AUTO_CLEARED
    selected_key: Optional[str] = None
    selected_option: Optional[GraphCandidateOption] = None
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
        """Determines if ambiguity requires interactive operator decision under SSOT margin rule."""
        if self.status != "OPEN":
            return False
        if len(self.candidates) < 2:
            return False
        from dual_pipeline.parameters import needs_operator_decision

        sorted_cands = sorted(self.candidates, key=lambda c: c.confidence, reverse=True)
        top_conf = sorted_cands[0].confidence
        second_conf = sorted_cands[1].confidence if len(sorted_cands) > 1 else 0.0
        return needs_operator_decision(top_conf, second_conf, threshold=margin_threshold)

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
