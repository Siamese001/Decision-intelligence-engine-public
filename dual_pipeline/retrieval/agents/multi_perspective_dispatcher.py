"""Agent-C2: Multi-Perspective Query Dispatcher Agent.

Decomposes complex executive decision questions into 4 specialized perspective queries
(Technical Architecture, Commercial Commitments, Adoption Friction, Critical Blockers).
Powered by OpenAI gpt-5.6-sol (reasoning_effort="high").
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from storage.credentials import CredentialManager

logger = logging.getLogger("dual_pipeline.agents.multi_perspective_dispatcher")


def get_multi_perspective_dispatcher_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    use_local_model: bool = False,
) -> AgentCard:
    from dual_pipeline.model_registry import ModelTier, get_binding
    from dual_pipeline.parameters import get_local_mlx_model
    binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
    resolved_model = model_id or binding.model_id
    effective_model = get_local_mlx_model("instruct_8b") if use_local_model else resolved_model
    provider = "mlx" if use_local_model else "openai"
    eff_effort = reasoning_effort or binding.reasoning_effort or "medium"
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-c:multi-perspective-dispatcher:1.0.0",
        name="MultiPerspectiveDispatcher",
        version="1.0.0",
        role="Retrieval Query Strategist",
        domain_focus="Deconstruct executive queries across technical, commercial, and advisory lenses",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else eff_effort,
        ),
        skills=["DeconstructDecisionQuery", "GenerateSubQueries", "BalancePerspectiveWeights"],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat"],
    )


def _build_mock_subqueries(task_id: str, query: str) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "COMPLETED",
        "sub_queries": [
            {
                "perspective": "TECHNICAL",
                "query_text": f"{query} technical architecture system dependencies data contract",
                "weight": 1.0,
            },
            {
                "perspective": "COMMERCIAL",
                "query_text": f"{query} delivery milestone SOW commitment target date",
                "weight": 0.9,
            },
            {
                "perspective": "ADOPTION",
                "query_text": f"{query} organizational adoption friction stakeholder inertia",
                "weight": 0.8,
            },
            {
                "perspective": "BLOCKER",
                "query_text": f"{query} blocker constraint dependency bottleneck",
                "weight": 1.0,
            },
        ],
        "perspective_count": 4,
    }


def _execute_local_mlx(task_id: str, prompt: str, query: str) -> Dict[str, Any]:
    try:
        from dual_pipeline.runtime.model_runtime import get_model_runtime

        runtime = get_model_runtime()
        res = runtime.generate(
            model_key="llama_3_1_8b_instruct",
            prompt=prompt,
            response_format="json_object",
        )
        data = res.parsed_json or json.loads(res.content or "{}")
        sub_queries = data.get("sub_queries", [])
        if not sub_queries:
            sub_queries = [{"perspective": "GENERAL", "query_text": query, "weight": 1.0}]
        return {
            "task_id": task_id,
            "status": "COMPLETED",
            "sub_queries": sub_queries,
            "perspective_count": len(sub_queries),
        }
    except Exception as exc:
        logger.warning("Local MLX MultiPerspectiveDispatcher call failed, using fallback: %s", exc)
        fallback_status = "COMPLETED"
        return {
            "task_id": task_id,
            "status": fallback_status,
            "degraded": True,
            "fallback_used": True,
            "degraded_reason": f"{type(exc).__name__}: {exc}",
            "sub_queries": [{"perspective": "GENERAL", "query_text": query, "weight": 1.0}],
            "perspective_count": 1,
        }


class MultiPerspectiveDispatcher:
    """Agent expanding and fanning out multi-perspective retrieval queries."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        mock_mode: bool = False,
        use_local_model: bool = False,
    ) -> None:
        self.use_local_model = use_local_model or CredentialManager.is_local_execution_mode()
        self.api_key = api_key or CredentialManager.get_credential("OPENAI_API_KEY")
        self.mock_mode = mock_mode or (not self.use_local_model and not bool(self.api_key))
        self.card = get_multi_perspective_dispatcher_card(use_local_model=self.use_local_model)

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Decomposes user query into 4 distinct lens sub-queries."""
        query = task.input_payload.get("query", "")
        logger.info(f"MultiPerspectiveDispatcher deconstructing query: '{query}'")

        if self.mock_mode or not query:
            return _build_mock_subqueries(task.task_id, query)

        multi_perspective_system_prompt = (
            "You are a Principal Enterprise Retrieval Architect.\n"
            "Decompose the executive inquiry into 4 distinct targeted retrieval search strings:\n"
            "1. TECHNICAL (systems, architecture, contracts)\n"
            "2. COMMERCIAL (milestones, delivery, deadlines, SOW)\n"
            "3. ADOPTION (organizational friction, change management, stakeholders)\n"
            "4. BLOCKER (dependencies, blockers, risks)\n\n"
            'Output JSON: {"sub_queries": [{"perspective": str, "query_text": str, "weight": float}]}'
        )
        user_content = f'Inquiry: "{query}"'
        prompt = f"{multi_perspective_system_prompt}\n\n{user_content}"

        if self.use_local_model or self.card.model_binding.provider == "mlx":
            return _execute_local_mlx(task.task_id, prompt, query)

        try:
            from config.credentials import get_async_openai_client
            client, creds = get_async_openai_client(
                allow_mock=self.mock_mode,
                model=self.card.model_binding.model_id,
                api_key=self.api_key,
            )
            if client is None:
                return {"task_id": task.task_id, "status": "COMPLETED", "degraded": True, "fallback_used": True, "degraded_reason": "OpenAI client unavailable", "sub_queries": [{"perspective": "GENERAL", "query_text": query, "weight": 1.0}], "perspective_count": 1}
            from config.request_deadline import remaining_timeout

            call_kwargs: Dict[str, Any] = {
                "model": self.card.model_binding.model_id,
                "messages": [
                    {"role": "system", "content": multi_perspective_system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "cache_namespace": "multi_perspective_dispatcher",
                "timeout": remaining_timeout(60.0),
            }
            from config.credentials import REASONING_MODEL_PREFIXES
            is_reasoning = (
                self.card.model_binding.model_id.lower().startswith(REASONING_MODEL_PREFIXES)
                or bool(getattr(self.card.model_binding, "reasoning_effort", None))
            )
            if is_reasoning:
                call_kwargs["reasoning_effort"] = getattr(self.card.model_binding, "reasoning_effort", None) or "medium"
            elif getattr(self.card.model_binding, "temperature", None) is not None:
                call_kwargs["temperature"] = self.card.model_binding.temperature
            resp = await client.chat.completions.create(**call_kwargs)
            data = json.loads(resp.choices[0].message.content or "{}")
            sub_queries = data.get("sub_queries", [])
            return {
                "task_id": task.task_id,
                "status": "COMPLETED",
                "sub_queries": sub_queries,
                "perspective_count": len(sub_queries),
            }
        except Exception as exc:
            logger.warning(f"MultiPerspectiveDispatcher call failed, using fallback: {exc}")
            fallback_status = "COMPLETED"
            return {
                "task_id": task.task_id, "status": fallback_status, "degraded": True, "fallback_used": True,
                "degraded_reason": f"{type(exc).__name__}: {exc}", "perspective_count": 1,
                "sub_queries": [{"perspective": "GENERAL", "query_text": query, "weight": 1.0}],
            }
