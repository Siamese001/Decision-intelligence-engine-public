"""Statistical calculations, percentiles, bootstrap confidence bounds, and qualification."""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Sequence, Tuple


def calculate_percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(float(sorted_vals[int(k)]), 4)
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return round(float(d0 + d1), 4)


def bootstrap_ratio_ucb(
    pre_samples: Sequence[float],
    post_samples: Sequence[float],
    statistic_fn,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> float:
    if not pre_samples or not post_samples:
        return 1.0

    rng = random.Random(seed)
    n_pre = len(pre_samples)
    n_post = len(post_samples)
    ratios: List[float] = []

    for _ in range(n_resamples):
        sample_pre = [pre_samples[rng.randint(0, n_pre - 1)] for _ in range(n_pre)]
        sample_post = [post_samples[rng.randint(0, n_post - 1)] for _ in range(n_post)]
        stat_pre = statistic_fn(sample_pre)
        stat_post = statistic_fn(sample_post)
        if stat_pre <= 0:
            continue
        ratios.append(stat_post / stat_pre)

    if not ratios:
        return 1.0
    # One-sided upper confidence bound
    return calculate_percentile(ratios, confidence * 100.0)


def evaluate_qualification(
    median_ratio: float,
    median_ratio_ucb: float,
    p95_ratio: float,
    p95_ratio_ucb: float,
    per_lane_regression: bool,
    tail_evidence_sufficient: bool,
) -> Tuple[str, str]:
    target_met = (
        median_ratio_ucb <= 0.40
        and p95_ratio_ucb <= 0.40
        and not per_lane_regression
        and tail_evidence_sufficient
    )
    if target_met:
        return "PROVEN", "60% speedup claimed: 95% UCB on median/p95 ratios <= 0.40 without per-lane regression"

    reasons = []
    if median_ratio_ucb > 0.40:
        reasons.append(f"Median ratio 95% UCB ({median_ratio_ucb:.3f}) > 0.40")
    if p95_ratio_ucb > 0.40:
        reasons.append(f"P95 ratio 95% UCB ({p95_ratio_ucb:.3f}) > 0.40")
    if per_lane_regression:
        reasons.append("Detected regression in at least one lane")
    if not tail_evidence_sufficient:
        reasons.append("Insufficient tail evidence across strata")

    reason_str = "; ".join(reasons) if reasons else "Speed target not demonstrated"
    return "NOT_PROVEN", f"Speedup claim NOT_PROVEN: {reason_str}"
