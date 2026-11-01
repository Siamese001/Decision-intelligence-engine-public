"""Layer 0 Runtime Outcome Streaming Helper for Wave 1-5 Autonomous Agents.

Streams terminal runtime loop outcomes, multi-hop retrieval misses, counterfactual
abstentions, and cross-artifact reconciliation repairs into LivePipelineIntegrator
using lazy decoupling to preserve strict L0 -> L6 unidirectional layer boundaries
and zero-overhead execution when outcome streaming is disabled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


def append_runtime_outcome_event(
    event: Mapping[str, Any],
    *,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Appends a normalized runtime outcome event to a JSONL log and returns the payload."""
    payload = dict(event)
    if log_path is not None:
        target = Path(log_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    return payload


def resolve_outcome_integrator(
    integrator: Optional[Any] = None,
    stream_outcomes: bool = False,
) -> Optional[Any]:
    """Resolves the target LivePipelineIntegrator or returns None when disabled."""
    if integrator is not None:
        return integrator
    if not stream_outcomes:
        return None
    try:
        from dual_pipeline.learning_adapters.live_pipeline_integrator import (  # noqa: lazy-decouple
            get_live_pipeline_integrator,  # noqa: lazy-decouple
        )
        return get_live_pipeline_integrator()
    except Exception:
        return None


def _record_non_completed_loop_outcome(
    eff: Any,
    agent_id: str,
    trace_id: str,
    terminal_outcome: str,
    eff_steps: int,
    eff_reason: str,
    max_iterations: Optional[int],
    accumulated_cost_usd: Optional[float],
) -> None:
    if hasattr(eff, "adapter") and hasattr(eff, "record_outcome"):
        evt = eff.adapter.build_retry_exhaustion_event(
            target_component=agent_id,
            total_attempts=max(1, eff_steps),
            reason=f"{terminal_outcome}: {eff_reason}",
            source_agent=agent_id,
            run_id=trace_id,
        )
        eff.record_outcome(evt)
        return
    det = {"iterations": eff_steps, "max_iterations": max_iterations, "cost_usd": accumulated_cost_usd}
    if terminal_outcome == "STAGNATED" and hasattr(eff, "record_grounding_rejection"):
        eff.record_grounding_rejection(source_agent=agent_id, reason=eff_reason, details=det, run_id=trace_id)
    elif hasattr(eff, "record_judge_retry_exhaustion"):
        eff.record_judge_retry_exhaustion(source_agent=agent_id, reason=eff_reason, details=det, run_id=trace_id)


def stream_loop_terminal_outcome(
    integrator: Optional[Any],
    *,
    stream_outcomes: bool = False,
    suppress_loop_success: bool = False,
    agent_id: str,
    trace_id: str = "trace-default",
    terminal_outcome: str,
    steps_executed: int = 1,
    termination_reason: str = "",
    iterations: Optional[int] = None,
    max_iterations: Optional[int] = None,
    accumulated_cost_usd: Optional[float] = None,
) -> None:
    """Streams AgentRuntimeLoop terminal outcomes to LivePipelineIntegrator."""
    eff_steps = int(iterations if iterations is not None else steps_executed)
    eff_reason = termination_reason or f"{terminal_outcome} after {eff_steps} step(s)"
    if suppress_loop_success and (terminal_outcome == "COMPLETED" or iterations is None):
        return
    eff = resolve_outcome_integrator(integrator, stream_outcomes)
    if eff is None:
        return
    try:
        if terminal_outcome in ("STAGNATED", "BUDGET_EXHAUSTED", "FAILED"):
            _record_non_completed_loop_outcome(
                eff, agent_id, trace_id, terminal_outcome, eff_steps, eff_reason, max_iterations, accumulated_cost_usd
            )
        elif terminal_outcome == "ESCALATED_HITL":
            eff.record_abstention(
                source_agent=agent_id, category="autonomy_margin", reason=eff_reason,
                stage="L4", details={"steps_executed": eff_steps}, run_id=trace_id,
            )
        elif terminal_outcome == "COMPLETED" and not suppress_loop_success:
            eff.record_success(
                source_agent=agent_id, category="runtime_loop", stage="L4",
                details={"steps_executed": eff_steps}, run_id=trace_id,
            )
    except Exception:
        return


def stream_navigator_outcome(
    integrator: Optional[Any],
    *,
    stream_outcomes: bool = False,
    query: str,
    unresolved_subquestions: Sequence[str],
    coverage_ratio: float,
    trace_id: str,
) -> None:
    """Streams MultiHopQueryNavigatorAgent coverage misses or successes."""
    eff = resolve_outcome_integrator(integrator, stream_outcomes)
    if eff is None:
        return
    try:
        if coverage_ratio < 1.0 or unresolved_subquestions:
            details = {
                "coverage_ratio": coverage_ratio,
                "depth_escalation": bool(coverage_ratio < 1.0),
                "unresolved_count": len(unresolved_subquestions),
            }
            try:
                eff.record_retrieval_miss(
                    query=query,
                    missing_requirements=list(unresolved_subquestions) or [query],
                    coverage_score=coverage_ratio,
                    run_id=trace_id,
                    source_agent="multihop_query_navigator_agent",
                    details=details,
                )
            except TypeError:
                eff.record_retrieval_miss(
                    query=query,
                    missing_requirements=list(unresolved_subquestions) or [query],
                    coverage_score=coverage_ratio,
                    run_id=trace_id,
                    source_agent="multihop_query_navigator_agent",
                )
        else:
            eff.record_success(
                source_agent="multihop_query_navigator_agent",
                category="coverage_gap",
                stage="L1",
                details={"coverage_ratio": coverage_ratio, "depth_escalation": False},
                run_id=trace_id,
            )
    except Exception:
        return


def stream_devil_advocate_outcome(
    integrator: Optional[Any],
    *,
    stream_outcomes: bool = False,
    option_id: str,
    post_margin: float = 0.0,
    requires_hitl_escalation: bool = False,
    disconfirming_ids: Sequence[str] = (),
    trace_id: str = "trace-default",
    original_confidence: Optional[float] = None,
    penalized_confidence: Optional[float] = None,
    Triggered_rules: Optional[Sequence[str]] = None,
) -> None:
    """Streams CounterfactualDevilAdvocateAgent stress-test outcomes."""
    eff = resolve_outcome_integrator(integrator, stream_outcomes)
    if eff is None:
        return
    try:
        orig_c = float(original_confidence) if original_confidence is not None else 1.0
        pen_c = float(penalized_confidence) if penalized_confidence is not None else max(0.0, orig_c + float(post_margin))
        survival_score = round(pen_c / max(0.01, orig_c), 4)
        details: Dict[str, Any] = {
            "option_id": option_id,
            "post_challenge_margin": post_margin,
            "disconfirming_ids": list(disconfirming_ids),
            "adversarial_survival_score": survival_score,
        }
        if Triggered_rules is not None:
            details["triggered_rules"] = list(Triggered_rules)
        should_escalate = requires_hitl_escalation or (
            original_confidence is not None and penalized_confidence is not None and (orig_c - pen_c) >= 0.15
        )
        if should_escalate:
            if hasattr(eff, "record_abstention"):
                eff.record_abstention(
                    source_agent="counterfactual_devil_advocate_agent",
                    category="counterfactual_stress",
                    reason=f"Post-challenge margin {post_margin:.4f} <= 0.20 or disconfirming={len(disconfirming_ids)}",
                    stage="L4",
                    details=details,
                    run_id=trace_id,
                )
            elif hasattr(eff, "record_grounding_rejection"):
                eff.record_grounding_rejection(
                    source_agent="counterfactual_devil_advocate_agent",
                    reason="Counterfactual stress penalty triggered escalation",
                    details=details,
                    run_id=trace_id,
                )
        elif hasattr(eff, "record_success"):
            eff.record_success(
                source_agent="counterfactual_devil_advocate_agent",
                category="counterfactual_stress",
                stage="L4",
                details=details,
                run_id=trace_id,
            )
    except Exception:
        return


def stream_reconciler_outcomes(
    integrator: Optional[Any],
    *,
    stream_outcomes: bool = False,
    drift_pairs: Sequence[Tuple[str, str, str]],
    repaired_count: int,
    output_01_immutable_verified: bool,
    trace_id: str,
    extra_metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    """Streams CrossArtifactReconciliationAgent owner/entity drift pairs and component repairs."""
    eff = resolve_outcome_integrator(integrator, stream_outcomes)
    if eff is None or not output_01_immutable_verified:
        return
    try:
        for from_id, to_id, drift_code in drift_pairs:
            if not from_id or not to_id or from_id == to_id:
                continue
            evt = eff.adapter.build_adjudication_error_event(
                conflicting_entities=[from_id, to_id],
                reason=drift_code,
                candidate_id=from_id,
                corrected_canonical=to_id,
                source_agent="cross_artifact_reconciliation_agent",
                run_id=trace_id,
            )
            evt.observed_outcome["from_id"] = from_id
            evt.observed_outcome["to_id"] = to_id
            evt.observed_outcome["drift_code"] = drift_code
            evt.observed_outcome["output_01_immutable_verified"] = True
            if extra_metadata:
                evt.observed_outcome.update(dict(extra_metadata))
            eff.record_outcome(evt)

        if repaired_count > 0:
            rep_evt = eff.adapter.build_component_repair_event(
                target_component="output_02_03_projection",
                retry_class="cross_artifact_reconcile",
                defect_message=f"Repaired {repaired_count} cross-artifact drift defects",
                was_successful=True,
                source_agent="cross_artifact_reconciliation_agent",
                run_id=trace_id,
                repair_action="reconcile_to_output_01",
            )
            eff.record_outcome(rep_evt)

        eff.record_success(
            source_agent="cross_artifact_reconciliation_agent",
            category="entity_resolution",
            stage="E0",
            details={"repaired_count": repaired_count},
            run_id=trace_id,
        )
    except Exception:
        return


__all__ = [
    "append_runtime_outcome_event",
    "resolve_outcome_integrator",
    "stream_devil_advocate_outcome",
    "stream_loop_terminal_outcome",
    "stream_navigator_outcome",
    "stream_reconciler_outcomes",
]
