"""Governed threshold configurations for Project LENS evaluation."""
from __future__ import annotations

from dual_pipeline.lens_evaluation.models import ThresholdConfig

STRICT_BASELINE = ThresholdConfig(
    name="STRICT_BASELINE",
    description="Current production baseline: 100% exact token consensus across Model A and Model B, strict pre-flight bounds, and zero-tolerance serial checks.",
    min_laplacian_variance=120.0,
    min_luminance=50.0,
    max_luminance=200.0,
    min_prefix_match_len=999,
    min_serial_levenshtein=1.0,
    require_exact_manufacturer=True,
    use_bounding_box_dominance=False,
    allow_zero_serial_exception=False,
    stp_confidence_threshold=0.90,
    assisted_confidence_threshold=0.75,
)

PROPOSED_RELAXED = ThresholdConfig(
    name="PROPOSED_RELAXED",
    description="Proposed relaxed consensus: Bounding box dominance for manufacturer, prefix anchor alignment (>=8 chars), Levenshtein similarity (>80%) on serials, and zero-serial exceptions.",
    min_laplacian_variance=100.0,
    min_luminance=40.0,
    max_luminance=220.0,
    min_prefix_match_len=8,
    min_serial_levenshtein=0.80,
    require_exact_manufacturer=False,
    use_bounding_box_dominance=True,
    allow_zero_serial_exception=True,
    stp_confidence_threshold=0.85,
    assisted_confidence_threshold=0.65,
)
