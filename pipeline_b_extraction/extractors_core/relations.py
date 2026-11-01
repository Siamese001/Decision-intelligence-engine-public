"""Pipeline B (Extraction): Relational Edge & Assertion Synthesis Core.

Synthesizes deterministic relational edges between co-occurring canonical nodes
and W2 Semantic Spine assertions from extracted entities.
"""
from __future__ import annotations

from typing import List

from storage.database import (
    AssertionRecord,
    CanonicalGraphNode,
    EvidenceRecord,
    GraphEdge,
)
from storage.identifiers import (
    deterministic_assertion_id,
    deterministic_edge_id,
)


def generate_relational_edges(
    evidence: EvidenceRecord,
    nodes: List[CanonicalGraphNode],
) -> List[GraphEdge]:
    """Synthesizes deterministic relational edges between co-occurring canonical nodes."""
    edges: List[GraphEdge] = []
    if len(nodes) >= 2:
        for i in range(len(nodes) - 1):
            src = nodes[i]
            dst = nodes[i + 1]
            edge_id = deterministic_edge_id(evidence.evidence_id, src.node_id, "CONNECTS_TO", dst.node_id)
            edges.append(GraphEdge(
                edge_id=edge_id,
                from_node_id=src.node_id,
                predicate="CONNECTS_TO",
                to_node_id=dst.node_id,
                weight=1.0,
                confidence=min(src.confidence, dst.confidence),
                is_quarantined=src.is_quarantined or dst.is_quarantined,
                namespace="CLIENT_SAFE",
                properties={"context": evidence.text, "speaker": evidence.speaker},
            ))
    return edges


def generate_assertions(
    evidence: EvidenceRecord,
    nodes: List[CanonicalGraphNode],
) -> List[AssertionRecord]:
    """Synthesizes W2 Semantic Spine first-class assertions from extracted nodes."""
    assertions: List[AssertionRecord] = []
    for node in nodes:
        asrt_id = deterministic_assertion_id(evidence.evidence_id, node.node_id, "CONFIRMS")
        asrt_type = (
            "DECISION" if "DECISION" in node.type
            else ("TECHNICAL" if "TODO" in node.type or "SPECIFICATION" in node.type else "SUMMARY")
        )
        assertions.append(AssertionRecord(
            assertion_id=asrt_id,
            subject_node_id=node.node_id,
            predicate="DECIDED" if asrt_type == "DECISION" else "SPECIFIED",
            object_node_id=None,
            literal_value=node.name,
            assertion_type=asrt_type,
            confidence=node.confidence,
            status="CONFIRMED",
            observed_at=None,
            stale_after=None,
            namespace="CLIENT_SAFE",
        ))
    return assertions
