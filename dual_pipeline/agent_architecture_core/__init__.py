"""Agent Architecture Core Package.

Modular core implementation of unified agent architecture pillars, contracts,
lifecycles, and static governance validation.
"""
from __future__ import annotations

from dual_pipeline.agent_architecture_core.auditor import (
    StaticAgenticGovernanceAuditor,
    validate_unified_architecture_governance,
)
from dual_pipeline.agent_architecture_core.contracts import (
    BUDGET_LIMITS,
    REQUIRED_DELEGATION_CONTRACT_FIELDS,
    REQUIRED_SUBAGENT_RETURN_FIELDS,
    REQUIRED_UNIFIED_ARCHITECTURE_PHRASES,
    VALID_SUBAGENT_STATUSES,
    validate_delegation_contract,
    validate_hook_definition,
    validate_subagent_return,
)
from dual_pipeline.agent_architecture_core.context_profile import (
    MAX_SUBAGENT_CONTEXT_TOKENS,
    SubagentContextProfile,
    estimate_token_count,
)
from dual_pipeline.agent_architecture_core.delegation_ledger import (
    DelegationCostLedger,
    DelegationCostRecord,
)
from dual_pipeline.agent_architecture_core.lifecycle import (
    ExecutionLifecycleTracker,
    UnifiedAgentLifecycleTracker,
)
from dual_pipeline.agent_architecture_core.models import (
    AgentDefinition,
    AgenticExecutionTrace,
    CrossCuttingConcern,
    HookDefinition,
    HookTiming,
    LifecycleStep,
    RuleDefinition,
    RuleType,
    SharedFoundations,
    SharedFoundationType,
    SkillDefinition,
    SkillStatePosture,
    SubAgentDefinition,
)

__all__ = [
    "AgentDefinition",
    "AgenticExecutionTrace",
    "BUDGET_LIMITS",
    "CrossCuttingConcern",
    "DelegationCostLedger",
    "DelegationCostRecord",
    "ExecutionLifecycleTracker",
    "HookDefinition",
    "HookTiming",
    "LifecycleStep",
    "MAX_SUBAGENT_CONTEXT_TOKENS",
    "REQUIRED_DELEGATION_CONTRACT_FIELDS",
    "REQUIRED_SUBAGENT_RETURN_FIELDS",
    "REQUIRED_UNIFIED_ARCHITECTURE_PHRASES",
    "RuleDefinition",
    "RuleType",
    "SharedFoundations",
    "SharedFoundationType",
    "SkillDefinition",
    "SkillStatePosture",
    "StaticAgenticGovernanceAuditor",
    "SubAgentDefinition",
    "SubagentContextProfile",
    "UnifiedAgentLifecycleTracker",
    "VALID_SUBAGENT_STATUSES",
    "estimate_token_count",
    "validate_delegation_contract",
    "validate_hook_definition",
    "validate_subagent_return",
    "validate_unified_architecture_governance",
]
