"""Manifest Runner Subsystem — modular, declarative transcript ingestion execution."""
from __future__ import annotations

from .executor import ManifestPipelineRunner
from .reporting import (
    print_mutation_breakdown,
    query_graph_counts,
    query_new_assertions,
    query_sidecar_counts,
)

__all__ = [
    "ManifestPipelineRunner",
    "print_mutation_breakdown",
    "query_graph_counts",
    "query_new_assertions",
    "query_sidecar_counts",
]
