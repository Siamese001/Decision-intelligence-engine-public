"""
Core package for PR queue render and column width characterization probe.
"""

from __future__ import annotations

from .html_builder import generate_observer_table_html, generate_variant_html
from .models import (
    DEFAULT_VIEWPORTS,
    SAMPLE_12COL_ROW,
    RCASummary,
    VariantMeasurement,
    find_chrome_binary,
)
from .runner import (
    format_summary_table,
    measure_batched_via_chrome,
    run_probe,
    simulate_layout_deterministic,
)

__all__ = [
    "DEFAULT_VIEWPORTS",
    "SAMPLE_12COL_ROW",
    "RCASummary",
    "VariantMeasurement",
    "find_chrome_binary",
    "generate_variant_html",
    "generate_observer_table_html",
    "format_summary_table",
    "measure_batched_via_chrome",
    "run_probe",
    "simulate_layout_deterministic",
]
