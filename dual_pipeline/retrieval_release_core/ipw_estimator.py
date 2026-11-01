"""Horvitz-Thompson Inverse Probability Weighting (IPW) estimator for stratified adjudication."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .decision_calibration import DecisionAction


@dataclass
class IPWAdjudicationSample:
    query_id: str
    candidate_decision: DecisionAction
    sampling_probability: float
    sampling_weight: float
    is_error: bool
    human_judgment_grade: Optional[int] = None
    critical_slice: str = "general"
    decision_id: Optional[str] = None
    ledger_verified: bool = False

    def __post_init__(self) -> None:
        if self.sampling_probability <= 0.0 or self.sampling_probability > 1.0:
            raise ValueError(
                f"Sampling probability must be in (0, 1], got {self.sampling_probability}"
            )
        if self.sampling_weight <= 0.0:
            raise ValueError(
                f"Sampling weight must be positive, got {self.sampling_weight}"
            )


@dataclass
class IPWEstimationResult:
    total_samples: int
    sampled_autonomous: int
    sampled_escalated: int
    raw_error_rate: float
    ipw_error_rate: float
    effective_sample_size: float
    variance_ipw: float
    std_error_ipw: float
    ci_lower: float
    ci_upper: float
    alpha_spent: float
    alpha_spending_boundary: float
    error_ceiling_breached: bool
    slice_breakdown: Dict[str, Dict[str, float]]


def compute_horvitz_thompson_ipw(
    samples: Sequence[IPWAdjudicationSample],
    max_allowed_error: float = 0.05,
    max_sample_size: int = 500,
    alpha_total: float = 0.05,
) -> IPWEstimationResult:
    """Compute unbiased Horvitz-Thompson IPW error estimate over stratified reviews."""
    if not samples:
        return IPWEstimationResult(
            total_samples=0,
            sampled_autonomous=0,
            sampled_escalated=0,
            raw_error_rate=0.0,
            ipw_error_rate=0.0,
            effective_sample_size=0.0,
            variance_ipw=0.0,
            std_error_ipw=0.0,
            ci_lower=0.0,
            ci_upper=1.0,
            alpha_spent=0.0,
            alpha_spending_boundary=0.0,
            error_ceiling_breached=False,
            slice_breakdown={},
        )

    n = len(samples)
    n_auto = sum(
        1 for s in samples if s.candidate_decision == DecisionAction.AUTONOMOUS
    )
    n_esc = sum(
        1 for s in samples if s.candidate_decision != DecisionAction.AUTONOMOUS
    )

    total_weight = sum(s.sampling_weight for s in samples)
    sum_weight_sq = sum(s.sampling_weight ** 2 for s in samples)
    effective_n = (total_weight ** 2) / sum_weight_sq if sum_weight_sq > 0 else 0.0

    raw_errors = sum(1 for s in samples if s.is_error)
    raw_error_rate = raw_errors / n

    weighted_errors = sum(s.sampling_weight for s in samples if s.is_error)
    ipw_error_rate = weighted_errors / total_weight if total_weight > 0 else 0.0

    # Hajek ratio estimator sample variance
    if total_weight > 0 and n > 1:
        sum_sq_diff = sum(
            (s.sampling_weight ** 2) * ((1.0 if s.is_error else 0.0) - ipw_error_rate) ** 2
            for s in samples
        )
        var_ipw = sum_sq_diff / (total_weight ** 2)
    else:
        var_ipw = 0.0

    se_ipw = math.sqrt(var_ipw) if var_ipw > 0.0 else 0.0

    # Sequential alpha spending: Lan-DeMets quadratic t^2 boundary
    info_fraction = min(1.0, max(0.01, n / max(1, max_sample_size)))
    alpha_spent = alpha_total * (info_fraction ** 2)
    z_critical = 1.96

    if se_ipw > 0.0:
        ci_lower = max(0.0, ipw_error_rate - z_critical * se_ipw)
        ci_upper = min(1.0, ipw_error_rate + z_critical * se_ipw)
    elif ipw_error_rate == 0.0:
        ci_lower = 0.0
        # Rule of three on effective sample size
        ci_upper = min(1.0, 3.0 / max(1.0, effective_n))
    else:
        ci_lower = max(0.0, ipw_error_rate - 0.01)
        ci_upper = min(1.0, ipw_error_rate + 0.01)

    breached = ipw_error_rate > max_allowed_error or ci_lower > max_allowed_error

    slice_data: Dict[str, Dict[str, float]] = {}
    slices = {s.critical_slice for s in samples}
    for slc in slices:
        sub = [s for s in samples if s.critical_slice == slc]
        sub_wt = sum(s.sampling_weight for s in sub)
        sub_err = sum(s.sampling_weight for s in sub if s.is_error)
        slice_data[slc] = {
            "sample_count": float(len(sub)),
            "ipw_error_rate": (sub_err / sub_wt) if sub_wt > 0 else 0.0,
        }

    return IPWEstimationResult(
        total_samples=n,
        sampled_autonomous=n_auto,
        sampled_escalated=n_esc,
        raw_error_rate=raw_error_rate,
        ipw_error_rate=ipw_error_rate,
        effective_sample_size=effective_n,
        variance_ipw=var_ipw,
        std_error_ipw=se_ipw,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        alpha_spent=alpha_spent,
        alpha_spending_boundary=alpha_spent,
        error_ceiling_breached=breached,
        slice_breakdown=slice_data,
    )
