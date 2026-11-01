"""Data contracts for Staleness Invalidation and Scope Modulation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StrategicIntentSignal:
    """Represents an explicit negative or modulatory scope signal extracted from a meeting."""

    signal_id: str
    target_topic_or_entity: str
    posture: str  # "DEFERRED_OUT_OF_SCOPE", "COMPLETED_IN_READOUT", "DISCONTINUED", "ACTIVE_REPLACED"
    evidence_statement_ids: List[str]
    rationale: str
    observed_at: str  # MM/DD/YYYY
    scope_type: str = "WORKSTREAM"  # WORKSTREAM, INITIATIVE, OPERATING_MODEL, FEATURE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "target_topic_or_entity": self.target_topic_or_entity,
            "posture": self.posture,
            "evidence_statement_ids": list(self.evidence_statement_ids),
            "rationale": self.rationale,
            "observed_at": self.observed_at,
            "scope_type": self.scope_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StrategicIntentSignal:
        return cls(
            signal_id=data["signal_id"],
            target_topic_or_entity=data["target_topic_or_entity"],
            posture=data["posture"],
            evidence_statement_ids=list(data.get("evidence_statement_ids", data.get("evidence_ids", []))),
            rationale=data.get("rationale", ""),
            observed_at=data.get("observed_at", ""),
            scope_type=data.get("scope_type", "WORKSTREAM"),
        )


@dataclass(frozen=True)
class TwoTrackClassification:
    """Classification of an item into Track A (Strategic Focus) vs Track B (Compliance/Risk)."""

    item_id: str
    item_type: str  # "action", "assertion", "validation"
    track: str  # "TRACK_A_STRATEGIC", "TRACK_B_COMPLIANCE"
    confidence_score: float
    second_confidence_score: float
    margin_delta: float
    hitl_required: bool
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "track": self.track,
            "confidence_score": round(self.confidence_score, 4),
            "second_confidence_score": round(self.second_confidence_score, 4),
            "margin_delta": round(self.margin_delta, 4),
            "hitl_required": self.hitl_required,
            "rationale": self.rationale,
        }


@dataclass
class InvalidationResolution:
    """Result of cascade invalidation against active SQLite graph."""

    directive_id: str
    meeting_id: str
    superseded_assertions: List[str] = field(default_factory=list)
    closed_actions: List[str] = field(default_factory=list)
    quarantined_compliance_items: List[str] = field(default_factory=list)
    graph_mutations: List[Dict[str, Any]] = field(default_factory=list)
    execution_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    receipt_digest: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "directive_id": self.directive_id,
            "meeting_id": self.meeting_id,
            "superseded_assertions": list(self.superseded_assertions),
            "closed_actions": list(self.closed_actions),
            "quarantined_compliance_items": list(self.quarantined_compliance_items),
            "graph_mutations": list(self.graph_mutations),
            "execution_timestamp": self.execution_timestamp,
            "receipt_digest": self.receipt_digest,
        }
