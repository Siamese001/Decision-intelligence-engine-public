"""
dual_pipeline/backtest_core

Core utilities for cost-optimized backtest re-runs:
- Run-over-run comparison report generation
- Output 02 section differential analysis
- Output 03 entity and assertion delta tracking
- Cost comparison between production baseline and backtest execution
"""
from __future__ import annotations

from .comparison import (
    diff_entities_and_assertions,
    diff_output2_sections,
    generate_backtest_comparison_report,
)

__all__ = [
    "generate_backtest_comparison_report",
    "diff_output2_sections",
    "diff_entities_and_assertions",
]
