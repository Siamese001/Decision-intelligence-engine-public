"""Agent-D: Dialectical Perspective Analyst.

Unified agent consolidating Red Team Adversarial Risk Analysis and
Strategic Feasibility & Value Analysis under a single parameterized abstraction.
Operates under GPT-5.6-sol / MLX local models.
"""
from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Dict, List, Optional

from config.credentials import CredentialManager
from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.parameters import get_local_mlx_model

logger = logging.getLogger("dual_pipeline.agents.dialectical_perspective")

DEFAULT_MODEL, DEFAULT_EFFORT = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)


class PerspectiveType(StrEnum):
    RED_TEAM = "red_team"
    STRATEGIC_FEASIBILITY = "strategic_feasibility"


def get_dialectical_perspective_card(
    perspective: PerspectiveType | str,
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    use_local_model: bool = False,
) -> AgentCard:
    ptype = PerspectiveType(perspective)
    resolved_model = model_id or DEFAULT_MODEL
    resolved_effort = reasoning_effort or DEFAULT_EFFORT or "high"
    effective_model = get_local_mlx_model("instruct_14b") if use_local_model else resolved_model
    provider = "mlx" if use_local_model else "openai"

    if ptype == PerspectiveType.RED_TEAM:
        return AgentCard(
            agent_id="urn:nee:agent:pipeline-d:red-team-risk:1.0.0",
            name="DialecticalPerspectiveAgent[red_team]",
            version="1.0.0",
            role="Red Team Adversarial Risk Analyst",
            domain_focus="Adversarial failure modes, catastrophic risks, architectural debt, operational hazards",
            compute_posture=ComputePosture.CLIENT_SAFE,
            model_binding=ModelBinding(
                model_id=effective_model,
                provider=provider,
                reasoning_effort=None if use_local_model else resolved_effort,
            ),
            skills=["AdversarialRiskAnalysis", "FailureModeIdentification", "ArchitecturalDebtEvaluation"],
            allowed_tools=["mlx_inference" if use_local_model else "openai_chat_completion", "risk_evaluator"],
        )
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:strategic-feasibility:1.0.0",
        name="DialecticalPerspectiveAgent[strategic_feasibility]",
        version="1.0.0",
        role="Strategic Feasibility & Value Analyst",
        domain_focus="Strategic alignment, delivery velocity, ROI, architectural elegance, business value",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else resolved_effort,
        ),
        skills=["StrategicAlignmentAnalysis", "FeasibilityAssessment", "ROIVelocityModeling"],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat_completion", "feasibility_calculator"],
    )


