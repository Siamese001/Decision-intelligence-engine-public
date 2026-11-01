"""Agent-C-L1: Query Cognition & Pre-Retrieval Planning Layer.

Executes strictly BEFORE L0 routing and multi-perspective dispatch in Pipeline C:
1. Decomposes query intent into primary and auxiliary semantic goals.
2. Dynamically calibrates perspective lens weights (Technical, Commercial, Adoption, Blocker).
3. Dynamically calibrates BGE-M3 hybrid retrieval weights (dense, sparse, ColBERT token).
4. Verifies Law 6 pre-flight policy clearance before vector encoding and search.
5. Governed by OpenAI gpt-5.6-luna (reasoning_effort="high") with offline fallback.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.agents.l1_query_cognition")

_TECH_TERMS = {"architecture", "bge-m3", "api", "model", "cloud", "s3", "container", "system", "infrastructure", "db", "vector"}
_COMM_TERMS = {"timeline", "deadline", "milestone", "delivery", "date", "sow", "cost", "budget", "commercial", "vendor"}
_RISK_TERMS = {"risk", "blocker", "issue", "debt", "friction", "obstacle", "delay", "vulnerability", "leak"}
_COMP_TERMS = {"compare", "vs", "versus", "tradeoff", "option", "choice", "dialectical", "alternative"}


@dataclass(frozen=True)
class L1RetrievalPlanContract:
    """Immutable plan contract produced by L1 query cognition prior to L0 routing."""
    query: str
    primary_intent: str
    perspective_weights: Dict[str, float]
    hybrid_weights: Dict[str, float]
    caller_clearance_verified: bool
    stealth_mode_required: bool
    model_binding: Dict[str, Any]
    sanitization_passed: bool = True
    quarantine_reasons: List[str] = field(default_factory=list)

    def model_dump(self) -> Dict[str, Any]:
        """Serializes contract to a dictionary."""
        return asdict(self)


class L1QueryCognition:
    """L1 Query Cognition & Pre-Retrieval Planning Engine for Pipeline C."""

    def __init__(
        self,
        default_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        parameters_path: Optional[Any] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelTier, get_binding
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.default_model = default_model or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "high"
        self.parameters_path = parameters_path
        self.model_binding = {
            "model_id": self.default_model,
            "provider": "openai",
            "reasoning_effort": self.reasoning_effort,
        }

    def plan(
        self,
        query: str,
        caller_namespace: str = "CLIENT_SAFE",
        stealth_mode: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> L1RetrievalPlanContract:
        """Cognitively decomposes query intent and plans multi-perspective hybrid search."""
        metadata = metadata or {}
        q_lower = query.lower()

        # 1. Primary Intent Classification with regex word-stem matching
        tech_hits = len(re.findall(r"\b(architect\w*|bge-m3|api|model\w*|cloud|s3|container\w*|system\w*|infrastruct\w*|db|vector\w*)\b", q_lower))
        comm_hits = len(re.findall(r"\b(timeline\w*|deadline\w*|milestone\w*|deliver\w*|date\w*|sow|cost\w*|budget\w*|commercial\w*|vendor\w*)\b", q_lower))
        risk_hits = len(re.findall(r"\b(risk\w*|blocker\w*|issue\w*|debt\w*|friction\w*|obstacle\w*|delay\w*|vulnerab\w*|leak\w*)\b", q_lower))
        comp_hits = len(re.findall(r"\b(compar\w*|vs|versus|tradeoff\w*|option\w*|choice\w*|dialectical\w*|alternat\w*)\b", q_lower))

        if comp_hits > 0 or " vs " in q_lower or "versus" in q_lower:
            primary_intent = "DIALECTICAL_COMPARISON"
        elif risk_hits > max(tech_hits, comm_hits, 0):
            primary_intent = "RISK_AUDIT"
        elif comm_hits > max(tech_hits, risk_hits, 0):
            primary_intent = "DELIVERY_TIMELINE"
        elif tech_hits > 0:
            primary_intent = "TECHNICAL_ARCHITECTURE"
        else:
            primary_intent = "GENERAL_INQUIRY"

        # 2. Dynamic Perspective Lens Weighting
        if primary_intent == "TECHNICAL_ARCHITECTURE":
            perspective_weights = {"TECHNICAL": 0.55, "BLOCKER": 0.25, "ADOPTION": 0.10, "COMMERCIAL": 0.10}
        elif primary_intent == "DELIVERY_TIMELINE":
            perspective_weights = {"COMMERCIAL": 0.45, "TECHNICAL": 0.30, "BLOCKER": 0.15, "ADOPTION": 0.10}
        elif primary_intent == "RISK_AUDIT":
            perspective_weights = {"BLOCKER": 0.50, "ADOPTION": 0.25, "TECHNICAL": 0.15, "COMMERCIAL": 0.10}
        elif primary_intent == "DIALECTICAL_COMPARISON":
            perspective_weights = {"TECHNICAL": 0.30, "COMMERCIAL": 0.30, "BLOCKER": 0.20, "ADOPTION": 0.20}
        else:
            perspective_weights = {"TECHNICAL": 0.25, "COMMERCIAL": 0.25, "ADOPTION": 0.25, "BLOCKER": 0.25}

        # 3. Dynamic BGE-M3 Hybrid Weight Tuning (dense, sparse, ColBERT token)
        # Entity-dense or code-like queries benefit from higher sparse weights (BM25)
        has_entities = bool(re.search(r"\b(BGE-M3|AWS|S3|OpenSearch|MLX|Teams|PartnerAdvisory|APEX|NEA)\b", query, re.IGNORECASE))
        if has_entities:
            hybrid_weights = {"dense": 0.40, "sparse": 0.40, "colbert": 0.20}
        else:
            hybrid_weights = {"dense": 0.55, "sparse": 0.25, "colbert": 0.20}

        # Check for calibrated intent and hybrid weights from runtime parameters
        calibrated_loaded = False
        if metadata.get("bge_release_gate_passed") is not False:
            if self.parameters_path:
                try:
                    import json
                    from pathlib import Path
                    p_file = Path(self.parameters_path)
                    if p_file.is_file():
                        p_data = json.loads(p_file.read_text(encoding="utf-8"))
                        ret_params = p_data.get("parameters", {}).get("retrieval", {})
                        if primary_intent in ret_params.get("intent_fusion_weights", {}):
                            hybrid_weights = dict(ret_params["intent_fusion_weights"][primary_intent])
                            calibrated_loaded = True
                        if primary_intent in ret_params.get("perspective_weights", {}):
                            perspective_weights = dict(ret_params["perspective_weights"][primary_intent])
                except Exception as err:
                    logger.debug("Failed reading calibrated retrieval parameters: %s", err)
            if not calibrated_loaded:
                try:
                    from dual_pipeline.parameters import get_parameter
                    cal_weights = get_parameter(f"retrieval.intent_fusion_weights.{primary_intent}")
                    if cal_weights and isinstance(cal_weights, dict):
                        hybrid_weights = dict(cal_weights)
                except Exception:
                    pass
        else:
            logger.info("BGE-M3 release gate not passed; rejecting calibrated weight overrides and using baseline defaults.")

        # 4. Law 6 Pre-Flight Policy Clearance
        caller_clearance_verified = True
        stealth_mode_required = stealth_mode
        quarantine_reasons = []

        if caller_namespace == "INTERNAL_ADVISORY" and not stealth_mode:
            caller_clearance_verified = False
            quarantine_reasons.append("NON_STEALTH_ADVISORY_QUERY_REJECTED")

        # Check for toxic tokens in query
        sanitization_passed = True
        if re.search(r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----", query) or re.search(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", query):
            sanitization_passed = False
            quarantine_reasons.append("RESTRICTED_KEY_IN_QUERY")

        # 5. Model Binding: gpt-5.6-luna (high)
        model_binding = {
            "model_id": metadata.get("model_id", self.default_model),
            "reasoning_effort": metadata.get("reasoning_effort", self.reasoning_effort),
            "provider": "openai",
        }

        contract = L1RetrievalPlanContract(
            query=query,
            primary_intent=primary_intent,
            perspective_weights=perspective_weights,
            hybrid_weights=hybrid_weights,
            caller_clearance_verified=caller_clearance_verified,
            stealth_mode_required=stealth_mode_required,
            model_binding=model_binding,
            sanitization_passed=sanitization_passed,
            quarantine_reasons=quarantine_reasons,
        )
        logger.info(f"L1 Retrieval Plan: intent={contract.primary_intent}, weights={contract.perspective_weights}")
        return contract
