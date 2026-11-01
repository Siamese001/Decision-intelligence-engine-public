"""Deterministic planned and actual GraphDB delta receipts for ingestion releases."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from storage.database import sqlite3

from .core import digest, sha256_file
from .graph_delta_core import (
    _COMPONENTS,
    _SIDECAR_COMPONENTS,
    _SIDECAR_NET_KEYS,
    DEFAULT_MONITORED_COMPONENTS,
    MetricTrendAnalyzer,
    StructuralMonotonicityError,
    _graph_counts,
    _planned_increments,
    _present_ids,
    _row,
    _sidecar_counts,
    build_actual_graph_delta,
    build_graph_mutation_summary,
    build_itemized_assertion_delta,
    build_planned_graph_delta,
    format_itemized_assertion_table,
    format_markdown_mutation_table,
    format_mutation_summary_banner,
    format_vector_health_report,
    inspect_vector_health,
    validate_planned_graph_delta,
    verify_structural_monotonicity,
)
from .node_type_policy import active_creatable_node_types

__all__ = [
    "Any",
    "Counter",
    "DEFAULT_MONITORED_COMPONENTS",
    "MetricTrendAnalyzer",
    "Path",
    "StructuralMonotonicityError",
    "_COMPONENTS",
    "_SIDECAR_COMPONENTS",
    "_SIDECAR_NET_KEYS",
    "_graph_counts",
    "_planned_increments",
    "_present_ids",
    "_row",
    "_sidecar_counts",
    "active_creatable_node_types",
    "build_actual_graph_delta",
    "build_graph_mutation_summary",
    "build_itemized_assertion_delta",
    "build_planned_graph_delta",
    "digest",
    "format_itemized_assertion_table",
    "format_markdown_mutation_table",
    "format_mutation_summary_banner",
    "format_vector_health_report",
    "inspect_vector_health",
    "sha256_file",
    "sqlite3",
    "validate_planned_graph_delta",
    "verify_structural_monotonicity",
]
