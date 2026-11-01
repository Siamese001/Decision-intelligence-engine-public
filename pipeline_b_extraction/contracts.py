"""Pipeline B (Extraction) Layer 0: Foundational Data Contracts, Types, and Protocols.

Zero-dependency contracts defining data models and interfaces across Pipeline B components.
No component in L0 may import from L1-L6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from storage.models import (
    AssertionRecord,
    CanonicalGraphNode,
    EvidenceRecord,
    GraphEdge,
    InternalAdvisoryNode,
    WorkItem,
)


class ExtractionInferenceError(RuntimeError):
    """Raised when an extraction model fails or produces unparseable output in non-mock mode."""

    def __init__(
        self,
        message: str,
        evidence_id: Optional[str] = None,
        original_exception: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.evidence_id = evidence_id
        self.original_exception = original_exception


@dataclass
class ForkedExtractionResult:
    """Represents the multi-agent partitioned extraction results across Compute Fork boundaries."""
    canonical_nodes: List[CanonicalGraphNode] = field(default_factory=list)
    advisory_nodes: List[InternalAdvisoryNode] = field(default_factory=list)
    canonical_edges: List[GraphEdge] = field(default_factory=list)
    assertions: List[AssertionRecord] = field(default_factory=list)
    client_safe_raw: Optional[Dict[str, Any]] = None
    advisory_raw: Optional[Dict[str, Any]] = None


@dataclass
class DiscourseBatch:
    """Aggregated discourse window combining adjacent evidence items."""
    work_items: List[WorkItem]
    evidence_records: List[EvidenceRecord]
    combined_text: str
    speaker_summary: str
    primary_evidence_id: str


class ExtractionTaskProcessor(Protocol):
    """Protocol for processors that execute compute fork extraction on discourse windows."""

    async def execute_fork(self, evidence: EvidenceRecord) -> ForkedExtractionResult:
        """Execute compute fork extraction on a single evidence record."""
        ...
