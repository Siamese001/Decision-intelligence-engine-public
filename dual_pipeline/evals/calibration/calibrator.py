"""
Multi-Judge Calibration and Inter-Rater Agreement Runner.

Benchmarks dialectical judges against golden reference datasets,
verifying Cohen's Kappa (kappa >= 0.82) and non-compensating gate compliance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

from .metrics import (
    compute_brier_score,
    compute_cohens_kappa,
    compute_expected_calibration_error,
)


@dataclass
class CalibrationReport:
    """Aggregated calibration report for an automated judge on a benchmark dataset."""

    evaluator_name: str
    dataset_id: str
    total_cases: int
    ground_truth_counts: Dict[str, int]
    predicted_counts: Dict[str, int]
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    cohens_kappa: float
    brier_score: float
    expected_calibration_error: float
    passed: bool
    gate_failures: List[str] = field(default_factory=list)
    evaluated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JudgeCalibrationRunner:
    """Executes calibration benchmarking of LLM judges against golden datasets."""

    def __init__(
        self,
        min_kappa: float = 0.82,
        min_recall: float = 0.85,
        min_accuracy: float = 0.85,
    ):
        self.min_kappa = min_kappa
        self.min_recall = min_recall
        self.min_accuracy = min_accuracy

    def run_calibration(
        self,
        golden_dataset: List[Dict[str, Any]],
        eval_fn: Callable[[Dict[str, Any]], Tuple[str, float]],
        evaluator_name: str = "DecomposedMultiJudge",
        dataset_id: str = "golden-benchmark-v1",
    ) -> CalibrationReport:
        """
        Run calibration on golden dataset.
        
        eval_fn: callable taking a case dict and returning (verdict_str, confidence_float).
        """
        if not golden_dataset:
            return CalibrationReport(
                evaluator_name=evaluator_name,
                dataset_id=dataset_id,
                total_cases=0,
                ground_truth_counts={},
                predicted_counts={},
                accuracy=0.0,
                precision=0.0,
                recall=0.0,
                f1_score=0.0,
                cohens_kappa=0.0,
                brier_score=0.0,
                expected_calibration_error=0.0,
                passed=False,
                gate_failures=["Empty benchmark dataset"],
            )

        ground_truths: List[str] = []
        predictions: List[str] = []
        confidences: List[float] = []
        gt_binary: List[int] = []

        gt_counts: Dict[str, int] = {}
        pred_counts: Dict[str, int] = {}

        for case in golden_dataset:
            gt = case.get("ground_truth_verdict", "PASS")
            pred, conf = eval_fn(case)

            ground_truths.append(gt)
            predictions.append(pred)
            confidences.append(conf)
            gt_binary.append(1 if gt == "PASS" else 0)

            gt_counts[gt] = gt_counts.get(gt, 0) + 1
            pred_counts[pred] = pred_counts.get(pred, 0) + 1

        n = len(ground_truths)

        # Calculate Cohen's Kappa
        kappa = compute_cohens_kappa(ground_truths, predictions)

        # Calculate Brier score and ECE
        brier = compute_brier_score(confidences, gt_binary)
        ece = compute_expected_calibration_error(confidences, gt_binary)

        # Calculate classification metrics
        tp = sum(1 for g, p in zip(ground_truths, predictions) if g == "PASS" and p == "PASS")
        tn = sum(1 for g, p in zip(ground_truths, predictions) if g == "FAIL" and p == "FAIL")
        fp = sum(1 for g, p in zip(ground_truths, predictions) if g == "FAIL" and p == "PASS")
        fn = sum(1 for g, p in zip(ground_truths, predictions) if g == "PASS" and p == "FAIL")

        accuracy = (tp + tn) / n if n > 0 else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
        f1 = (
            2 * (precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        # Evaluate non-compensating release gates
        gate_failures = []
        if kappa < self.min_kappa:
            gate_failures.append(f"Cohen's Kappa {kappa:.3f} < {self.min_kappa:.3f}")
        if recall < self.min_recall:
            gate_failures.append(f"Recall {recall:.3f} < {self.min_recall:.3f}")
        if accuracy < self.min_accuracy:
            gate_failures.append(f"Accuracy {accuracy:.3f} < {self.min_accuracy:.3f}")

        passed = len(gate_failures) == 0

        return CalibrationReport(
            evaluator_name=evaluator_name,
            dataset_id=dataset_id,
            total_cases=n,
            ground_truth_counts=gt_counts,
            predicted_counts=pred_counts,
            accuracy=round(accuracy, 4),
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1_score=round(f1, 4),
            cohens_kappa=kappa,
            brier_score=brier,
            expected_calibration_error=ece,
            passed=passed,
            gate_failures=gate_failures,
        )
