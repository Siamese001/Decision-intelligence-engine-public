"""L2 Execute Healing Contracts & Fact-Preservation Invariants.

Provides immutable data contracts governing in-flight surgical repair across
Pipelines A–D, standardizing on OpenAI gpt-4o-mini-2024-07-18 (reasoning_effort="medium").
Enforces the Fact-Preservation Invariant: repaired_entities <= allowed_entities.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Set

from dual_pipeline.model_registry import ModelTier, get_binding

logger = logging.getLogger("dual_pipeline.l2_healing_contracts")


class HealingScope(str, Enum):
    """Scoped pipeline domain target for L2 execute healing."""
    PIPELINE_A_TRANSCRIPT = "pipeline_a_transcript"
    PIPELINE_B_PROJECTION = "pipeline_b_projection"
    PIPELINE_C_RETRIEVAL = "pipeline_c_retrieval"
    PIPELINE_D_BRIEFING = "pipeline_d_briefing"


@dataclass(frozen=True)
class L2HealingRequest:
    """Immutable contract governing an L2 surgical healing attempt."""
    scope: HealingScope
    candidate_artifact: Any
    failure_reasons: Sequence[str]
    allowed_evidence_ids: Set[str] = field(default_factory=set)
    allowed_entity_ids: Set[str] = field(default_factory=set)
    context_metadata: Mapping[str, Any] = field(default_factory=dict)
    model_id: str = field(default_factory=lambda: get_binding(ModelTier.SURGICAL_HEALER).model_id)
    reasoning_effort: str = field(default_factory=lambda: get_binding(ModelTier.SURGICAL_HEALER).reasoning_effort or "high")
    max_repair_calls: int = 1


@dataclass(frozen=True)
class L2HealingResult:
    """Outcome of an in-flight L2 healing invocation."""
    status: str  # "HEALED" | "UNREPAIRABLE" | "FAILED"
    healed_artifact: Optional[Any]
    patch_diff: str
    repaired_fields: Sequence[str] = field(default_factory=list)
    fact_preservation_passed: bool = True
    tokens_used: int = 0
    cached_tokens_used: int = 0
    latency_ms: float = 0.0
    error_message: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == "HEALED" and self.fact_preservation_passed and self.healed_artifact is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "patch_diff": self.patch_diff,
            "repaired_fields": list(self.repaired_fields),
            "fact_preservation_passed": self.fact_preservation_passed,
            "tokens_used": self.tokens_used,
            "cached_tokens_used": self.cached_tokens_used,
            "latency_ms": round(self.latency_ms, 2),
            "error_message": self.error_message,
        }


def extract_entity_ids(artifact: Any) -> Set[str]:
    """Extract all entity IDs present in an artifact (dict, str, or object)."""
    entities: Set[str] = set()

    if isinstance(artifact, str):
        # Extract prefixes matching W2 entity spine: e.g. person:alex-sanchez, system:aurora
        found = re.findall(
            r"\b(?:person|org|system|workstream|project|metric|role|policy):[a-zA-Z0-9_\-\.]+\b",
            artifact,
        )
        entities.update(found)
        # Try JSON parse if applicable
        try:
            parsed = json.loads(artifact)
            entities.update(extract_entity_ids(parsed))
        except Exception:
            pass
        return entities

    if isinstance(artifact, Mapping):
        for _k, v in artifact.items():
            if isinstance(v, (str, Mapping, list, tuple, set)):
                entities.update(extract_entity_ids(v))

    elif isinstance(artifact, (list, tuple, set)):
        for item in artifact:
            entities.update(extract_entity_ids(item))

    elif hasattr(artifact, "__dict__"):
        entities.update(extract_entity_ids(artifact.__dict__))

    return entities


def extract_evidence_ids(artifact: Any) -> Set[str]:
    """Extract all evidence locator IDs present in an artifact (e.g. ev-001, stmt-04)."""
    evidence: Set[str] = set()

    if isinstance(artifact, str):
        found = re.findall(r"\b(?:ev|stmt|src)-\d+[a-zA-Z0-9_\-]*\b", artifact)
        evidence.update(found)
        try:
            parsed = json.loads(artifact)
            evidence.update(extract_evidence_ids(parsed))
        except Exception:
            pass
        return evidence

    if isinstance(artifact, Mapping):
        for _k, v in artifact.items():
            if isinstance(v, (str, Mapping, list, tuple, set)):
                evidence.update(extract_evidence_ids(v))

    elif isinstance(artifact, (list, tuple, set)):
        for item in artifact:
            evidence.update(extract_evidence_ids(item))

    return evidence


def verify_fact_preservation(
    original_artifact: Any,
    healed_artifact: Any,
    allowed_entities: Optional[Set[str]] = None,
    allowed_evidence: Optional[Set[str]] = None,
) -> bool:
    """Enforces mathematical invariant: repaired_entities <= allowed_entities.

    Prevents L2 healing from hallucinating new entities, stakeholders, or evidence.
    """
    if healed_artifact is None:
        return True

    # Base allowed sets from original artifact
    base_entities = extract_entity_ids(original_artifact)
    base_evidence = extract_evidence_ids(original_artifact)

    total_allowed_entities = set(base_entities)
    if allowed_entities:
        total_allowed_entities.update(allowed_entities)

    total_allowed_evidence = set(base_evidence)
    if allowed_evidence:
        total_allowed_evidence.update(allowed_evidence)

    healed_entities = extract_entity_ids(healed_artifact)
    healed_evidence = extract_evidence_ids(healed_artifact)

    # Check entity boundary
    if not healed_entities.issubset(total_allowed_entities):
        disallowed = healed_entities - total_allowed_entities
        logger.error(f"FACT PRESERVATION VIOLATION: L2 Healing introduced ungrounded entities: {disallowed}")
        return False

    # Check evidence boundary
    if not healed_evidence.issubset(total_allowed_evidence):
        disallowed = healed_evidence - total_allowed_evidence
        logger.error(f"EVIDENCE PRESERVATION VIOLATION: L2 Healing introduced ungrounded evidence: {disallowed}")
        return False

    return True
