"""A2A Agent Card Specification and Registry.

Enforces:
- Law 1 & Law 2 Compute Posture isolation (CLIENT_SAFE vs INTERNAL_ADVISORY).
- Declarative capabilities, schemas, and model bindings (e.g. GPT-5.6-sol with reasoning effort).
- /.well-known/agent-card.json standard alignment.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class ComputePosture(str, Enum):
    """Compute and network egress boundary."""
    CLIENT_SAFE = "CLIENT_SAFE"
    INTERNAL_ADVISORY = "INTERNAL_ADVISORY"


@dataclass(frozen=True)
class ModelBinding:
    """Model infrastructure assigned to an A2A agent."""
    model_id: str
    provider: str  # "openai", "mlx", "local", "bge"
    reasoning_effort: Optional[str] = None  # "high", "medium", "low" (for reasoning models)
    api_key_env: Optional[str] = "OPENAI_API_KEY"
    temperature: Optional[float] = None
    context_window: int = 128000

    def __post_init__(self) -> None:
        # Reasoning models forbid temperature in OpenAI API (triggers HTTP 400 invalid parameter)
        from config.credentials import REASONING_MODEL_PREFIXES
        is_reasoning_model = (
            self.reasoning_effort is not None
            or self.model_id.lower().startswith(REASONING_MODEL_PREFIXES)
        )
        if is_reasoning_model:
            object.__setattr__(self, "temperature", None)
            if self.reasoning_effort is None:
                object.__setattr__(self, "reasoning_effort", "medium")
        elif self.temperature is None:
            object.__setattr__(self, "temperature", 0.0)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        if self.reasoning_effort is None:
            res.pop("reasoning_effort", None)
        if self.temperature is None:
            res.pop("temperature", None)
        return res

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelBinding:
        raw_temp = data.get("temperature")
        temp = float(raw_temp) if raw_temp is not None else None
        return cls(
            model_id=str(data["model_id"]),
            provider=str(data.get("provider", "openai")),
            reasoning_effort=data.get("reasoning_effort"),
            api_key_env=data.get("api_key_env", "OPENAI_API_KEY"),
            temperature=temp,
            context_window=int(data.get("context_window", 128000)),
        )


@dataclass(frozen=True)
class AgentCard:
    """Standardized metadata card declaring agent capability, identity, and trust posture."""
    agent_id: str
    name: str
    version: str
    role: str
    domain_focus: str
    compute_posture: ComputePosture
    model_binding: ModelBinding
    skills: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    input_schema_urn: Optional[str] = None
    output_schema_urn: Optional[str] = None
    trust_scope: str = "packet_integrity_only"
    endpoint_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "version": self.version,
            "role": self.role,
            "domain_focus": self.domain_focus,
            "compute_posture": self.compute_posture.value,
            "model_binding": self.model_binding.to_dict(),
            "skills": list(self.skills),
            "allowed_tools": list(self.allowed_tools),
            "input_schema_urn": self.input_schema_urn,
            "output_schema_urn": self.output_schema_urn,
            "trust_scope": self.trust_scope,
            "endpoint_url": self.endpoint_url,
            "metadata": dict(self.metadata),
        }

    def to_agent_card_json(self) -> str:
        """Emits /.well-known/agent-card.json projection."""
        payload = {
            "$schema": "https://a2a-protocol.org/schemas/agent-card-v1.json",
            "name": self.name,
            "id": self.agent_id,
            "version": self.version,
            "description": f"{self.role}: {self.domain_focus}",
            "capabilities": {
                "compute_posture": self.compute_posture.value,
                "skills": self.skills,
                "tools": self.allowed_tools,
                "model": self.model_binding.to_dict(),
            },
            "contracts": {
                "input": self.input_schema_urn,
                "output": self.output_schema_urn,
            },
            "trust": {
                "scope": self.trust_scope,
            },
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentCard:
        return cls(
            agent_id=str(data["agent_id"]),
            name=str(data["name"]),
            version=str(data["version"]),
            role=str(data["role"]),
            domain_focus=str(data["domain_focus"]),
            compute_posture=ComputePosture(str(data["compute_posture"])),
            model_binding=ModelBinding.from_dict(data["model_binding"]),
            skills=list(data.get("skills", [])),
            allowed_tools=list(data.get("allowed_tools", [])),
            input_schema_urn=data.get("input_schema_urn"),
            output_schema_urn=data.get("output_schema_urn"),
            trust_scope=str(data.get("trust_scope", "packet_integrity_only")),
            endpoint_url=data.get("endpoint_url"),
            metadata=dict(data.get("metadata", {})),
        )


class AgentRegistry:
    """Registry maintaining active, approved A2A Agent Cards and enforcing compatibility."""

    def __init__(self) -> None:
        self._cards: Dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> None:
        """Register an agent card."""
        self._cards[card.agent_id] = card

    def get(self, agent_id: str) -> Optional[AgentCard]:
        return self._cards.get(agent_id)

    def find_by_role(self, role: str) -> List[AgentCard]:
        return [c for c in self._cards.values() if c.role.lower() == role.lower()]

    def find_by_skill(self, skill: str) -> List[AgentCard]:
        return [c for c in self._cards.values() if skill in c.skills]

    def find_by_posture(self, posture: ComputePosture) -> List[AgentCard]:
        return [c for c in self._cards.values() if c.compute_posture == posture]

    def list_all(self) -> List[AgentCard]:
        return list(self._cards.values())

    def validate_delegation_compatibility(
        self,
        sender_card: AgentCard,
        recipient_card: AgentCard,
    ) -> tuple[bool, str]:
        """Enforces Law 1 & Law 2 boundary: an INTERNAL_ADVISORY agent cannot delegate to a CLIENT_SAFE cloud agent."""
        if (
            sender_card.compute_posture == ComputePosture.INTERNAL_ADVISORY
            and recipient_card.compute_posture == ComputePosture.CLIENT_SAFE
        ):
            return (
                False,
                f"COMPUTE_FORK_VIOLATION: Internal advisory agent '{sender_card.agent_id}' cannot delegate unmasked tasks "
                f"to client-safe cloud agent '{recipient_card.agent_id}'. Law 1 requires physical isolation.",
            )

        return (True, "Delegation boundary verified.")
