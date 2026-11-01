"""Decision Intelligence Post-Debate L1 Reviewer.

Provides deterministic verification and semantic sufficiency review for
Pipeline D dialectical debate outputs (Red Team Risk & Strategic Feasibility)
prior to HITL arbitration.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.l1_review_contracts import (
    DeterministicCheckItem,
    DeterministicReasonCode,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)


def _check_option_coverage(
    options: Sequence[Mapping[str, Any]],
    risk_map: Mapping[str, Any],
    feasibility_map: Mapping[str, Any],
) -> DeterministicCheckItem:
    missing_risk: List[str] = []
    missing_feas: List[str] = []
    for opt in options:
        opt_key = opt.get("key") or opt.get("id") or opt.get("label") or str(opt)
        if opt_key not in risk_map:
            missing_risk.append(opt_key)
        if opt_key not in feasibility_map:
            missing_feas.append(opt_key)

    if missing_risk or missing_feas:
        details = f"Incomplete debate coverage: missing risk for {missing_risk}, missing feasibility for {missing_feas}"
        return DeterministicCheckItem(
            name="dialectical_option_coverage",
            passed=False,
            details=details,
            reason_code=DeterministicReasonCode.REQUIRED_FIELDS_MISSING.value,
        )
    return DeterministicCheckItem(
        name="dialectical_option_coverage",
        passed=True,
        details=f"All {len(options)} options evaluated across risk and feasibility",
    )


def _check_score_bounds(
    risk_map: Mapping[str, Any],
    feasibility_map: Mapping[str, Any],
) -> DeterministicCheckItem:
    invalid_scores: List[str] = []
    for k, v in risk_map.items():
        score = v.get("risk_score")
        if score is None or not (0.0 <= score <= 1.0):
            invalid_scores.append(f"risk[{k}]={score}")
    for k, v in feasibility_map.items():
        score = v.get("feasibility_score")
        if score is None or not (0.0 <= score <= 1.0):
            invalid_scores.append(f"feasibility[{k}]={score}")

    if invalid_scores:
        return DeterministicCheckItem(
            name="dialectical_score_bounds",
            passed=False,
            details=f"Scores out of [0.0, 1.0] bounds: {invalid_scores}",
            reason_code=DeterministicReasonCode.SCHEMA_INVALID.value,
        )
    return DeterministicCheckItem(
        name="dialectical_score_bounds",
        passed=True,
        details="All dialectical scores conform to [0.0, 1.0] normalized bounds",
    )


def _evaluate_semantic_differentiation(
    options: Sequence[Mapping[str, Any]],
    risk_map: Mapping[str, Any],
    feasibility_map: Mapping[str, Any],
) -> Tuple[bool, Dict[str, Any], Optional[Dict[str, Any]]]:
    if len(options) <= 1:
        return True, {"rationale": "Single option frame; differentiation check skipped."}, None

    risk_scores = [risk_map[opt.get("key") or opt.get("id") or str(opt)].get("risk_score", 0.5) for opt in options if (opt.get("key") or opt.get("id") or str(opt)) in risk_map]
    feas_scores = [feasibility_map[opt.get("key") or opt.get("id") or str(opt)].get("feasibility_score", 0.5) for opt in options if (opt.get("key") or opt.get("id") or str(opt)) in feasibility_map]

    # If all options have identical scores across both risk and feasibility, dialectical tension is degenerate
    zero_var_risk = len(set(risk_scores)) <= 1 and len(risk_scores) > 1
    zero_var_feas = len(set(feas_scores)) <= 1 and len(feas_scores) > 1

    if zero_var_risk and zero_var_feas:
        replan = {
            "directive_type": "DEEPEN_DIALECTICAL_TENSION",
            "message": "Options received identical risk and feasibility scores; request deeper comparative trade-off analysis.",
        }
        return False, {"rationale": "Degenerate zero-variance scores across all options."}, replan

    return True, {"rationale": "Options exhibit meaningful dialectical score differentiation."}, None


class DecisionPostDebateReviewer:
    """L1 cognitive evaluator for Pipeline D dialectical debate."""

    def review_debate(
        self,
        *,
        decision_id: str,
        options: Sequence[Mapping[str, Any]],
        risk_assessments: Sequence[Mapping[str, Any]],
        feasibility_assessments: Sequence[Mapping[str, Any]],
        l1_plan: Any = None,
    ) -> L1PostL2ReviewContract:
        risk_map = {r.get("option_key", ""): r for r in risk_assessments if "option_key" in r}
        feas_map = {f.get("option_key", ""): f for f in feasibility_assessments if "option_key" in f}

        c1 = _check_option_coverage(options, risk_map, feas_map)
        c2 = _check_score_bounds(risk_map, feas_map)
        deterministic_checks = [c1, c2]
        deterministic_passed = all(c.passed for c in deterministic_checks)

        if not deterministic_passed:
            sem_passed = False
            sem_reasoning = {"rationale": "Deterministic debate checks failed."}
            replan: Optional[Dict[str, Any]] = {
                "directive_type": "COMPLETE_OPTION_EVALUATION",
                "missing": [c.details for c in deterministic_checks if not c.passed],
            }
            verdict = L1ReviewVerdict.INSUFFICIENT_REPLAN
        else:
            sem_passed, sem_reasoning, replan = _evaluate_semantic_differentiation(
                options, risk_map, feas_map
            )
            verdict = (
                L1ReviewVerdict.SUFFICIENT
                if sem_passed
                else L1ReviewVerdict.INSUFFICIENT_REPLAN
            )

        plan_id = getattr(l1_plan, "plan_id", f"plan_{decision_id}")
        return L1PostL2ReviewContract.create(
            run_id=f"run_{decision_id}",
            query_id=decision_id,
            plan_id=plan_id,
            sealed_artifact_id=f"sealed_debate_{decision_id}",
            verdict=verdict,
            deterministic_passed=deterministic_passed,
            deterministic_checks=deterministic_checks,
            semantic_passed=sem_passed,
            semantic_reasoning=sem_reasoning,
            replan_directive=replan,
        )
