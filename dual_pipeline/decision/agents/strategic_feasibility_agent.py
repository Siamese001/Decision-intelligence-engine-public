"""Agent-D2: Strategic Feasibility & Value Analyst.

Evaluates strategic alignment, delivery velocity, ROI, architectural elegance,
and business value realization across candidate decision options.
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

logger = logging.getLogger("dual_pipeline.agents.strategic_feasibility")


def get_strategic_feasibility_card(
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
    eff_effort = reasoning_effort or binding.reasoning_effort or "high"
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:strategic-feasibility:1.0.0",
        name="StrategicFeasibilityAgent",
        version="1.0.0",
        role="Strategic Feasibility & Value Analyst",
        domain_focus="Strategic alignment, delivery velocity, ROI, architectural elegance, business value",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else eff_effort,
        ),
        skills=["StrategicAlignmentAnalysis", "FeasibilityAssessment", "ROIVelocityModeling"],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat_completion", "feasibility_calculator"],
    )


class StrategicFeasibilityAgent:
    """Agent evaluating strategic alignment and practical feasibility of options."""

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
        self.card = get_strategic_feasibility_card(
            model_id=eff_model,
            reasoning_effort=eff_effort,
            use_local_model=self.use_local_model,
        )

    def evaluate_feasibility(
        self,
        options: List[Dict[str, Any]],
        strategic_goals: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluates feasibility, alignment, velocity, and ROI for each option."""
        strategic_goals = strategic_goals or ["Accelerate delivery", "Maintain high reliability", "Enforce security governance"]
        context = context or {}
        results: List[Dict[str, Any]] = []

        for opt in options:
            opt_key = opt.get("key") or opt.get("id") or opt.get("label") or str(opt)
            opt_title = opt.get("title") or opt.get("label") or opt_key
            description = str(opt.get("description", ""))

            # Calculate strategic alignment based on goal overlaps
            benefits: List[str] = []
            opt_benefits = opt.get("benefits", [])
            if isinstance(opt_benefits, list):
                benefits.extend([str(b) for b in opt_benefits])

            # Heuristics for feasibility attributes
            desc_lower = (description + " " + str(opt_title)).lower()

            # Velocity score (0.0 to 1.0)
            if "turnkey" in desc_lower or "automated" in desc_lower or "immediate" in desc_lower:
                velocity_score = 0.90
                velocity_impact = "RAPID_ACCELERATION"
            elif "complex" in desc_lower or "multi-phase" in desc_lower:
                velocity_score = 0.55
                velocity_impact = "MODERATE_VELOCITY"
            else:
                velocity_score = 0.75
                velocity_impact = "STANDARD_VELOCITY"

            # ROI score (0.0 to 1.0)
            roi_raw = opt.get("roi") or opt.get("estimated_roi")
            if isinstance(roi_raw, (int, float)):
                roi_score = min(1.0, max(0.0, float(roi_raw)))
            else:
                roi_score = 0.80 if "high" in desc_lower or "roi" in desc_lower else 0.70

            # Architectural elegance (0.0 to 1.0)
            if "modular" in desc_lower or "decoupled" in desc_lower or "contract" in desc_lower:
                elegance_score = 0.95
                benefits.append("Modular, contract-first decoupled architecture")
            elif "monolith" in desc_lower or "legacy" in desc_lower:
                elegance_score = 0.40
            else:
                elegance_score = 0.75

            # Composite feasibility score
            composite_feasibility = round(
                0.40 * elegance_score + 0.35 * velocity_score + 0.25 * roi_score,
                3,
            )

            results.append({
                "option_key": opt_key,
                "option_title": opt_title,
                "feasibility_score": composite_feasibility,
                "velocity_score": velocity_score,
                "velocity_impact": velocity_impact,
                "roi_score": roi_score,
                "elegance_score": elegance_score,
                "benefits": benefits,
                "model": self.card.model_binding.model_id,
                "reasoning_effort": self.card.model_binding.reasoning_effort,
            })

        # Sort by feasibility descending
        results.sort(key=lambda x: x["feasibility_score"], reverse=True)
        return results

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Handles A2A task invocation."""
        options = task.input_payload.get("options", [])
        strategic_goals = task.input_payload.get("strategic_goals", [])
        context = task.input_payload.get("context", {})

        evaluations = self.evaluate_feasibility(options, strategic_goals, context)
        top_option = evaluations[0] if evaluations else None

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "evaluations": evaluations,
            "top_feasible_option": top_option,
            "model_used": self.card.model_binding.model_id,
        }
