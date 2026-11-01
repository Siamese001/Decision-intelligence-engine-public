"""Domain models and enums for unified agent architecture.

Defines schemas and enums for:
- Five Core Architectural Pillars: Skills, Agents, Sub-Agents, Hooks, Rules
- Four Shared Foundations
- 7-Step Lifecycle steps
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class SkillStatePosture(str, Enum):
    STATELESS = "STATELESS"
    STATEFUL = "STATEFUL"


class HookTiming(str, Enum):
    PRE = "PRE"
    POST = "POST"
    AROUND = "AROUND"


class CrossCuttingConcern(str, Enum):
    LOGGING = "logging"
    METRICS = "metrics"
    CACHING = "caching"
    NOTIFICATIONS = "notifications"
    GUARDRAILS = "guardrails"
    MODERATION = "moderation"


class RuleType(str, Enum):
    GOVERNANCE_GUARDRAIL = "governance_guardrail"
    PRE_CONDITION = "pre_condition"
    POST_CONDITION = "post_condition"
    ROUTING_DECISION = "routing_decision"
    COMPLIANCE_SAFETY = "compliance_safety"


class SharedFoundationType(str, Enum):
    MEMORY = "memory"
    KNOWLEDGE_BASES = "knowledge_bases"
    MODELS_AND_TOOLS = "models_and_tools"
    OBSERVABILITY = "observability"


class LifecycleStep(str, Enum):
    GOAL_AND_RULE_VERIFICATION = "1_goal_and_rule_verification"
    PLANNING_AND_SKILL_SELECTION = "2_planning_and_skill_selection"
    SUB_AGENT_DELEGATION = "3_sub_agent_delegation"
    SKILL_EXECUTION = "4_skill_execution"
    HOOK_TRIGGERS = "5_hook_triggers"
    CONTINUOUS_RULE_VALIDATION = "6_continuous_rule_validation"
    UPSTREAM_SYNTHESIS = "7_upstream_synthesis"


@dataclass(frozen=True)
class SkillDefinition:
    """Encapsulates 'What work gets done'."""
    id: str
    name: str
    description: str
    state_posture: SkillStatePosture = SkillStatePosture.STATELESS
    model_agnostic: bool = True
    tool_agnostic: bool = True
    discoverable: bool = True
    composable: bool = True
    inputs_schema: Mapping[str, Any] = field(default_factory=dict)
    outputs_schema: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDefinition:
    """Encapsulates 'Who decides & orchestrates'."""
    id: str
    role_name: str
    description: str
    goal_driven: bool = True
    owns_strategy_and_control: bool = True
    maintains_context_and_state: bool = True
    can_delegate: bool = True
    allowed_rules: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubAgentDefinition:
    """Encapsulates 'Specialized executors'."""
    id: str
    parent_agent_id: str
    domain_focus: str
    lightweight_autonomy: bool = True
    can_have_sub_agents: bool = True
    returns_results_upstream: bool = True
    assigned_skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HookDefinition:
    """Encapsulates 'When to extend'."""
    id: str
    event: str
    timing: HookTiming
    cross_cutting_concern: CrossCuttingConcern
    non_intrusive: bool = True
    description: str = ""


@dataclass(frozen=True)
class RuleDefinition:
    """Encapsulates 'What must be true'."""
    id: str
    rule_type: RuleType
    title: str
    description: str
    enforce_pre_condition: bool = True
    enforce_post_condition: bool = True


@dataclass(frozen=True)
class SharedFoundations:
    """Encapsulates common foundations across all pillars."""
    has_memory: bool = True
    has_knowledge_bases: bool = True
    has_models_and_tools: bool = True
    has_observability: bool = True


@dataclass(frozen=True)
class AgenticExecutionTrace:
    """Runtime execution telemetry span."""
    trace_id: str
    agent_id: str
    step: LifecycleStep
    status: str
    payload: Mapping[str, Any] = field(default_factory=dict)
