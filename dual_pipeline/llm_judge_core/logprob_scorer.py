"""Logprob-calibrated continuous expected value scoring and Shannon entropy ambiguity detection.

Implements:
1. G-Eval logprob-weighted continuous expected value:
   E[Score] = sum(weight_s * P(s))
2. Exact token predictive variance:
   Var(Score) = sum((weight_s - E[Score])^2 * P(s))
3. Normalized Shannon entropy for single-pass ambiguity detection:
   H(P) = - (1 / log(|S|)) * sum(P(s) * log(P(s)))
4. Automatic ambiguity margin gating (Delta = P(top) - P(second) <= 0.30 or H(P) > 0.65).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold


@dataclass(frozen=True)
class LogprobScoreResult:
    """Analytical distribution metrics computed from token logprobs."""
    expected_value: float
    variance: float
    std_dev: float
    normalized_entropy: float
    top_token: str
    top_probability: float
    margin: float
    probabilities: Dict[str, float]
    is_ambiguous: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_value": round(self.expected_value, 4),
            "variance": round(self.variance, 6),
            "std_dev": round(self.std_dev, 4),
            "normalized_entropy": round(self.normalized_entropy, 4),
            "top_token": self.top_token,
            "top_probability": round(self.top_probability, 4),
            "margin": round(self.margin, 4),
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "is_ambiguous": self.is_ambiguous,
        }


def compute_logprob_distribution(
    logprobs_map: Mapping[str, float],
    token_weights: Mapping[str, float],
    temperature: float = 1.0,
    entropy_threshold: float = 0.65,
    margin_threshold: float | None = None,
) -> LogprobScoreResult:
    """Compute continuous expected value, variance, and entropy across candidate score tokens."""
    if margin_threshold is None:
        margin_threshold = get_hitl_ambiguity_margin_threshold()
    if not logprobs_map or not token_weights:
        raise ValueError("logprobs_map and token_weights must be non-empty")

    valid_tokens = [tok for tok in token_weights if tok in logprobs_map] or list(token_weights.keys())
    raw_logprobs = {tok: logprobs_map.get(tok, 0.0) for tok in valid_tokens}

    tau = max(1e-4, float(temperature))
    max_lp = max(raw_logprobs.values())
    exp_vals = {tok: math.exp((lp - max_lp) / tau) for tok, lp in raw_logprobs.items()}
    sum_exp = sum(exp_vals.values()) or 1.0
    probabilities = {tok: v / sum_exp for tok, v in exp_vals.items()}

    sorted_probs = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)
    top_tok, top_p = sorted_probs[0]
    second_p = sorted_probs[1][1] if len(sorted_probs) > 1 else 0.0
    margin = top_p - second_p

    expected_value = sum(token_weights[tok] * p for tok, p in probabilities.items())
    variance = sum(((token_weights[tok] - expected_value) ** 2) * p for tok, p in probabilities.items())
    std_dev = math.sqrt(variance)

    k = len(token_weights)
    shannon = -sum(p * math.log(max(p, 1e-12)) for p in probabilities.values()) if k > 1 else 0.0
    normalized_entropy = min(1.0, max(0.0, shannon / math.log(k))) if k > 1 else 0.0
    is_ambiguous = (margin <= margin_threshold) or (normalized_entropy > entropy_threshold)

    return LogprobScoreResult(
        expected_value=expected_value, variance=variance, std_dev=std_dev,
        normalized_entropy=normalized_entropy, top_token=top_tok,
        top_probability=top_p, margin=margin, probabilities=probabilities,
        is_ambiguous=is_ambiguous,
    )


# Standard 4-level Behaviorally Anchored Rating Scale (BARS) token weights
BARS_4_WEIGHTS: Mapping[str, float] = {
    "PASS": 1.0,
    "MINOR_DEFECT": 0.75,
    "MAJOR_DEFECT": 0.40,
    "CRITICAL_FAIL": 0.0,
}

# Standard Binary Checklist weights
BINARY_CHECKLIST_WEIGHTS: Mapping[str, float] = {
    "PASS": 1.0,
    "FAIL": 0.0,
}


def rollup_atomic_criteria(
    criteria_results: Mapping[str, LogprobScoreResult],
    criterion_weights: Optional[Mapping[str, float]] = None,
    max_dimension_points: float = 25.0,
) -> Tuple[float, float, float, bool, Dict[str, Any]]:
    """Roll up atomic sub-criteria into a deterministic dimension score.

    Args:
        criteria_results: Dict mapping subcriterion ID to its LogprobScoreResult.
        criterion_weights: Relative weights of each subcriterion. Defaults to equal weights.
        max_dimension_points: Maximum score points for the dimension.

    Returns:
        (dimension_score, pooled_variance, max_entropy, is_ambiguous, diagnostics)
    """
    if not criteria_results:
        return 0.0, 0.0, 0.0, False, {}

    n = len(criteria_results)
    if criterion_weights is None:
        raw_weights = {k: 1.0 / n for k in criteria_results}
    else:
        total_w = sum(criterion_weights.get(k, 1.0) for k in criteria_results) or 1.0
        raw_weights = {k: criterion_weights.get(k, 1.0) / total_w for k in criteria_results}

    expected_ratio = sum(raw_weights[k] * res.expected_value for k, res in criteria_results.items())
    dimension_score = round(expected_ratio * max_dimension_points, 2)

    pooled_variance = sum((raw_weights[k] ** 2) * res.variance for k, res in criteria_results.items()) * (max_dimension_points ** 2)
    max_entropy = max(res.normalized_entropy for res in criteria_results.values())
    is_ambiguous = any(res.is_ambiguous for res in criteria_results.values())

    diagnostics = {
        "dimension_score": dimension_score,
        "max_dimension_points": max_dimension_points,
        "pooled_variance": round(pooled_variance, 6),
        "max_entropy": round(max_entropy, 4),
        "is_ambiguous": is_ambiguous,
        "subcriteria": {k: res.to_dict() for k, res in criteria_results.items()},
    }
    return dimension_score, pooled_variance, max_entropy, is_ambiguous, diagnostics


def rollup_scorecard_logprobs(
    dimension_results: Mapping[str, Tuple[float, float, float, bool, Dict[str, Any]]],
) -> Tuple[float, Dict[str, Any]]:
    """Deterministically roll up dimensions into a composite score and diagnostic report.

    Args:
        dimension_results: Dict mapping dimension key to tuple from rollup_atomic_criteria.

    Returns:
        (composite_score, logprob_diagnostics)
    """
    composite_score = sum(dim[0] for dim in dimension_results.values())
    composite_score = round(composite_score, 2)
    any_ambiguous = any(dim[3] for dim in dimension_results.values())
    diagnostics = {
        "composite_score": composite_score,
        "is_ambiguous": any_ambiguous,
        "dimensions": {k: dim[4] for k, dim in dimension_results.items()},
    }
    return composite_score, diagnostics

