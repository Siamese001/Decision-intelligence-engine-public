"""L1 Feedback Loop State Machine & Bounded Re-Planning Coordinator.

Orchestrates the feedback cycle between L1 Reason/Plan, L2 Execute, and L1 Review,
enforcing finite loop bounds (max_iterations <= 2) and preventing infinite execution cycles.
All transitions and re-planning decisions emit structured FeedbackAuditEvent records.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Mapping, Optional

from dual_pipeline.feedback_topology_core.contracts import (
    FeedbackAuditEvent,
    FeedbackTopologyClass,
    LoopDescriptor,
    LoopState,
    LoopTerminationReason,
)
from dual_pipeline.l1_review_contracts import (
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)

__all__ = [
    "FeedbackAuditEvent",
    "FeedbackTopologyClass",
    "L1FeedbackStateMachine",
    "LoopDescriptor",
    "LoopState",
    "LoopTerminationReason",
    "resolve_l1_metadata_adapter",
]


class L1FeedbackStateMachine:
    """Finite state machine governing the bounded Class A L1 -> L2 -> L1 feedback loop."""

    HARD_CEILING_MAX_ITERATIONS = 2

    def __init__(
        self,
        max_iterations: int = 2,
        loop_id: Optional[str] = None,
        recorder: Optional[Any] = None,
    ) -> None:
        self.max_iterations = min(max(1, max_iterations), self.HARD_CEILING_MAX_ITERATIONS)
        self.current_iteration = 0
        self.state = LoopState.IDLE
        self.loop_id = loop_id or f"loop-{uuid.uuid4().hex[:8]}"
        self.recorder = recorder
        self.history: list[dict[str, Any]] = []
        self.audit_events: list[FeedbackAuditEvent] = []
        self.replan_directives: list[dict[str, Any]] = []
        self.termination_reason: Optional[LoopTerminationReason] = None

    def start_cycle(self) -> int:
        """Transitions state to PLANNING for the current iteration."""
        if self.state not in (LoopState.IDLE, LoopState.REPLANNING):
            raise RuntimeError(f"Cannot start cycle from state '{self.state}'")
        self.state = LoopState.PLANNING
        self._record_transition("start_cycle")
        return self.current_iteration

    def mark_executed(self) -> None:
        """Transitions state from PLANNING to EXECUTING."""
        if self.state != LoopState.PLANNING:
            raise RuntimeError(f"Cannot mark executed from state '{self.state}'")
        self.state = LoopState.EXECUTING
        self._record_transition("mark_executed")

    def start_review(self) -> None:
        """Transitions state from EXECUTING to REVIEWING."""
        if self.state != LoopState.EXECUTING:
            raise RuntimeError(f"Cannot start review from state '{self.state}'")
        self.state = LoopState.REVIEWING
        self._record_transition("start_review")

    def process_review_verdict(
        self,
        contract: L1PostL2ReviewContract,
    ) -> tuple[LoopState, Optional[dict[str, Any]]]:
        """Evaluates L1 review verdict and computes the next state transition."""
        if self.state != LoopState.REVIEWING:
            raise RuntimeError(f"Cannot process verdict from state '{self.state}'")

        if contract.verdict == L1ReviewVerdict.SUFFICIENT:
            self.state = LoopState.SUFFICIENT
            self.termination_reason = LoopTerminationReason.SUFFICIENT_COMPLETION
            self._record_transition("verdict_sufficient", {"verdict": contract.verdict.value})
            return self.state, None

        if contract.verdict == L1ReviewVerdict.FATAL_BLOCK:
            self.state = LoopState.BLOCKED
            self.termination_reason = LoopTerminationReason.FATAL_BLOCK
            self._record_transition("verdict_fatal_block", {"verdict": contract.verdict.value})
            return self.state, {"reason": "FATAL_BLOCK", "checks": [c.to_dict() for c in contract.deterministic_checks]}

        # Handling INSUFFICIENT_REPLAN or INSUFFICIENT_RETRY
        if self.current_iteration >= self.max_iterations:
            self.state = LoopState.EXHAUSTED
            self.termination_reason = LoopTerminationReason.BUDGET_EXHAUSTED
            exhaustion_payload = {
                "reason": "L1_LOOP_BUDGET_EXHAUSTED",
                "iterations_completed": self.current_iteration,
                "max_iterations": self.max_iterations,
                "final_verdict": contract.verdict.value,
            }
            self._record_transition("loop_exhausted", exhaustion_payload)
            return self.state, exhaustion_payload

        # Re-plan approved: advance iteration and queue directive
        self.current_iteration += 1
        self.state = LoopState.REPLANNING
        directive = contract.replan_directive or {
            "directive_type": "REPLAN_SUBGOALS",
            "iteration": self.current_iteration,
            "focus": "Address missing aspects from prior attempt",
        }
        self.replan_directives.append(directive)
        self._record_transition("verdict_replan", {"iteration": self.current_iteration, "directive": directive})
        return self.state, directive

    def is_terminal(self) -> bool:
        """Returns True if the state machine has reached an exit state."""
        return self.state in (LoopState.SUFFICIENT, LoopState.EXHAUSTED, LoopState.BLOCKED)

    def can_proceed_to_exit_gates(self) -> bool:
        """Only SUFFICIENT observations are eligible to enter runtime X-gates."""
        return self.state == LoopState.SUFFICIENT

    def to_descriptor(self) -> LoopDescriptor:
        """Export comprehensive descriptor of current loop state."""
        return LoopDescriptor(
            loop_id=self.loop_id,
            topology_class=FeedbackTopologyClass.CLASS_A_BOUNDED_REPLAN,
            max_iterations=self.max_iterations,
            current_iteration=self.current_iteration,
            current_state=self.state,
            terminal=self.is_terminal(),
            termination_reason=self.termination_reason,
            transitions_count=len(self.history),
            replan_directives_count=len(self.replan_directives),
        )

    def get_audit_events(self) -> list[FeedbackAuditEvent]:
        """Return immutable copy of structured feedback audit events."""
        return list(self.audit_events)

    def _record_transition(self, event: str, payload: Optional[dict[str, Any]] = None) -> None:
        from_st = self.history[-1]["state"] if self.history else LoopState.IDLE.value
        to_st = self.state.value
        audit_event = FeedbackAuditEvent.create(
            loop_id=self.loop_id,
            event_type=event,
            from_state=from_st,
            to_state=to_st,
            iteration=self.current_iteration,
            payload=payload or {},
        )
        self.audit_events.append(audit_event)
        self.history.append({
            "event": event,
            "state": self.state.value,
            "iteration": self.current_iteration,
            "payload": payload or {},
            "event_id": audit_event.event_id,
        })
        if self.recorder and hasattr(self.recorder, "record_event"):
            try:
                self.recorder.record_event(
                    event_type=f"nee.feedback_loop.{event}",
                    payload=audit_event.to_dict(),
                )
            except Exception:
                pass

    @staticmethod
    def apply_replan_to_plan(
        plan: Mapping[str, Any],
        directive: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Pure function: adapts an L1 query plan based on a re-plan directive."""
        new_plan = deepcopy(dict(plan))
        extensions = dict(new_plan.get("extensions") or {})
        replan_ext = dict(extensions.get("com.partner_advisory.nee.l1-replan") or {})
        replan_history = list(replan_ext.get("replan_history", []))
        replan_history.append(dict(directive))
        replan_ext["replan_history"] = replan_history
        replan_ext["replan_iteration"] = len(replan_history)

        unanswered = directive.get("unanswered_subquestions", [])
        prior_focus = set(replan_ext.get("focus_subquestions", []))
        has_novel_unanswered = any(q not in prior_focus for q in unanswered)
        if unanswered:
            replan_ext["focus_subquestions"] = list(unanswered)

        suggested_expansions = directive.get("suggested_query_expansion", [])
        reqs = list(new_plan.get("evidence_requirements", []))
        prior_reqs = set(reqs)
        novel_expansions = [exp for exp in suggested_expansions if exp not in prior_reqs]
        if novel_expansions and "evidence_requirements" in new_plan:
            reqs.extend(novel_expansions)
            new_plan["evidence_requirements"] = reqs

        has_novel_action = bool(novel_expansions or has_novel_unanswered)
        replan_ext["has_novel_action"] = has_novel_action
        bound_manifest = None

        if has_novel_action:
            replan_ext["status"] = "REPLAN_APPLIED"
            # Bind a new manifest for the novel iteration
            bound_manifest = f"manifest_replan_iter_{len(replan_history)}"
            replan_ext["bound_manifest_id"] = bound_manifest
        else:
            replan_ext["status"] = "STOPPED_NO_NOVEL_ACTION"

        extensions["com.partner_advisory.nee.l1-replan"] = replan_ext
        new_plan["extensions"] = extensions

        # For uncontracted plain dicts in unit tests
        if "contract_id" not in new_plan:
            new_plan["replan_history"] = replan_history
            new_plan["replan_iteration"] = len(replan_history)
            new_plan["has_novel_action"] = has_novel_action
            new_plan["replan_status"] = replan_ext["status"]
            if bound_manifest is not None:
                new_plan["manifest_id"] = bound_manifest
            if unanswered:
                new_plan["focus_subquestions"] = list(unanswered)

        if "canonical_digest" in new_plan:
            from dual_pipeline.core import seal_artifact
            new_plan = seal_artifact(new_plan)

        return new_plan


def resolve_l1_metadata_adapter(provider: str = "google", model: Optional[str] = None):
    """Factory for Stage L1 header metadata adapter based on provider routing."""
    if provider.lower() in ("google", "gemini"):
        from dual_pipeline.l1_google_adapter import L1GoogleMetadataAdapter
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
        return L1GoogleMetadataAdapter(
            model=model or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH)
        )
    raise ValueError(f"Unsupported L1 metadata adapter provider: {provider}")
