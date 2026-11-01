"""PR Topology Core package for branch graph and file contention analysis."""

from __future__ import annotations

from .clustering import BranchCluster, compute_topological_clusters
from .disposition import BranchDisposition, compute_dispositions
from .matrix import BranchDiff, TopologyMatrix, compute_topology_matrix
from .supersession import (
    BranchSupersessionResult,
    SupersessionEvidence,
    SupersessionTarget,
    evaluate_branch_to_branch_supersession,
    evaluate_commit_supersession,
    evaluate_dirty_file_coverage,
)

__all__ = [
    "BranchCluster",
    "BranchDiff",
    "BranchDisposition",
    "BranchSupersessionResult",
    "SupersessionEvidence",
    "SupersessionTarget",
    "TopologyMatrix",
    "compute_dispositions",
    "compute_topological_clusters",
    "compute_topology_matrix",
    "evaluate_branch_to_branch_supersession",
    "evaluate_commit_supersession",
    "evaluate_dirty_file_coverage",
]
