"""
dual_pipeline/query_core/offline_models.py

Data models for offline graph context retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class GraphEntityReference:
    """Represents a verified entity retrieved from the canonical graph."""
    node_id: str
    canonical_name: str
    display_name: str
    node_type: str
    organization: Optional[str] = None
    role: Optional[str] = None
    associated_workstreams: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphAssertionReference:
    """Represents an active assertion or decision retrieved from the graph."""
    assertion_id: str
    predicate: str
    subject_node_id: str
    object_node_id: Optional[str] = None
    claim_text: Optional[str] = None
    kind: str = "summary"
    observed_at: Optional[str] = None


@dataclass
class GraphContextPacket:
    """Structured context envelope extracted for meeting synthesis."""
    entities: List[GraphEntityReference] = field(default_factory=list)
    decisions: List[GraphAssertionReference] = field(default_factory=list)
    risks: List[GraphAssertionReference] = field(default_factory=list)
    lexical_terms: List[str] = field(default_factory=list)
    formatted_prior_context: str = ""
    duration_ms: float = 0.0
    graph_path_resolved: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return (
            len(self.entities) == 0
            and len(self.decisions) == 0
            and len(self.risks) == 0
            and len(self.lexical_terms) == 0
        )

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return {
            "entities": [asdict(e) for e in self.entities],
            "decisions": [asdict(d) for d in self.decisions],
            "risks": [asdict(r) for r in self.risks],
            "lexical_terms": self.lexical_terms,
            "duration_ms": self.duration_ms,
            "is_empty": self.is_empty,
        }
