"""
tools/benchmark_latency_core/__init__.py

Core statistics, data models, and instrumentation probe generator for
record-start latency benchmarking under Wave 5 latency and diagnosis governance.
"""
from __future__ import annotations

from tools.benchmark_latency_core.stats import (
    compute_latency_stats,
    compute_percentile,
    evaluate_startup_budget_qualification,
)

__all__ = [
    "compute_latency_stats",
    "compute_percentile",
    "evaluate_startup_budget_qualification",
]
