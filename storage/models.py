"""
Dual-Artery Enterprise Hybrid Architecture: Storage Models & Value Objects.
Defines enums, records, and mapper functions for canonical and shadow entities.
"""
from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class WorkItemState(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class AdvisoryCategory(str, enum.Enum):
    STAKEHOLDER_DYNAMICS = "STAKEHOLDER_DYNAMICS"
    DECISION_INFLUENCE = "DECISION_INFLUENCE"
    ADOPTION_RISK = "ADOPTION_RISK"
    BLOCKER = "BLOCKER"


@dataclass
class EvidenceRecord:
    """Immutable ground-truth raw STT audio/text segment."""
    evidence_id: str
    meeting_id: str
    speaker: str
    text: str
    start_timestamp: float
    end_timestamp: Optional[float] = None
    is_final: bool = True
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkItem:
    """Durable queue unit consumed by Slow Path worker pools."""
    event_id: str
    evidence_id: str
    state: WorkItemState
    retry_count: int = 0
    checkpoint: Optional[str] = None
    locked_at: Optional[float] = None
    locked_by: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value if isinstance(self.state, WorkItemState) else self.state
        return d


@dataclass
class CanonicalGraphNode:
    """
    Public/Client-Safe Graph Fact.
    Zero biometric, zero Machiavellian leakage. Bound directly to evidence_id.
    """
    node_id: str
    evidence_id: str
    type: str
    name: str
    properties: Dict[str, Any] = field(default_factory=dict)
    strategic_value: float = 0.0      # 0.0 - 10.0 scale
    evidence_quality: float = 1.0     # 0.0 - 1.0 scale
    confidence: float = 1.0           # 0.0 - 1.0 scale
    tacticality: float = 0.0          # 0.0 - 10.0 (High = tactical to-do)
    is_quarantined: bool = False      # Soft-quarantine flag
    quarantine_reason: Optional[str] = None
    namespace: str = "CLIENT_SAFE"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AssertionRecord:
    """
    W2 Semantic Spine First-Class Assertion.
    Captures temporal decisions, risks, dependencies, and strategic commitments.
    """
    assertion_id: str
    subject_node_id: str
    predicate: str
    object_node_id: Optional[str] = None
    literal_value: Optional[str] = None
    assertion_type: str = "DECISION"  # DECISION, RISK, DEPENDENCY, TECHNICAL, STRATEGIC, SUMMARY
    confidence: float = 1.0
    status: str = "CONFIRMED"          # PROPOSED, CONFIRMED, SUPERSEDED, RETRACTED
    observed_at: Optional[str] = None
    stale_after: Optional[str] = None
    namespace: str = "CLIENT_SAFE"
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InternalAdvisoryNode:
    """
    Encrypted Local Shadow Graph Fact.
    Captures political capital, blockers, adoption risks, and stakeholder dynamics.
    Strictly isolated from client-facing exports and standard vector queries.
    """
    node_id: str
    evidence_id: str
    type: str
    name: str
    category: AdvisoryCategory
    target_entity_hmac: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    strategic_value: float = 0.0      # 0.0 - 10.0 scale
    tacticality: float = 0.0          # 0.0 - 10.0 scale
    confidence: float = 1.0           # 0.0 - 1.0 scale
    is_quarantined: bool = False
    quarantine_reason: Optional[str] = None
    stealth_score: float = 1.0        # Sensitivity tier (1.0 = Highly Confidential)
    namespace: str = "INTERNAL_ADVISORY"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value if isinstance(self.category, AdvisoryCategory) else self.category
        return d


@dataclass
class GraphEdge:
    """
    First-class relational graph edge connecting two nodes with typed predicate.
    Enables multi-hop traversal, causal chains (BLOCKS, REQUIRES, ENABLES, MITIGATES),
    and graph-native neighborhood queries.
    """
    edge_id: str
    from_node_id: str
    predicate: str
    to_node_id: str
    weight: float = 1.0
    confidence: float = 1.0
    is_quarantined: bool = False
    quarantine_reason: Optional[str] = None
    namespace: str = "CLIENT_SAFE"
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def row_to_canonical_node(r: Any) -> CanonicalGraphNode:
    props = {}
    if r["properties"]:
        try:
            props = json.loads(r["properties"])
        except Exception:
            props = {"raw": r["properties"]}

    return CanonicalGraphNode(
        node_id=r["node_id"],
        evidence_id=r["evidence_id"],
        type=r["type"],
        name=r["name"],
        properties=props,
        strategic_value=float(r["strategic_value"]),
        evidence_quality=float(r["evidence_quality"]),
        confidence=float(r["confidence"]),
        tacticality=float(r["tacticality"]),
        is_quarantined=bool(r["is_quarantined"]),
        quarantine_reason=r["quarantine_reason"],
        namespace=r["namespace"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def row_to_advisory_node(r: Any) -> InternalAdvisoryNode:
    props = {}
    if r["properties"]:
        try:
            props = json.loads(r["properties"])
        except Exception:
            props = {"raw": r["properties"]}

    cat_val = r["category"]
    try:
        category = AdvisoryCategory(cat_val)
    except ValueError:
        category = AdvisoryCategory.STAKEHOLDER_DYNAMICS

    target_hmac = r["target_entity_hmac"] if "target_entity_hmac" in r.keys() else None

    return InternalAdvisoryNode(
        node_id=r["node_id"],
        evidence_id=r["evidence_id"],
        type=r["type"],
        name=r["name"],
        category=category,
        target_entity_hmac=target_hmac,
        properties=props,
        strategic_value=float(r["strategic_value"]),
        tacticality=float(r["tacticality"]),
        confidence=float(r["confidence"]),
        is_quarantined=bool(r["is_quarantined"]),
        quarantine_reason=r["quarantine_reason"],
        stealth_score=float(r["stealth_score"]),
        namespace=r["namespace"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@dataclass
class ExecutiveDecisionImpact:
    """Canonical model linking architectural decisions to business capabilities and financial blast radius."""
    impact_id: str
    decision_node_id: str
    capability_l1: str
    capability_l2: Optional[str] = None
    financial_value_usd: float = 0.0
    capex_opex_classification: str = "HYBRID"
    risk_blast_radius_tier: str = "MEDIUM"
    downstream_affected_systems: List[str] = field(default_factory=list)
    strategic_kpi_linked: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategicValueStream:
    """Canonical model mapping technical assets to high-level enterprise value streams."""
    stream_id: str
    stream_name: str
    business_domain: str
    lead_executive_sponsor: str
    underlying_technical_assets: List[str] = field(default_factory=list)
    revenue_impact_tier: str = "CORE_REVENUE"
    maturity_score: float = 0.0
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ThoughtLeadershipBlueprint:
    """Canonical model for reusable architectural blueprints and publication frameworks."""
    blueprint_id: str
    title: str
    target_publication: str
    abstract_concept: str
    executive_takeaway: str = ""
    status: str = "DRAFT"
    anonymized_industry_patterns: List[str] = field(default_factory=list)
    architectural_exhibits: List[str] = field(default_factory=list)
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StakeholderInfluenceRecord:
    """Encrypted shadow graph record for informal organizational power and stakeholder stance."""
    influence_id: str
    stakeholder_name_hmac: str
    informal_role: str
    title_authority_gap: str
    sentiment_trajectory: str
    single_point_of_failure_risk: bool = False
    advisory_notes_encrypted: str = ""
    is_quarantined: bool = False
    quarantine_reason: Optional[str] = None
    stealth_score: float = 1.0
    namespace: str = "INTERNAL_ADVISORY"
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CareerLeverageRecord:
    """Encrypted shadow graph record for $1.2M executive career positioning and negotiation capital."""
    milestone_id: str
    target_outcome: str
    executive_narrative: str
    demonstrated_multi_million_impact: float = 0.0
    thought_leadership_citations: List[str] = field(default_factory=list)
    strategic_leverage_levers: List[str] = field(default_factory=list)
    stealth_score: float = 1.0
    namespace: str = "INTERNAL_ADVISORY"
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_assertion_evidence_bindings(
    assertion_id: str,
    evidence_bindings: Optional[List[Tuple[str, str]]] = None,
    *,
    require_evidence: bool = False,
) -> List[Tuple[str, str]]:
    """Validate assertion evidence bindings under Law 4 ClaimCheck."""
    if evidence_bindings is not None:
        validated = [
            (str(k).strip(), str(v).strip())
            for k, v in evidence_bindings
            if str(k).strip() and str(v).strip()
        ]
        if not validated:
            raise ValueError(
                f"Law 4 ClaimCheck Violation: Assertion '{assertion_id}' "
                "cannot be upserted with empty or blank evidence_bindings."
            )
        return validated
    if require_evidence:
        raise ValueError(
            f"Law 4 ClaimCheck Violation: Assertion '{assertion_id}' "
            "requires non-empty evidence_bindings."
        )
    return []

