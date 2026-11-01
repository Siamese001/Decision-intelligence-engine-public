"""
dual_pipeline/ptc/decision_evaluator.py

Programmatic Decision Matrix Evaluator (PDME) for Lifecycle 2.
Evaluates decision options strictly against evidenced criteria and 18-role
stakeholder taxonomy, completely decoupled from retrieval similarity rank.
Enforces the 20% ambiguity margin gate (Delta > 0.20 autonomous vs Delta <= 0.20 HITL).
Layer: L4 (Decision Reasoning & Option Matrix).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD

DEFAULT_ROLES: Sequence[str] = (
    "enterprise_architect",
    "lead_data_engineer",
    "infosec_compliance",
    "finops_controller",
    "product_manager",
    "operations_lead",
)

ROLE_CRITERIA_BIAS: Dict[str, Dict[str, float]] = {
    "enterprise_architect": {"scalability": 1.4, "architectural_fit": 1.5, "technical_debt": 1.3},
    "lead_data_engineer": {"data_throughput": 1.5, "maintainability": 1.3, "latency": 1.2},
    "infosec_compliance": {"security_isolation": 1.6, "auditability": 1.4, "zero_trust": 1.5},
    "finops_controller": {"operational_cost": 1.6, "licensing": 1.4, "roi": 1.3},
    "product_manager": {"time_to_market": 1.5, "feature_velocity": 1.4, "adoption_risk": 1.2},
    "operations_lead": {"reliability": 1.5, "observability": 1.4, "incident_risk": 1.3},
}


@dataclass
class PTCDecisionOption:
    """Declared choice option evaluated against evidenced criteria."""
    option_id: str
    title: str
    description: str = ""
    evidenced_claims: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    base_feasibility: float = 0.80


@dataclass
class PTCDecisionCriterion:
    """Dimension against which options are weighed."""
    name: str
    weight: float = 1.0
    description: str = ""
    stakeholder_role: str = ""


@dataclass(frozen=True)
class PTCDecisionMatrixPlan:
    """Input plan declaring decision frame, options, and evidence packet."""
    decision_question: str
    options: Sequence[PTCDecisionOption]
    criteria: Sequence[PTCDecisionCriterion]
    evidence_pack: Mapping[str, Any]
    roles: Sequence[str] = field(default_factory=lambda: list(DEFAULT_ROLES))
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD


@dataclass
class PTCDecisionEvaluationReceipt:
    """Sealed decision intelligence receipt recording trade-off matrix evaluation."""
    decision_question: str
    terminal_state: str  # DECISION_PRODUCED | CLARIFICATION_REQUIRED | NO_DECISION
    top_option_id: Optional[str]
    top_confidence: float
    runner_up_option_id: Optional[str]
    runner_up_confidence: float
    confidence_margin_delta: float
    is_autonomous_resolution: bool
    option_rankings: List[Dict[str, Any]]
    hitl_decision_payload: Optional[Dict[str, Any]]
    receipt_digest: str

    def to_compact_json(self) -> str:
        data = asdict(self)
        return json.dumps(data, separators=(",", ":"), default=str)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _evaluate_criterion(
    crit: PTCDecisionCriterion,
    opt: PTCDecisionOption,
    role_bias: Mapping[str, float],
    evidence_text_corpus: str,
) -> tuple[float, float]:
    crit_name = crit.name.lower()
    crit_weight = crit.weight * role_bias.get(crit_name, 1.0)

    claim_corroboration = 0.5
    for claim in opt.evidenced_claims:
        claim_tokens = set(claim.lower().split())
        if claim_tokens and any(t in evidence_text_corpus for t in claim_tokens):
            claim_corroboration = min(1.0, claim_corroboration + 0.25)

    risk_penalty = len(opt.risk_factors) * 0.05
    effective_crit_score = max(0.1, min(1.0, claim_corroboration - risk_penalty))
    return effective_crit_score, crit_weight


def _score_single_option(
    opt: PTCDecisionOption,
    roles: Sequence[str],
    criteria: Sequence[PTCDecisionCriterion],
    evidence_text_corpus: str,
) -> Dict[str, Any]:
    role_evaluations: Dict[str, float] = {}
    total_weighted_score = 0.0
    total_weight = 0.0

    for role in roles:
        role_bias = ROLE_CRITERIA_BIAS.get(role, {})
        role_score = opt.base_feasibility

        for crit in criteria:
            effective_crit_score, crit_weight = _evaluate_criterion(
                crit, opt, role_bias, evidence_text_corpus
            )
            total_weighted_score += effective_crit_score * crit_weight
            total_weight += crit_weight
            role_score += (effective_crit_score - 0.5) * 0.1

        role_evaluations[role] = round(max(0.1, min(1.0, role_score)), 3)

    composite_score = round(total_weighted_score / total_weight, 4) if total_weight > 0 else opt.base_feasibility
    return {
        "option_id": opt.option_id,
        "title": opt.title,
        "score": composite_score,
        "role_evaluations": role_evaluations,
        "claim_count": len(opt.evidenced_claims),
        "risk_count": len(opt.risk_factors),
    }


def _build_hitl_clarification_payload(
    plan: PTCDecisionMatrixPlan,
    top: Dict[str, Any],
    second: Optional[Dict[str, Any]],
    delta: float,
) -> Dict[str, Any]:
    sec_id = second["option_id"] if second else "opt_none"
    sec_title = second["title"] if second else "None"
    sec_conf = f"{int(second['score'] * 100)}%" if second else "0%"
    sec_tier = "MEDIUM" if (second and second["score"] >= 0.50) else "LOW"
    sec_score = second["score"] if second else 0.0
    return {
        "question": f"HITL Ambiguity Resolution: {plan.decision_question}",
        "confidence_margin_delta": delta,
        "options": [
            {
                "option_id": top["option_id"],
                "title": f"(Recommended) {top['title']}",
                "confidence": f"{int(top['score'] * 100)}%",
                "tier": "HIGH" if top["score"] >= 0.85 else "MEDIUM",
                "rationale": f"Leading score ({top['score']:.2f}) based on evidenced claims.",
            },
            {
                "option_id": sec_id,
                "title": sec_title,
                "confidence": sec_conf,
                "tier": sec_tier,
                "rationale": f"Runner-up score ({sec_score:.2f}). Margin delta is {delta:.2f} (< {plan.margin_threshold:.2f}).",
            },
        ],
    }


class PTCDecisionMatrixEvaluator:
    """In-process decision matrix evaluator enforcing rank-decoupling and margin gating."""

    def evaluate(self, plan: PTCDecisionMatrixPlan) -> PTCDecisionEvaluationReceipt:
        if not plan.options:
            receipt = PTCDecisionEvaluationReceipt(
                decision_question=plan.decision_question,
                terminal_state="NO_DECISION",
                top_option_id=None,
                top_confidence=0.0,
                runner_up_option_id=None,
                runner_up_confidence=0.0,
                confidence_margin_delta=0.0,
                is_autonomous_resolution=False,
                option_rankings=[],
                hitl_decision_payload=None,
                receipt_digest="sha256:0000000000000000",
            )
            return receipt

        # 1. Inspect evidence pack facts and constraints (Decoupled from retrieval rank)
        evidence_facts = plan.evidence_pack.get("top_facts", plan.evidence_pack.get("facts", []))
        evidence_text_corpus = " ".join([
            f"{f.get('name', '')} {f.get('snippet', '')} {json.dumps(f.get('properties', {}))}".lower()
            for f in evidence_facts
        ])

        # 2. Score options against criteria and stakeholder roles
        option_scores = [
            _score_single_option(opt, plan.roles, plan.criteria, evidence_text_corpus)
            for opt in plan.options
        ]

        # 3. Rank Options by Composite Score
        option_scores.sort(key=lambda x: x["score"], reverse=True)

        top = option_scores[0]
        second = option_scores[1] if len(option_scores) > 1 else None

        c_top = top["score"]
        c_second = second["score"] if second else 0.0
        delta = round(c_top - c_second, 4)

        # 4. Enforce 25% Margin Ambiguity Gate
        is_autonomous = delta > plan.margin_threshold

        if is_autonomous:
            terminal_state = "DECISION_PRODUCED"
            hitl_payload = None
        else:
            terminal_state = "CLARIFICATION_REQUIRED"
            hitl_payload = _build_hitl_clarification_payload(plan, top, second, delta)

        digest_body = {
            "decision_question": plan.decision_question,
            "terminal_state": terminal_state,
            "top_option": top["option_id"],
            "delta": delta,
            "rankings": option_scores,
        }
        receipt_digest = f"sha256:{hashlib.sha256(json.dumps(digest_body, default=str).encode()).hexdigest()}"

        return PTCDecisionEvaluationReceipt(
            decision_question=plan.decision_question,
            terminal_state=terminal_state,
            top_option_id=top["option_id"],
            top_confidence=c_top,
            runner_up_option_id=second["option_id"] if second else None,
            runner_up_confidence=c_second,
            confidence_margin_delta=delta,
            is_autonomous_resolution=is_autonomous,
            option_rankings=option_scores,
            hitl_decision_payload=hitl_payload,
            receipt_digest=receipt_digest,
        )
