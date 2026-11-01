"""Pipeline C (Retrieval) Layer 0: Contracts, Constants, and Data Types.

Zero-dependency contracts defining retrieved facts and query namespace boundaries.
"""
from __future__ import annotations

from dual_pipeline.retrieval.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    HybridWeights,
    RetrievedFact,
)

__all__ = [
    "CLIENT_SAFE_NAMESPACE",
    "INTERNAL_ADVISORY_NAMESPACE",
    "HybridWeights",
    "RetrievedFact",
]
