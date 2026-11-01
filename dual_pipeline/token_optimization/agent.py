"""
dual_pipeline/token_optimization/agent.py

TokenOptimizationAgent orchestrator implementing Unified Agent Architecture
(5 Pillars, 4 Foundations, 7-Step Lifecycle).
Calibrated for `gpt-5.6-luna` and governed pipelines.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.unified_agent_architecture import (
    AgentDefinition,
    ExecutionLifecycleTracker,
    HookDefinition,
    HookTiming,
    LifecycleStep,
    RuleDefinition,
    RuleType,
    SkillDefinition,
    SubAgentDefinition,
    validate_delegation_contract,
)

from .profiler import TokenProfiler
from .schemas import (  # noqa: lazy-decouple
    AstraModelPricing,
    OptimizationDirective,
    PromptComponents,
    ReasoningEffortTier,
    TokenBudget,
    evaluate_effort_hysteresis,
    get_model_pricing,
)

logger = logging.getLogger("dual_pipeline.token_optimization.agent")


class TokenOptimizationAgent:
    """
    Goal-driven orchestrator managing token optimization, prompt cache alignment,
    and cost governance across all pipelines.
    """

    def __init__(
        self,
        default_model: Optional[str] = None,
        default_reasoning_effort: Optional[str] = None,
        budget: Optional[TokenBudget] = None,
        pricing: Optional[AstraModelPricing] = None,
        integrator: Optional[Any] = None,
        retry_history: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_REASONING)
        self.default_model = default_model or def_m
        self.default_reasoning_effort = default_reasoning_effort or def_e or "high"
        self.budget = budget or TokenBudget()
        self.pricing = pricing or get_model_pricing(self.default_model)
        self.profiler = TokenProfiler(default_model=self.default_model)
        self.retry_history = list(retry_history) if retry_history is not None else []
        self._explicit_integrator = integrator is not None
        self._active_effort_overrides: Dict[str, ReasoningEffortTier] = {}
        self._clean_run_streaks: Dict[str, int] = {}
        self._superseded_exhaustion_counts: Dict[str, int] = {}
        if integrator is not None:
            self.integrator = integrator
        else:
            try:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import get_live_pipeline_integrator  # noqa: lazy-decouple
                self.integrator = get_live_pipeline_integrator()
            except ImportError:
                self.integrator = None
        self.agent_definition = self._build_agent_definition()
        self.subagents = self._build_subagents()
        self.skills = self._build_skills()
        self.rules = self._build_rules()
        self.hooks = self._build_hooks()

    def _build_agent_definition(self) -> AgentDefinition:
        return AgentDefinition(
            id="agent:token-optimization:1.0.0",
            role_name="TokenOptimizationAgent",
            description="Goal-driven orchestrator governing token optimization, prefix caching, and Astra model budgets.",
            goal_driven=True,
            owns_strategy_and_control=True,
            maintains_context_and_state=True,
            can_delegate=True,
            allowed_rules=[
                "rule:fact-preservation-invariant",
                "rule:provider-free-boundary",
                "rule:career-leverage-firewall",
                "rule:fail-closed-budget",
                "rule:ssot-credential-masking",
            ],
        )

    def _build_subagents(self) -> Dict[str, SubAgentDefinition]:
        root_id = "agent:token-optimization:1.0.0"
        return {
            "pipeline-audit-subagent": SubAgentDefinition("subagent:pipeline-audit:1.0.0", root_id, "Pipeline token profiling, crude slice detection, and unbatched call auditing.", assigned_skills=["skill:token-profiler:1.0.0"]),
            "prompt-compression-subagent": SubAgentDefinition("subagent:prompt-compression:1.0.0", root_id, "Whitespace compaction, compact JSON serialization, and salient window extraction.", assigned_skills=["skill:sliding-window-compressor:1.0.0", "skill:token-profiler:1.0.0"]),
            "reasoning-calibrator-subagent": SubAgentDefinition("subagent:reasoning-calibrator:1.0.0", root_id, "Dynamic calibration of reasoning effort (minimal/low/medium/high/xhigh) based on task complexity.", assigned_skills=["skill:heuristic-prefilter:1.0.0"]),
            "cache-optimizer-subagent": SubAgentDefinition("subagent:cache-optimizer:1.0.0", root_id, "Prompt prefix stabilization (>=1,024 tokens) to achieve high OpenAI prompt cache hit rate.", assigned_skills=["skill:prompt-prefix-stabilizer:1.0.0"]),
            "cost-governance-subagent": SubAgentDefinition("subagent:cost-governance:1.0.0", root_id, "Astra model cost accounting, cache discount tracking, and fail-closed budget circuit breakers.", assigned_skills=["skill:astra-cost-model:1.0.0"]),
        }

    def _build_skills(self) -> Dict[str, SkillDefinition]:
        return {
            "skill:token-profiler:1.0.0": SkillDefinition("skill:token-profiler:1.0.0", "TokenProfilerSkill", "Exact BPE tokenizer measuring and framing token accounting for gpt-5.6-luna."),
            "skill:prompt-prefix-stabilizer:1.0.0": SkillDefinition("skill:prompt-prefix-stabilizer:1.0.0", "PromptPrefixStabilizerSkill", "Isolates static prompt rubrics into >=1,024 token immutable system prefixes for OpenAI prompt caching."),
            "skill:batch-aggregator:1.0.0": SkillDefinition("skill:batch-aggregator:1.0.0", "BatchAggregatorSkill", "Coalesces multiple fine-grained extractions into atomic single-request payloads."),
            "skill:heuristic-prefilter:1.0.0": SkillDefinition("skill:heuristic-prefilter:1.0.0", "HeuristicPreFilterSkill", "Fast regex and deterministic filtering to bypass unnecessary cloud LLM calls."),
            "skill:sliding-window-compressor:1.0.0": SkillDefinition("skill:sliding-window-compressor:1.0.0", "SlidingWindowCompressorSkill", "Context-aware semantic windowing replacing monolithic 300k character dumps."),
            "skill:astra-cost-model:1.0.0": SkillDefinition("skill:astra-cost-model:1.0.0", "AstraCostModelSkill", "Calculates USD cost for uncached input, cached input, and reasoning output."),
        }

    def _build_rules(self) -> Dict[str, RuleDefinition]:
        return {
            "rule:fact-preservation-invariant": RuleDefinition(
                id="rule:fact-preservation-invariant",
                rule_type=RuleType.GOVERNANCE_GUARDRAIL,
                title="Fact Preservation Invariant",
                description="Compression or windowing must never drop verified entities, decisions, or risk signals.",
            ),
            "rule:provider-free-boundary": RuleDefinition(
                id="rule:provider-free-boundary",
                rule_type=RuleType.COMPLIANCE_SAFETY,
                title="Provider-Free Boundary",
                description="Ingestion L1, normalization, identity resolution, and graph writes must remain 100% provider-free.",
            ),
            "rule:career-leverage-firewall": RuleDefinition(
                id="rule:career-leverage-firewall",
                rule_type=RuleType.COMPLIANCE_SAFETY,
                title="Career Leverage Firewall",
                description="No internal advisory or private career leverage tokens may leak into client-facing outputs.",
            ),
            "rule:fail-closed-budget": RuleDefinition(
                id="rule:fail-closed-budget",
                rule_type=RuleType.PRE_CONDITION,
                title="Fail-Closed Token Budget",
                description="Invocations exceeding max_total_tokens or max_cost_usd must fail closed.",
            ),
            "rule:ssot-credential-masking": RuleDefinition(
                id="rule:ssot-credential-masking",
                rule_type=RuleType.POST_CONDITION,
                title="SSOT Credential Masking",
                description="OpenAI API keys must remain masked in all logs and telemetry.",
            ),
        }

    def _build_hooks(self) -> List[HookDefinition]:
        return [
            HookDefinition(
                id="hook:pre-llm-budget-check",
                event="pre_llm_call",
                timing=HookTiming.PRE,
                cross_cutting_concern="guardrails",
                description="Validates that input tokens are within budget limits before dispatch.",
            ),
            HookDefinition(
                id="hook:post-llm-usage-accounting",
                event="post_llm_call",
                timing=HookTiming.POST,
                cross_cutting_concern="metrics",
                description="Extracts token metrics and logs cache hit rate into telemetry.",
            ),
        ]

    def create_delegation_contract(
        self,
        subagent_id: str,
        objective: str,
        scope_in: Sequence[str],
        scope_out: Sequence[str],
        allowed_tools: Sequence[str],
        deliverable: str,
        definition_of_done: Sequence[str],
        budget: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Formulates and validates a contract-bound delegation payload to a Sub-Agent."""
        contract = {
            "task_id": f"task-{uuid.uuid4().hex[:8]}",
            "parent_task_id": "parent-token-optimization-root",
            "subagent_id": subagent_id,
            "objective": objective,
            "scope_in": list(scope_in),
            "scope_out": list(scope_out),
            "allowed_tools": list(allowed_tools),
            "deliverable": deliverable,
            "definition_of_done": list(definition_of_done),
            "budget": dict(budget),
            "escalation_rule": "Escalate to TokenOptimizationAgent on budget breach or rule violation.",
        }
        valid, errors = validate_delegation_contract(contract)
        if not valid:
            raise ValueError(f"Invalid delegation contract: {errors}")
        return contract

    def _count_matching_exhaustions(self, task_type: str) -> int:
        if not self.integrator:
            return 0
        try:
            from dual_pipeline.learning_adapters.agent_outcome_adapter import OutcomeType
            events = self.integrator.get_recorded_events(outcome_type=OutcomeType.RETRY_EXHAUSTION)
            return sum(
                1 for e in events
                if task_type.lower() in str(e.observed_outcome.get("target_component", "")).lower()
                or str(e.observed_outcome.get("target_component", "")).lower() in task_type.lower()
            )
        except Exception:
            return 0

    def _check_retry_exhaustion(self, task_type: str, retry_exhausted: bool) -> bool:
        if retry_exhausted:
            return True
        active_ex = self._count_matching_exhaustions(task_type)
        if active_ex > self._superseded_exhaustion_counts.get(task_type, 0):
            return True
        if self.retry_history:
            return any(
                str(h.get("target_component", "")).lower() in task_type.lower()
                and (h.get("was_successful") is False or h.get("exhausted") is True)
                for h in self.retry_history
            )
        return False

    def _base_task_effort(self, task_type: str, recent_defects: int = 0) -> ReasoningEffortTier:
        if recent_defects > 1:
            return ReasoningEffortTier.HIGH
        task_lower = task_type.lower()
        if any(w in task_lower for w in ("router", "gatekeeper", "audio_merge", "disambiguat")):
            return ReasoningEffortTier.LOW
        if any(w in task_lower for w in ("reconcil", "dispatcher", "perspective", "stage01", "forensic")):
            return ReasoningEffortTier.MEDIUM
        if any(w in task_lower for w in ("adversarial", "reconciliation_failure")):
            return ReasoningEffortTier.XHIGH
        return ReasoningEffortTier.HIGH

    def calibrate_reasoning_effort(
        self,
        task_type: str,
        recent_defects: Optional[int] = None,
        retry_exhausted: bool = False,
    ) -> ReasoningEffortTier:
        """Calibrates reasoning effort for Astra model based on task complexity and empirical defect/retry telemetry."""
        if not retry_exhausted and not recent_defects and task_type in self._active_effort_overrides:
            return self._active_effort_overrides[task_type]
        if self._check_retry_exhaustion(task_type, retry_exhausted):
            return ReasoningEffortTier.XHIGH
        return self._base_task_effort(task_type, recent_defects or 0)

    def calibrate_from_runtime_loop(
        self,
        task_type: str,
        loop_result: Mapping[str, Any],
    ) -> ReasoningEffortTier:
        """Upgrades reasoning effort on loop stagnation/exhaustion and steps down after 3 clean 1-step runs."""
        outcome = str(loop_result.get("terminal_outcome", "")).upper()
        base_t = self._base_task_effort(task_type, 0)
        esc_t = ReasoningEffortTier.XHIGH if outcome == "BUDGET_EXHAUSTED" else self._base_task_effort(task_type, 2)
        curr_t = self._active_effort_overrides.get(task_type)
        if curr_t is None and self._check_retry_exhaustion(task_type, False):
            curr_t = ReasoningEffortTier.XHIGH
        next_t, next_streak, rec_ex = evaluate_effort_hysteresis(
            curr_t, self._clean_run_streaks.get(task_type, 0), loop_result, base_t, esc_t
        )
        self._clean_run_streaks[task_type] = next_streak
        if rec_ex and self._explicit_integrator and self.integrator is not None:
            evt = self.integrator.adapter.build_retry_exhaustion_event(
                target_component=task_type, total_attempts=max(1, int(loop_result.get("steps_executed", 1))),
                reason=f"Loop {outcome}", source_agent="TokenOptimizationAgent",
            )
            self.integrator.record_outcome(evt)
        elif curr_t is not None and next_t != curr_t and next_streak == 0 and not rec_ex:
            self._superseded_exhaustion_counts[task_type] = self._count_matching_exhaustions(task_type)
            self.retry_history = [h for h in self.retry_history if str(h.get("target_component", "")).lower() not in task_type.lower()]
        if next_t == base_t and not rec_ex and int(loop_result.get("steps_executed", 0)) < 3:
            self._active_effort_overrides.pop(task_type, None)
        else:
            self._active_effort_overrides[task_type] = next_t
        return next_t

    def optimize_request(
        self,
        system_prompt: str,
        user_content: str,
        task_type: str = "general_synthesis",
        model: Optional[str] = None,
        max_completion_tokens: int = 64000,
    ) -> Tuple[OptimizationDirective, PromptComponents]:
        """
        Executes full 7-step lifecycle to optimize a prompt request for gpt-5.6-luna.
        """
        tracker = ExecutionLifecycleTracker()
        tracker.record_step(LifecycleStep.GOAL_AND_RULE_VERIFICATION)
        target_model = model or self.default_model
        tracker.record_step(LifecycleStep.PLANNING_AND_SKILL_SELECTION)
        tracker.record_step(LifecycleStep.SUB_AGENT_DELEGATION)
        tracker.record_step(LifecycleStep.SKILL_EXECUTION)
        components = self.profiler.disassemble_prompt(system_prompt, user_content, target_model)

        baseline_tokens = (
            self.profiler.count_tokens(system_prompt, target_model)
            + self.profiler.count_tokens(user_content, target_model)
        )
        optimized_input_tokens = (
            components.estimated_prefix_tokens
            + self.profiler.count_tokens(components.user_payload, target_model)
        )

        calibrated_effort = self.calibrate_reasoning_effort(task_type)

        # Step 5: Hook Triggers (pre-call budget verification)
        tracker.record_step(LifecycleStep.HOOK_TRIGGERS)
        if optimized_input_tokens > self.budget.max_input_tokens:
            if self.budget.enforce_fail_closed:
                raise ValueError(
                    f"PRE_LLM_BUDGET_EXCEEDED: Input tokens ({optimized_input_tokens}) "
                    f"exceed budget ({self.budget.max_input_tokens})."
                )

        # Step 6: Continuous Rule Validation
        tracker.record_step(LifecycleStep.CONTINUOUS_RULE_VALIDATION)

        # Step 7: Upstream Synthesis
        tracker.record_step(LifecycleStep.UPSTREAM_SYNTHESIS)
        valid, errors = tracker.validate_lifecycle_progression()
        if not valid:
            logger.warning(f"Lifecycle tracker validation warnings: {errors}")

        savings_ratio = max(0.0, 1.0 - (optimized_input_tokens / max(1, baseline_tokens)))

        directive = OptimizationDirective(
            recommended_reasoning_effort=calibrated_effort,
            static_system_prefix=components.static_system_prefix,
            dynamic_user_content=components.user_payload,
            target_max_completion_tokens=min(max_completion_tokens, self.budget.max_output_tokens),
            enable_compact_json=True,
            estimated_baseline_tokens=baseline_tokens,
            estimated_optimized_tokens=optimized_input_tokens,
            projected_savings_ratio=round(savings_ratio, 4),
            rationale=(
                f"Prefix tokens: {components.estimated_prefix_tokens} (cacheable={components.is_cacheable_prefix}). "
                f"Calibrated reasoning: {calibrated_effort.value}."
            ),
        )

        return directive, components

    def record_ptc_execution(
        self,
        task_id: str,
        baseline_bytes: int,
        spooled_summary_bytes: int,
        execution_time_ms: float,
    ) -> Dict[str, Any]:
        """Records token savings achieved via Programmatic Tool Calling in working memory."""
        estimated_baseline_tokens = max(1, baseline_bytes // 4)
        estimated_ptc_tokens = max(1, spooled_summary_bytes // 4)
        saved_tokens = max(0, estimated_baseline_tokens - estimated_ptc_tokens)
        savings_ratio = round(saved_tokens / estimated_baseline_tokens, 4) if estimated_baseline_tokens else 0.0

        return {
            "task_id": task_id,
            "baseline_bytes": baseline_bytes,
            "spooled_summary_bytes": spooled_summary_bytes,
            "estimated_saved_tokens": saved_tokens,
            "savings_ratio": savings_ratio,
            "execution_time_ms": execution_time_ms,
        }


TokenCostOptimizationAgent = TokenOptimizationAgent
