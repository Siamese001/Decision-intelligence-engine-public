"""Factory for instantiating and configuring Antigravity SDK agents.

Enforces governed budget limits, compaction thresholds, connection strategies,
and subagent capability boundaries across both cloud (Gemini / Vertex) and
offline on-device (LiteRT) execution environments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from config.credentials import CredentialManager

from google.antigravity import (
    AgentBehavior,
    LiteRTAgentConfig,
    LocalAgentConfig,
    LocalOpenAIAgentConfig,
)
from google.antigravity.types import (
    BudgetConfig,
    CapabilitiesConfig,
    CompactionConfig,
    RetryConfig,
    SubagentConfig,
)

from dual_pipeline.agent_sdk.connection import (
    ConnectionResolutionResult,
    ConnectionStrategy,
    resolve_connection_strategy,
)
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.parameters import get_agent_sdk_defaults

_AGENT_SDK_DEFAULTS = get_agent_sdk_defaults()
DEFAULT_MAX_MODEL_CALLS = _AGENT_SDK_DEFAULTS["default_max_model_calls"]
DEFAULT_MAX_TOOL_CALLS = _AGENT_SDK_DEFAULTS["default_max_tool_calls"]
DEFAULT_COMPACTION_TOKEN_THRESHOLD = _AGENT_SDK_DEFAULTS["default_compaction_token_threshold"]
DEFAULT_MAX_SUBAGENT_DEPTH = _AGENT_SDK_DEFAULTS["default_max_subagent_depth"]


@dataclass(frozen=True)
class GovernedAgentProfile:
    """Standardized profile for governed agent behavior and budget constraints."""

    name: str
    behavior: AgentBehavior = AgentBehavior.AUTONOMOUS
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    token_compaction_threshold: int = DEFAULT_COMPACTION_TOKEN_THRESHOLD
    max_subagent_depth: int = DEFAULT_MAX_SUBAGENT_DEPTH
    enable_subagents: bool = True


class AgentConfigFactory:
    """Builds fully governed, validated Antigravity SDK agent configurations."""

    def __init__(
        self,
        env: Optional[Mapping[str, str]] = None,
        default_profile: Optional[GovernedAgentProfile] = None,
    ) -> None:
        self._env = env if env is not None else CredentialManager.get_process_environment()
        self._profile = default_profile or GovernedAgentProfile(name="default_governed")

    @property
    def profile(self) -> GovernedAgentProfile:
        return self._profile

    def resolve_connection(
        self,
        requested_strategy: Optional[ConnectionStrategy] = None,
        model_override: Optional[str] = None,
    ) -> ConnectionResolutionResult:
        """Resolve active connection strategy given factory environment."""
        return resolve_connection_strategy(
            env=self._env,
            requested_strategy=requested_strategy,
            model_override=model_override,
        )

    def create_budget_config(
        self,
        max_model_calls: Optional[int] = None,
        max_tool_calls: Optional[int] = None,
    ) -> BudgetConfig:
        """Create governed budget configuration."""
        return BudgetConfig(
            max_model_calls=max_model_calls or self._profile.max_model_calls,
            max_tool_calls=max_tool_calls or self._profile.max_tool_calls,
        )

    def create_compaction_config(
        self,
        token_threshold: Optional[int] = None,
    ) -> CompactionConfig:
        """Create governed trajectory compaction threshold."""
        return CompactionConfig(
            token_threshold=token_threshold or self._profile.token_compaction_threshold
        )

    def create_capabilities_config(
        self,
        enabled_tools: Optional[Sequence[str]] = None,
        disabled_tools: Optional[Sequence[str]] = None,
        allowed_subagents: Optional[Sequence[str]] = None,
        max_subagent_depth: Optional[int] = None,
        agent_behavior: Optional[AgentBehavior] = None,
    ) -> CapabilitiesConfig:
        """Create governed capabilities and subagent boundary configuration."""
        subagents_enabled = self._profile.enable_subagents
        depth = (max_subagent_depth or self._profile.max_subagent_depth) if subagents_enabled else None
        subagents_list = list(allowed_subagents) if (allowed_subagents and subagents_enabled) else None

        return CapabilitiesConfig(
            enable_subagents=subagents_enabled,
            agent_behavior=agent_behavior or self._profile.behavior,
            enabled_tools=list(enabled_tools) if enabled_tools else None,
            disabled_tools=list(disabled_tools) if disabled_tools else None,
            allowed_subagents=subagents_list,
            max_subagent_depth=depth,
        )

    def create_local_agent_config(
        self,
        system_instructions: Optional[str] = None,
        tools: Optional[Sequence[Any]] = None,
        subagents: Optional[Sequence[SubagentConfig]] = None,
        mcp_servers: Optional[Sequence[Any]] = None,
        mcp_profile: Optional[str] = None,
        model_name: Optional[str] = None,
        requested_strategy: Optional[ConnectionStrategy] = None,
        retry_config: Optional[RetryConfig] = None,
    ) -> LocalAgentConfig:
        """Create LocalAgentConfig for hosted Gemini API or Vertex AI."""
        resolution = self.resolve_connection(
            requested_strategy=requested_strategy,
            model_override=model_name,
        )

        budget_cfg = self.create_budget_config()
        compaction_cfg = self.create_compaction_config()
        capabilities_cfg = self.create_capabilities_config()

        resolved_mcp: Optional[list[Any]] = None
        if mcp_servers:
            resolved_mcp = list(mcp_servers)
        elif mcp_profile:
            from dual_pipeline.agent_sdk.mcp_bridge import McpServerRegistry

            registry = McpServerRegistry()
            resolved_mcp = registry.build_profile_servers(mcp_profile)

        kwargs: dict[str, Any] = {
            "system_instructions": system_instructions,
            "tools": list(tools) if tools else None,
            "subagents": list(subagents) if subagents else None,
            "mcp_servers": resolved_mcp,
            "capabilities": capabilities_cfg,
            "budget_config": budget_cfg,
            "compaction_config": compaction_cfg,
            "retry_config": retry_config or RetryConfig(),
            "model": resolution.model_name or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH),
        }

        if resolution.vertex_enabled:
            kwargs["vertex"] = True
            kwargs["project"] = resolution.project_id
            kwargs["location"] = resolution.location
            api_key = self._env.get("VERTEX_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key
        else:
            api_key = self._env.get("GEMINI_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key

        return LocalAgentConfig(**kwargs)

    def create_litert_agent_config(
        self,
        model_path: Optional[str] = None,
        system_instructions: Optional[str] = None,
        tools: Optional[Sequence[Any]] = None,
        backend: Optional[str] = None,
    ) -> LiteRTAgentConfig:
        """Create LiteRTAgentConfig for offline on-device inference."""
        target_path = model_path or self._env.get("LITERT_MODEL_PATH", "models/gemma-4-26b.bin")
        budget_cfg = self.create_budget_config()
        compaction_cfg = self.create_compaction_config()
        capabilities_cfg = self.create_capabilities_config()

        return LiteRTAgentConfig(
            model_path=target_path,
            system_instructions=system_instructions,
            tools=list(tools) if tools else None,
            backend=backend or "cpu",
            capabilities=capabilities_cfg,
            budget_config=budget_cfg,
            compaction_config=compaction_cfg,
        )

    def create_config_for_environment(
        self,
        system_instructions: Optional[str] = None,
        tools: Optional[Sequence[Any]] = None,
        model_name: Optional[str] = None,
    ) -> Any:
        """Auto-detect environment and instantiate the optimal agent configuration."""
        resolution = self.resolve_connection(model_override=model_name)
        if resolution.strategy == ConnectionStrategy.LITERT_LOCAL:
            return self.create_litert_agent_config(
                system_instructions=system_instructions,
                tools=tools,
            )
        if resolution.strategy == ConnectionStrategy.OPENAI_COMPATIBLE:
            return LocalOpenAIAgentConfig(
                system_instructions=system_instructions,
                tools=list(tools) if tools else None,
                budget_config=self.create_budget_config(),
                compaction_config=self.create_compaction_config(),
                capabilities=self.create_capabilities_config(),
            )
        return self.create_local_agent_config(
            system_instructions=system_instructions,
            tools=tools,
            model_name=model_name,
            requested_strategy=resolution.strategy,
        )
