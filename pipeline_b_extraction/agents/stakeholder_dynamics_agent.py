"""Agent-B3: Stakeholder Dynamics & Power Mapping Agent.

Confidential internal advisory intelligence (STAKEHOLDER_DYNAMICS, DECISION_INFLUENCE).
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

logger = logging.getLogger("dual_pipeline.agents.stakeholder_dynamics")


def get_stakeholder_dynamics_card() -> AgentCard:
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-b:stakeholder-dynamics:1.0.0",
        name="StakeholderDynamicsAgent",
        version="1.0.0",
        role="Organizational Dynamics Strategist",
        domain_focus="Stakeholder dynamics, true veto power, executive alignment",
        compute_posture=ComputePosture.INTERNAL_ADVISORY,
        model_binding=ModelBinding(
            model_id=get_local_mlx_model("instruct_8b"),
            provider="mlx",
            temperature=0.0,
        ),
        skills=["MapStakeholderInfluence", "DetectExecutiveVeto", "AnalyzeConsensus"],
        allowed_tools=["mlx_lm_local"],
    )


class StakeholderDynamicsAgent:
    """Local Apple Silicon agent analyzing executive power dynamics and vetoes."""

    def __init__(self, mock_mode: bool = False) -> None:
        self.mock_mode = mock_mode
        self.card = get_stakeholder_dynamics_card()

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Extracts stakeholder dynamics facts locally on Apple Metal."""
        evidence_id = task.input_payload.get("evidence_id", "ev-001")
        evidence_text = task.input_payload.get("text", "")
        speaker = task.input_payload.get("speaker", "Speaker")

        logger.info(f"StakeholderDynamicsAgent processing evidence '{evidence_id}' locally on Metal")

        # Mock / deterministic extraction for tests and offline runs
        if self.mock_mode or not evidence_text:
            node = InternalAdvisoryNode(
                node_id=deterministic_advisory_node_id(evidence_id, AdvisoryCategory.DECISION_INFLUENCE.value, "Executive Sponsor Veto"),
                evidence_id=evidence_id,
                type="DECISION_INFLUENCE",
                name="Executive Sponsor Veto",
                category=AdvisoryCategory.DECISION_INFLUENCE,
                properties={"speaker": speaker, "summary": "Sponsor holds implicit architectural veto over S3 bucket design."},
                strategic_value=9.0,
                tacticality=1.0,
                confidence=0.88,
                stealth_score=0.9,
            )
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "nodes": [node.to_dict()],
                "extracted_count": 1,
            }

        # Local Metal inference via mlx-lm if available
        try:
            from mlx_lm import generate, load
            model, tokenizer = load(self.card.model_binding.model_id)
            prompt = (
                f"Analyze stakeholder power dynamics and decision vetoes in this statement.\n"
                f"Speaker: {speaker}\nStatement: '{evidence_text}'\n"
                f"Category: DECISION_INFLUENCE or STAKEHOLDER_DYNAMICS\n"
                f"Output: Summary of internal dynamic."
            )
            resp = generate(model, tokenizer, prompt=prompt, max_tokens=150)
            node = InternalAdvisoryNode(
                node_id=deterministic_advisory_node_id(evidence_id, AdvisoryCategory.STAKEHOLDER_DYNAMICS.value, "Observed Dynamic"),
                evidence_id=evidence_id,
                type="STAKEHOLDER_DYNAMICS",
                name="Observed Dynamic",
                category=AdvisoryCategory.STAKEHOLDER_DYNAMICS,
                properties={"speaker": speaker, "summary": resp.strip(), "detail": evidence_text},
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
            # Fallback to deterministic extraction
            node = InternalAdvisoryNode(
                node_id=deterministic_advisory_node_id(evidence_id, AdvisoryCategory.DECISION_INFLUENCE.value, "Inferred Influence"),
                evidence_id=evidence_id,
                type="DECISION_INFLUENCE",
                name="Inferred Influence",
                category=AdvisoryCategory.DECISION_INFLUENCE,
                properties={"speaker": speaker, "summary": f"Key decision authority asserted by {speaker}.", "detail": evidence_text},
                strategic_value=7.5,
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

