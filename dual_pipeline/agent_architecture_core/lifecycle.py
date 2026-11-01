"""Lifecycle tracking and sequencing validation for unified agent architecture.

Enforces:
- 7-step End-to-End Execution Flow sequencing.
- Rule verification and planning prerequisites before skill execution.
- Bounded re-planning feedback loops (max 2 feedback iterations, max 3 executions).
- Upstream synthesis as terminal progression step.
"""
from __future__ import annotations

from typing import Sequence

from dual_pipeline.agent_architecture_core.models import LifecycleStep


def _check_step1_and_step2_order(step_names: Sequence[str], step4_idx: int) -> list[str]:
    errors: list[str] = []
    try:
        step1_idx = step_names.index(LifecycleStep.GOAL_AND_RULE_VERIFICATION.value)
    except ValueError:
        errors.append("LIFECYCLE_MISSING_STEP_1: Step 1 (Goal & Rule Verification) was not performed.")
        step1_idx = -1

    if step4_idx != -1 and step1_idx != -1 and step1_idx > step4_idx:
        errors.append("LIFECYCLE_OUT_OF_ORDER: Step 1 (Rule Verification) must occur before Step 4 (Skill Execution).")

    try:
        step2_idx = step_names.index(LifecycleStep.PLANNING_AND_SKILL_SELECTION.value)
        if step4_idx != -1 and step2_idx > step4_idx:
            errors.append("LIFECYCLE_OUT_OF_ORDER: Step 2 (Planning) must occur before Step 4 (Skill Execution).")
    except ValueError:
        if step4_idx != -1:
            errors.append("LIFECYCLE_MISSING_STEP_2: Planning and skill selection required prior to execution.")
    return errors


def _check_delegation_order(step_names: Sequence[str], step4_idx: int) -> list[str]:
    errors: list[str] = []
    if LifecycleStep.SUB_AGENT_DELEGATION.value in step_names and step4_idx != -1:
        step3_idx = step_names.index(LifecycleStep.SUB_AGENT_DELEGATION.value)
        if step3_idx > step4_idx:
            errors.append("LIFECYCLE_OUT_OF_ORDER: Step 3 (Delegation) must occur before delegated Skill Execution.")
    return errors


def _check_feedback_loops(step_names: Sequence[str]) -> list[str]:
    errors: list[str] = []
    skill_indices = [i for i, s in enumerate(step_names) if s == LifecycleStep.SKILL_EXECUTION.value]
    if len(skill_indices) <= 1:
        return errors
    for first_exec, next_exec in zip(skill_indices[:-1], skill_indices[1:]):
        intermediate_steps = step_names[first_exec + 1:next_exec]
        if LifecycleStep.PLANNING_AND_SKILL_SELECTION.value not in intermediate_steps:
            errors.append(
                "LIFECYCLE_FEEDBACK_LOOP_VIOLATION: Re-execution of Step 4 requires intermediate Step 2 (Planning/Re-plan)."
            )
    if len(skill_indices) > 3:
        errors.append(
            f"LIFECYCLE_FEEDBACK_LOOP_EXHAUSTION: Re-execution count {len(skill_indices) - 1} exceeds max allowable feedback iterations (2)."
        )
    return errors


def _check_terminal_synthesis(last_step: LifecycleStep) -> list[str]:
    errors: list[str] = []
    if last_step != LifecycleStep.UPSTREAM_SYNTHESIS:
        errors.append(
            f"LIFECYCLE_NON_TERMINAL_SYNTHESIS: Final step should be Upstream Synthesis, found {last_step.value}."
        )
    return errors


class ExecutionLifecycleTracker:
    """Tracks and validates the 7-step End-to-End Execution Flow."""

    def __init__(self) -> None:
        self._history: list[LifecycleStep] = []

    def record_step(self, step: LifecycleStep | str) -> None:
        if isinstance(step, str):
            matched = None
            for member in LifecycleStep:
                if step.lower() in member.value.lower() or member.value.lower() in step.lower():
                    matched = member
                    break
            if matched is None:
                raise ValueError(f"Unknown lifecycle step: {step}")
            self._history.append(matched)
        else:
            self._history.append(step)

    @property
    def history(self) -> list[LifecycleStep]:
        return list(self._history)

    def validate_lifecycle_progression(self) -> tuple[bool, list[str]]:
        """Validates that execution adhered to the required governance order."""
        if not self._history:
            return False, ["LIFECYCLE_EMPTY: No steps recorded."]

        step_names = [s.value for s in self._history]
        try:
            step4_idx = step_names.index(LifecycleStep.SKILL_EXECUTION.value)
        except ValueError:
            step4_idx = -1

        errors: list[str] = []
        errors.extend(_check_step1_and_step2_order(step_names, step4_idx))
        errors.extend(_check_delegation_order(step_names, step4_idx))
        errors.extend(_check_feedback_loops(step_names))
        errors.extend(_check_terminal_synthesis(self._history[-1]))

        return len(errors) == 0, errors


UnifiedAgentLifecycleTracker = ExecutionLifecycleTracker
