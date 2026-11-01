"""Backward-compatibility re-export of HITL Bayesian calibration primitives."""
from __future__ import annotations

from dual_pipeline.hitl_governance_core.bayesian import (
    CalibratedScore,
    ConfidenceResult,
    _match_keys,
    canonicalize_pattern_key,
    compute_bayesian_calibrated_score,
    compute_beta_credible_interval,
)

__all__ = [
    "CalibratedScore",
    "ConfidenceResult",
    "_match_keys",
    "canonicalize_pattern_key",
    "compute_bayesian_calibrated_score",
    "compute_beta_credible_interval",
]
