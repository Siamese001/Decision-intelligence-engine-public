"""Agent-B5-Healer: Canonical Projection Healer Agent.

Surgically repairs Gate 3 defects in Output 3 (03_canonical_signal_projection.json)
without re-running multi-agent extraction or Stage 03 synthesis.
Powered by OpenAI gpt-5.6-sol (reasoning_effort="medium").
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from dual_pipeline.l2_healer_service import UnifiedL2HealerService
from dual_pipeline.l2_healing_contracts import (
    HealingScope,
    L2HealingRequest,
    L2HealingResult,
)
from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
from dual_pipeline.model_registry import ModelTier, get_binding
from storage.credentials import CredentialManager

if TYPE_CHECKING:
    from dual_pipeline.learning_adapters.live_pipeline_integrator import (
        LivePipelineIntegrator,
    )

logger = logging.getLogger("dual_pipeline.agents.canonical_projection_healer")


def get_canonical_projection_healer_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> AgentCard:
    binding = get_binding(ModelTier.SURGICAL_HEALER)
    eff_model = model_id or binding.model_id
    eff_effort = reasoning_effort or binding.reasoning_effort
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-b:projection-healer:1.0.0",
        name="CanonicalProjectionHealerAgent",
        version="1.0.0",
        role="Canonical Projection Healer",
        domain_focus="Surgical in-flight repair of Schema 5.7 defects, orphan nodes, prefix violations, and mutation budgets",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=eff_model,
            provider="openai",
            reasoning_effort=eff_effort,
        ),

        skills=[
            "HealPydanticSchema",
            "ResolveTopologyOrphans",
            "PruneMutationBudget",
            "NormalizeW2Prefixes",
            "EnforceW3TemporalHygiene",
        ],
        allowed_tools=["openai_chat"],
    )


class CanonicalProjectionHealerAgent:
    """Specialized healer repairing Gate 3 graph projection defects in-flight."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        mock_mode: bool = False,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        integrator: Optional[Any] = None,
    ) -> None:
        self.api_key = api_key or CredentialManager.get_credential("OPENAI_API_KEY")
        self.mock_mode = mock_mode or not bool(self.api_key)
        if integrator is not None:
            self.integrator = integrator
        else:
            try:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import (
                    get_live_pipeline_integrator,
                )
                self.integrator = get_live_pipeline_integrator()
            except ImportError:
                self.integrator = None
        binding = get_binding(ModelTier.SURGICAL_HEALER)
        self.model_id = model_id or binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "medium"
        self.card = get_canonical_projection_healer_card(
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
        )
        self.healer_service = UnifiedL2HealerService(
            api_key=self.api_key,
            model=self.card.model_binding.model_id,
            reasoning_effort=self.reasoning_effort,
            mock_mode=self.mock_mode,
        )

    async def heal_projection(
        self,
        candidate_projection: Dict[str, Any],
        failure_reasons: Sequence[str],
        forensic_transcript: str = "",
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> L2HealingResult:
        """Executes surgical in-flight repair of Output 3."""
        request = L2HealingRequest(
            scope=HealingScope.PIPELINE_B_PROJECTION,
            candidate_artifact=candidate_projection,
            failure_reasons=failure_reasons,
            context_metadata={"forensic_transcript_snippet": forensic_transcript[:2000]},
            model_id=self.card.model_binding.model_id,
            reasoning_effort="high",
            max_repair_calls=1,
        )

        result = await self.healer_service.execute_healing(request)

        # Emit outcome event for component repair learning
        if self.integrator:
            evt = AgentOutcomeAdapter.build_component_repair_event(
                target_component="CanonicalProjectionHealerAgent",
                retry_class="canonical_projection_repair",
                defect_message="; ".join(failure_reasons) if failure_reasons else "generic_projection_repair",
                was_successful=result.is_success,
                attempt=1,
                source_agent="CanonicalProjectionHealerAgent",
                run_id=run_id,
                receipt_id=receipt_id,
            )
            self.integrator.record_outcome(evt)

        return result

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """A2A task handler for standalone or mesh delegation."""
        candidate_projection = task.input_payload.get("candidate_projection", {})
        failure_reasons = task.input_payload.get("failure_reasons", [])
        forensic_transcript = task.input_payload.get("forensic_transcript", "")

        result = await self.heal_projection(
            candidate_projection=candidate_projection,
            failure_reasons=failure_reasons,
            forensic_transcript=forensic_transcript,
        )

        return {
            "task_id": task.task_id,
            "status": "COMPLETED" if result.is_success else "FAILED",
            "healing_result": result.to_dict(),
            "healed_projection": result.healed_artifact if result.is_success else candidate_projection,
        }
