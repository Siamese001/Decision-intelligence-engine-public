"""Read-only source-bound adapter from a graph to decision inputs.

Backward-compatibility facade delegating to `dual_pipeline.decision_adapter_core`.
"""
from __future__ import annotations

from .decision_adapter_core import (
    MAX_CONTEXT_RECORDS,
    PROJECTION_QUALIFIER,
    PROJECTION_SCHEMA_VERSION,
    RUNTIME_SOURCE_ADAPTER_ID,
    RUNTIME_SOURCE_ADAPTER_VERSION,
    RuntimeDecisionSourceAdapter,
    RuntimeDecisionSourceError,
)

__all__ = [
    "MAX_CONTEXT_RECORDS",
    "PROJECTION_QUALIFIER",
    "PROJECTION_SCHEMA_VERSION",
    "RUNTIME_SOURCE_ADAPTER_ID",
    "RUNTIME_SOURCE_ADAPTER_VERSION",
    "RuntimeDecisionSourceAdapter",
    "RuntimeDecisionSourceError",
]
