"""
Agentic Trajectory and Step-Level Evaluator.

Evaluates multi-step agent trajectories against 2026+ Anthropic & OpenAI standards:
- Step-level credit assignment.
- Efficiency ratio (optimal steps / actual steps).
- Repetitive loop and oscillating cycle detection.
- Redundant invocation penalties.
- Error recovery adaptability.
- Non-compensating quality thresholds.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    StepEvaluationVerdict,
    TrajectoryScorecard,
    TrajectoryStep,
    TrajectoryTrace,
)
from .schema_validator import ToolSchemaValidator


def _check_call_patterns(
    call_sig: Tuple[str, str],
    seen_calls: List[Tuple[str, str]],
) -> Tuple[bool, bool, float, List[str]]:
    """Checks for consecutive duplicates, 3-in-a-row loops, and 2-step oscillations."""
    is_redundant = False
    is_loop_strike = False
    penalty = 0.0
    reasons: List[str] = []

    # Immediate duplicate
    if seen_calls and seen_calls[-1] == call_sig:
        is_redundant = True
        penalty += 0.30
        reasons.append("Consecutive identical tool call with same arguments")

    # 3-in-a-row loop
    if len(seen_calls) >= 2 and seen_calls[-1] == call_sig and seen_calls[-2] == call_sig:
        is_loop_strike = True
        penalty += 0.50
        reasons.append("3-in-a-row identical loop strike detected")

    # 2-step oscillation (A -> B -> A -> B)
    if len(seen_calls) >= 3 and seen_calls[-2] == call_sig and seen_calls[-3] == seen_calls[-1]:
        is_loop_strike = True
        penalty += 0.50
        reasons.append("Oscillating A-B-A-B loop cycle detected")

    return is_redundant, is_loop_strike, penalty, reasons


def _diagnose_step(
    step: TrajectoryStep,
    validator: ToolSchemaValidator,
    seen_calls: List[Tuple[str, str]],
) -> Tuple[StepEvaluationVerdict, bool, bool, int]:
    """Diagnoses schema compliance, redundancy, and loop patterns for a single step."""
    is_valid = True
    schema_errors: List[str] = []
    penalty = 0.0
    reasons: List[str] = []
    is_redundant = False
    is_loop_strike = False

    if step.action_type == "tool_call" and step.tool_name:
        valid_call, errs = validator.validate_call(step.tool_name, step.tool_arguments)
        if not valid_call:
            is_valid = False
            schema_errors = errs
            penalty += 0.25 * len(errs)
            reasons.append(f"Schema violation: {'; '.join(errs)}")

        call_sig = (step.tool_name, json.dumps(step.tool_arguments, sort_keys=True))
        is_redundant, is_loop_strike, pat_penalty, pat_reasons = _check_call_patterns(call_sig, seen_calls)
        penalty += pat_penalty
        reasons.extend(pat_reasons)
        seen_calls.append(call_sig)

    verdict = StepEvaluationVerdict(
        step_index=step.step_index,
        is_valid=is_valid,
        is_redundant=is_redundant,
        is_loop_strike=is_loop_strike,
        schema_errors=schema_errors,
        penalty_score=min(1.0, penalty),
        reasoning=" | ".join(reasons) if reasons else "Nominal execution",
    )
    return verdict, is_redundant, is_loop_strike, len(schema_errors)


def _diagnose_trajectory_steps(
    steps: List[TrajectoryStep],
    validator: ToolSchemaValidator,
) -> Tuple[List[StepEvaluationVerdict], int, int, int]:
    """Evaluates all steps for schema errors, redundancy, and loop strikes."""
    step_verdicts: List[StepEvaluationVerdict] = []
    seen_calls: List[Tuple[str, str]] = []
    redundant_count = 0
    loop_count = 0
    schema_errors_total = 0

    for step in steps:
        verdict, is_red, is_loop, err_cnt = _diagnose_step(step, validator, seen_calls)
        step_verdicts.append(verdict)
        if is_red:
            redundant_count += 1
        if is_loop:
            loop_count += 1
        schema_errors_total += err_cnt

    return step_verdicts, redundant_count, loop_count, schema_errors_total


def _analyze_step_recovery(
    step: TrajectoryStep,
    next_step: Optional[TrajectoryStep],
) -> Tuple[bool, bool]:
    """Determines whether next_step represents an adaptation attempt and/or a recovery."""
    if next_step is None:
        return False, False
    diff_tool = step.tool_name != next_step.tool_name
    diff_args = step.tool_arguments != next_step.tool_arguments
    adaptation = diff_tool or diff_args or next_step.status == "SUCCESS"
    recovery = next_step.status == "SUCCESS"
    return adaptation, recovery


def _analyze_error_recovery(steps: List[TrajectoryStep]) -> Tuple[int, int, int, int]:
    """Computes error count, adaptation attempts, recovered count, and unresolved errors."""
    error_count = 0
    adaptation_attempts = 0
    recovered_count = 0
    total_steps = len(steps)

    for idx, step in enumerate(steps):
        if step.status in ("ERROR", "TIMEOUT", "BLOCKED"):
            error_count += 1
            next_step = steps[idx + 1] if idx < total_steps - 1 else None
            adapted, recovered = _analyze_step_recovery(step, next_step)
            if adapted:
                adaptation_attempts += 1
            if recovered:
                recovered_count += 1

    unresolved = max(0, error_count - recovered_count)
    return error_count, adaptation_attempts, recovered_count, unresolved


def _verify_terminal_task_success(steps: List[TrajectoryStep], metadata: Dict[str, Any]) -> bool:
    """Verifies that the trajectory achieved authentic terminal task success."""
    if not steps:
        return False
    terminal_step = steps[-1]
    is_terminal_failure = terminal_step.status in ("ERROR", "TIMEOUT", "BLOCKED")
    explicit_task_success = metadata.get("task_succeeded")
    if explicit_task_success is not None:
        return bool(explicit_task_success) and not is_terminal_failure
    return not is_terminal_failure


def _compute_error_recovery_rate(error_count: int, recovered_count: int, terminal_task_success: bool) -> float:
    """Computes honest error recovery rate capped if terminal task failed."""
    if error_count == 0:
        return 1.0
    if not terminal_task_success:
        return min(0.40, recovered_count / error_count)
    return recovered_count / error_count


def _compute_composite_score(
    efficiency_ratio: float,
    schema_compliance: float,
    non_redundancy: float,
    recovery_rate: float,
    loop_count: int,
    terminal_task_success: bool,
) -> float:
    """Calculates weighted composite score with penalties."""
    composite = (
        (0.35 * efficiency_ratio)
        + (0.25 * schema_compliance)
        + (0.20 * non_redundancy)
        + (0.20 * recovery_rate)
    ) * 100.0

    if loop_count > 0:
        composite = max(0.0, composite - (loop_count * 25.0))
    if not terminal_task_success:
        composite = min(40.0, composite)
    return composite


def _evaluate_trajectory_gates(
    loop_count: int,
    max_allowed_loops: int,
    optimal_steps_available: bool,
    efficiency_ratio: float,
    min_efficiency_ratio: float,
    schema_compliance_rate: float,
    min_schema_compliance: float,
    terminal_task_success: bool,
    composite: float,
) -> Tuple[bool, List[str]]:
    """Evaluates non-compensating quality and efficiency gates."""
    gate_failures: List[str] = []
    if loop_count > max_allowed_loops:
        gate_failures.append(f"Exceeded max loops: {loop_count} > {max_allowed_loops}")
    if not optimal_steps_available:
        gate_failures.append("Unknown optimal path: cannot earn qualified success without optimal_step_count")
    elif efficiency_ratio < min_efficiency_ratio:
        gate_failures.append(f"Efficiency {efficiency_ratio:.2f} < {min_efficiency_ratio:.2f}")
    if schema_compliance_rate < min_schema_compliance:
        gate_failures.append(f"Schema compliance {schema_compliance_rate:.2f} < {min_schema_compliance:.2f}")
    if not terminal_task_success:
        gate_failures.append("Terminal task failure: last step or task ended in error/blocked state")
    if composite < 70.0:
        gate_failures.append(f"Composite score {composite:.1f} < 70.0")

    return len(gate_failures) == 0, gate_failures


class TrajectoryEvaluator:
    """Evaluates agentic execution trajectories against efficiency, schema, and resilience rubrics."""

    def __init__(
        self,
        schema_validator: Optional[ToolSchemaValidator] = None,
        max_allowed_loops: int = 0,
        min_efficiency_ratio: float = 0.50,
        min_schema_compliance: float = 0.85,
    ):
        self.validator = schema_validator or ToolSchemaValidator()
        self.max_allowed_loops = max_allowed_loops
        self.min_efficiency_ratio = min_efficiency_ratio
        self.min_schema_compliance = min_schema_compliance

    def evaluate_trace(self, trace: TrajectoryTrace) -> TrajectoryScorecard:
        """Run complete step-level and trajectory evaluation on a trace."""
        steps = trace.steps
        total_steps = len(steps)

        if total_steps == 0:
            return TrajectoryScorecard(
                trace_id=trace.trace_id,
                pipeline_id=trace.pipeline_id,
                total_steps=0,
                optimal_steps=trace.optimal_step_count or 0,
                efficiency_ratio=0.0,
                schema_compliance_rate=1.0,
                loop_count=0,
                redundant_step_count=0,
                error_recovery_rate=0.0,
                composite_score=0.0,
                passed=False,
                terminal_task_success=False,
                adaptation_attempts=0,
                successful_recoveries=0,
                optimal_steps_available=False,
                unresolved_error_count=0,
                details={"reason": "Empty trajectory trace"},
            )

        optimal_steps_available = bool(trace.optimal_step_count and trace.optimal_step_count > 0)
        optimal_steps = int(trace.optimal_step_count) if optimal_steps_available else 0

        # Step diagnostics
        step_verdicts, redundant_count, loop_count, schema_errors_total = _diagnose_trajectory_steps(
            steps, self.validator
        )

        # Recovery & adaptation analysis
        error_count, adaptation_attempts, recovered_count, unresolved_error_count = _analyze_error_recovery(steps)

        # Terminal success
        terminal_task_success = _verify_terminal_task_success(steps, trace.metadata)

        # Rates
        error_recovery_rate = _compute_error_recovery_rate(error_count, recovered_count, terminal_task_success)
        efficiency_ratio = min(1.0, optimal_steps / total_steps) if optimal_steps_available and total_steps > 0 else 0.0

        tool_call_steps = sum(1 for s in steps if s.action_type == "tool_call")
        schema_compliance_rate = (
            max(0.0, (tool_call_steps - schema_errors_total) / tool_call_steps)
            if tool_call_steps > 0
            else 1.0
        )
        non_redundancy_rate = max(0.0, (total_steps - redundant_count) / total_steps)

        # Composite score
        composite = _compute_composite_score(
            efficiency_ratio, schema_compliance_rate, non_redundancy_rate,
            error_recovery_rate, loop_count, terminal_task_success
        )

        # Gates
        passed, gate_failures = _evaluate_trajectory_gates(
            loop_count, self.max_allowed_loops, optimal_steps_available,
            efficiency_ratio, self.min_efficiency_ratio,
            schema_compliance_rate, self.min_schema_compliance,
            terminal_task_success, composite,
        )

        return TrajectoryScorecard(
            trace_id=trace.trace_id,
            pipeline_id=trace.pipeline_id,
            total_steps=total_steps,
            optimal_steps=optimal_steps,
            efficiency_ratio=round(efficiency_ratio, 4),
            schema_compliance_rate=round(schema_compliance_rate, 4),
            loop_count=loop_count,
            redundant_step_count=redundant_count,
            error_recovery_rate=round(error_recovery_rate, 4),
            composite_score=round(composite, 2),
            passed=passed,
            terminal_task_success=terminal_task_success,
            adaptation_attempts=adaptation_attempts,
            successful_recoveries=recovered_count,
            optimal_steps_available=optimal_steps_available,
            unresolved_error_count=unresolved_error_count,
            step_verdicts=step_verdicts,
            details={
                "gate_failures": gate_failures,
                "error_count": error_count,
                "adaptation_attempts": adaptation_attempts,
                "recovered_count": recovered_count,
                "unresolved_error_count": unresolved_error_count,
                "terminal_task_success": terminal_task_success,
            },
        )
