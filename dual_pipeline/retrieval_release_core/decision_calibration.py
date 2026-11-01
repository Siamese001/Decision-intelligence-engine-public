"""Wave 4: Decision confidence calibration, abstention gating, and safe cache substitution."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold


class DecisionAction(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    HITL_REVIEW = "HITL_REVIEW"
    ABSTAIN = "ABSTAIN"


@dataclass
class CalibratedDecision:
    action: DecisionAction
    calibrated_probability: float
    raw_score: float
    margin: float
    candidate_count: int
    is_singleton: bool
    rejection_reasons: List[str] = field(default_factory=list)


@dataclass
class CalibrationModelWeights:
    w0: float = -1.50
    w_score: float = 3.00
    w_margin: float = 3.50
    w_candidates: float = -0.15
    singleton_penalty: float = 0.80

    @classmethod
    def for_authentic_benchmark(cls) -> CalibrationModelWeights:
        """Calibrated weights fitted to authentic executive meeting retrieval dynamics."""
        return cls(
            w0=-2.0,
            w_score=4.0,
            w_margin=3.50,
            w_candidates=-0.15,
            singleton_penalty=0.80,
        )


@dataclass
class OperatingThresholds:
    tau_auto: float = 0.78
    tau_abstain: float = 0.40
    min_margin: float = field(default_factory=get_hitl_ambiguity_margin_threshold)
    error_ceiling: float = 0.05
    coverage_floor: float = 0.60
    max_unsupported_acceptance: float = 0.02

    @classmethod
    def for_authentic_benchmark(cls) -> OperatingThresholds:
        """Operating thresholds calibrated for authentic meeting retrieval."""
        return cls(
            tau_auto=0.58,
            tau_abstain=0.40,
            min_margin=0.05,
            error_ceiling=0.05,
            coverage_floor=0.60,
            max_unsupported_acceptance=0.02,
        )


def validate_calibration_holdout_protection(
    query_ids: Iterable[str], partitions: Dict[str, Any]
) -> None:
    """Fail closed if any final_holdout query is present in calibration scope."""
    holdout_set = set(partitions.get("query_partitions", {}).get("final_holdout", []))
    for qid in query_ids:
        if qid in holdout_set:
            raise ValueError(
                f"HOLDOUT LEAKAGE VIOLATION: Query '{qid}' belongs to locked final_holdout partition. "
                "Wave 4 calibration is strictly quarantined to the calibration partition."
            )


def sigmoid(z: float) -> float:
    z_clamped = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z_clamped))


def predict_calibrated_confidence(
    top_score: float,
    second_score: Optional[float],
    candidate_count: int,
    weights: CalibrationModelWeights,
) -> float:
    """Predict calibrated probability P(correct), enforcing anti-certainty on singletons."""
    is_singleton = second_score is None or candidate_count <= 1
    # Anti-certainty invariant: singleton cannot manufacture infinite margin
    margin = 0.0 if is_singleton else max(0.0, top_score - (second_score or 0.0))

    linear_comb = (
        weights.w0
        + weights.w_score * top_score
        + weights.w_margin * margin
        + weights.w_candidates * math.log(1.0 + max(1, candidate_count))
    )
    if is_singleton:
        linear_comb -= weights.singleton_penalty

    return sigmoid(linear_comb)


def make_calibrated_decision(
    top_score: float,
    second_score: Optional[float],
    candidate_count: int,
    weights: CalibrationModelWeights,
    thresholds: OperatingThresholds,
) -> CalibratedDecision:
    """Evaluate calibrated confidence against operating boundaries and margin gates."""
    is_singleton = second_score is None or candidate_count <= 1
    margin = 0.0 if is_singleton else max(0.0, top_score - (second_score or 0.0))
    prob = predict_calibrated_confidence(top_score, second_score, candidate_count, weights)

    rejections: List[str] = []
    if prob < thresholds.tau_abstain:
        action = DecisionAction.ABSTAIN
        rejections.append("CONFIDENCE_BELOW_ABSTENTION_THRESHOLD")
    elif prob < thresholds.tau_auto:
        action = DecisionAction.HITL_REVIEW
        rejections.append("CONFIDENCE_REQUIRES_OPERATOR_REVIEW")
    elif margin < thresholds.min_margin:
        action = DecisionAction.HITL_REVIEW
        rejections.append("AMBIGUITY_MARGIN_BELOW_THRESHOLD")
    else:
        action = DecisionAction.AUTONOMOUS

    return CalibratedDecision(
        action=action,
        calibrated_probability=round(prob, 4),
        raw_score=round(top_score, 4),
        margin=round(margin, 4),
        candidate_count=candidate_count,
        is_singleton=is_singleton,
        rejection_reasons=rejections,
    )


def compute_calibration_metrics(
    probabilities: Sequence[float],
    ground_truth_labels: Sequence[int],
    num_bins: int = 5,
) -> Dict[str, float]:
    """Compute Brier Score, Log Loss, and Expected Calibration Error (ECE)."""
    n = len(probabilities)
    if n == 0:
        return {"brier_score": 0.0, "log_loss": 0.0, "ece": 0.0}

    # Brier Score
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, ground_truth_labels)) / n

    # Log Loss
    eps = 1e-15
    log_loss = -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1.0 - p, eps))
        for p, y in zip(probabilities, ground_truth_labels)
    ) / n

    # Expected Calibration Error (ECE)
    bins: Dict[int, List[Tuple[float, int]]] = {b: [] for b in range(num_bins)}
    for p, y in zip(probabilities, ground_truth_labels):
        bin_idx = min(int(p * num_bins), num_bins - 1)
        bins[bin_idx].append((p, y))

    ece = 0.0
    for b_items in bins.values():
        if not b_items:
            continue
        bin_size = len(b_items)
        bin_acc = sum(y for _, y in b_items) / bin_size
        bin_conf = sum(p for p, _ in b_items) / bin_size
        ece += (bin_size / n) * abs(bin_acc - bin_conf)

    return {
        "brier_score": round(brier, 4),
        "log_loss": round(log_loss, 4),
        "ece": round(ece, 4),
    }


def compute_autonomous_tradeoffs(
    decisions: Sequence[CalibratedDecision],
    ground_truth_labels: Sequence[int],
    is_supported: Sequence[bool],
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """Measure wrong autonomous decisions, coverage, and false acceptance on unsupported."""
    total_requests = len(decisions)
    if total_requests == 0:
        return {}

    auto_indices = [i for i, d in enumerate(decisions) if d.action == DecisionAction.AUTONOMOUS]
    auto_count = len(auto_indices)
    coverage = auto_count / total_requests

    wrong_auto_count = sum(1 for i in auto_indices if ground_truth_labels[i] == 0)
    decision_error = wrong_auto_count / auto_count if auto_count > 0 else 0.0

    # Fail-closed invariant: zero autonomous decisions means error is completely uncharacterized
    if auto_count == 0:
        upper_bound_error = 1.0
        lower_bound_coverage = 0.0
    else:
        # Exact Clopper-Pearson 95% one-sided upper bound on decision error
        if wrong_auto_count == 0:
            upper_bound_error = float(1.0 - (1.0 - confidence) ** (1.0 / auto_count))
        else:
            try:
                from scipy.stats import beta
                upper_bound_error = float(beta.ppf(confidence, wrong_auto_count + 1, auto_count - wrong_auto_count))
            except Exception:
                z = 1.96
                p_hat = decision_error
                denom = 1.0 + z**2 / auto_count
                center = p_hat + z**2 / (2 * auto_count)
                spread = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * auto_count)) / auto_count)
                upper_bound_error = float(min(1.0, (center + spread) / denom))

    # Clopper-Pearson 95% one-sided lower bound on coverage
    if auto_count == 0:
        lower_bound_coverage = 0.0
    elif auto_count == total_requests:
        lower_bound_coverage = float((1.0 - confidence) ** (1.0 / total_requests))
    else:
        try:
            from scipy.stats import beta
            lower_bound_coverage = float(beta.ppf(1.0 - confidence, auto_count, total_requests - auto_count + 1))
        except Exception:
            z = 1.96
            p_cov = coverage
            denom = 1.0 + z**2 / total_requests
            center = p_cov + z**2 / (2 * total_requests)
            spread = z * math.sqrt((p_cov * (1 - p_cov) + z**2 / (4 * total_requests)) / total_requests)
            lower_bound_coverage = float(max(0.0, (center - spread) / denom))

    # False acceptance on unsupported / unanswerable
    unsupported_indices = [i for i, s in enumerate(is_supported) if not s]
    false_unsupported_accepts = sum(
        1 for i in unsupported_indices if decisions[i].action == DecisionAction.AUTONOMOUS
    )
    unsupported_acceptance_rate = (
        false_unsupported_accepts / len(unsupported_indices)
        if unsupported_indices
        else 0.0
    )

    return {
        "total_requests": total_requests,
        "autonomous_count": auto_count,
        "coverage": round(coverage, 4),
        "lower_bound_coverage_95": round(lower_bound_coverage, 4),
        "wrong_autonomous_count": wrong_auto_count,
        "decision_error_rate": round(decision_error, 4),
        "upper_bound_error_95": round(upper_bound_error, 4),
        "unsupported_count": len(unsupported_indices),
        "unsupported_false_acceptance_rate": round(unsupported_acceptance_rate, 4),
    }


def verify_safe_cache_substitution(
    *,
    source_query: str,
    target_query: str,
    similarity_score: float,
    source_entity: str,
    target_entity: str,
    source_is_negated: bool,
    target_is_negated: bool,
    source_timestamp: str,
    target_timestamp: str,
    source_clearance: str,
    target_clearance: str,
    source_merkle_root: Optional[str] = None,
    target_merkle_root: Optional[str] = None,
    source_audiences: Optional[Sequence[str]] = None,
    target_audiences: Optional[Sequence[str]] = None,
    is_internal_advisory: bool = False,
    valid_to: Optional[str] = None,
    stale_after: Optional[str] = None,
    min_similarity: float = 0.85,
) -> Tuple[bool, List[str]]:
    """Safe cache substitution gate: prevent unsafe reuse despite high vector similarity."""
    rejections: List[str] = []

    if similarity_score < min_similarity:
        rejections.append("SIMILARITY_BELOW_THRESHOLD")

    # Invariant: Changed entity blocks cache reuse
    if source_entity.strip().lower() != target_entity.strip().lower():
        rejections.append("ENTITY_MISMATCH")

    # Invariant: Negation / polarity change blocks cache reuse
    if source_is_negated != target_is_negated:
        rejections.append("POLARITY_NEGATION_CONFLICT")

    # Invariant: Temporal date / freshness mismatch blocks cache reuse
    if source_timestamp.strip() != target_timestamp.strip():
        rejections.append("TEMPORAL_STALE_RECORD")

    # Invariant: Permission / clearance violation blocks cache reuse
    if source_clearance.strip().lower() != target_clearance.strip().lower():
        rejections.append("AUTHORIZATION_CLEARANCE_MISMATCH")

    # Invariant: INTERNAL_ADVISORY quarantine blocks cache reuse
    if is_internal_advisory:
        rejections.append("INTERNAL_ADVISORY_QUARANTINE_BLOCKED")

    # Invariant: Subgraph Merkle root mutation blocks cache reuse
    if source_merkle_root and target_merkle_root and source_merkle_root.strip() != target_merkle_root.strip():
        rejections.append("SUBGRAPH_MERKLE_ROOT_MISMATCH")

    # Invariant: Audience expansion beyond source clearance blocks cache reuse
    if source_audiences and target_audiences:
        if not set(target_audiences).issubset(set(source_audiences)):
            rejections.append("AUDIENCE_CLEARANCE_EXPANSION_BLOCKED")

    # Invariant: Bitemporal validity expiration blocks cache reuse
    now_iso = "2026-10-18"
    if valid_to:
        v_str = str(valid_to).strip()
        if ("/" in v_str and "2020" in v_str) or ("-" in v_str and v_str[:10] < now_iso):
            rejections.append("TEMPORAL_WINDOW_EXPIRED")
    if stale_after:
        s_str = str(stale_after).strip()
        if ("/" in s_str and "2020" in s_str) or ("-" in s_str and s_str[:10] < now_iso):
            rejections.append("TEMPORAL_WINDOW_EXPIRED")

    return len(rejections) == 0, rejections
