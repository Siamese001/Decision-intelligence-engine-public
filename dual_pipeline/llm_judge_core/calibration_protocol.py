"""Calibration protocol and quality metrics evaluation for JARVIS micro-judges.

Provides:
1. PredeclaredTestCase: standard test case with human ground-truth labels.
2. CalibrationOutcome: classification into TP, TN, False Acceptance, False Rejection.
3. CalibrationReport: aggregates error rates, repair effectiveness, and token/call savings
   versus a frozen full-rerun baseline.
4. Paired counterfactual validation isolating content, transport, and criterion changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.release_thresholds import (
    OUTPUT1_PRODUCTION_PASS_THRESHOLD,
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
)


@dataclass
class PredeclaredTestCase:
    """Predeclared calibration fixture with authoritative human evaluation labels."""

    case_id: str
    meeting_name: str
    modality: CanonicalModality
    archetype: str  # "full_meeting", "sparse_speaker", "meeting_notes", "document"
    human_label: str  # "PASS" or "FAIL"
    f1_text: Optional[str] = None
    f2_text: Optional[str] = None
    f3_text: Optional[str] = None
    source_text: str = ""
    expected_defects: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CalibrationCaseOutcome:
    """Individual case evaluation result comparing judge decision to human ground truth."""

    case_id: str
    human_label: str
    judge_verdict: str
    composite_scores: Dict[str, float]
    outcome_category: str  # "TRUE_ACCEPTANCE", "TRUE_REJECTION", "FALSE_ACCEPTANCE", "FALSE_REJECTION"
    calls_made: int
    tokens_consumed: int
    remediation_attempt: int = 1
    repaired_successfully: Optional[bool] = None


@dataclass
class CalibrationReport:
    """Consolidated calibration evaluation report across a cohort of cases."""

    total_cases: int
    true_acceptance_count: int
    true_rejection_count: int
    false_acceptance_count: int
    false_rejection_count: int
    false_acceptance_rate: float
    false_rejection_rate: float
    repair_effectiveness: float
    total_calls: int
    total_tokens: int
    baseline_calls: int
    baseline_tokens: int
    calls_saved_percent: float
    tokens_saved_percent: float
    case_outcomes: List[CalibrationCaseOutcome] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "false_acceptance_count": self.false_acceptance_count,
            "false_rejection_count": self.false_rejection_count,
            "false_acceptance_rate": round(self.false_acceptance_rate, 4),
            "false_rejection_rate": round(self.false_rejection_rate, 4),
            "repair_effectiveness": round(self.repair_effectiveness, 4),
            "total_calls": self.total_calls,
            "baseline_calls": self.baseline_calls,
            "calls_saved_percent": round(self.calls_saved_percent, 2),
            "total_tokens": self.total_tokens,
            "baseline_tokens": self.baseline_tokens,
            "tokens_saved_percent": round(self.tokens_saved_percent, 2),
        }


def classify_calibration_outcome(human_label: str, judge_verdict: str) -> str:
    """Classifies candidate evaluation against authoritative human label."""
    norm_human = human_label.strip().upper()
    norm_judge = judge_verdict.strip().upper()

    if norm_human == "PASS":
        if norm_judge == "PASS":
            return "TRUE_ACCEPTANCE"
        return "FALSE_REJECTION"
    else:  # Human marked FAIL
        if norm_judge == "PASS":
            return "FALSE_ACCEPTANCE"
        return "TRUE_REJECTION"


def evaluate_calibration_cohort(
    outcomes: Sequence[CalibrationCaseOutcome],
    baseline_calls_per_case: int = 9,
    baseline_tokens_per_case: int = 4500,
) -> CalibrationReport:
    """
    Computes false acceptance, false rejection, repair effectiveness, and
    cost/token savings versus a frozen full-rerun baseline.
    """
    total = len(outcomes)
    if total == 0:
        return CalibrationReport(
            total_cases=0,
            true_acceptance_count=0,
            true_rejection_count=0,
            false_acceptance_count=0,
            false_rejection_count=0,
            false_acceptance_rate=0.0,
            false_rejection_rate=0.0,
            repair_effectiveness=0.0,
            total_calls=0,
            total_tokens=0,
            baseline_calls=0,
            baseline_tokens=0,
            calls_saved_percent=0.0,
            tokens_saved_percent=0.0,
            case_outcomes=[],
        )

    ta = sum(1 for o in outcomes if o.outcome_category == "TRUE_ACCEPTANCE")
    tr = sum(1 for o in outcomes if o.outcome_category == "TRUE_REJECTION")
    fa = sum(1 for o in outcomes if o.outcome_category == "FALSE_ACCEPTANCE")
    fr = sum(1 for o in outcomes if o.outcome_category == "FALSE_REJECTION")

    # Rates
    # False Acceptance Rate: FA / (all human FAIL cases)
    human_fails = tr + fa
    fa_rate = (fa / human_fails) if human_fails > 0 else 0.0

    # False Rejection Rate: FR / (all human PASS cases)
    human_passes = ta + fr
    fr_rate = (fr / human_passes) if human_passes > 0 else 0.0

    # Repair effectiveness: repaired_successfully / cases that needed repair
    repair_eligible = [o for o in outcomes if o.remediation_attempt > 1 or o.repaired_successfully is not None]
    if repair_eligible:
        repaired = sum(1 for o in repair_eligible if o.repaired_successfully is True)
        repair_eff = repaired / len(repair_eligible)
    else:
        repair_eff = 1.0

    total_calls = sum(o.calls_made for o in outcomes)
    total_tokens = sum(o.tokens_consumed for o in outcomes)

    baseline_calls = total * baseline_calls_per_case
    baseline_tokens = total * baseline_tokens_per_case

    calls_saved = max(0.0, (1.0 - (total_calls / baseline_calls)) * 100.0) if baseline_calls > 0 else 0.0
    tokens_saved = max(0.0, (1.0 - (total_tokens / baseline_tokens)) * 100.0) if baseline_tokens > 0 else 0.0

    return CalibrationReport(
        total_cases=total,
        true_acceptance_count=ta,
        true_rejection_count=tr,
        false_acceptance_count=fa,
        false_rejection_count=fr,
        false_acceptance_rate=fa_rate,
        false_rejection_rate=fr_rate,
        repair_effectiveness=repair_eff,
        total_calls=total_calls,
        total_tokens=total_tokens,
        baseline_calls=baseline_calls,
        baseline_tokens=baseline_tokens,
        calls_saved_percent=calls_saved,
        tokens_saved_percent=tokens_saved,
        case_outcomes=list(outcomes),
    )


def compute_expected_calibration_error(
    confidences: Sequence[float],
    actuals: Sequence[float],
    num_bins: int = 10,
) -> float:
    """Computes Expected Calibration Error (ECE) across confidence bins."""
    if not confidences or not actuals or len(confidences) != len(actuals):
        return 0.0
    n = len(confidences)
    bin_size = 1.0 / num_bins
    ece = 0.0
    for b in range(num_bins):
        bin_lower = b * bin_size
        bin_upper = (b + 1) * bin_size
        indices = [
            i for i, c in enumerate(confidences)
            if bin_lower <= c < bin_upper or (b == num_bins - 1 and bin_lower <= c <= bin_upper)
        ]
        if not indices:
            continue
        bin_acc = sum(actuals[i] for i in indices) / len(indices)
        bin_conf = sum(confidences[i] for i in indices) / len(indices)
        ece += (len(indices) / n) * abs(bin_acc - bin_conf)
    return round(ece, 4)


def get_calibrated_borderline_band(
    judge_name: str,
    output_num: int = 2,
    priors_path: Optional[Any] = None,
) -> Tuple[float, float]:
    """Derives dynamic Bayesian-calibrated borderline HITL escalation band for a judge.

    Preserves non-compensating hard-fail floor: floor is strictly bounded >= 85.0.
    """
    from dual_pipeline.hitl_governance_core.priors import load_learned_priors
    from dual_pipeline.release_thresholds import (
        OUTPUT1_PRODUCTION_PASS_THRESHOLD,
        OUTPUT2_PRODUCTION_PASS_THRESHOLD,
        OUTPUT3_PRODUCTION_PASS_THRESHOLD,
    )

    if output_num == 1:
        default_ceiling = float(OUTPUT1_PRODUCTION_PASS_THRESHOLD)
    elif output_num == 3:
        default_ceiling = float(OUTPUT3_PRODUCTION_PASS_THRESHOLD)
    else:
        default_ceiling = float(OUTPUT2_PRODUCTION_PASS_THRESHOLD)
    hard_floor = 85.0

    priors_data = load_learned_priors(priors_path)
    learned = priors_data.get("learned_priors", {})

    # Check for judge-specific or general judge deduction calibration
    norm_name = judge_name.lower().replace(" ", "_")
    cat_keys = [
        f"judge_{norm_name}",
        norm_name,
        "judge_deduction_clarification",
    ]
    priors_entry = None
    for k in cat_keys:
        if k in learned and isinstance(learned[k], dict):
            priors_entry = learned[k]
            break

    if not priors_entry:
        return (hard_floor, default_ceiling)

    # Compute affirmation ratio
    aff_data = priors_entry.get("AFFIRM_VALID", {})
    uphold_data = priors_entry.get("UPHOLD_DEDUCTION", {})
    aff_weight = float(aff_data.get("affirmed_weight", aff_data.get("affirmations", 1.0)))
    uphold_weight = float(uphold_data.get("affirmed_weight", uphold_data.get("affirmations", 1.0)))

    total = aff_weight + uphold_weight
    if total <= 0:
        return (hard_floor, default_ceiling)

    affirm_ratio = aff_weight / total
    # Operator affirmation tendency adjusts ceiling within bounded [87.0, 95.0]
    delta = (affirm_ratio - 0.5) * 4.0
    calibrated_ceiling = max(87.0, min(95.0, default_ceiling + delta))

    # CRITICAL INVARIANT: Non-compensating floor never drops below 85.0
    calibrated_floor = max(85.0, hard_floor)

    return (calibrated_floor, round(calibrated_ceiling, 2))


def record_judge_override(
    judge_name: str,
    candidate_score: float,
    operator_action: str,
    reason: str = "",
    run_id: Optional[str] = None,
    receipt_id: Optional[str] = None,
) -> Any:
    """Records an authentic judge-vs-operator override event into live telemetry stream."""
    try:
        from dual_pipeline.learning_adapters.live_pipeline_integrator import (  # noqa: lazy-decouple
            get_live_pipeline_integrator,
        )
        integrator = get_live_pipeline_integrator()
        if integrator:
            return integrator.record_decision_override(
                decision_id=f"dec-judge-{judge_name.lower().replace(' ', '_')}",
                category="judge_deduction_clarification",
                recommended="UPHOLD_DEDUCTION",
                chosen=operator_action,
                margin=None,
                source_agent=judge_name,
                receipt_id=receipt_id,
            )
    except Exception:
        pass
    return None

