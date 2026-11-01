"""Core components for deterministic GraphDB delta receipts."""

from __future__ import annotations

from .constants import _COMPONENTS, _SIDECAR_COMPONENTS, _SIDECAR_NET_KEYS
from .counts import (
    _graph_counts,
    _planned_increments,
    _present_ids,
    _row,
    _sidecar_counts,
)
from .delta import (
    build_actual_graph_delta,
    build_planned_graph_delta,
    validate_planned_graph_delta,
)
from .itemized_assertion import (
    build_itemized_assertion_delta,
    format_itemized_assertion_table,
)
from .monotonicity import (
    DEFAULT_MONITORED_COMPONENTS,
    StructuralMonotonicityError,
    verify_structural_monotonicity,
)
from .summary import (
    build_graph_mutation_summary,
    format_markdown_mutation_table,
    format_mutation_summary_banner,
)
from .trend import (
    MetricTrendAnalyzer,
)
from .vector_health import (
    format_vector_health_report,
    inspect_vector_health,
)

__all__ = [
    "DEFAULT_MONITORED_COMPONENTS",
    "MetricTrendAnalyzer",
    "StructuralMonotonicityError",
    "_COMPONENTS",
    "_SIDECAR_COMPONENTS",
    "_SIDECAR_NET_KEYS",
    "_graph_counts",
    "_planned_increments",
    "_present_ids",
    "_row",
    "_sidecar_counts",
    "build_actual_graph_delta",
    "build_graph_mutation_summary",
    "build_itemized_assertion_delta",
    "build_planned_graph_delta",
    "format_itemized_assertion_table",
    "format_markdown_mutation_table",
    "format_mutation_summary_banner",
    "format_vector_health_report",
    "inspect_vector_health",
    "validate_planned_graph_delta",
    "verify_structural_monotonicity",
]
