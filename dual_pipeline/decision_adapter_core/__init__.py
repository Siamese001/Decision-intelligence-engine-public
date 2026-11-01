"""Decision Intelligence Runtime Source Adapter Core Package."""
from __future__ import annotations

from .adapter import RuntimeDecisionSourceAdapter
from .constants import (
    MAX_CONTEXT_RECORDS,
    PROJECTION_QUALIFIER,
    PROJECTION_SCHEMA_VERSION,
    RUNTIME_SOURCE_ADAPTER_ID,
    RUNTIME_SOURCE_ADAPTER_VERSION,
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
