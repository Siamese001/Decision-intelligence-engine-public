"""
Pareto Analysis and Token Economics Package.

Exposes ParetoPoint, RegressionVerdict, and ParetoFrontierAnalyzer.
"""

from __future__ import annotations

from .analyzer import (
    ParetoFrontierAnalyzer,
    ParetoPoint,
    RegressionVerdict,
)

__all__ = [
    "ParetoPoint",
    "RegressionVerdict",
    "ParetoFrontierAnalyzer",
]