class DialecticalPerspectiveAgent:
    """Unified dialectical analysis agent parameterized by perspective."""

    def __init__(
        self,
        perspective: PerspectiveType | str = PerspectiveType.RED_TEAM,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        use_local_model: bool = False,
        role_weight: float = 0.35,
    ) -> None:
        self.perspective = PerspectiveType(perspective)
        self.role_weight = max(0.10, min(0.45, float(role_weight)))
        self.use_local_model = use_local_model or CredentialManager.is_local_execution_mode()
        self.card = get_dialectical_perspective_card(
            perspective=self.perspective,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
            use_local_model=self.use_local_model,
        )

    def set_role_weight(self, weight: float) -> float:
        """Calibrates perspective role weight within bounded clamps [0.10, 0.45]."""
        self.role_weight = max(0.10, min(0.45, float(weight)))
        return self.role_weight

    def _extract_declared_risks(self, opt_risks: Any, risks: List[Dict[str, Any]]) -> bool:
        has_fatal_flaw = False
        if not isinstance(opt_risks, list):
            return has_fatal_flaw
        for r in opt_risks:
            if isinstance(r, dict):
                sev = r.get("severity", "MEDIUM").upper()
                is_fatal = (sev == "CRITICAL")
                risks.append({
                    "risk_id": f"risk-{len(risks)+1}",
                    "title": r.get("title", "Identified risk"),
                    "severity": sev,
                    "likelihood": r.get("likelihood", "MEDIUM").upper(),
                    "blast_radius": r.get("blast_radius", "COMPONENT"),
                    "mitigation": r.get("mitigation", "Implement defensive guardrails"),
                    "is_fatal_flaw": is_fatal,
                })
                if is_fatal:
                    has_fatal_flaw = True
            elif isinstance(r, str):
                risks.append({
                    "risk_id": f"risk-{len(risks)+1}",
                    "title": r,
                    "severity": "MEDIUM",
                    "likelihood": "MEDIUM",
                    "blast_radius": "COMPONENT",
                    "mitigation": "Review operational constraints",
                    "is_fatal_flaw": False,
                })
        return has_fatal_flaw

    def _check_inferred_risks(self, desc_lower: str, risks: List[Dict[str, Any]]) -> bool:
        has_fatal = False
        if "monolith" in desc_lower or "legacy" in desc_lower:
            risks.append({
                "risk_id": f"risk-{len(risks)+1}",
                "title": "Downstream Architectural Debt & Tight Coupling",
                "severity": "HIGH",
                "likelihood": "HIGH",
                "blast_radius": "SYSTEM_WIDE",
                "mitigation": "Enforce modular domain boundaries and decoupled interfaces",
                "is_fatal_flaw": False,
            })
        if "unbounded" in desc_lower or "bypass" in desc_lower or "unvalidated" in desc_lower:
            risks.append({
                "risk_id": f"risk-{len(risks)+1}",
                "title": "Governance & Compliance Bypass Risk",
                "severity": "CRITICAL",
                "likelihood": "HIGH",
                "blast_radius": "ENTERPRISE",
                "mitigation": "Mandate fail-closed authorization and receipt verification",
                "is_fatal_flaw": True,
            })
            has_fatal = True
        if "single point of failure" in desc_lower or "spof" in desc_lower:
            risks.append({
                "risk_id": f"risk-{len(risks)+1}",
                "title": "Single Point of Failure (SPOF)",
                "severity": "HIGH",
                "likelihood": "MEDIUM",
                "blast_radius": "AVAILABILITY",
                "mitigation": "Introduce redundancy and automated failover",
                "is_fatal_flaw": False,
            })
        return has_fatal

    def _analyze_single_option_risk(self, opt: Dict[str, Any]) -> Dict[str, Any]:
        opt_key = opt.get("key") or opt.get("id") or opt.get("label") or str(opt)
        opt_title = opt.get("title") or opt.get("label") or opt_key
        description = opt.get("description", "")
        risks: List[Dict[str, Any]] = []

        fatal_declared = self._extract_declared_risks(opt.get("risks", []), risks)
        desc_lower = (str(description) + " " + str(opt_title)).lower()
        fatal_inferred = self._check_inferred_risks(desc_lower, risks)
        has_fatal_flaw = fatal_declared or fatal_inferred

        severity_weights = {"CRITICAL": 0.50, "HIGH": 0.30, "MEDIUM": 0.15, "LOW": 0.05}
        raw_risk = sum(severity_weights.get(r["severity"], 0.1) for r in risks)
        risk_score = round(min(1.0, raw_risk), 3)

        return {
            "option_key": opt_key,
            "option_title": opt_title,
            "risk_score": risk_score,
            "has_fatal_flaw": has_fatal_flaw,
            "risks": risks,
            "risk_count": len(risks),
            "role_weight": self.role_weight,
            "model": self.card.model_binding.model_id,
            "reasoning_effort": self.card.model_binding.reasoning_effort,
        }

    def analyze_risks(
        self,
        options: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Adversarially scrutinizes each candidate option."""
        return [self._analyze_single_option_risk(opt) for opt in options]

    def _calculate_velocity(self, desc_lower: str) -> Tuple[float, str]:
        if "turnkey" in desc_lower or "automated" in desc_lower or "immediate" in desc_lower:
            return 0.90, "RAPID_ACCELERATION"
        if "complex" in desc_lower or "multi-phase" in desc_lower:
            return 0.55, "MODERATE_VELOCITY"
        return 0.75, "STANDARD_VELOCITY"

    def _calculate_roi(self, opt: Dict[str, Any], desc_lower: str) -> float:
        roi_raw = opt.get("roi") or opt.get("estimated_roi")
        if isinstance(roi_raw, (int, float)):
            return min(1.0, max(0.0, float(roi_raw)))
        return 0.80 if ("high" in desc_lower or "roi" in desc_lower) else 0.70

    def _calculate_elegance(self, desc_lower: str, benefits: List[str]) -> float:
        if "modular" in desc_lower or "decoupled" in desc_lower or "contract" in desc_lower:
            benefits.append("Modular, contract-first decoupled architecture")
            return 0.95
        if "monolith" in desc_lower or "legacy" in desc_lower:
            return 0.40
        return 0.75

    def _evaluate_single_option_feasibility(
        self, opt: Dict[str, Any], strategic_goals: List[str]
    ) -> Dict[str, Any]:
        opt_key = opt.get("key") or opt.get("id") or opt.get("label") or str(opt)
        opt_title = opt.get("title") or opt.get("label") or opt_key
        description = str(opt.get("description", ""))

        benefits: List[str] = [str(b) for b in opt.get("benefits", []) if isinstance(opt.get("benefits"), list)]
        desc_lower = (description + " " + str(opt_title)).lower()

        velocity_score, velocity_impact = self._calculate_velocity(desc_lower)
        roi_score = self._calculate_roi(opt, desc_lower)
        elegance_score = self._calculate_elegance(desc_lower, benefits)

        composite_feasibility = round(
            0.40 * elegance_score + 0.35 * velocity_score + 0.25 * roi_score,
            3,
        )

        return {
            "option_key": opt_key,
            "option_title": opt_title,
            "feasibility_score": composite_feasibility,
            "velocity_score": velocity_score,
            "velocity_impact": velocity_impact,
            "elegance_score": elegance_score,
            "roi_score": roi_score,
            "role_weight": self.role_weight,
            "benefits": benefits,
            "alignment_goals": strategic_goals,
            "model": self.card.model_binding.model_id,
            "reasoning_effort": self.card.model_binding.reasoning_effort,
        }

    def evaluate_feasibility(
        self,
        options: List[Dict[str, Any]],
        strategic_goals: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluates feasibility, alignment, velocity, and ROI for each option."""
        goals = strategic_goals or [
            "Accelerate delivery",
            "Maintain high reliability",
            "Enforce security governance",
        ]
        results = [self._evaluate_single_option_feasibility(opt, goals) for opt in options]
        results.sort(key=lambda x: x["feasibility_score"], reverse=True)
        return results

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Handles A2A task invocation based on configured perspective."""
        options = task.input_payload.get("options", [])
        context = task.input_payload.get("context", {})

        if self.perspective == PerspectiveType.RED_TEAM:
            evidence = task.input_payload.get("evidence", [])
            risk_assessments = self.analyze_risks(options, context, evidence)
            critical_count = sum(1 for a in risk_assessments if a["has_fatal_flaw"])
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "risk_assessments": risk_assessments,
                "critical_flaw_detected": critical_count > 0,
                "model_used": self.card.model_binding.model_id,
            }

        strategic_goals = task.input_payload.get("strategic_goals", [])
        evaluations = self.evaluate_feasibility(options, strategic_goals, context)
        top_option = evaluations[0] if evaluations else None
        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "evaluations": evaluations,
            "top_feasible_option": top_option,
            "model_used": self.card.model_binding.model_id,
        }


__all__ = [
    "DialecticalPerspectiveAgent",
    "PerspectiveType",
    "get_dialectical_perspective_card",
]
