"""
Statistical Inter-Rater Reliability and Judge Calibration Metrics.

Implements 2026+ Anthropic and OpenAI evaluation metrics:
- Cohen's Kappa (inter-rater agreement for binary and multiclass judgments).
- Fleiss' Kappa (generalized agreement for multi-judge juries).
- Brier Score (mean squared error of probabilistic predictions).
- Expected Calibration Error (ECE across binned confidence intervals).
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple


def compute_cohens_kappa(
    ground_truth: Sequence[str], predictions: Sequence[str]
) -> float:
    """
    Compute Cohen's Kappa inter-rater agreement coefficient between two raters.
    
    Returns a value in [-1.0, 1.0]:
        1.0: Perfect agreement
        0.81 - 1.00: Almost perfect / excellent agreement (Anthropic SOTA target >= 0.82)
        0.61 - 0.80: Substantial agreement
        0.41 - 0.60: Moderate agreement
        <= 0.00: Poor / chance or inverse agreement
    """
    if len(ground_truth) != len(predictions):
        raise ValueError(
            f"Length mismatch: ground_truth={len(ground_truth)}, predictions={len(predictions)}"
        )
    n = len(ground_truth)
    if n == 0:
        return 1.0

    # Collect all unique labels
    categories = sorted(list(set(ground_truth) | set(predictions)))
    num_cats = len(categories)

    if num_cats == 1:
        # Both raters predicted identical single class throughout
        return 1.0

    cat_to_idx = {c: i for i, c in enumerate(categories)}

    # Build confusion matrix C[actual][predicted]
    matrix = [[0] * num_cats for _ in range(num_cats)]
    for gt, pred in zip(ground_truth, predictions):
        matrix[cat_to_idx[gt]][cat_to_idx[pred]] += 1

    # Observed agreement (p_o)
    p_o = sum(matrix[i][i] for i in range(num_cats)) / n

    # Expected chance agreement (p_e)
    row_sums = [sum(matrix[i][j] for j in range(num_cats)) for i in range(num_cats)]
    col_sums = [sum(matrix[i][j] for i in range(num_cats)) for j in range(num_cats)]

    p_e = sum((row_sums[i] * col_sums[i]) / (n * n) for i in range(num_cats))

    if math.isclose(p_e, 1.0, rel_tol=1e-9):
        return 1.0 if math.isclose(p_o, 1.0, rel_tol=1e-9) else 0.0

    kappa = (p_o - p_e) / (1.0 - p_e)
    return max(-1.0, min(1.0, round(kappa, 4)))


def compute_fleiss_kappa(ratings_matrix: Sequence[Sequence[int]]) -> float:
    """
    Compute Fleiss' Kappa for inter-rater agreement across multiple raters.
    
    ratings_matrix: N x K matrix where N is items, K is categories,
                    and matrix[i][j] is the number of raters that assigned
                    item i to category j.
    """
    n_items = len(ratings_matrix)
    if n_items == 0:
        return 1.0

    n_categories = len(ratings_matrix[0])
    if n_categories <= 1:
        return 1.0

    n_raters = sum(ratings_matrix[0])
    if n_raters <= 1:
        return 1.0

    # Total ratings across all items
    total_ratings = n_items * n_raters

    # Proportion of all assignments to each category j
    p_j = [0.0] * n_categories
    for j in range(n_categories):
        p_j[j] = sum(ratings_matrix[i][j] for i in range(n_items)) / total_ratings

    p_e = sum(pj * pj for pj in p_j)

    # Degree of agreement for item i
    p_i = [0.0] * n_items
    for i in range(n_items):
        sum_squares = sum(ratings_matrix[i][j] ** 2 for j in range(n_categories))
        p_i[i] = (sum_squares - n_raters) / (n_raters * (n_raters - 1))

    p_o = sum(p_i) / n_items

    if math.isclose(p_e, 1.0, rel_tol=1e-9):
        return 1.0 if math.isclose(p_o, 1.0, rel_tol=1e-9) else 0.0

    kappa = (p_o - p_e) / (1.0 - p_e)
    return max(-1.0, min(1.0, round(kappa, 4)))


def compute_brier_score(
    probabilities: Sequence[float], ground_truth: Sequence[int]
) -> float:
    """
    Compute Brier Score: Mean squared error of calibrated probability forecasts.
    
    probabilities: list of predicted probabilities in [0.0, 1.0].
    ground_truth: list of actual binary outcomes in {0, 1}.
    """
    if len(probabilities) != len(ground_truth):
        raise ValueError("Length mismatch between probabilities and ground_truth")
    n = len(probabilities)
    if n == 0:
        return 0.0

    mse = sum((p - gt) ** 2 for p, gt in zip(probabilities, ground_truth)) / n
    return round(mse, 4)


def compute_expected_calibration_error(
    probabilities: Sequence[float], ground_truth: Sequence[int], num_bins: int = 10
) -> float:
    """
    Compute Expected Calibration Error (ECE) across binned confidence intervals.
    
    ECE measures the absolute difference between average predicted confidence
    and empirical accuracy within each bin.
    """
    if len(probabilities) != len(ground_truth):
        raise ValueError("Length mismatch between probabilities and ground_truth")
    n = len(probabilities)
    if n == 0:
        return 0.0

    bins: List[List[Tuple[float, int]]] = [[] for _ in range(num_bins)]
    for p, gt in zip(probabilities, ground_truth):
        bin_idx = min(num_bins - 1, int(p * num_bins))
        bins[bin_idx].append((p, gt))

    ece = 0.0
    for b in bins:
        if not b:
            continue
        bin_size = len(b)
        avg_confidence = sum(p for p, _ in b) / bin_size
        avg_accuracy = sum(gt for _, gt in b) / bin_size
        ece += (bin_size / n) * abs(avg_accuracy - avg_confidence)

    return round(ece, 4)
