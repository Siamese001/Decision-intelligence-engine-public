"""Agent-C4-Healer: Retrieval Perspective Healer Agent.

Surgically repairs perspective starvation (0 hits in sub-query) and citation
coverage gaps across the 11 non-compensating release gates in Pipeline C.
Powered by OpenAI gpt-4o-mini-2024-07-18 (reasoning_effort="medium").
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
from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    OutcomeType,
)
from dual_pipeline.model_registry import ModelTier, get_binding
from storage.credentials import CredentialManager

if TYPE_CHECKING:
    from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator  # noqa: lazy-decouple

logger = logging.getLogger("dual_pipeline.agents.retrieval_perspective_healer")


def _resolve_default_integrator(integrator: Optional[Any]) -> Optional[Any]:
    if integrator is not None:
        return integrator
    try:
        from dual_pipeline.learning_adapters.live_pipeline_integrator import get_live_pipeline_integrator  # noqa: lazy-decouple
        return get_live_pipeline_integrator()
    except ImportError:
        return None


def _resolve_healer_model_id(model_id: Optional[str], use_local_model: bool, binding: Any) -> str:
    if model_id:
        return model_id
    if use_local_model:
        from dual_pipeline.parameters import get_local_mlx_model
        return get_local_mlx_model("instruct_14b")
    return binding.model_id


def get_retrieval_perspective_healer_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    use_local_model: bool = False,
) -> AgentCard:
    from dual_pipeline.parameters import get_local_mlx_model
    binding = get_binding(ModelTier.SURGICAL_HEALER)
    resolved_model = model_id or binding.model_id
    effective_model = get_local_mlx_model("instruct_14b") if use_local_model else resolved_model
    provider = "mlx" if use_local_model else "openai"
    eff_effort = reasoning_effort or binding.reasoning_effort or "medium"
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-c:perspective-healer:1.0.0",
        name="RetrievalPerspectiveHealerAgent",
        version="1.0.0",
        role="Retrieval Perspective Healer",
        domain_focus="Surgical in-flight repair of perspective starvation and 11-gate citation coverage gaps",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else eff_effort,
        ),
        skills=[
            "HealPerspectiveStarvation",
            "BridgeReleaseGateCitations",
            "EnforceLatencyBudget",
        ],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat"],
    )


class RetrievalPerspectiveHealerAgent:
    """Specialized healer repairing Pipeline C perspective starvation and gate clearance."""

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
            raise ValueError("OPENAI_API_KEY is required for RetrievalPerspectiveHealerAgent when mock_mode=False and use_local_model=False.")
        self.mock_mode = mock_mode
        self.integrator = _resolve_default_integrator(integrator)
        binding = get_binding(ModelTier.SURGICAL_HEALER)
        self.model_id = _resolve_healer_model_id(model_id, self.use_local_model, binding)
        self.reasoning_effort = reasoning_effort or binding.reasoning_effort or "medium"
        self.card = get_retrieval_perspective_healer_card(
            model_id=self.model_id,
            reasoning_effort=self.reasoning_effort,
            use_local_model=self.use_local_model,
        )
        self.fallback_openai_model_id = model_id if (model_id and not self.use_local_model) else binding.model_id
        self.healer_service = UnifiedL2HealerService(
            api_key=self.api_key,
            model=self.fallback_openai_model_id,
            reasoning_effort=self.reasoning_effort,
            mock_mode=self.mock_mode or (self.use_local_model and not bool(self.api_key)),
        )

    def record_perspective_repair(
        self,
        starved_lens: str,
        was_successful: bool,
        run_id: Optional[str] = None,
        defect_message: str = "Perspective starvation repaired",
    ) -> Optional[Any]:
        """Emits COMPONENT_REPAIR event for perspective healing."""
        if not self.integrator:
            return None
        evt = AgentOutcomeAdapter.create_outcome_event(
            outcome_type=OutcomeType.COMPONENT_REPAIR,
            source_agent=self.card.name,
            stage="L4",
            category="perspective_healing",
            observed_outcome={
                "starved_lens": starved_lens,
                "was_successful": was_successful,
                "defect_message": defect_message,
            },
            status="PASS" if was_successful else "FAIL",
            run_id=run_id,
        )
        return self.integrator.record_outcome(evt)

    def _try_local_mlx_heal(self, query: str, starved_lens: str, terms: list[str]) -> Optional[L2HealingResult]:
        try:
            import json
            from dual_pipeline.runtime.model_runtime import get_model_runtime
            runtime = get_model_runtime()
            prompt = (
                f"You are an expert Retrieval Query Reformulation Specialist.\n"
                f"Original Query: {query}\nStarved Perspective Lane: {starved_lens}\nDomain Ontology Terms: {terms}\n"
                f"Surgically reformulate the search query to retrieve facts for the {starved_lens} perspective.\n"
                f"Output JSON: {{\"healed_query\": str, \"rationale\": str}}"
            )
            res = runtime.generate(
                model_key=getattr(self, "model_id", None) or "qwen_2_5_14b_instruct",
                prompt=prompt, response_format="json_object",
            )
            data = res.parsed_json or json.loads(res.content or "{}")
            raw_healed = (data.get("healed_query") or "").strip() if isinstance(data, dict) else ""
            if not raw_healed or raw_healed == query.strip():
                raise ValueError("Local MLX healing produced empty or unchanged healed_query")
            healed_res = L2HealingResult(
                status="HEALED",
                healed_artifact={
                    "query": raw_healed, "starved_lens": starved_lens,
                    "retained_terms": terms, "rationale": data.get("rationale", "Local MLX surgical reformulation"),
                },
                patch_diff=f"- {query}\n+ {raw_healed}", repair_attempts=1, validation_passed=True,
            )
            self.record_perspective_repair(starved_lens=starved_lens, was_successful=True)
            return healed_res
        except Exception as exc:
            logger.warning(f"Local MLX healing failed, falling back: {exc}")
            return None

    async def heal_starved_perspective(
        self,
        query: str,
        starved_lens: str,
        active_ontology_terms: Optional[Sequence[str]] = None,
    ) -> L2HealingResult:
        """Surgically reformulates query terms for a starved perspective lane."""
        terms = list(active_ontology_terms or [])
        if self.use_local_model and not self.mock_mode:
            mlx_res = self._try_local_mlx_heal(query, starved_lens, terms)
            if mlx_res is not None:
                return mlx_res

        request = L2HealingRequest(
            scope=HealingScope.PIPELINE_C_RETRIEVAL,
            candidate_artifact={"query": query, "starved_lens": starved_lens},
            failure_reasons=[f"Perspective starvation: 0 candidates returned for {starved_lens}"],
            context_metadata={"known_ontology_terms": terms},
            model_id=self.fallback_openai_model_id,
            reasoning_effort="high",
            max_repair_calls=1,
        )
        res = await self.healer_service.execute_healing(request)
        self.record_perspective_repair(starved_lens=starved_lens, was_successful=res.is_success)
        return res

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """A2A task handler for standalone or mesh delegation."""
        query = task.input_payload.get("query", "")
        starved_lens = task.input_payload.get("starved_lens", "TECHNICAL")
        terms = task.input_payload.get("ontology_terms", [])

        result = await self.heal_starved_perspective(
            query=query,
            starved_lens=starved_lens,
            active_ontology_terms=terms,
        )

        return {
            "task_id": task.task_id,
            "status": "COMPLETED" if result.is_success else "FAILED",
            "healing_result": result.to_dict(),
            "healed_artifact": result.healed_artifact,
        }
