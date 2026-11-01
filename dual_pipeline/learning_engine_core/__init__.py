"""
dual_pipeline/learning_engine_core/__init__.py

Core components for multi-cohort aggregation, provenance binding, and rollback safety.
"""
from __future__ import annotations

from .aggregation import aggregate_cohort_proposals
from .provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
    verify_proposal_provenance,
)
from .rollback import (
    execute_rollback,
    record_rollback_snapshot,
    snapshot_database_digests,
    verify_zero_graph_mutation,
)

__all__ = [
    "PROVENANCE_EXTENSION_KEY",
    "aggregate_cohort_proposals",
    "build_signed_provenance",
    "execute_rollback",
    "record_rollback_snapshot",
    "snapshot_database_digests",
    "verify_proposal_provenance",
    "verify_zero_graph_mutation",
]
