"""Dual-Pipeline Layer 0: Governed Core Contracts, Primitives, and Envelopes.

Defines the single source of truth for data models, identity, provenance,
and handoff structures across Pipelines A, B, C, and D.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ── Canonical Namespaces & Enums ─────────────────────────────────────────────

class Namespace(str, Enum):
    """Governed compute and storage namespaces (Laws 1 & 2)."""
    CLIENT_SAFE = "CLIENT_SAFE"
    INTERNAL_ADVISORY = "INTERNAL_ADVISORY"


CLIENT_SAFE_NAMESPACE = Namespace.CLIENT_SAFE.value
INTERNAL_ADVISORY_NAMESPACE = Namespace.INTERNAL_ADVISORY.value


class AdvisoryCategory(str, Enum):
    """Four closed strategic advisory categories for internal knowledge (Law 2)."""
    STAKEHOLDER_DYNAMICS = "STAKEHOLDER_DYNAMICS"
    DECISION_INFLUENCE = "DECISION_INFLUENCE"
    ADOPTION_RISK = "ADOPTION_RISK"
    BLOCKER = "BLOCKER"


class QuarantineReason(str, Enum):
    """Standardized reasons for applying soft-quarantine (Law 5)."""
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNVERIFIED_SOURCE = "UNVERIFIED_SOURCE"
    HIGH_TACTICALITY = "HIGH_TACTICALITY"
    LOW_STRATEGIC_VALUE = "LOW_STRATEGIC_VALUE"
    POLICY_RESTRICTION = "POLICY_RESTRICTION"
    AMBIGUOUS_ATTRIBUTION = "AMBIGUOUS_ATTRIBUTION"
    NONE = "NONE"


class TerminalState(str, Enum):
    """Terminal outcomes for dialectical decision queries."""
    DECISION_PRODUCED = "DECISION_PRODUCED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    ABSTENTION = "ABSTENTION"
    EVIDENCE_READY_DECISION_NOT_PRODUCED = "EVIDENCE_READY_DECISION_NOT_PRODUCED"
    IN_PROGRESS = "IN_PROGRESS"


class ConfidenceTier(str, Enum):
    """Calibrated confidence tiers for HITL and automated routing."""
    HIGH = "HIGH"        # >= 85%
    MEDIUM = "MEDIUM"    # 50% - 84%
    LOW = "LOW"          # < 50%


class OperationType(str, Enum):
    """Permitted graph mutation operations."""
    CREATE_NODE = "CREATE_NODE"
    UPDATE_NODE = "UPDATE_NODE"
    MERGE_NODE = "MERGE_NODE"
    CREATE_EDGE = "CREATE_EDGE"
    UPDATE_EDGE = "UPDATE_EDGE"
    QUARANTINE_NODE = "QUARANTINE_NODE"
    RELEASE_QUARANTINE = "RELEASE_QUARANTINE"


# ── Governed Layer 0 Primitives & Contracts ──────────────────────────────────

@dataclass(frozen=True)
class ContentDigest:
    """Cryptographic content hash representing verifiable physical reality."""
    sha256: str
    byte_count: int
    algorithm: str = "sha256"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ClaimCheck:
    """Immutable pointer binding a content digest to storage location (Law 4)."""
    claim_id: str
    digest: str
    storage_uri: str
    namespace: str = CLIENT_SAFE_NAMESPACE
    tenant_id: str = "default"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceSpan:
    """Exact textual / acoustic citation span in source material."""
    start_offset: int
    end_offset: int
    quote: str
    speaker: str = "Speaker"
    timestamp_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceRecord:
    """Ground-truth raw evidence segment recorded in append-only SQLite WAL (Law 4)."""
    evidence_id: str
    session_id: str
    source_id: str
    speaker: str
    text: str
    timestamp: float
    namespace: str = CLIENT_SAFE_NAMESPACE
    tenant_id: str = "default"
    digest: str = ""
    spans: List[EvidenceSpan] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


@dataclass
class CanonicalGraphNode:
    """Extracted public/client-safe entity adhering to W2 semantic spine."""
    node_id: str
    evidence_id: str
    namespace: str = CLIENT_SAFE_NAMESPACE
    tenant_id: str = "default"
    entity_type: str = "CONCEPT"
    name: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    is_quarantined: bool = False
    strategic_value: float = 0.5
    confidence: float = 0.5
    tacticality: float = 0.5
    quarantine_reason: str = QuarantineReason.NONE.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InternalAdvisoryNode:
    """Confidential internal strategic insight strictly contained to local Metal (Law 2)."""
    node_id: str
    evidence_id: str
    category: str = AdvisoryCategory.STAKEHOLDER_DYNAMICS.value
    claim_text: str = ""
    confidence: float = 0.5
    stealth_score: float = 1.0
    is_quarantined: bool = False
    tenant_id: str = "default"
    properties: Dict[str, Any] = field(default_factory=dict)
    namespace: str = INTERNAL_ADVISORY_NAMESPACE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GraphEdge:
    """Relationship between two graph nodes backed by immutable evidence."""
    edge_id: str
    source_node_id: str
    target_node_id: str
    predicate: str
    evidence_id: str
    namespace: str = CLIENT_SAFE_NAMESPACE
    confidence: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalGraphDelta:
    """Immutable batch of candidate mutations proposed for UWG release (Output 03)."""
    delta_id: str
    source_evidence_ids: List[str]
    nodes_created: List[CanonicalGraphNode] = field(default_factory=list)
    nodes_updated: List[CanonicalGraphNode] = field(default_factory=list)
    edges_created: List[GraphEdge] = field(default_factory=list)
    advisory_nodes: List[InternalAdvisoryNode] = field(default_factory=list)
    namespace: str = CLIENT_SAFE_NAMESPACE
    tenant_id: str = "default"
    digest: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "source_evidence_ids": self.source_evidence_ids,
            "nodes_created": [n.to_dict() for n in self.nodes_created],
            "nodes_updated": [n.to_dict() for n in self.nodes_updated],
            "edges_created": [e.to_dict() for e in self.edges_created],
            "advisory_nodes": [a.to_dict() for a in self.advisory_nodes],
            "namespace": self.namespace,
            "tenant_id": self.tenant_id,
            "digest": self.digest,
            "created_at": self.created_at,
        }


@dataclass
class ExecutiveRecap:
    """Email-ready C-suite executive briefing (Output 02, Law 7)."""
    recap_id: str
    meeting_id: str
    title: str
    date: str                        # Strictly MM/DD/YYYY
    start_time: str                  # ET
    end_time: str                    # ET
    objective: str
    attendees: List[Dict[str, Any]]
    key_decisions: List[str]
    next_steps: List[Dict[str, str]] # Owner, Action, Due Date (MM/DD/YYYY)
    status: str = "DRAFT"
    digest: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MutationRecord:
    """Cryptographic audit trail entry recording an exact state mutation."""
    mutation_id: str
    delta_id: str
    operation_type: str
    entity_id: str
    before_hash: str
    after_hash: str
    timestamp: float = field(default_factory=time.time)
    applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    """Result of validating an entity or handoff envelope against schema rules."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rule_ids: List[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PolicyContext:
    """Security, tenant isolation, and namespace clearance context (Law 6)."""
    tenant_id: str = "default"
    caller_id: str = "system"
    allowed_namespaces: List[str] = field(
        default_factory=lambda: [CLIENT_SAFE_NAMESPACE]
    )
    clearance_level: str = "STANDARD"
    allow_quarantined: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "caller_id": self.caller_id,
            "allowed_namespaces": list(self.allowed_namespaces),
            "clearance_level": self.clearance_level,
            "allow_quarantined": self.allow_quarantined,
        }


