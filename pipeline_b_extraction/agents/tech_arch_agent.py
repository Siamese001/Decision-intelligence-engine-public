"""Agent-B1: Technical Architecture & Systems Agent.

Extracts canonical system architectures, interface contracts, throughput SLAs,
and infrastructure dependencies into CANONICAL_GRAPH.db.
Powered by OpenAI gpt-5.6-sol (reasoning_effort="high").
"""
from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, Dict, List, Optional

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)
from dual_pipeline.model_registry import ModelTier, get_binding
from dual_pipeline.parameters import get_local_mlx_model
from storage.credentials import CredentialManager
from storage.database import CanonicalGraphNode
from storage.identifiers import deterministic_canonical_node_id

logger = logging.getLogger("dual_pipeline.agents.tech_arch")


def get_tech_arch_card(
    model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    use_local_model: bool = False,
) -> AgentCard:
    binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)
    eff_model = model_id or binding.model_id
    eff_effort = reasoning_effort or binding.reasoning_effort
    effective_model = get_local_mlx_model("instruct_14b") if use_local_model else eff_model
    provider = "mlx" if use_local_model else binding.provider
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-b:tech-arch:1.0.0",
        name="TechArchExtractor",
        version="1.0.0",
        role="Principal Systems Architect",
        domain_focus="Extract architectural decisions, data contracts, and technical specs",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id=effective_model,
            provider=provider,
            reasoning_effort=None if use_local_model else eff_effort,
        ),
        skills=["ExtractArchitecture", "ExtractDataContracts", "MapSystemDependencies"],
        allowed_tools=["mlx_inference" if use_local_model else "openai_chat"],
    )


def _build_mock_tech_arch_nodes(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for item in items:
        evidence_id = item.get("evidence_id", "ev-001")
        text = item.get("text", "")
        if not text:
            continue
        node = CanonicalGraphNode(
            node_id=deterministic_canonical_node_id(evidence_id, "ARCHITECTURAL_DECISION", "LENS Pipeline Architecture"),
            evidence_id=evidence_id,
            type="ARCHITECTURAL_DECISION",
            name="LENS Pipeline Architecture",
            properties={"component": "VLM Nameplate Extraction", "platform": "AWS S3 / Metal"},
            strategic_value=9.0,
            confidence=0.95,
            tacticality=2.0,
        )
        nodes.append(node.to_dict())
    return nodes


def _parse_tech_arch_facts(raw_facts: List[Dict[str, Any]], fallback_ev_id: str) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for fact in raw_facts:
        ev_id = fact.get("evidence_id") or fallback_ev_id
        n_type = fact.get("type", "ARCHITECTURAL_DECISION")
        n_name = fact.get("name", "Technical Specification")
        node = CanonicalGraphNode(
            node_id=deterministic_canonical_node_id(ev_id, n_type, n_name),
            evidence_id=ev_id,
            type=n_type,
            name=n_name,
            properties=fact.get("properties", {}),
            strategic_value=float(fact.get("strategic_value", 7.0)),
            confidence=0.90,
            tacticality=float(fact.get("tacticality", 3.0)),
        )
        nodes.append(node.to_dict())
    return nodes


class TechArchExtractor:
    """Agent mining client-safe technical architecture facts."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        mock_mode: bool = False,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        use_local_model: bool = False,
    ) -> None:
        self.use_local_model = use_local_model or CredentialManager.is_local_execution_mode()
        self.api_key = api_key or CredentialManager.get_credential("OPENAI_API_KEY")
        self.mock_mode = mock_mode or (not self.use_local_model and not bool(self.api_key))
        self.card = get_tech_arch_card(use_local_model=self.use_local_model)
        if model_id or reasoning_effort:
            new_mb = dataclasses.replace(
                self.card.model_binding,
                model_id=model_id or self.card.model_binding.model_id,
                reasoning_effort=reasoning_effort if reasoning_effort is not None else self.card.model_binding.reasoning_effort,
            )
            self.card = dataclasses.replace(self.card, model_binding=new_mb)

    async def extract_batch(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extracts technical architecture facts from a batch of evidence items in a single call."""
        if not items:
            return []

        if self.mock_mode:
            return _build_mock_tech_arch_nodes(items)

        try:
            client, _ = CredentialManager.get_async_openai_client(
                allow_mock=self.mock_mode,
                model=self.card.model_binding.model_id,
                reasoning_effort=getattr(self.card.model_binding, "reasoning_effort", None) or "high",
                api_key=self.api_key,
            )
            if client is None:
                raise ValueError("No live client configured; fallback to mock")
            compact_items = json.dumps(
                [
                    {
                        "evidence_id": it.get("evidence_id", f"ev-{i}"),
                        "speaker": it.get("speaker", "Speaker"),
                        "text": it.get("text", ""),
                    }
                    for i, it in enumerate(items)
                    if it.get("text")
                ],
                separators=(",", ":"),
            )

            tech_arch_system_prompt = (
                "You are a Principal Enterprise Systems Architect.\n"
                "Analyze the following batch of evidence items and extract technical architecture facts for each item:\n"
                "- ARCHITECTURAL_DECISION\n"
                "- TECHNICAL_SPECIFICATION\n"
                "- DATA_CONTRACT\n"
                "- SYSTEM_DEPENDENCY\n\n"
                'Return JSON: {"facts": [{"evidence_id": str, "type": str, "name": str, "properties": dict, "strategic_value": float, "tacticality": float}]}'
            )
            user_content = f"EVIDENCE ITEMS (JSON):\n{compact_items}"
            prompt = f"{tech_arch_system_prompt}\n\n{user_content}"

            from config.request_deadline import remaining_timeout

            raw_kwargs: Dict[str, Any] = {
                "model": self.card.model_binding.model_id,
                "messages": [
                    {"role": "system", "content": tech_arch_system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "reasoning_effort": getattr(self.card.model_binding, "reasoning_effort", None) or "high",
                "cache_namespace": "tech_arch_agent",
                "timeout": remaining_timeout(120.0),
            }
            call_kwargs = CredentialManager.sanitize_call_kwargs(self.card.model_binding.model_id, raw_kwargs)
            resp = await client.chat.completions.create(**call_kwargs)
            try:
                from dual_pipeline.prompt_cache import normalize_cached_tokens

                cached_toks = normalize_cached_tokens(resp)
                if cached_toks > 0:
                    logger.debug("TechArchAgent utilized %d cached tokens.", cached_toks)
            except Exception:
                pass
            data = json.loads(resp.choices[0].message.content or "{}")
            raw_facts = data.get("facts", [])
            fallback_ev_id = items[0].get("evidence_id", "ev-001") if items else "ev-001"
            return _parse_tech_arch_facts(raw_facts, fallback_ev_id)
        except Exception as exc:
            logger.warning(f"TechArchExtractor batch call failed: {exc}")
            return []

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Extracts technical architecture facts from an evidence item or batch."""
        if "items" in task.input_payload:
            items = task.input_payload["items"]
            nodes = await self.extract_batch(items)
        else:
            item = {
                "evidence_id": task.input_payload.get("evidence_id", "ev-001"),
                "text": task.input_payload.get("text", ""),
                "speaker": task.input_payload.get("speaker", "Speaker"),
            }
            nodes = await self.extract_batch([item])

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "nodes": nodes,
            "extracted_count": len(nodes),
        }


# Alias for uniform agent naming
TechArchAgent = TechArchExtractor
