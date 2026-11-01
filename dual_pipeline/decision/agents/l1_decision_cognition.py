"""Agent-D-L1: Decision Cognition & Pre-Debate Planning Layer.

Executes strictly BEFORE L0 routing and dialectical debate in Pipeline D:
1. Audits candidate option orthogonality and mutual exclusivity.
2. Pre-screens for repository fatal flaws and invariant violations.
3. Analyzes Law 3 Untrusted Fast-Path live turns for epistemic tension with canonical evidence.
4. Retrieves Bayesian priors from the governed decision ledger (hitl_decision_ledger.json).
5. Governed by OpenAI gpt-5.6-luna (reasoning_effort="high") with offline fallback.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.agents.l1_decision_cognition")

_FATAL_FLAW_PATTERNS = [
    (re.compile(r"\b(bypass\s+governance|bypass\s+law\s*1|unbounded\s+writes)\b", re.IGNORECASE), "GOVERNANCE_BYPASS_ATTEMPT"),
    (re.compile(r"\b(leak\s+advisory|egress\s+internal|external\s+leak)\b", re.IGNORECASE), "LAW_2_ADVISORY_EGRESS_VIOLATION"),
    (re.compile(r"\b(synthetic\s+embedding|fake\s+vector)\b", re.IGNORECASE), "SYNTHETIC_EMBEDDING_BAN_VIOLATION"),
    (re.compile(r"\b(drop\s+table|delete\s+from\s+canonical)\b", re.IGNORECASE), "DESTRUCTIVE_MUTATION_VIOLATION"),
]


@dataclass(frozen=True)
class L1DecisionPlanContract:
    """Immutable plan contract produced by L1 decision cognition prior to dialectical debate."""
    decision_id: str
    decision_type: str
    orthogonality_score: float
    is_valid_decision_frame: bool
    fatal_flaws_detected: List[str]
    fast_path_epistemic_tension: bool
    historical_prior_biases: Dict[str, float]
    model_binding: Dict[str, Any]
    sanitization_passed: bool = True
    quarantine_reasons: List[str] = field(default_factory=list)
    perspective_weights: Dict[str, float] = field(default_factory=dict)

    def model_dump(self) -> Dict[str, Any]:
        """Serializes contract to a dictionary representation."""
        return asdict(self)


class L1DecisionCognition:
    """L1 Decision Cognition & Pre-Debate Planning Engine for Pipeline D."""

    def __init__(self, default_model: Optional[str] = None, reasoning_effort: Optional[str] = None) -> None:
        from dual_pipeline.model_registry import ModelTier, get_binding
        binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
        self.default_model = default_model or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "high"
        self.model_binding = {
            "model_id": self.default_model,
            "provider": "openai",
            "reasoning_effort": self.reasoning_effort,
        }

    def plan(
        self,
        decision_id: str,
        question: str,
        options: List[Dict[str, Any]],
        canonical_evidence: Optional[List[Dict[str, Any]]] = None,
        live_turns: Optional[List[Dict[str, Any]]] = None,
        ledger_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> L1DecisionPlanContract:
        """Audits options, evaluates fast-path tension, and retrieves Bayesian priors."""
        metadata = metadata or {}
        canonical_evidence = canonical_evidence or []
        live_turns = live_turns or []

        # 1. Option Orthogonality & Redundancy Audit
        if len(options) < 2:
            is_valid_frame = False
            orthogonality_score = 0.0
        else:
            titles = [opt.get("title") or opt.get("label") or opt.get("key") or "" for opt in options]
            unique_titles = set(t.strip().lower() for t in titles if t.strip())
            if len(unique_titles) < len(options):
                orthogonality_score = 0.20
                is_valid_frame = False
            else:
                # Compute pairwise Jaccard similarity across token sets of descriptions/titles
                max_pairwise_sim = 0.0
                token_sets = []
                for opt in options:
                    tokens = set(re.findall(r"\w+", f"{opt.get('title', '')} {opt.get('description', '')}".lower()))
                    token_sets.append(tokens)
                for i in range(len(token_sets)):
                    for j in range(i + 1, len(token_sets)):
                        s1, s2 = token_sets[i], token_sets[j]
                        if s1 and s2:
                            sim = len(s1 & s2) / len(s1 | s2)
                            if sim > max_pairwise_sim:
                                max_pairwise_sim = sim
                orthogonality_score = round(1.0 - max_pairwise_sim, 2)
                if orthogonality_score < 0.50 or max_pairwise_sim > 0.50:
                    is_valid_frame = False
                else:
                    is_valid_frame = True

        # 2. Fatal Flaw Pre-Screening
        fatal_flaws = []
        for opt in options:
            opt_desc = f"{opt.get('title', '')} {opt.get('description', '')}"
            for pat, flaw_name in _FATAL_FLAW_PATTERNS:
                if pat.search(opt_desc):
                    fatal_flaws.append(f"{opt.get('key', 'unknown')}:{flaw_name}")

        if fatal_flaws:
            is_valid_frame = False

        # 3. Decision Type Classification
        q_lower = question.lower()
        if any(w in q_lower for w in ("architect", "system", "engine", "mesh", "broker")):
            decision_type = "ARCHITECTURAL_CHOICE"
        elif any(w in q_lower for w in ("date", "schedule", "deadline", "timeline", "commit")):
            decision_type = "TIMELINE_COMMITMENT"
        elif any(w in q_lower for w in ("vendor", "partner", "procurement", "sow")):
            decision_type = "VENDOR_SELECTION"
        else:
            decision_type = "POLICY_GOVERNANCE"

        # 4. Law 3 Fast-Path Epistemic Tension Pre-Analysis
        fast_path_tension = False
        if live_turns and canonical_evidence:
            live_text = " ".join(t.get("text", "") for t in live_turns).lower()
            ev_text = json.dumps(canonical_evidence).lower()
            # Tension check: live turn contradicts or raises ungrounded friction
            if ("not aligned" in live_text or "delay" in live_text or "disagree" in live_text) and ("commit" in ev_text or "aligned" in ev_text):
                fast_path_tension = True

        # 5. Bayesian Prior Pre-Lookup from Decision Ledger
        historical_priors: Dict[str, float] = {}
        if ledger_path and os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r", encoding="utf-8") as f:
                    ledger_data = json.load(f)
                    decisions = ledger_data.get("decisions", []) if isinstance(ledger_data, dict) else ledger_data
                    for entry in decisions:
                        affirmed = entry.get("chosen_option")
                        if affirmed:
                            historical_priors[affirmed] = historical_priors.get(affirmed, 0.0) + 0.05
            except Exception as ex:
                logger.debug(f"Could not read priors from ledger: {ex}")

        # 5.5 Dynamic Perspective Weighting with Bounded Clamping [0.10, 0.45]
        base_weights = {"RED_TEAM": 0.35, "STRATEGIC_FEASIBILITY": 0.35, "SYNTHESIZER": 0.30}
        override_count = int(metadata.get("operator_override_count", 0))
        if override_count > 0:
            shift = min(0.10, override_count * 0.05)
            base_weights["RED_TEAM"] += shift
            base_weights["STRATEGIC_FEASIBILITY"] -= shift

        clamped_weights = {k: max(0.10, min(0.45, w)) for k, w in base_weights.items()}
        total_w = sum(clamped_weights.values())
        perspective_weights = {k: round(w / total_w, 3) for k, w in clamped_weights.items()}

        # 6. Sanitization check
        sanitization_passed = True
        quarantine_reasons = []
        for opt in options:
            opt_str = json.dumps(opt)
            if re.search(r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----", opt_str) or re.search(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", opt_str):
                sanitization_passed = False
                quarantine_reasons.append("RESTRICTED_KEY_IN_OPTION")
                is_valid_frame = False
                break

        # 7. Model Binding: gpt-5.6-luna (high)
        model_binding = {
            "model_id": metadata.get("model_id", self.default_model),
            "reasoning_effort": metadata.get("reasoning_effort", self.reasoning_effort),
            "provider": "openai",
        }

        contract = L1DecisionPlanContract(
            decision_id=decision_id,
            decision_type=decision_type,
            orthogonality_score=orthogonality_score,
            is_valid_decision_frame=is_valid_frame,
            fatal_flaws_detected=fatal_flaws,
            fast_path_epistemic_tension=fast_path_tension,
            historical_prior_biases=historical_priors,
            model_binding=model_binding,
            sanitization_passed=sanitization_passed,
            quarantine_reasons=quarantine_reasons,
            perspective_weights=perspective_weights,
        )
        logger.info(f"L1 Decision Plan: type={contract.decision_type}, valid={contract.is_valid_decision_frame}, flaws={contract.fatal_flaws_detected}")
        return contract
