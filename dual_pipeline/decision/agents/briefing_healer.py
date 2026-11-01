"""Agent-D5-Healer: Briefing and Decision Healer Agent.

Surgically repairs Gate 2 defects in Output 2 (02_executive_recap.md),
Figma 4K visual governance layout copy, and non-orthogonal decision frames.
Powered by OpenAI gpt-4o-mini-2024-07-18 (reasoning_effort="medium").
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set

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
from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    OutcomeType,
)
from storage.credentials import CredentialManager

if TYPE_CHECKING:
    from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator  # noqa: lazy-decouple

logger = logging.getLogger("dual_pipeline.agents.briefing_healer")


def get_briefing_healer_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    use_local_model: bool = False,
) -> AgentCard:
    from dual_pipeline.model_registry import ModelTier, get_binding
    from dual_pipeline.parameters import get_local_mlx_model
    binding = get_binding(ModelTier.SURGICAL_HEALER)
    resolved_model = model_id or binding.model_id
    effective_model = get_local_mlx_model("instruct_14b") if use_local_model else resolved_model
    provider = "mlx" if use_local_model else "openai"
    eff_effort = reasoning_effort or binding.reasoning_effort or "medium"
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-d:briefing-healer:1.0.0",
        name="BriefingAndDecisionHealerAgent",
        version="1.0.0",
        role="Briefing and Decision Healer",
        domain_focus="Surgical in-flight repair of Gate 2 passive action verbs, unassigned owners, Figma 4K copy condensation, and decision frame orthogonality",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else eff_effort,
        ),

        skills=[
            "HealPassiveActionVerbs",
            "AssignRosterOwners",
            "EnforceFigma4KRepairHierarchy",
            "OrthogonalizeDecisionFrames",
        ],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat"],
    )


class BriefingAndDecisionHealerAgent:
    """Specialized single-pass healer repairing Pipeline D briefing and decision frame defects in-flight."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        mock_mode: bool = False,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        use_local_model: bool = False,
        integrator: Optional[Any] = None,
    ) -> None:
        self.use_local_model = use_local_model or CredentialManager.is_local_execution_mode()
        self.api_key = api_key or CredentialManager.get_credential("OPENAI_API_KEY")
        if not mock_mode and not self.use_local_model and not bool(self.api_key):
            raise ValueError("OPENAI_API_KEY is required for BriefingAndDecisionHealerAgent when mock_mode=False and use_local_model=False.")
        self.mock_mode = mock_mode
        if integrator is not None:
            self.integrator = integrator
        else:
            try:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import get_live_pipeline_integrator  # noqa: lazy-decouple
                self.integrator = get_live_pipeline_integrator()
            except ImportError:
                self.integrator = None
        from dual_pipeline.model_registry import ModelTier, get_binding
        from dual_pipeline.parameters import get_local_mlx_model
        binding = get_binding(ModelTier.SURGICAL_HEALER)
        if model_id:
            self.model_id = model_id
        elif self.use_local_model:
            self.model_id = get_local_mlx_model("instruct_14b")
        else:
            self.model_id = binding.model_id
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "medium"
        self.card = get_briefing_healer_card(
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
            use_local_model=self.use_local_model,
        )
        self.healer_service = UnifiedL2HealerService(
            api_key=self.api_key,
            model=self.card.model_binding.model_id,
            reasoning_effort=self.reasoning_effort,
            mock_mode=self.mock_mode,
        )

    def record_briefing_repair(
        self,
        defect_type: str,
        was_successful: bool,
        run_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Emits COMPONENT_REPAIR event for executive recap / decision frame repair."""
        if not self.integrator:
            return None
        evt = AgentOutcomeAdapter.create_outcome_event(
            outcome_type=OutcomeType.COMPONENT_REPAIR,
            source_agent=self.card.name,
            stage="L4",
            category="briefing_decision_healing",
            observed_outcome={
                "defect_type": defect_type,
                "was_successful": was_successful,
            },
            status="PASS" if was_successful else "FAIL",
            run_id=run_id,
        )
        return self.integrator.record_outcome(evt)

    async def heal_executive_recap(
        self,
        recap_md: str,
        failure_reasons: Sequence[str],
        roster_attendees: Optional[List[Dict[str, Any] | str]] = None,
    ) -> L2HealingResult:
        """Surgically heals Gate 2 defects in Output 2 (action items, owners, dates, headers)."""
        roster = roster_attendees or []
        allowed_entities: Set[str] = set()

        for item in roster:
            if isinstance(item, dict):
                eid = item.get("entity_id") or f"person:{item.get('name', '').lower().replace(' ', '-')}"
                allowed_entities.add(eid)
            elif isinstance(item, str):
                allowed_entities.add(f"person:{item.lower().replace(' ', '-')}")

        request = L2HealingRequest(
            scope=HealingScope.PIPELINE_D_BRIEFING,
            candidate_artifact=recap_md,
            failure_reasons=failure_reasons,
            allowed_entity_ids=allowed_entities,
            model_id=self.card.model_binding.model_id,
            reasoning_effort="high",
            max_repair_calls=1,
        )

        res = await self.healer_service.execute_healing(request)
        self.record_briefing_repair(defect_type="executive_recap_defects", was_successful=res.is_success)
        return res

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """A2A task handler for standalone or mesh delegation."""
        recap_md = task.input_payload.get("recap_md", "")
        failure_reasons = task.input_payload.get("failure_reasons", [])
        roster = task.input_payload.get("roster_context", [])

        result = await self.heal_executive_recap(
            recap_md=recap_md,
            failure_reasons=failure_reasons,
            roster_attendees=roster,
        )

        return {
            "task_id": task.task_id,
            "status": "COMPLETED" if result.is_success else "FAILED",
            "healing_result": result.to_dict(),
            "healed_recap_md": result.healed_artifact if result.is_success else recap_md,
        }
