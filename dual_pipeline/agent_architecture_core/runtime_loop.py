"""Bounded Agent Runtime Loop & Stagnation-Protected Execution Engine.

Implements the canonical production loop:
    Goal -> Observe -> Decide -> Act/Delegate -> Observe result -> Evaluate -> Continue, Escalate, or Stop

Enforces:
- Hard step, tool-call, token, and wall-clock timeout budgets.
- Deterministic stagnation detection across consecutive steps (SHA-256 state/score delta).
- SSOT ambiguity margin evaluation (autonomous progression when delta > 0.20 and c_top >= 0.65).
- A2ATask lifecycle state progression and ClaimCheckPointer verification.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.a2a.protocol import A2ATask, A2ATaskStatus, ClaimCheckPointer, DelegationBudget
from dual_pipeline.agent_architecture_core.contracts import validate_delegation_contract, validate_subagent_return
from dual_pipeline.agent_architecture_core.lifecycle import ExecutionLifecycleTracker
from dual_pipeline.agent_architecture_core.models import AgenticExecutionTrace, LifecycleStep
from dual_pipeline.agent_architecture_core.outcome_streaming import stream_loop_terminal_outcome
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_min_autonomous_confidence_floor,
)


class LoopTerminalOutcome(str, Enum):
    """Terminal outcomes for a bounded agent runtime loop."""

    COMPLETED = "COMPLETED"
    ESCALATED_HITL = "ESCALATED_HITL"
    STAGNATED = "STAGNATED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RuntimeLoopConfig:
    """Configuration contract bounding an agentic execution loop."""

    agent_id: str
    goal: str
    max_steps: int = 3
    max_tool_calls: int = 20
    max_tokens: int = 16000
    timeout_seconds: float = 60.0
    delegation_depth: int = 2
    ambiguity_margin_threshold: float = field(default_factory=get_hitl_ambiguity_margin_threshold)
    min_autonomous_confidence: float = field(default_factory=get_hitl_min_autonomous_confidence_floor)
    allowed_tools: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.max_steps <= 0 or self.max_tool_calls <= 0 or self.timeout_seconds <= 0:
            raise ValueError("max_steps, max_tool_calls, and timeout_seconds must be positive.")
        if self.delegation_depth < 0 or self.delegation_depth > 3:
            raise ValueError("delegation_depth must be within [0, 3].")


@dataclass(frozen=True)
class AutonomyMarginDecision:
    """Result of evaluating candidate action confidence against SSOT margin rules."""

    top_option: str
    top_confidence: float
    second_confidence: float
    margin_delta: float
    outcome_type: str  # "AUTONOMOUS" | "ESCALATE_HITL"
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LoopStepRecord:
    """Telemetry record for a single Observe -> Decide -> Act -> Evaluate cycle."""

    step_index: int
    observation_digest: str
    selected_action: str
    tool_calls_used: int
    tokens_used: int
    quality_score: float
    state_signature: str
    margin_decision: Optional[Dict[str, Any]] = None
    a2a_task_id: Optional[str] = None
    status: str = "PASS"


@dataclass
class RuntimeLoopResult:
    """Final verified result of an AgentRuntimeLoop execution."""

    trace_id: str
    agent_id: str
    goal: str
    terminal_outcome: LoopTerminalOutcome
    steps_executed: int
    total_tool_calls: int
    total_tokens: int
    elapsed_seconds: float
    final_output: Dict[str, Any]
    step_records: List[LoopStepRecord]
    traces: List[AgenticExecutionTrace]
    a2a_tasks: List[Dict[str, Any]]
    lifecycle_valid: bool
    termination_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id, "agent_id": self.agent_id, "goal": self.goal,
            "terminal_outcome": self.terminal_outcome.value, "steps_executed": self.steps_executed,
            "total_tool_calls": self.total_tool_calls, "total_tokens": self.total_tokens,
            "elapsed_seconds": self.elapsed_seconds, "final_output": dict(self.final_output),
            "step_records": [asdict(s) for s in self.step_records], "traces": [asdict(t) for t in self.traces],
            "a2a_tasks": list(self.a2a_tasks), "lifecycle_valid": self.lifecycle_valid,
            "termination_reason": self.termination_reason,
        }


def evaluate_autonomy_margin(
    candidates: Sequence[Tuple[str, float]],
    *,
    margin_threshold: Optional[float] = None,
    min_confidence: Optional[float] = None,
    is_tier0_irreversible: bool = False,
) -> AutonomyMarginDecision:
    """Evaluates whether the top candidate can proceed autonomously under SSOT margin rules."""
    if not candidates:
        return AutonomyMarginDecision("none", 0.0, 0.0, 0.0, "ESCALATE_HITL", "No candidate actions available.")
    if margin_threshold is None:
        margin_threshold = get_hitl_ambiguity_margin_threshold()
    if min_confidence is None:
        min_confidence = get_hitl_min_autonomous_confidence_floor()

    ranked = sorted(candidates, key=lambda x: (-float(x[1]), str(x[0])))
    top_opt, top_conf = str(ranked[0][0]), round(float(ranked[0][1]), 4)
    sec_conf = round(float(ranked[1][1]), 4) if len(ranked) > 1 else 0.0
    delta = round(top_conf - sec_conf, 4)

    if is_tier0_irreversible:
        return AutonomyMarginDecision(
            top_opt, top_conf, sec_conf, delta, "ESCALATE_HITL",
            "Tier-0 irreversible operation always requires explicit operator authorization.",
        )
    if delta > margin_threshold and top_conf >= min_confidence:
        return AutonomyMarginDecision(
            top_opt, top_conf, sec_conf, delta, "AUTONOMOUS",
            f"Margin {delta:.2f} > {margin_threshold:.2f} and confidence {top_conf:.2f} >= {min_confidence:.2f}.",
        )
    return AutonomyMarginDecision(
        top_opt, top_conf, sec_conf, delta, "ESCALATE_HITL",
        f"Ambiguity margin {delta:.2f} <= {margin_threshold:.2f} or confidence {top_conf:.2f} < {min_confidence:.2f}.",
    )


def verify_claim_check_pointers(claim_checks: Sequence[ClaimCheckPointer]) -> Tuple[bool, List[str]]:
    """Verifies that all ClaimCheckPointers possess non-empty source, evidence, and valid SHA-256 digests."""
    errors: List[str] = []
    for idx, ptr in enumerate(claim_checks):
        if not isinstance(ptr, ClaimCheckPointer):
            errors.append(f"claim_checks[{idx}]: Expected ClaimCheckPointer instance.")
            continue
        if not ptr.source_id.strip() or not ptr.evidence_id.strip():
            errors.append(f"claim_checks[{idx}]: Missing source_id or evidence_id.")
        digest_clean = ptr.content_digest.strip().lower()
        if len(digest_clean) != 64 or any(c not in "0123456789abcdef" for c in digest_clean):
            errors.append(f"claim_checks[{idx}]: Invalid SHA-256 content_digest '{ptr.content_digest}'.")
    return len(errors) == 0, errors


def _compute_state_signature(payload: Mapping[str, Any]) -> str:
    stable = {k: v for k, v in payload.items() if k not in ("timestamp", "elapsed_ms", "step_index", "trace_id")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _finalize_a2a_task(a2a_task: A2ATask, ret_payload: Dict[str, Any]) -> None:
    a2a_task.output_payload = ret_payload
    sub_status = str(ret_payload.get("status", "COMPLETE"))
    if sub_status == "ESCALATED":
        a2a_task.transition_to(A2ATaskStatus.HUMAN_APPROVAL_REQUIRED)
    elif sub_status == "FAILED":
        a2a_task.transition_to(A2ATaskStatus.FAILED, error="Subagent reported FAILED.")
    else:
        a2a_task.transition_to(A2ATaskStatus.COMPLETED)


class AgentRuntimeLoop:
    """Bounded Observe -> Decide -> Act -> Evaluate execution loop with stagnation protection."""

    def __init__(
        self, config: RuntimeLoopConfig, *, integrator: Optional[Any] = None,
        stream_outcomes: bool = False, _suppress_loop_success: bool = False,
    ) -> None:
        self.config, self.integrator, self.stream_outcomes = config, integrator, stream_outcomes
        self._suppress_loop_success = _suppress_loop_success
        self.trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        self.lifecycle = ExecutionLifecycleTracker()
        self.traces: List[AgenticExecutionTrace] = []
        self.step_records: List[LoopStepRecord] = []
        self.a2a_tasks: List[A2ATask] = []

    def _record_trace(self, step: LifecycleStep, status: str, payload: Mapping[str, Any]) -> None:
        self.lifecycle.record_step(step)
        self.traces.append(AgenticExecutionTrace(self.trace_id, self.config.agent_id, step, status, dict(payload)))

    def _validate_task_preconditions(
        self, allowed_tools: Sequence[str], claim_checks: Sequence[ClaimCheckPointer], contract_payload: Dict[str, Any]
    ) -> None:
        if self.config.allowed_tools:
            unauthorized = [t for t in allowed_tools if t not in self.config.allowed_tools]
            if unauthorized:
                raise PermissionError(f"UNAUTHORIZED_TOOL_SCOPE: Tools not permitted by supervisor: {unauthorized}")
        cc_ok, cc_errors = verify_claim_check_pointers(claim_checks)
        if not cc_ok:
            raise ValueError(f"INVALID_CLAIM_CHECK: {'; '.join(cc_errors)}")
        valid_contract, contract_errors = validate_delegation_contract(contract_payload)
        if not valid_contract:
            raise ValueError(f"INVALID_DELEGATION_CONTRACT: {'; '.join(contract_errors)}")

    def dispatch_subagent_task(
        self,
        *,
        recipient_agent_id: str,
        objective: str,
        scope_in: Sequence[str],
        scope_out: Sequence[str],
        allowed_tools: Sequence[str],
        deliverable: Any,
        definition_of_done: Any,
        claim_checks: Sequence[ClaimCheckPointer] = (),
        executor_fn: Optional[Callable[[A2ATask], Dict[str, Any]]] = None,
    ) -> Tuple[A2ATask, Dict[str, Any]]:
        """Validates delegation contract, claim checks, and executes a bounded A2ATask lifecycle."""
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        budget_dict = {"max_turns": self.config.max_steps, "max_tool_calls": self.config.max_tool_calls, "timeout_seconds": int(self.config.timeout_seconds)}
        contract_payload = {
            "task_id": task_id, "parent_task_id": self.trace_id, "objective": objective,
            "scope_in": list(scope_in), "scope_out": list(scope_out), "allowed_tools": list(allowed_tools),
            "deliverable": deliverable, "definition_of_done": definition_of_done, "budget": budget_dict,
            "escalation_rule": f"Escalate if margin <= {self.config.ambiguity_margin_threshold:.2f}.",
        }
        self._validate_task_preconditions(allowed_tools, claim_checks, contract_payload)
        a2a_task = A2ATask(
            task_id=task_id, parent_task_id=self.trace_id, sender_agent_id=self.config.agent_id,
            recipient_agent_id=recipient_agent_id, objective=objective, status=A2ATaskStatus.SUBMITTED,
            scope_in=list(scope_in), scope_out=list(scope_out), allowed_tools=list(allowed_tools),
            budget=DelegationBudget.from_dict(budget_dict), claim_checks=list(claim_checks), input_payload=contract_payload,
        )
        a2a_task.transition_to(A2ATaskStatus.ACCEPTED)
        a2a_task.transition_to(A2ATaskStatus.WORKING)
        ret_payload = executor_fn(a2a_task) if executor_fn is not None else {
            "task_id": task_id, "status": "COMPLETE", "deliverable": {"objective": objective, "resolved": True},
            "evidence_refs": [c.evidence_id for c in claim_checks] or ["ev-default"],
            "metrics": {"tool_calls_used": 1, "elapsed_seconds": 0.01, "tokens_used": 64},
        }
        valid_ret, ret_errors = validate_subagent_return(ret_payload)
        if not valid_ret:
            a2a_task.transition_to(A2ATaskStatus.FAILED, error="; ".join(ret_errors))
            self.a2a_tasks.append(a2a_task)
            raise ValueError(f"INVALID_SUBAGENT_RETURN: {'; '.join(ret_errors)}")
        _finalize_a2a_task(a2a_task, ret_payload)
        self.a2a_tasks.append(a2a_task)
        return a2a_task, ret_payload

    def _evaluate_step_margin(
        self, decision: Dict[str, Any], obs_digest: str, step_idx: int, last_score: float, state: Dict[str, Any]
    ) -> Tuple[Optional[AutonomyMarginDecision], bool]:
        candidates = decision.get("candidates", [])
        if not candidates:
            return None, False
        margin_dec = evaluate_autonomy_margin(
            candidates,
            margin_threshold=self.config.ambiguity_margin_threshold,
            min_confidence=self.config.min_autonomous_confidence,
            is_tier0_irreversible=bool(decision.get("is_tier0_irreversible", False)),
        )
        if margin_dec.outcome_type == "ESCALATE_HITL" and decision.get("halt_on_ambiguity", True):
            self.step_records.append(
                LoopStepRecord(
                    step_idx, obs_digest, margin_dec.top_option, 0, 0,
                    max(0.0, last_score), obs_digest, margin_dec.to_dict(), status="ESCALATED_HITL",
                )
            )
            state["escalated_decision"] = margin_dec.to_dict()
            return margin_dec, True
        return margin_dec, False

    def _check_stagnation(
        self, state_sig: str, seen_signatures: set[str], step_idx: int, score: float, last_score: float, eval_res: Mapping[str, Any]
    ) -> bool:
        if state_sig in seen_signatures:
            return True
        if step_idx > 1 and score <= last_score and not eval_res.get("allow_equal_score"):
            return True
        return False

    def _run_single_step(
        self,
        step_idx: int,
        state: Dict[str, Any],
        last_score: float,
        observe_fn: Callable[[Dict[str, Any], int], Dict[str, Any]],
        decide_fn: Callable[[Dict[str, Any], int], Dict[str, Any]],
        act_fn: Callable[[Dict[str, Any], Dict[str, Any], int], Dict[str, Any]],
        evaluate_fn: Callable[[Dict[str, Any], Dict[str, Any], int], Dict[str, Any]],
    ) -> Tuple[str, str, int, int, float, str, Dict[str, Any]]:
        obs = observe_fn(state, step_idx)
        obs_digest = _compute_state_signature(obs)
        self._record_trace(LifecycleStep.PLANNING_AND_SKILL_SELECTION, "PASS", {"step": step_idx})
        decision = decide_fn(obs, step_idx)
        margin_dec, halted = self._evaluate_step_margin(decision, obs_digest, step_idx, last_score, state)
        if halted and margin_dec is not None:
            return "ESCALATE", margin_dec.rationale, 0, 0, last_score, obs_digest, {}
        if step_idx == 1 and decision.get("delegated", False):
            self._record_trace(LifecycleStep.SUB_AGENT_DELEGATION, "PASS", {"action": decision.get("action", "execute")})
        self._record_trace(LifecycleStep.SKILL_EXECUTION, "PASS", {"step": step_idx})
        act_res = act_fn(state, decision, step_idx)
        step_tools, step_tokens = int(act_res.get("tool_calls_used", 1)), int(act_res.get("tokens_used", 0))
        eval_res = evaluate_fn(state, act_res, step_idx)
        self._record_trace(LifecycleStep.CONTINUOUS_RULE_VALIDATION, "PASS", {"done": bool(eval_res.get("done"))})
        state.update(act_res.get("state_updates", {}))
        state.update(eval_res.get("state_updates", {}))
        score, state_sig = float(eval_res.get("quality_score", 0.0)), _compute_state_signature(state)
        self.step_records.append(
            LoopStepRecord(
                step_idx, obs_digest, str(decision.get("action", "step")), step_tools, step_tokens,
                score, state_sig, margin_dec.to_dict() if margin_dec else None, act_res.get("a2a_task_id"),
                "COMPLETE" if eval_res.get("done") else "CONTINUE",
            )
        )
        return "STEP_DONE", str(eval_res.get("reason", "Definition of done satisfied.")), step_tools, step_tokens, score, state_sig, eval_res

    def execute(
        self,
        *,
        initial_state: Mapping[str, Any],
        observe_fn: Callable[[Dict[str, Any], int], Dict[str, Any]],
        decide_fn: Callable[[Dict[str, Any], int], Dict[str, Any]],
        act_fn: Callable[[Dict[str, Any], Dict[str, Any], int], Dict[str, Any]],
        evaluate_fn: Callable[[Dict[str, Any], Dict[str, Any], int], Dict[str, Any]],
    ) -> RuntimeLoopResult:
        """Executes the bounded Observe -> Decide -> Act -> Evaluate loop."""
        start_t = time.monotonic()
        state: Dict[str, Any] = dict(initial_state)
        total_tools, total_tokens, last_score = 0, 0, -1.0
        seen_signatures: set[str] = set()
        self._record_trace(LifecycleStep.GOAL_AND_RULE_VERIFICATION, "PASS", {"goal": self.config.goal})
        outcome, reason = LoopTerminalOutcome.FAILED, "Max steps reached without completion."

        for step_idx in range(1, self.config.max_steps + 1):
            if (time.monotonic() - start_t) > self.config.timeout_seconds:
                outcome, reason = LoopTerminalOutcome.BUDGET_EXHAUSTED, "Wall-clock timeout exhausted."
                break
            status, step_reason, s_tools, s_tokens, score, state_sig, eval_res = self._run_single_step(
                step_idx, state, last_score, observe_fn, decide_fn, act_fn, evaluate_fn
            )
            if status == "ESCALATE":
                outcome, reason = LoopTerminalOutcome.ESCALATED_HITL, step_reason
                break
            total_tools += s_tools
            total_tokens += s_tokens
            self._record_trace(LifecycleStep.HOOK_TRIGGERS, "PASS", {"total_tools": total_tools})
            if total_tools > self.config.max_tool_calls or total_tokens > self.config.max_tokens:
                self.step_records.pop()
                outcome, reason = LoopTerminalOutcome.BUDGET_EXHAUSTED, f"Budget exhausted at step {step_idx}."
                break
            if eval_res.get("done", False):
                outcome, reason = LoopTerminalOutcome.COMPLETED, step_reason
                break
            if self._check_stagnation(state_sig, seen_signatures, step_idx, score, last_score, eval_res):
                outcome, reason = LoopTerminalOutcome.STAGNATED, f"Stagnation detected at step {step_idx}."
                break
            seen_signatures.add(state_sig)
            last_score = score

        self._record_trace(LifecycleStep.UPSTREAM_SYNTHESIS, outcome.value, {"steps": len(self.step_records)})
        lifecycle_ok, _ = self.lifecycle.validate_lifecycle_progression()
        stream_loop_terminal_outcome(
            self.integrator, stream_outcomes=self.stream_outcomes, suppress_loop_success=self._suppress_loop_success,
            agent_id=self.config.agent_id, trace_id=self.trace_id, terminal_outcome=outcome.value,
            steps_executed=len(self.step_records), termination_reason=reason,
        )
        return RuntimeLoopResult(
            self.trace_id, self.config.agent_id, self.config.goal, outcome, len(self.step_records),
            total_tools, total_tokens, round(time.monotonic() - start_t, 4), state,
            self.step_records, self.traces, [t.to_dict() for t in self.a2a_tasks], lifecycle_ok, reason,
        )


__all__ = [
    "AgentRuntimeLoop",
    "AutonomyMarginDecision",
    "LoopStepRecord",
    "LoopTerminalOutcome",
    "RuntimeLoopConfig",
    "RuntimeLoopResult",
    "evaluate_autonomy_margin",
    "verify_claim_check_pointers",
]
