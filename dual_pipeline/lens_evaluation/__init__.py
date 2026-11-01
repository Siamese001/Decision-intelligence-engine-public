"""Project LENS Single-Turbine Evaluation and Threshold Comparison Engine.

Provides deterministic evaluation of turbine component photographic inputs,
segregating Stage 0 pre-flight capture defects from Stage 1/2 extraction errors,
and comparing Strict Baseline vs. Proposed Relaxed dual-model consensus thresholds.
"""
from __future__ import annotations

from dual_pipeline.lens_evaluation.id_tag_extractor import (
    IDTagExtractor,
    IDTagNormalizationResult,
    IDTagRecord,
)
from dual_pipeline.lens_evaluation.models import (
    ComparisonMetrics,
    ErrorSample,
    ModelExtractionResult,
    PreFlightQualityResult,
    ReconciliationOutcome,
    ThresholdConfig,
    TurbinePhotoRecord,
)
from dual_pipeline.lens_evaluation.thresholds import (
    PROPOSED_RELAXED,
    STRICT_BASELINE,
)

__all__ = [
    "ComparisonMetrics",
    "ErrorSample",
    "IDTagExtractor",
    "IDTagNormalizationResult",
    "IDTagRecord",
    "ModelExtractionResult",
    "PreFlightQualityResult",
    "ReconciliationOutcome",
    "ThresholdConfig",
    "TurbinePhotoRecord",
    "STRICT_BASELINE",
    "PROPOSED_RELAXED",
]

