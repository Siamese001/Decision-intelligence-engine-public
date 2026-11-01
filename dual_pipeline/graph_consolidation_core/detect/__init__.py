"""
dual_pipeline/graph_consolidation_core/detect/__init__.py

Detection subpackage for concept deduplication, assertion overlaps, and evidence fragmentation.
"""
from __future__ import annotations

from .assertions import detect_assertion_overlaps
from .evidence import detect_evidence_fragmentation
from .nodes import detect_node_clusters

__all__ = [
    "detect_node_clusters",
    "detect_assertion_overlaps",
    "detect_evidence_fragmentation",
]