@dataclass
class QuarantineProfile:
    """Independent 4-dimensional soft-quarantine scoring (Law 5)."""
    strategic_value: float = 0.5
    evidence_quality: float = 0.5
    confidence: float = 0.5
    tacticality: float = 0.5
    is_quarantined: bool = False
    quarantine_reason: str = QuarantineReason.NONE.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FastPathContext:
    """
    Untrusted, ephemeral live STT streaming context (Law 3).
    Strictly read-only; prohibited from persisting facts or mutating graph.
    """
    event_id: str
    meeting_id: str
    speaker: str
    text: str
    start_ts: float
    end_ts: float
    badge: str = "[UNTRUSTED_FAST_PATH]"
    is_ephemeral: bool = True
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HandoffEnvelope:
    """Typed, versioned inter-pipeline transport envelope (A->B, B->C, C->D, A->D)."""
    envelope_id: str
    source_pipeline: str             # "PIPELINE_A" | "PIPELINE_B" | "PIPELINE_C" | "PIPELINE_D"
    target_pipeline: str
    payload_type: str                # e.g., "TRANSCRIPT_DELTA", "EVIDENCE_BATCH", "GRAPH_DELTA", "FAST_PATH"
    claim_check: ClaimCheck
    payload: Dict[str, Any]
    traceparent: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "source_pipeline": self.source_pipeline,
            "target_pipeline": self.target_pipeline,
            "payload_type": self.payload_type,
            "claim_check": self.claim_check.to_dict(),
            "payload": self.payload,
            "traceparent": self.traceparent,
            "created_at": self.created_at,
        }


@dataclass
class EvidencePack:
    """Unified evidence portfolio returned from Pipeline C to Pipeline D."""
    pack_id: str
    inquiry_id: str
    query_text: str
    canonical_facts: List[Dict[str, Any]] = field(default_factory=list)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    namespace: str = CLIENT_SAFE_NAMESPACE
    tenant_id: str = "default"
    retrieved_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeReceipt:
    """Immutable, digest-bound execution receipt."""
    receipt_id: str
    wave_id: str
    step_name: str
    status: str                      # "COMPLETE" | "BLOCKED" | "FAILED"
    input_digests: List[str] = field(default_factory=list)
    output_digests: List[str] = field(default_factory=list)
    receipt_digest: str = ""
    parent_receipt_digest: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
