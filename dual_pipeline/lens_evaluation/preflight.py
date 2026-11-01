"""Stage 0 Pre-Flight Quality Gate for Project LENS."""
from __future__ import annotations

from dual_pipeline.lens_evaluation.models import (
    PreFlightQualityResult,
    ThresholdConfig,
    TurbinePhotoRecord,
)


class PreFlightQualityGate:
    """Evaluates image capture quality before model inference."""

    @staticmethod
    def evaluate(
        record: TurbinePhotoRecord,
        config: ThresholdConfig,
    ) -> PreFlightQualityResult:
        """Determines if a photographic frame meets legibility requirements.

        Isolates capture defects into the Quarantine Audit Vault without
        penalizing model accuracy.
        """
        # 1. Alphanumeric presence check (detects blank / non-asset frames, e.g. FAA lights)
        if record.detected_token_count <= 0:
            return PreFlightQualityResult(
                is_legible=False,
                sharpness_score=record.laplacian_variance,
                luminance_score=record.mean_luminance,
                has_text_tokens=False,
                defect_code="ERR_NO_TEXT",
            )

        # 2. Sharpness / blur check (hand motion, focal blur)
        if record.laplacian_variance < config.min_laplacian_variance:
            return PreFlightQualityResult(
                is_legible=False,
                sharpness_score=record.laplacian_variance,
                luminance_score=record.mean_luminance,
                has_text_tokens=True,
                defect_code="ERR_BLUR",
            )

        # 3. Exposure check (underexposed/dark nacelle or overexposed/glare)
        if (
            record.mean_luminance < config.min_luminance
            or record.mean_luminance > config.max_luminance
        ):
            return PreFlightQualityResult(
                is_legible=False,
                sharpness_score=record.laplacian_variance,
                luminance_score=record.mean_luminance,
                has_text_tokens=True,
                defect_code="ERR_LIGHTING",
            )

        # Legible frame passing all Stage 0 quality gates
        return PreFlightQualityResult(
            is_legible=True,
            sharpness_score=record.laplacian_variance,
            luminance_score=record.mean_luminance,
            has_text_tokens=True,
            defect_code=None,
        )
