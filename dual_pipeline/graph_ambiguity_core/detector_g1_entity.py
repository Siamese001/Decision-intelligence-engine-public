"""Domain G1 Detector: Entity Identity Resolution & Canonical URI Namespace Grounding.

Ensures all entities in Output 03 adhere to allowed W2 prefixes:
person:, org:, system:, workstream:, project:, metric:, role:, policy:
and resolves ambiguous entity identity bindings prior to graph mutation.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from .models import GraphAmbiguityDomain, GraphAmbiguityRecord, GraphCandidateOption

ALLOWED_ENTITY_PREFIXES: Set[str] = {
    "person:",
    "org:",
    "system:",
    "workstream:",
    "project:",
    "metric:",
    "role:",
    "policy:",
}

INVALID_PREFIX_MAPPING: Dict[str, List[Dict[str, Any]]] = {
    "vendor:": [
        {"prefix": "org:", "confidence": 0.55, "label": "Map to Organization (org:)"},
        {"prefix": "system:", "confidence": 0.45, "label": "Map to System (system:)"},
    ],
    "app:": [
        {"prefix": "system:", "confidence": 0.60, "label": "Map to Enterprise System (system:)"},
        {"prefix": "project:", "confidence": 0.40, "label": "Map to Project (project:)"},
    ],
    "service:": [
        {"prefix": "system:", "confidence": 0.55, "label": "Map to Enterprise System (system:)"},
        {"prefix": "org:", "confidence": 0.45, "label": "Map to Internal Team/Org (org:)"},
    ],
    "platform:": [
        {"prefix": "system:", "confidence": 0.58, "label": "Map to Enterprise System (system:)"},
        {"prefix": "project:", "confidence": 0.42, "label": "Map to Platform Project (project:)"},
    ],
    "database:": [
        {"prefix": "system:", "confidence": 0.65, "label": "Map to Database System (system:)"},
        {"prefix": "project:", "confidence": 0.35, "label": "Map to Data Project (project:)"},
    ],
}


class G1EntityIdentityDetector:
    """Detects entity identity and URI namespace ambiguities for Output 03."""

    def detect(
        self,
        f1_text: str,
        f3_candidate: Optional[Dict[str, Any]] = None,
    ) -> List[GraphAmbiguityRecord]:
        ambiguities: List[GraphAmbiguityRecord] = []

        if not f3_candidate:
            return ambiguities

        # Check entity_upserts in graph_mutations
        mutations = f3_candidate.get("graph_mutations", {})
        entity_upserts = mutations.get("entity_upserts", [])

        for idx, entity in enumerate(entity_upserts):
            entity_id = entity.get("entity_id", "") if isinstance(entity, dict) else str(entity)
            entity_name = entity.get("name", entity_id) if isinstance(entity, dict) else entity_id

            # 1. Check for illegal entity prefix
            prefix_match = re.match(r"^([a-z_]+:)", entity_id)
            if prefix_match:
                prefix = prefix_match.group(1)
                if prefix not in ALLOWED_ENTITY_PREFIXES:
                    raw_suffix = entity_id[len(prefix):]
                    candidates: List[GraphCandidateOption] = []
                    mappings = INVALID_PREFIX_MAPPING.get(prefix, [
                        {"prefix": "system:", "confidence": 0.52, "label": "Map to system:"},
                        {"prefix": "project:", "confidence": 0.48, "label": "Map to project:"},
                    ])
                    for m in mappings:
                        target_prefix = m["prefix"]
                        canonical_id = f"{target_prefix}{raw_suffix}"
                        candidates.append(
                            GraphCandidateOption(
                                key=canonical_id,
                                label=m["label"],
                                description=f"Resolve '{entity_id}' into canonical URI '{canonical_id}'",
                                confidence=m["confidence"],
                                rationale=f"Prefix '{prefix}' is forbidden; canonical substitute is '{target_prefix}'",
                                metadata={"original_id": entity_id, "target_prefix": target_prefix},
                            )
                        )

                    rec = GraphAmbiguityRecord(
                        ambiguity_id=f"G1-PREFIX-{idx}-{prefix.rstrip(':')}",
                        domain=GraphAmbiguityDomain.G1_ENTITY_IDENTITY_NAMESPACE,
                        title=f"Disallowed Entity Prefix: '{prefix}' in '{entity_id}'",
                        context_snippet=f"Entity '{entity_name}' uses disallowed prefix '{prefix}'. Must use allowed W2 prefix.",
                        candidates=candidates,
                    )
                    ambiguities.append(rec)
            else:
                # Missing prefix entirely
                candidates = [
                    GraphCandidateOption(
                        key=f"system:{entity_id.lower().replace(' ', '_')}",
                        label="Assign prefix 'system:'",
                        description=f"Resolve '{entity_id}' as enterprise technical system",
                        confidence=0.51,
                        rationale="Appears to describe enterprise infrastructure or software platform",
                    ),
                    GraphCandidateOption(
                        key=f"project:{entity_id.lower().replace(' ', '_')}",
                        label="Assign prefix 'project:'",
                        description=f"Resolve '{entity_id}' as organizational work initiative",
                        confidence=0.49,
                        rationale="Appears to describe planned or ongoing project stream",
                    ),
                ]
                rec = GraphAmbiguityRecord(
                    ambiguity_id=f"G1-NOPREFIX-{idx}",
                    domain=GraphAmbiguityDomain.G1_ENTITY_IDENTITY_NAMESPACE,
                    title=f"Missing Canonical Prefix: '{entity_id}'",
                    context_snippet=f"Entity '{entity_id}' does not specify a canonical prefix.",
                    candidates=candidates,
                )
                ambiguities.append(rec)

        return ambiguities
