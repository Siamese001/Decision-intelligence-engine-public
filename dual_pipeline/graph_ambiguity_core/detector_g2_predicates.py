"""Domain G2 Detector: Ontological Predicate Specificity & Edge Directionality.

Ensures all graph relationship signals adhere to canonical W2 predicates:
DEPENDS_ON, GOVERNS, VALIDATES, INTEGRATES_WITH, CONSTRAINS, CONTRIBUTES_TO,
CONTEXT_FOR, OWNS, ACCELERATES, MEMBER_OF.
Replaces generic/vague predicates (RELATED_TO, ASSOCIATED_WITH) and resolves
directionality ambiguities prior to GraphDB ingestion.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .models import GraphAmbiguityDomain, GraphAmbiguityRecord, GraphCandidateOption

CANONICAL_PREDICATES: Set[str] = {
    "DEPENDS_ON",
    "GOVERNS",
    "VALIDATES",
    "INTEGRATES_WITH",
    "CONSTRAINS",
    "CONTRIBUTES_TO",
    "CONTEXT_FOR",
    "OWNS",
    "ACCELERATES",
    "MEMBER_OF",
}

GENERIC_PREDICATE_REPLACEMENTS: Dict[str, List[Dict[str, Any]]] = {
    "RELATED_TO": [
        {"predicate": "INTEGRATES_WITH", "confidence": 0.54, "label": "Technical Integration (INTEGRATES_WITH)"},
        {"predicate": "DEPENDS_ON", "confidence": 0.46, "label": "Functional Dependency (DEPENDS_ON)"},
    ],
    "ASSOCIATED_WITH": [
        {"predicate": "CONTRIBUTES_TO", "confidence": 0.52, "label": "Strategic Contribution (CONTRIBUTES_TO)"},
        {"predicate": "CONTEXT_FOR", "confidence": 0.48, "label": "Contextual Alignment (CONTEXT_FOR)"},
    ],
    "DISCUSSED_WITH": [
        {"predicate": "GOVERNS", "confidence": 0.51, "label": "Governance Oversight (GOVERNS)"},
        {"predicate": "CONTRIBUTES_TO", "confidence": 0.49, "label": "Collaborative Delivery (CONTRIBUTES_TO)"},
    ],
    "CONNECTED_TO": [
        {"predicate": "INTEGRATES_WITH", "confidence": 0.55, "label": "API/System Integration (INTEGRATES_WITH)"},
        {"predicate": "DEPENDS_ON", "confidence": 0.45, "label": "Architectural Dependency (DEPENDS_ON)"},
    ],
    "LINKS_TO": [
        {"predicate": "INTEGRATES_WITH", "confidence": 0.53, "label": "Direct Link/Interface (INTEGRATES_WITH)"},
        {"predicate": "CONTEXT_FOR", "confidence": 0.47, "label": "Reference Context (CONTEXT_FOR)"},
    ],
}


class G2PredicateDirectionalityDetector:
    """Detects vague predicates and directionality ambiguities in Output 03 relationships."""

    def detect(
        self,
        f1_text: str,
        f3_candidate: Optional[Dict[str, Any]] = None,
    ) -> List[GraphAmbiguityRecord]:
        ambiguities: List[GraphAmbiguityRecord] = []

        if not f3_candidate:
            return ambiguities

        mutations = f3_candidate.get("graph_mutations", {})
        relationships = mutations.get("relationship_signals", [])

        for idx, rel in enumerate(relationships):
            if not isinstance(rel, dict):
                continue
            from_id = rel.get("from_id", "")
            to_id = rel.get("to_id", "")
            predicate = rel.get("relationship", "").upper()

            # 1. Check for generic or non-canonical predicate
            if predicate not in CANONICAL_PREDICATES:
                replacements = GENERIC_PREDICATE_REPLACEMENTS.get(
                    predicate,
                    [
                        {"predicate": "INTEGRATES_WITH", "confidence": 0.52, "label": "Technical Integration (INTEGRATES_WITH)"},
                        {"predicate": "DEPENDS_ON", "confidence": 0.48, "label": "Functional Dependency (DEPENDS_ON)"},
                    ],
                )
                candidates: List[GraphCandidateOption] = []
                for rep in replacements:
                    cand_pred = rep["predicate"]
                    candidates.append(
                        GraphCandidateOption(
                            key=f"{from_id}->{cand_pred}->{to_id}",
                            label=rep["label"],
                            description=f"Specify edge as ({from_id}) -[{cand_pred}]-> ({to_id})",
                            confidence=rep["confidence"],
                            rationale=f"Generic predicate '{predicate}' violates W2 ontology; replace with specific canonical verb '{cand_pred}'",
                            metadata={"from_id": from_id, "to_id": to_id, "predicate": cand_pred},
                        )
                    )

                rec = GraphAmbiguityRecord(
                    ambiguity_id=f"G2-PREDICATE-{idx}-{predicate}",
                    domain=GraphAmbiguityDomain.G2_PREDICATE_DIRECTIONALITY,
                    title=f"Non-Canonical Predicate: '{predicate}' between '{from_id}' and '{to_id}'",
                    context_snippet=f"Edge ({from_id}) -[{predicate}]-> ({to_id}) uses non-canonical or generic predicate '{predicate}'.",
                    candidates=candidates,
                )
                ambiguities.append(rec)

            # 2. Check for directional asymmetry in DEPENDS_ON / GOVERNS
            if predicate in ("DEPENDS_ON", "GOVERNS") and from_id.startswith("system:") and to_id.startswith("system:"):
                # If transcript mentions bidirectional or mutual coupling
                reverse_predicate = "DEPENDS_ON" if predicate == "DEPENDS_ON" else "GOVERNS"
                candidates = [
                    GraphCandidateOption(
                        key=f"{from_id}->{predicate}->{to_id}",
                        label=f"Forward Direction: ({from_id}) -[{predicate}]-> ({to_id})",
                        description=f"{from_id} is downstream consumer depending on {to_id}",
                        confidence=0.53,
                        rationale="Source text implies upstream service role for target system",
                        metadata={"from_id": from_id, "to_id": to_id, "predicate": predicate},
                    ),
                    GraphCandidateOption(
                        key=f"{to_id}->{reverse_predicate}->{from_id}",
                        label=f"Inverted Direction: ({to_id}) -[{reverse_predicate}]-> ({from_id})",
                        description=f"{to_id} is downstream consumer depending on {from_id}",
                        confidence=0.47,
                        rationale="System interaction could indicate inverted dependency flow",
                        metadata={"from_id": to_id, "to_id": from_id, "predicate": reverse_predicate},
                    ),
                ]
                rec = GraphAmbiguityRecord(
                    ambiguity_id=f"G2-DIRECTION-{idx}-{from_id}-{to_id}",
                    domain=GraphAmbiguityDomain.G2_PREDICATE_DIRECTIONALITY,
                    title=f"Dependency Edge Directionality: '{from_id}' vs '{to_id}'",
                    context_snippet=f"Direction of {predicate} between {from_id} and {to_id} requires confirmation.",
                    candidates=candidates,
                )
                ambiguities.append(rec)

        return ambiguities
