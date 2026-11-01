"""Agent-B-L1: Extraction Cognition & Pre-Fork Planning Layer.

Executes strictly BEFORE L0 routing and multi-agent fan-out in Pipeline B:
1. Classifies compute posture (CLIENT_SAFE vs INTERNAL_ADVISORY) under Law 1 & Law 2.
2. Evaluates domain relevance across the 4 extraction facets (TechArch, Commitments, Stakeholder, Friction).
3. Pre-validates entity mentions against the W2 Semantic Spine.
4. Governed by OpenAI gpt-5.6-luna (reasoning_effort="high") with offline fallback.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from dual_pipeline.a2a import ComputePosture
from dual_pipeline.model_registry import ModelTier, get_binding
from dual_pipeline.transcript_metadata_core.speaker_priors_registry import (
    load_extractor_routing_weights,
)

logger = logging.getLogger("dual_pipeline.agents.l1_extraction_cognition")

# Allowed entity prefixes in W2 Semantic Spine
W2_ALLOWED_PREFIXES = (
    "person:",
    "org:",
    "system:",
    "workstream:",
    "project:",
    "metric:",
    "role:",
    "policy:",
)

# Advisory leverage markers that require local Apple Metal isolation (Law 1 & Law 2)
_ADVISORY_MARKERS = [
    "asymmetric leverage",
    "career visibility",
    "career wedge",
    "veto power",
    "political friction",
    "margin wedge",
    "shadow decision",
    "internal advisory",
    "power dynamic",
    "compensation",
    "personal leverage",
]

# Domain keywords for relevance scoring
_TECH_KEYWORDS = {"architecture", "api", "database", "service", "bge-m3", "aws", "cloud", "model", "schema", "cluster", "latency", "pipeline"}
_COMM_KEYWORDS = {"commit", "deadline", "milestone", "delivery", "sow", "target", "by", "schedule", "launch", "deploy"}
_STK_KEYWORDS = {"stakeholder", "alignment", "executive", "sponsor", "champion", "hesitation", "relationship", "leadership", "trust"}
_FRIC_KEYWORDS = {"blocker", "friction", "delay", "risk", "issue", "obstacle", "bottleneck", "defect", "contention", "churn"}


@dataclass(frozen=True)
class L1ExtractionPlanContract:
    """Immutable plan contract produced by L1 extraction cognition prior to L0 routing."""
    evidence_id: str
    compute_posture: ComputePosture
    domain_relevance: Dict[str, float]
    active_agents: List[str]
    candidate_entity_prefixes: List[str]
    model_binding: Dict[str, Any]
    has_advisory_leverage: bool = False
    quarantine_risk_estimate: float = 0.0
    sanitization_passed: bool = True
    quarantine_reasons: List[str] = field(default_factory=list)

    def model_dump(self) -> Dict[str, Any]:
        """Serializes contract to a dictionary."""
        return asdict(self)


class L1ExtractionCognition:
    """L1 Cognition & Pre-Fork Planning Engine for Pipeline B."""

    def __init__(
        self,
        default_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        priors_path: Optional[Any] = None,
    ) -> None:
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.default_model = default_model or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "high"
        self.priors_path = priors_path
        self.model_binding = {
            "model_id": self.default_model,
            "provider": "openai",
            "reasoning_effort": self.reasoning_effort,
        }

    def plan(
        self,
        evidence_id: str,
        text: str = "",
        speaker: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        source_id: str = "",
        content_digest: str = "",
        transcript_text: str = "",
        stealth_mode: bool = False,
    ) -> L1ExtractionPlanContract:
        """Analyzes evidence text and emits a binding L1ExtractionPlanContract."""
        metadata = dict(metadata or {})
        if stealth_mode:
            metadata["stealth_mode"] = True
        text = text or transcript_text
        text_lower = text.lower()

        # 1. Law 1 & Law 2 Compute Posture Classification
        has_advisory = any(marker in text_lower for marker in _ADVISORY_MARKERS) or metadata.get("namespace") == "INTERNAL_ADVISORY"

        if has_advisory or metadata.get("stealth_mode", False):
            compute_posture = ComputePosture.INTERNAL_ADVISORY
        else:
            compute_posture = ComputePosture.CLIENT_SAFE

        # 2. Domain Relevance Scoring
        words = set(re.findall(r"\b[a-zA-Z0-9_\-]+\b", text_lower))

        tech_score = len(words & _TECH_KEYWORDS) / max(1, len(_TECH_KEYWORDS))
        comm_score = len(words & _COMM_KEYWORDS) / max(1, len(_COMM_KEYWORDS))
        stk_score = len(words & _STK_KEYWORDS) / max(1, len(_STK_KEYWORDS))
        fric_score = len(words & _FRIC_KEYWORDS) / max(1, len(_FRIC_KEYWORDS))

        # Check for date patterns boosting commitment score
        if re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text):
            comm_score = max(comm_score, 0.4)

        # Apply learned extractor chunk-routing weights
        weights = load_extractor_routing_weights(self.priors_path)

        domain_relevance = {
            "TECH_ARCH": round(min(1.0, tech_score * 3.0 * weights.get("TECH_ARCH", 1.0)), 3),
            "STRATEGIC_COMMITMENTS": round(min(1.0, comm_score * 3.0 * weights.get("STRATEGIC_COMMITMENTS", 1.0)), 3),
            "STAKEHOLDER_DYNAMICS": round(min(1.0, stk_score * 3.0 * weights.get("STAKEHOLDER_DYNAMICS", 1.0)), 3),
            "FRICTION_BLOCKER": round(min(1.0, fric_score * 3.0 * weights.get("FRICTION_BLOCKER", 1.0)), 3),
        }

        # Determine active agents based on relevance and compute posture
        active_agents = []
        if compute_posture == ComputePosture.INTERNAL_ADVISORY:
            # Law 2: Advisory materials route only to Metal-local agents
            active_agents.append("stakeholder_dynamics")
            active_agents.append("friction_blocker")
            # If client-safe facets also detected, dual-fork
            if domain_relevance["TECH_ARCH"] > 0.05 or metadata.get("force_all_agents", True):
                active_agents.append("tech_arch")
            if domain_relevance["STRATEGIC_COMMITMENTS"] > 0.05 or metadata.get("force_all_agents", True):
                active_agents.append("strategic_commitments")
        else:
            # Client-safe default
            if domain_relevance["TECH_ARCH"] > 0.05 or metadata.get("force_all_agents", True):
                active_agents.append("tech_arch")
            if domain_relevance["STRATEGIC_COMMITMENTS"] > 0.05 or metadata.get("force_all_agents", True):
                active_agents.append("strategic_commitments")
            if domain_relevance["STAKEHOLDER_DYNAMICS"] > 0.05 or metadata.get("force_all_agents", True):
                active_agents.append("stakeholder_dynamics")
            if domain_relevance["FRICTION_BLOCKER"] > 0.05 or metadata.get("force_all_agents", True):
                active_agents.append("friction_blocker")

        # Fallback to ensure at least one agent is active if text is non-empty
        if not active_agents and text.strip():
            active_agents = ["tech_arch", "strategic_commitments"]

        # 3. W2 Semantic Spine Entity Pre-Validation
        candidate_entity_prefixes = []
        for prefix in W2_ALLOWED_PREFIXES:
            if prefix.rstrip(":") in text_lower or f"{prefix}" in text:
                candidate_entity_prefixes.append(prefix)
        if not candidate_entity_prefixes:
            candidate_entity_prefixes = ["system:", "workstream:"]

        # 4. Sanitization & Quarantine Pre-Screening
        sanitization_passed = True
        quarantine_reasons = []
        if re.search(r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----", text) or re.search(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", text):
            sanitization_passed = False
            quarantine_reasons.append("RESTRICTED_SECRET_OR_KEY_DETECTED")

        # 5. Governed Model Binding: gpt-5.6-luna (high)
        model_binding = {
            "model_id": metadata.get("model_id", self.default_model),
            "reasoning_effort": metadata.get("reasoning_effort", self.reasoning_effort),
            "provider": "openai",
        }

        contract = L1ExtractionPlanContract(
            evidence_id=evidence_id,
            compute_posture=compute_posture,
            domain_relevance=domain_relevance,
            active_agents=active_agents,
            candidate_entity_prefixes=candidate_entity_prefixes,
            model_binding=model_binding,
            has_advisory_leverage=has_advisory,
            quarantine_risk_estimate=0.1 if sanitization_passed else 1.0,
            sanitization_passed=sanitization_passed,
            quarantine_reasons=quarantine_reasons,
        )
        logger.info(f"L1 Extraction Plan: posture={contract.compute_posture}, active={contract.active_agents}")
        return contract
