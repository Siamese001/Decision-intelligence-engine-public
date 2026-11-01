"""Agent-D1: Red Team Adversarial Risk Analyst.

Identifies adversarial failure modes, single points of failure (SPOF),
downstream architectural debt, operational hazards, and catastrophic risks.
Operates under GPT-5.6-sol with reasoning_effort='high'.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config.credentials import CredentialManager
from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)

logger = logging.getLogger("dual_pipeline.agents.red_team_risk")


def get_red_team_risk_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    use_local_model: bool = False,
) -> AgentCard:
    from dual_pipeline.model_registry import ModelTier, get_binding
    from dual_pipeline.parameters import get_local_mlx_model
    binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
    resolved_model = model_id or binding.model_id
    effective_model = get_local_mlx_model("instruct_14b") if use_local_model else resolved_model
    provider = "mlx" if use_local_model else "openai"
    eff_effort = reasoning_effort or binding.reasoning_effort or "medium"
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:red-team-risk:1.0.0",
        name="RedTeamRiskAgent",
        version="1.0.0",
        role="Red Team Adversarial Risk Analyst",
        domain_focus="Adversarial failure modes, catastrophic risks, architectural debt, operational hazards",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else eff_effort,
        ),
        skills=["AdversarialRiskAnalysis", "FailureModeIdentification", "ArchitecturalDebtEvaluation"],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat_completion", "risk_evaluator"],
    )


class RedTeamRiskAgent:
    """Agent executing adversarial risk analysis across decision options."""

    def __init__(
        self,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        use_local_model: bool = False,
    ) -> None:
        from dual_pipeline.model_registry import ModelTier, get_binding
        binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
        eff_model = model_id or binding.model_id
        eff_effort = reasoning_effort or binding.reasoning_effort or "high"
        self.use_local_model = use_local_model or CredentialManager.is_local_execution_mode()
        self.card = get_red_team_risk_card(
            model_id=eff_model,
            reasoning_effort=eff_effort,
            use_local_model=self.use_local_model,
        )

    def analyze_risks(
        self,
        options: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Adversarially scrutinizes each candidate option."""
        context = context or {}
        evidence = evidence or []
        results: List[Dict[str, Any]] = []

        for opt in options:
            opt_key = opt.get("key") or opt.get("id") or opt.get("label") or str(opt)
            opt_title = opt.get("title") or opt.get("label") or opt_key
            description = opt.get("description", "")

            # Identify risk signals based on option attributes and heuristics
            risks: List[Dict[str, Any]] = []
            has_fatal_flaw = False

            # Check if option declares explicit risks or triggers standard architectural hazards
            opt_risks = opt.get("risks", [])
            if isinstance(opt_risks, list):
                for r in opt_risks:
                    if isinstance(r, dict):
                        sev = r.get("severity", "MEDIUM").upper()
                        risks.append({
                            "risk_id": f"risk-{len(risks)+1}",
                            "title": r.get("title", "Identified risk"),
                            "severity": sev,
                            "likelihood": r.get("likelihood", "MEDIUM").upper(),
                            "blast_radius": r.get("blast_radius", "COMPONENT"),
                            "mitigation": r.get("mitigation", "Implement defensive guardrails"),
                            "is_fatal_flaw": sev == "CRITICAL",
                        })
                        if sev == "CRITICAL":
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

            # Check for standard architectural debt and operational failure modes
            desc_lower = (str(description) + " " + str(opt_title)).lower()
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
                has_fatal_flaw = True
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

            # Calculate composite risk score: 0.0 (no risk) to 1.0 (untenable risk)
            # Weights: CRITICAL=0.5, HIGH=0.3, MEDIUM=0.15, LOW=0.05
            severity_weights = {"CRITICAL": 0.50, "HIGH": 0.30, "MEDIUM": 0.15, "LOW": 0.05}
            raw_risk = sum(severity_weights.get(r["severity"], 0.1) for r in risks)
            risk_score = round(min(1.0, raw_risk), 3)

            results.append({
                "option_key": opt_key,
                "option_title": opt_title,
                "risk_score": risk_score,
                "has_fatal_flaw": has_fatal_flaw,
                "risks": risks,
                "risk_count": len(risks),
                "model": self.card.model_binding.model_id,
                "reasoning_effort": self.card.model_binding.reasoning_effort,
            })

        return results

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Handles A2A task invocation."""
        options = task.input_payload.get("options", [])
        context = task.input_payload.get("context", {})
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
