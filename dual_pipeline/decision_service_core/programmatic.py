"""Programmatic entrypoint for dialectical trade-off matrix evaluation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD


def evaluate_programmatic_decision_plan(
    decision_question: str,
    options: Sequence[Mapping[str, Any]],
    criteria: Sequence[Mapping[str, Any]],
    evidence_pack: Mapping[str, Any],
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
) -> dict[str, Any]:
    """Programmatic entrypoint for dialectical trade-off matrix evaluation (PDME)."""
    from dual_pipeline.ptc.decision_evaluator import (
        PTCDecisionCriterion,
        PTCDecisionMatrixEvaluator,
        PTCDecisionMatrixPlan,
        PTCDecisionOption,
    )

    opt_fields = {
        "option_id",
        "title",
        "description",
        "evidenced_claims",
        "risk_factors",
        "base_feasibility",
    }
    crit_fields = {"name", "weight", "description", "stakeholder_role"}
    opts = [
        PTCDecisionOption(**{k: v for k, v in dict(o).items() if k in opt_fields})
        for o in options
    ]
    crits = [
        PTCDecisionCriterion(**{k: v for k, v in dict(c).items() if k in crit_fields})
        for c in criteria
    ]
    plan = PTCDecisionMatrixPlan(
        decision_question=decision_question,
        options=opts,
        criteria=crits,
        evidence_pack=evidence_pack,
        margin_threshold=margin_threshold,
    )
    evaluator = PTCDecisionMatrixEvaluator()
    receipt = evaluator.evaluate(plan)
    return receipt.to_dict()


__all__ = ["evaluate_programmatic_decision_plan"]
