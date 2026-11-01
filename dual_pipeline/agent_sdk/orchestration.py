"""Multi-agent supervisor and specialist hierarchy for Decision Intelligence.

Implements the hierarchical decomposition across:
- Tier 0: Decision Supervisor (Goal decomposition, HITL ambiguity gating, synthesis)
- Tier 1 Core Specialists: Ingestion, Retrieval, Spark Analytics
- Tier 1 Runtime Agents: Multi-Hop Navigator, Counterfactual Devil's Advocate,
  Cross-Artifact Reconciler, Live Proactive Inflection, Temporal Drift Supersession,
  and Adaptive Retrieval Calibrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from google.antigravity import AgentBehavior, LocalAgentConfig
from google.antigravity.types import SubagentCapabilities, SubagentConfig

from dual_pipeline.agent_sdk.config import AgentConfigFactory, GovernedAgentProfile
from dual_pipeline.agent_sdk.tools import (
    compute_hybrid_scoring,
    evaluate_candidate_retrieval,
    execute_spark_sql,
    inspect_graph_snapshot,
    inspect_spark_batch_status,
    query_knowledge_graph,
    read_assertion_provenance,
    submit_spark_graph_batch,
    validate_rfc8259_canonical_hash,
    verify_contract_compliance,
)
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.unified_agent_architecture import (
    validate_delegation_contract,
    validate_subagent_return,
)


@dataclass(frozen=True)
class SpecialistSpec:
    """Specification for a delegated specialist subagent."""

    name: str
    description: str
    system_instructions: str
    tools: Sequence[Any]
    model: str = field(
        default_factory=lambda: resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH)
    )


class NeeAgentHierarchy:
    """Manages creation and binding of the Antigravity agent hierarchy."""

    INGESTION_AGENT = "ingestion_specialist"
    RETRIEVAL_AGENT = "retrieval_specialist"
    SPARK_AGENT = "spark_analytics_specialist"
    NAVIGATOR_AGENT = "multihop_query_navigator_agent"
    DEVIL_ADVOCATE_AGENT = "counterfactual_devil_advocate_agent"
    RECONCILIATION_AGENT = "cross_artifact_reconciliation_agent"
    INFLECTION_AGENT = "live_proactive_inflection_agent"
    TEMPORAL_DRIFT_AGENT = "temporal_drift_supersession_agent"
    CALIBRATOR_AGENT = "adaptive_retrieval_calibrator_agent"

    @classmethod
    def _core_specialist_specs(cls) -> Dict[str, SpecialistSpec]:
        return {
            cls.INGESTION_AGENT: SpecialistSpec(
                name=cls.INGESTION_AGENT,
                description="Specialist executing Stage U0/L1/L0/C0 intake, validation, and contract verification.",
                system_instructions=(
                    "You are a bounded Ingestion Specialist. Verify intake packages, "
                    "compute RFC 8259 canonical hashes, and enforce schema compliance. "
                    "Remain provider-free for L1 normalization and identity resolution."
                ),
                tools=[verify_contract_compliance, validate_rfc8259_canonical_hash],
            ),
            cls.RETRIEVAL_AGENT: SpecialistSpec(
                name=cls.RETRIEVAL_AGENT,
                description="Specialist executing hybrid search, BGE-M3 clearance, and 11 retrieval release gates.",
                system_instructions=(
                    "You are a bounded Retrieval Specialist. Perform hybrid BM25 + semantic "
                    "searches over the knowledge graph and verify candidates against the 11 release gates."
                ),
                tools=[
                    query_knowledge_graph, inspect_graph_snapshot, read_assertion_provenance,
                    evaluate_candidate_retrieval, compute_hybrid_scoring,
                ],
            ),
            cls.SPARK_AGENT: SpecialistSpec(
                name=cls.SPARK_AGENT,
                description="Specialist executing Dataproc Serverless batch analytics and memory-safe Spark SQL.",
                system_instructions=(
                    "You are a bounded Spark Analytics Specialist. Analyze graph topology, "
                    "submit batch entity resolution jobs, and execute memory-safe Spark SQL queries."
                ),
                tools=[execute_spark_sql, submit_spark_graph_batch, inspect_spark_batch_status],
            ),
        }

    @classmethod
    def _runtime_specialist_specs(cls) -> Dict[str, SpecialistSpec]:
        return {
            cls.NAVIGATOR_AGENT: SpecialistSpec(
                name=cls.NAVIGATOR_AGENT,
                description="Specialist executing iterative 2nd/3rd-hop graph frontier expansion to fill query coverage gaps.",
                system_instructions="You are the Multi-Hop Query Navigator Agent. Expand bridge predicates until max_steps=3.",
                tools=[query_knowledge_graph, inspect_graph_snapshot, read_assertion_provenance],
            ),
            cls.DEVIL_ADVOCATE_AGENT: SpecialistSpec(
                name=cls.DEVIL_ADVOCATE_AGENT,
                description="Specialist stress-testing candidate decisions by retrieving disconfirming risk and contradiction edges.",
                system_instructions="You are the Counterfactual Devil's Advocate Agent. Challenge top candidate options using HAS_RISK and CONTRADICTS.",
                tools=[query_knowledge_graph, read_assertion_provenance, compute_hybrid_scoring],
            ),
            cls.RECONCILIATION_AGENT: SpecialistSpec(
                name=cls.RECONCILIATION_AGENT,
                description="Specialist reconciling entity, owner, and quote-pointer alignment across Outputs 01, 02, and 03.",
                system_instructions="You are the Cross-Artifact Reconciliation Agent. Emit SurgicalRepairDirective payloads without mutating Output 01.",
                tools=[verify_contract_compliance, validate_rfc8259_canonical_hash],
            ),
            cls.INFLECTION_AGENT: SpecialistSpec(
                name=cls.INFLECTION_AGENT,
                description="Specialist monitoring live ConversationContextModel turns to pre-stage grounded copilot insights.",
                system_instructions="You are the Live Proactive Inflection Agent. Detect live contradictions and pre-stage speak-gate insights.",
                tools=[query_knowledge_graph, inspect_graph_snapshot],
            ),
            cls.TEMPORAL_DRIFT_AGENT: SpecialistSpec(
                name=cls.TEMPORAL_DRIFT_AGENT,
                description="Specialist detecting bi-temporal assertion supersession vs concurrent contradiction.",
                system_instructions="You are the Temporal Drift Supersession Agent. Compare chronological timestamps and emit propose-only packages.",
                tools=[inspect_graph_snapshot, read_assertion_provenance, verify_contract_compliance],
            ),
            cls.CALIBRATOR_AGENT: SpecialistSpec(
                name=cls.CALIBRATOR_AGENT,
                description="Specialist calibrating route multipliers and hybrid weights via sandboxed holdout replay.",
                system_instructions="You are the Adaptive Retrieval Calibrator Agent. Run 11-gate holdout simulations and emit propose-only proposals.",
                tools=[evaluate_candidate_retrieval, compute_hybrid_scoring, verify_contract_compliance],
            ),
        }

    @classmethod
    def get_specialist_specs(cls) -> Dict[str, SpecialistSpec]:
        """Return declared specifications for all Tier 1 specialist agents."""
        return {**cls._core_specialist_specs(), **cls._runtime_specialist_specs()}

    @classmethod
    def adapt_specialist_for_failures(cls, specialist_name: str, failure_count: int) -> SpecialistSpec:
        """Dynamically upgrades specialist model tier and instructions based on empirical retry failures."""
        specs = cls.get_specialist_specs()
        if specialist_name not in specs:
            raise KeyError(f"Unknown specialist '{specialist_name}'")
        orig = specs[specialist_name]
        if failure_count >= 2:
            upgraded_model = resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING)
            return SpecialistSpec(
                name=orig.name,
                description=orig.description,
                system_instructions=f"{orig.system_instructions} RECOVERY MODE ACTIVE: Multi-retry failure detected ({failure_count}). Apply exhaustive reasoning.",
                tools=orig.tools,
                model=upgraded_model,
            )
        return orig

    @classmethod
    def evaluate_specialist_completion(
        cls,
        specialist_name: str,
        return_payload: Mapping[str, Any],
        failure_count: int = 0,
    ) -> Tuple[bool, str, Optional[SpecialistSpec]]:
        """Evaluates specialist upstream return against contract schema and determines if re-delegation is required."""
        valid, errors = validate_subagent_return(dict(return_payload))
        if not valid:
            upgraded = cls.adapt_specialist_for_failures(specialist_name, failure_count + 1)
            return False, f"INVALID_RETURN: {'; '.join(errors)}", upgraded
        status = str(return_payload.get("status", "FAILED"))
        if status == "COMPLETE":
            return True, "COMPLETED", None
        if status == "ESCALATED":
            return False, "ESCALATED_TO_HITL", None
        upgraded = cls.adapt_specialist_for_failures(specialist_name, failure_count + 1)
        return False, f"RETRY_WITH_UPGRADED_SPECIALIST ({status})", upgraded

    @classmethod
    def build_subagent_configs(cls) -> List[SubagentConfig]:
        """Build Antigravity SubagentConfig objects for all leaf specialists."""
        specs = cls.get_specialist_specs()
        subagent_configs: List[SubagentConfig] = []

        for spec in specs.values():
            cap = SubagentCapabilities(
                agent_behavior=AgentBehavior.AUTONOMOUS,
                allowed_subagents=[],
            )
            subagent_configs.append(
                SubagentConfig(
                    name=spec.name,
                    description=spec.description,
                    system_instructions=spec.system_instructions,
                    tools=list(spec.tools),
                    capabilities=cap,
                    model=spec.model,
                )
            )

        return subagent_configs

    @classmethod
    def build_supervisor_config(
        cls,
        factory: Optional[AgentConfigFactory] = None,
        mcp_profile: Optional[str] = "graph",
    ) -> LocalAgentConfig:
        """Construct the Tier 0 Decision Supervisor configuration with all subagents bound."""
        profile = GovernedAgentProfile(
            name="decision_intelligence_supervisor",
            behavior=AgentBehavior.AUTONOMOUS,
            max_model_calls=50,
            max_tool_calls=100,
            token_compaction_threshold=80000,
            max_subagent_depth=2,
            enable_subagents=True,
        )
        active_factory = factory or AgentConfigFactory(default_profile=profile)
        subagents = cls.build_subagent_configs()

        supervisor_instructions = (
            "You are the Decision Intelligence Supervisor (Tier 0). "
            "Your mandate is to decompose high-level user goals, delegate subtasks to specialized subagents, "
            "enforce HITL ambiguity gating when margin is within SSOT ambiguity threshold, "
            "and synthesize verified evidence-backed decisions."
        )

        return active_factory.create_local_agent_config(
            system_instructions=supervisor_instructions,
            subagents=subagents,
            mcp_profile=mcp_profile,
            model_name=resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_PRO),
        )

    @classmethod
    def create_delegation_contract(
        cls,
        specialist_name: str,
        task_id: str,
        parent_task_id: str,
        objective: str,
        scope_in: Sequence[str],
        scope_out: Sequence[str],
        deliverable: str,
        definition_of_done: str,
        budget: Dict[str, int],
        escalation_rule: str,
    ) -> tuple[Dict[str, Any], bool, List[str]]:
        """Formulate and validate a governed delegation contract before delegating to a specialist."""
        specs = cls.get_specialist_specs()
        if specialist_name not in specs:
            raise KeyError(f"Unknown specialist '{specialist_name}'. Allowed: {list(specs.keys())}")

        allowed_tools = [
            getattr(t, "__name__", str(t)) for t in specs[specialist_name].tools
        ]

        payload = {
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "objective": objective,
            "scope_in": list(scope_in),
            "scope_out": list(scope_out),
            "allowed_tools": allowed_tools,
            "deliverable": deliverable,
            "definition_of_done": definition_of_done,
            "budget": budget,
            "escalation_rule": escalation_rule,
        }

        is_valid, errors = validate_delegation_contract(payload)
        return payload, is_valid, errors
