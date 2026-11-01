"""Batch evaluation engine for Project LENS Single-Turbine threshold comparison."""
from __future__ import annotations

from typing import List, Tuple

from dual_pipeline.lens_evaluation.models import (
    ComparisonMetrics,
    ErrorSample,
    ReconciliationOutcome,
    ThresholdConfig,
    TurbinePhotoRecord,
)
from dual_pipeline.lens_evaluation.preflight import PreFlightQualityGate
from dual_pipeline.lens_evaluation.reconciler import DualModelReconciler


def _extract_record_error_samples(
    rec: TurbinePhotoRecord,
    outcome: ReconciliationOutcome,
) -> List[ErrorSample]:
    """Builds itemized error sample records for false acceptances."""
    samples: List[ErrorSample] = []
    for field_name in outcome.error_fields:
        extracted_val = getattr(outcome, f"resolved_{field_name}", "UNKNOWN")
        gt_val = getattr(rec, f"ground_truth_{field_name}", "UNKNOWN")
        mA_val = getattr(rec.model_a, field_name, "UNKNOWN")
        mB_val = getattr(rec.model_b, field_name, "UNKNOWN")
        samples.append(
            ErrorSample(
                photo_id=rec.photo_id,
                component_type=rec.component_type,
                field_name=field_name,
                extracted_value=str(extracted_val),
                ground_truth_value=str(gt_val),
                model_a_value=str(mA_val),
                model_b_value=str(mB_val),
            )
        )
    return samples


def _build_comparison_metrics(
    config_name: str,
    total_photos: int,
    capture_defects: int,
    legible_photos: int,
    auto_accepted: int,
    human_review: int,
    correct_auto_accepted: int,
    false_acceptances: int,
    error_samples: List[ErrorSample],
) -> ComparisonMetrics:
    """Computes straight-through, correctness, error, and defect rates."""
    stp_rate = (auto_accepted / legible_photos * 100.0) if legible_photos > 0 else 0.0
    checked_correctness = (
        (correct_auto_accepted / auto_accepted * 100.0) if auto_accepted > 0 else 100.0
    )
    false_accept_rate = (
        (false_acceptances / auto_accepted * 100.0) if auto_accepted > 0 else 0.0
    )
    review_rate = (human_review / legible_photos * 100.0) if legible_photos > 0 else 0.0
    defect_rate = (capture_defects / total_photos * 100.0) if total_photos > 0 else 0.0

    return ComparisonMetrics(
        profile_name=config_name,
        total_photos=total_photos,
        capture_defects=capture_defects,
        legible_photos=legible_photos,
        auto_accepted=auto_accepted,
        human_review=human_review,
        correct_auto_accepted=correct_auto_accepted,
        false_acceptances=false_acceptances,
        stp_rate=round(stp_rate, 2),
        checked_correctness_rate=round(checked_correctness, 2),
        false_acceptance_rate=round(false_accept_rate, 2),
        review_volume_rate=round(review_rate, 2),
        defect_rate=round(defect_rate, 2),
        error_samples=error_samples,
    )


class SingleTurbineEvaluator:
    """Evaluates turbine component photograph batches across threshold configurations."""

    @classmethod
    def evaluate_batch(
        cls,
        records: List[TurbinePhotoRecord],
        config: ThresholdConfig,
    ) -> Tuple[ComparisonMetrics, List[ReconciliationOutcome]]:
        """Executes full evaluation pipeline across all records under the given configuration."""
        total_photos = len(records)
        capture_defects = 0
        legible_photos = 0
        auto_accepted = 0
        human_review = 0
        correct_auto_accepted = 0
        false_acceptances = 0
        error_samples: List[ErrorSample] = []
        outcomes: List[ReconciliationOutcome] = []

        for rec in records:
            preflight = PreFlightQualityGate.evaluate(rec, config)
            if not preflight.is_legible:
                capture_defects += 1
                outcomes.append(
                    ReconciliationOutcome(
                        photo_id=rec.photo_id,
                        routing_tier="ZONE_0_QUARANTINED",
                        resolved_manufacturer=None,
                        resolved_model_number=None,
                        resolved_serial_number=None,
                        confidence_score=0.0,
                        is_auto_accepted=False,
                        is_correct=False,
                        is_capture_defect=True,
                        defect_reason=preflight.defect_code,
                    )
                )
                continue

            legible_photos += 1
            outcome = DualModelReconciler.reconcile(rec, config)
            outcomes.append(outcome)

            if not outcome.is_auto_accepted:
                human_review += 1
                continue

            auto_accepted += 1
            if outcome.is_correct:
                correct_auto_accepted += 1
            else:
                false_acceptances += 1
                error_samples.extend(_extract_record_error_samples(rec, outcome))

        metrics = _build_comparison_metrics(
            config_name=config.name,
            total_photos=total_photos,
            capture_defects=capture_defects,
            legible_photos=legible_photos,
            auto_accepted=auto_accepted,
            human_review=human_review,
            correct_auto_accepted=correct_auto_accepted,
            false_acceptances=false_acceptances,
            error_samples=error_samples,
        )
        return metrics, outcomes

