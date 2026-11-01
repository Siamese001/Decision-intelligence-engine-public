"""Pipeline B (Extraction): Offline Mock Extractors for Testing.

Used exclusively in test environments or when mock_mode=True is explicitly configured.
"""
from __future__ import annotations

from typing import List, Optional

from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    EvidenceRecord,
    InternalAdvisoryNode,
)
from storage.identifiers import (
    deterministic_advisory_node_id,
    deterministic_canonical_node_id,
)


def mock_extract_canonical(
    evidence: EvidenceRecord,
    clean_text: Optional[str] = None,
) -> List[CanonicalGraphNode]:
    """Deterministic mock extraction for tests and offline operations."""
    txt = (clean_text if clean_text is not None else evidence.text).lower()
    nodes: List[CanonicalGraphNode] = []

    if any(w in txt for w in ["schedule", "invite", "calendar", "follow-up", "to-do", "operational"]):
        node_id = deterministic_canonical_node_id(evidence.evidence_id, "OPERATIONAL_TODO", "Action Item: Schedule Follow-up")
        nodes.append(CanonicalGraphNode(
            node_id=node_id,
            evidence_id=evidence.evidence_id,
            type="OPERATIONAL_TODO",
            name="Action Item: Schedule Follow-up",
            properties={"context": txt, "assignee": evidence.speaker},
            strategic_value=1.5,
            tacticality=9.0,
            evidence_quality=0.9,
            confidence=0.9,
            is_quarantined=False,
            namespace="CLIENT_SAFE",
        ))

    if any(w in txt for w in ["architect", "dual-artery", "wal", "finalized", "storage", "decision", "decided", "decouple"]) or not nodes:
        node_id = deterministic_canonical_node_id(evidence.evidence_id, "ARCHITECTURAL_DECISION", "Governed Architecture Alignment")
        nodes.append(CanonicalGraphNode(
            node_id=node_id,
            evidence_id=evidence.evidence_id,
            type="ARCHITECTURAL_DECISION",
            name="Governed Architecture Alignment",
            properties={"context": txt, "speaker": evidence.speaker},
            strategic_value=8.5,
            tacticality=1.0,
            evidence_quality=1.0,
            confidence=0.95,
            is_quarantined=False,
            namespace="CLIENT_SAFE",
        ))
    return nodes


def mock_extract_advisory(evidence: EvidenceRecord) -> List[InternalAdvisoryNode]:
    """Deterministic mock advisory extraction for tests and offline operations."""
    txt = evidence.text.lower()
    nodes: List[InternalAdvisoryNode] = []

    if any(w in txt for w in ["risk", "reluctant", "delay", "blocker", "hesitant", "friction"]):
        node_id = deterministic_advisory_node_id(evidence.evidence_id, AdvisoryCategory.ADOPTION_RISK.value, "Stakeholder Adoption Friction")
        nodes.append(InternalAdvisoryNode(
            node_id=node_id,
            evidence_id=evidence.evidence_id,
            type="ADOPTION_RISK",
            name="Stakeholder Adoption Friction",
            category=AdvisoryCategory.ADOPTION_RISK,
            properties={"speaker": evidence.speaker, "signal": evidence.text},
            strategic_value=8.0,
            tacticality=1.0,
            confidence=0.92,
            is_quarantined=False,
            stealth_score=0.9,
            namespace="INTERNAL_ADVISORY",
        ))
    elif any(w in txt for w in ["sponsor", "align", "cto", "vp", "director", "decision"]):
        node_id = deterministic_advisory_node_id(evidence.evidence_id, AdvisoryCategory.DECISION_INFLUENCE.value, "Executive Sponsorship Path")
        nodes.append(InternalAdvisoryNode(
            node_id=node_id,
            evidence_id=evidence.evidence_id,
            type="DECISION_INFLUENCE",
            name="Executive Sponsorship Path",
            category=AdvisoryCategory.DECISION_INFLUENCE,
            properties={"speaker": evidence.speaker, "rationale": evidence.text},
            strategic_value=9.0,
            tacticality=0.5,
            confidence=0.95,
            is_quarantined=False,
            stealth_score=1.0,
            namespace="INTERNAL_ADVISORY",
        ))

    if not nodes:
        node_id = deterministic_advisory_node_id(evidence.evidence_id, AdvisoryCategory.STAKEHOLDER_DYNAMICS.value, "General Stakeholder Alignment")
        nodes.append(InternalAdvisoryNode(
            node_id=node_id,
            evidence_id=evidence.evidence_id,
            type="STAKEHOLDER_DYNAMICS",
            name="General Stakeholder Alignment",
            category=AdvisoryCategory.STAKEHOLDER_DYNAMICS,
            properties={"speaker": evidence.speaker, "signal": evidence.text},
            strategic_value=5.0,
            tacticality=1.0,
            confidence=0.85,
            is_quarantined=False,
            stealth_score=0.8,
            namespace="INTERNAL_ADVISORY",
        ))
    return nodes
