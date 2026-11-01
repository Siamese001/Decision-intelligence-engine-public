"""Pipeline C (Retrieval) Layer 0: Foundational Retrieval Contracts, Data Types, and Namespaces.

Zero-dependency data models defining the retrieval units across Pipeline C.
No component in L0 may import from L1-L6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CLIENT_SAFE_NAMESPACE = "CLIENT_SAFE"
INTERNAL_ADVISORY_NAMESPACE = "INTERNAL_ADVISORY"


@dataclass
class RetrievedFact:
    """Unified retrieval unit presented to Copilot or UI."""
    node_id: str
    evidence_id: str
    namespace: str
    type: str
    name: str
    snippet: str
    strategic_value: float
    confidence: float
    tacticality: float
    is_quarantined: bool
    relevance_score: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)
    connected_edges: List[Dict[str, Any]] = field(default_factory=list)
    traceparent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "evidence_id": self.evidence_id,
            "namespace": self.namespace,
            "type": self.type,
            "name": self.name,
            "snippet": self.snippet,
            "strategic_value": self.strategic_value,
            "confidence": self.confidence,
            "tacticality": self.tacticality,
            "is_quarantined": self.is_quarantined,
            "relevance_score": self.relevance_score,
            "properties": self.properties,
            "connected_edges": self.connected_edges,
        }


@dataclass
class HybridWeights:
    """Dynamic retrieval weights for Quad-Engine BGE-M3."""
    dense: float = 0.40
    sparse: float = 0.20
    bm25: float = 0.20
    colbert: float = 0.20

    def validate(self) -> bool:
        total = self.dense + self.sparse + self.bm25 + self.colbert
        return abs(total - 1.0) < 1e-4
