"""
Judge Calibration and Agreement Package.

Exposes metrics, position-swap debiasing, and calibration runner.
"""

from __future__ import annotations

from .calibrator import CalibrationReport, JudgeCalibrationRunner
from .metrics import (
    compute_brier_score,
    compute_cohens_kappa,
    compute_expected_calibration_error,
    compute_fleiss_kappa,
)
from .position_swap import PositionSwapDebiaser, PositionSwapResult

__all__ = [
    "compute_brier_score",
    "compute_cohens_kappa",
    "compute_expected_calibration_error",
    "compute_fleiss_kappa",
    "PositionSwapDebiaser",
    "PositionSwapResult",
    "CalibrationReport",
    "JudgeCalibrationRunner",
]
