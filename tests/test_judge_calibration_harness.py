"""
Unit and acceptance test suite for Wave 2: Dialectical Judge Calibration & Agreement Harness.

Verifies:
- Cohen's Kappa for binary and multiclass judgments.
- Fleiss' Kappa across multi-judge juries.
- Brier score and Expected Calibration Error (ECE).
- PositionSwapDebiaser order bias detection and mitigation.
- JudgeCalibrationRunner execution on golden datasets and release gating.
"""

from __future__ import annotations

from dual_pipeline.evals.calibration import (
    JudgeCalibrationRunner,
    PositionSwapDebiaser,
    compute_brier_score,
    compute_cohens_kappa,
    compute_expected_calibration_error,
    compute_fleiss_kappa,
)


def test_cohens_kappa_spectrum():
    """Verify Cohen's Kappa on perfect, single-class, partial, and inverse agreement."""
    # Perfect agreement
    gt = ["PASS", "PASS", "FAIL", "FAIL", "PASS"]
    pred = ["PASS", "PASS", "FAIL", "FAIL", "PASS"]
    assert compute_cohens_kappa(gt, pred) == 1.0

    # Single-class identical agreement
    gt_single = ["PASS", "PASS", "PASS"]
    pred_single = ["PASS", "PASS", "PASS"]
    assert compute_cohens_kappa(gt_single, pred_single) == 1.0

    # Substantial partial agreement
    gt_partial = ["PASS", "PASS", "FAIL", "FAIL", "PASS", "FAIL", "PASS", "FAIL"]
    pred_partial = ["PASS", "PASS", "FAIL", "FAIL", "PASS", "FAIL", "PASS", "PASS"]  # 1 disagreement
    k = compute_cohens_kappa(gt_partial, pred_partial)
    assert 0.70 <= k < 1.0

    # Inverse agreement
    gt_inv = ["PASS", "PASS", "FAIL", "FAIL"]
    pred_inv = ["FAIL", "FAIL", "PASS", "PASS"]
    assert compute_cohens_kappa(gt_inv, pred_inv) < 0.0


def test_fleiss_kappa_multirater():
    """Verify Fleiss' Kappa across multi-judge juries."""
    # 3 raters, 4 items, 2 categories (PASS=0, FAIL=1)
    # Complete agreement: all raters agree for each item
    perfect_matrix = [
        [3, 0],  # Item 1: 3 raters say PASS
        [3, 0],  # Item 2: 3 raters say PASS
        [0, 3],  # Item 3: 3 raters say FAIL
        [0, 3],  # Item 4: 3 raters say FAIL
    ]
    assert compute_fleiss_kappa(perfect_matrix) == 1.0

    # Partial agreement with 1 minor split item
    mixed_matrix = [
        [3, 0],
        [3, 0],
        [2, 1],
        [0, 3],
    ]
    k = compute_fleiss_kappa(mixed_matrix)
    assert 0.50 <= k < 1.0


def test_brier_and_calibration_error():
    """Verify Brier score and ECE on well-calibrated vs miscalibrated predictions."""
    # Well-calibrated: high confidence matches positive labels, low confidence matches negative
    probs_good = [0.95, 0.90, 0.05, 0.10]
    gt_good = [1, 1, 0, 0]
    brier_good = compute_brier_score(probs_good, gt_good)
    ece_good = compute_expected_calibration_error(probs_good, gt_good)
    assert brier_good < 0.05
    assert ece_good < 0.10

    # Miscalibrated: completely wrong with high confidence
    probs_bad = [0.99, 0.95, 0.01, 0.02]
    gt_bad = [0, 0, 1, 1]
    brier_bad = compute_brier_score(probs_bad, gt_bad)
    ece_bad = compute_expected_calibration_error(probs_bad, gt_bad)
    assert brier_bad > 0.85
    assert ece_bad > 0.85


def test_position_swap_order_bias_detection():
    """Verify that PositionSwapDebiaser catches order bias vs consistent judgment."""
    debiaser = PositionSwapDebiaser()

    cand_a = {"id": "model_alpha", "text": "Superior detailed response"}
    cand_b = {"id": "model_beta", "text": "Basic short response"}

    # Consistent judge: always chooses model_alpha regardless of whether it's first or second
    def consistent_judge(first, second):
        if first["id"] == "model_alpha":
            return "model_alpha", 0.92
        return "model_alpha", 0.90

    res_consistent = debiaser.evaluate_pair(cand_a, cand_b, consistent_judge)
    assert res_consistent.is_consistent
    assert not res_consistent.order_bias_detected
    assert res_consistent.final_winner == "model_alpha"

    # Positional-biased judge: always chooses the FIRST option presented
    def biased_judge(first, second):
        return first["id"], 0.85

    res_biased = debiaser.evaluate_pair(cand_a, cand_b, biased_judge)
    assert not res_biased.is_consistent
    assert res_biased.order_bias_detected
    assert res_biased.final_winner == "INCONCLUSIVE_ORDER_BIAS"


def test_judge_calibration_runner_success():
    """Verify JudgeCalibrationRunner approves calibrated high-agreement judge."""
    runner = JudgeCalibrationRunner(min_kappa=0.82, min_recall=0.85, min_accuracy=0.85)

    golden_dataset = [
        {"id": "case-1", "ground_truth_verdict": "PASS"},
        {"id": "case-2", "ground_truth_verdict": "PASS"},
        {"id": "case-3", "ground_truth_verdict": "PASS"},
        {"id": "case-4", "ground_truth_verdict": "PASS"},
        {"id": "case-5", "ground_truth_verdict": "PASS"},
        {"id": "case-6", "ground_truth_verdict": "FAIL"},
        {"id": "case-7", "ground_truth_verdict": "FAIL"},
        {"id": "case-8", "ground_truth_verdict": "FAIL"},
        {"id": "case-9", "ground_truth_verdict": "FAIL"},
        {"id": "case-10", "ground_truth_verdict": "FAIL"},
    ]

    # Highly calibrated judge agreeing 100%
    def mock_eval_good(case):
        return case["ground_truth_verdict"], 0.95

    report = runner.run_calibration(golden_dataset, mock_eval_good, evaluator_name="TestJudge")
    assert report.passed
    assert report.cohens_kappa == 1.0
    assert report.accuracy == 1.0
    assert report.recall == 1.0
    assert len(report.gate_failures) == 0


def test_judge_calibration_runner_fails_on_degraded_kappa():
    """Verify JudgeCalibrationRunner rejects judge when kappa falls below threshold."""
    runner = JudgeCalibrationRunner(min_kappa=0.82)

    golden_dataset = [
        {"id": "case-1", "ground_truth_verdict": "PASS"},
        {"id": "case-2", "ground_truth_verdict": "PASS"},
        {"id": "case-3", "ground_truth_verdict": "PASS"},
        {"id": "case-4", "ground_truth_verdict": "PASS"},
        {"id": "case-5", "ground_truth_verdict": "FAIL"},
        {"id": "case-6", "ground_truth_verdict": "FAIL"},
    ]

    # Flawed judge with multiple false passes / false fails
    flawed_predictions = ["PASS", "FAIL", "PASS", "FAIL", "PASS", "FAIL"]

    def mock_eval_flawed(case):
        idx = int(case["id"].split("-")[1]) - 1
        return flawed_predictions[idx], 0.60

    report = runner.run_calibration(golden_dataset, mock_eval_flawed, evaluator_name="FlawedJudge")
    assert not report.passed
    assert report.cohens_kappa < 0.82
    assert any("Cohen's Kappa" in f for f in report.gate_failures)
