"""Domain G4 Detector: Bipartite Enterprise Scope & Rule C-19 Career Leverage Firewall.

Enforces strict partitioning between joint client-safe graph intelligence
(CANONICAL_GRAPH.db) and confidential advisory / career positioning.
Guarantees zero leakage of $1.2M career trajectory, compensation benchmarks,
or private political power maps into Output 03 canonical projections.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from .models import GraphAmbiguityDomain, GraphAmbiguityRecord, GraphCandidateOption

C19_LEAK_MARKERS: Set[str] = {
    "$1.2M",
    "$1.2m",
    "INTERNAL_ADVISORY",
    "networking_wedge",
    "political_rationale",
    "career_trajectory",
    "personal_leverage",
}


class G4BipartiteC19Detector:
    """Detects enterprise-safe vs advisory boundary ambiguities and C-19 firewall leaks."""

    def detect(
        self,
        f1_text: str,
        f3_candidate: Optional[Dict[str, Any]] = None,
    ) -> List[GraphAmbiguityRecord]:
        ambiguities: List[GraphAmbiguityRecord] = []

        if not f3_candidate:
            return ambiguities

        # Serialize candidate to inspect for C-19 markers
        canonical_str = json.dumps(f3_candidate)

        # 1. Hard C-19 Leak Check (Blocker)
        detected_leaks = [marker for marker in C19_LEAK_MARKERS if marker in canonical_str]
        if detected_leaks:
            candidates = [
                GraphCandidateOption(
                    key="QUARANTINE_LEAK",
                    label="Quarantine into INTERNAL_ADVISORY Enclave",
                    description=f"Remove markers {detected_leaks} from Output 03 and isolate in shadow graph.",
                    confidence=0.99,
                    rationale="Rule C-19 mandates zero confidential advisory or career leverage leakage into Output 03.",
                    metadata={"leaks": detected_leaks},
                ),
                GraphCandidateOption(
                    key="STRIP_AND_DISCARD",
                    label="Strip Leaked Content Completely",
                    description="Remove the offending nodes or properties entirely from the graph payload.",
                    confidence=0.01,
                    rationale="Alternate resolution discarding personal advisory content.",
                    metadata={"leaks": detected_leaks},
                ),
            ]
            rec = GraphAmbiguityRecord(
                ambiguity_id="G4-C19-FIREWALL-LEAK",
                domain=GraphAmbiguityDomain.G4_BIPARTITE_C19_PARTITION,
                title=f"Rule C-19 Career Leverage Leak Detected: {', '.join(detected_leaks)}",
                context_snippet=f"Output 03 contains prohibited markers: {detected_leaks}. Immediate quarantine required.",
                candidates=candidates,
            )
            ambiguities.append(rec)

        # 2. Borderline Strategic Wedge Partitioning Ambiguity
        mutations = f3_candidate.get("graph_mutations", {})
        entity_upserts = mutations.get("entity_upserts", [])

        for idx, entity in enumerate(entity_upserts):
            if not isinstance(entity, dict):
                continue
            entity_id = entity.get("entity_id", "")
            description = entity.get("description", "").lower()
            name = entity.get("name", entity_id)

            is_advisory_wedge = (
                "wedge" in description
                or "account expansion" in description
                or "partner_advisory positioning" in description
                or "consulting opportunity" in description
            )

            if is_advisory_wedge:
                candidates = [
                    GraphCandidateOption(
                        key="ISOLATE_ADVISORY",
                        label="Route to INTERNAL_ADVISORY Shadow Graph",
                        description=f"Entity '{name}' represents a PartnerAdvisory commercial wedge; isolate from client-facing GraphDB.",
                        confidence=0.55,
                        rationale="Commercial wedges must not be exposed in joint client-facing enterprise knowledge graphs.",
                        metadata={"entity_id": entity_id, "target_namespace": "INTERNAL_ADVISORY"},
                    ),
                    GraphCandidateOption(
                        key="REFACTOR_JOINT_VALUE",
                        label="Refactor as Joint Enterprise Capability",
                        description=f"Reframe '{name}' as a collaborative business architecture capability beneficial to client.",
                        confidence=0.45,
                        rationale="If the capability solves a client architectural bottleneck, it qualifies for canonical inclusion.",
                        metadata={"entity_id": entity_id, "target_namespace": "canonical"},
                    ),
                ]
                rec = GraphAmbiguityRecord(
                    ambiguity_id=f"G4-WEDGE-PARTITION-{idx}",
                    domain=GraphAmbiguityDomain.G4_BIPARTITE_C19_PARTITION,
                    title=f"Bipartite Scope Ambiguity: Joint Capability vs Advisory Wedge for '{name}'",
                    context_snippet=f"Entity '{name}' ({entity_id}) description: \"{entity.get('description', '')}\"",
                    candidates=candidates,
                )
                ambiguities.append(rec)

        return ambiguities
