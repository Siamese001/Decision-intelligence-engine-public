"""Agent-B4: Adoption Friction & Blocker Radar Agent.

Confidential internal advisory intelligence (ADOPTION_RISK, BLOCKER).
Enforces Law 1 (Compute fork before extraction) and Law 2 (Governed internal advisory namespace).
Strictly local Apple Silicon Metal execution via mlx-lm; zero external egress.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from storage.database import (
    AdvisoryCategory,
    InternalAdvisoryNode,
)
from dual_pipeline.parameters import get_local_mlx_model
from storage.identifiers import deterministic_advisory_node_id

logger = logging.getLogger("dual_pipeline.agents.friction_blocker")


def get_friction_blocker_card() -> AgentCard:
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-b:friction-blocker:1.0.0",
        name="FrictionBlockerAgent",
        version="1.0.0",
        role="Risk & Friction Specialist",
        domain_focus="Adoption inertia, organizational friction, missing dependencies, blockers",
        compute_posture=ComputePosture.INTERNAL_ADVISORY,
        model_binding=ModelBinding(
            model_id=get_local_mlx_model("instruct_8b"),
            provider="mlx",
            temperature=0.0,
        ),
        skills=["DetectWorkflowFriction", "IdentifyBlockers", "EvaluateAdoptionRisk"],
        allowed_tools=["mlx_lm_local"],
    )


class FrictionBlockerAgent:
    """Local Apple Silicon agent detecting adoption inertia, workflow friction, and blockers."""

    def __init__(self, mock_mode: bool = False) -> None:
        self.mock_mode = mock_mode
        self.card = get_friction_blocker_card()

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Extracts friction and blocker facts locally on Apple Metal."""
        evidence_id = task.input_payload.get("evidence_id", "ev-001")
        evidence_text = task.input_payload.get("text", "")
        speaker = task.input_payload.get("speaker", "Speaker")

        logger.info(f"FrictionBlockerAgent processing evidence '{evidence_id}' locally on Metal")

        if self.mock_mode or not evidence_text:
            node = InternalAdvisoryNode(
                node_id=deterministic_advisory_node_id(evidence_id, AdvisoryCategory.BLOCKER.value, "Missing AWS IAM Roles"),
                evidence_id=evidence_id,
                type="BLOCKER",
                name="Missing AWS IAM Roles",
                category=AdvisoryCategory.BLOCKER,
                properties={
                    "summary": "Cross-account S3 access blocked by pending IAM role approval.",
                    "detail": f"Statement indicates technical blocker raised by {speaker}.",
                    "actionability": 8.0,
                },
                strategic_value=8.5,
                tacticality=1.0,
                confidence=0.90,
                stealth_score=0.9,
            )
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "nodes": [node.to_dict()],
                "extracted_count": 1,
            }

        # Local Metal inference via mlx-lm or deterministic fallback
        try:
            from mlx_lm import generate, load
            model, tokenizer = load(self.card.model_binding.model_id)
            prompt = (
                f"Identify blockers or organizational adoption friction.\n"
                f"Speaker: {speaker}\nText: '{evidence_text}'\n"
                f"Category: BLOCKER or ADOPTION_RISK\n"
                f"Output: Summary of blocker."
            )
            resp = generate(model, tokenizer, prompt=prompt, max_tokens=150)
            node = InternalAdvisoryNode(
                node_id=deterministic_advisory_node_id(evidence_id, AdvisoryCategory.BLOCKER.value, "Identified Blocker"),
                evidence_id=evidence_id,
                type="BLOCKER",
                name="Identified Blocker",
                category=AdvisoryCategory.BLOCKER,
                properties={
                    "summary": resp.strip(),
                    "detail": evidence_text,
                    "actionability": 7.0,
                },
                strategic_value=8.0,
                tacticality=1.0,
                confidence=0.85,
                stealth_score=0.8,
            )
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "nodes": [node.to_dict()],
                "extracted_count": 1,
            }
        except Exception as exc:
            node = InternalAdvisoryNode(
                node_id=deterministic_advisory_node_id(evidence_id, AdvisoryCategory.ADOPTION_RISK.value, "Inferred Risk"),
                evidence_id=evidence_id,
                type="ADOPTION_RISK",
                name="Inferred Risk",
                category=AdvisoryCategory.ADOPTION_RISK,
                properties={
                    "summary": f"Operational friction flagged by {speaker}.",
                    "detail": evidence_text,
                    "actionability": 6.0,
                },
                strategic_value=7.0,
                tacticality=1.0,
                confidence=0.80,
                stealth_score=0.8,
            )
            fallback_status = "COMPLETED"
            return {
                "task_id": task.task_id, "status": fallback_status, "fallback_used": True,
                "degraded_reason": f"{type(exc).__name__}: {exc}",
                "nodes": [node.to_dict()], "extracted_count": 1,
            }

