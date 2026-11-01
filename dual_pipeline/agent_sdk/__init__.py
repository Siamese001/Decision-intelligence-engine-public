"""Antigravity SDK Integration Package for Decision Intelligence Engine.

Provides first-party agent runtime primitives, governed configuration factories,
connection resolution, and multi-agent lifecycle orchestration.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from google.antigravity import (
    Agent,
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

from dual_pipeline.agent_sdk.config import (
    DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MAX_SUBAGENT_DEPTH,
    DEFAULT_MAX_TOOL_CALLS,
    AgentConfigFactory,
    GovernedAgentProfile,
)
from dual_pipeline.agent_sdk.connection import (
    ConnectionResolutionResult,
    ConnectionStrategy,
    resolve_connection_strategy,
)
from dual_pipeline.agent_sdk.hooks import NeeLifecycleHooks
from dual_pipeline.agent_sdk.mcp_bridge import (
    DEFAULT_TOOL_ALLOWLISTS,
    PROFILE_PRESETS,
    McpServerDefinition,
    McpServerRegistry,
)
from dual_pipeline.agent_sdk.orchestration import (
    NeeAgentHierarchy,
    SpecialistSpec,
)
from dual_pipeline.agent_sdk.policies import (
    HITL_REQUIRED_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    STRICTLY_DENIED_TOOLS,
    NeeSecurityPolicy,
)


def create_governed_agent(
    system_instructions: Optional[str] = None,
    tools: Optional[Sequence[Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    profile: Optional[GovernedAgentProfile] = None,
    model_name: Optional[str] = None,
) -> Agent:
    """Create an Antigravity Agent with governed defaults for budget and capabilities."""
    factory = AgentConfigFactory(env=env, default_profile=profile)
    config = factory.create_config_for_environment(
        system_instructions=system_instructions,
        tools=tools,
        model_name=model_name,
    )
    return Agent(config)


__all__ = [
    "Agent",
    "AgentBehavior",
    "AgentConfigFactory",
    "BudgetConfig",
    "CapabilitiesConfig",
    "CompactionConfig",
    "ConnectionResolutionResult",
    "ConnectionStrategy",
    "DEFAULT_COMPACTION_TOKEN_THRESHOLD",
    "DEFAULT_MAX_MODEL_CALLS",
    "DEFAULT_MAX_SUBAGENT_DEPTH",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_TOOL_ALLOWLISTS",
    "GovernedAgentProfile",
    "HITL_REQUIRED_TOOLS",
    "LiteRTAgentConfig",
    "LocalAgentConfig",
    "LocalOpenAIAgentConfig",
    "McpServerDefinition",
    "McpServerRegistry",
    "NeeAgentHierarchy",
    "NeeLifecycleHooks",
    "NeeSecurityPolicy",
    "PROFILE_PRESETS",
    "READ_ONLY_SAFE_TOOLS",
    "RetryConfig",
    "STRICTLY_DENIED_TOOLS",
    "SpecialistSpec",
    "SubagentConfig",
    "create_governed_agent",
    "resolve_connection_strategy",
]
