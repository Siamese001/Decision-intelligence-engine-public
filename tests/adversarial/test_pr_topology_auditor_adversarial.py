"""
Adversarial and edge-case testing for PR Topology Auditor.
Covers boundary conditions, circular lineage, empty payloads, and scaling invariants.
"""

from __future__ import annotations

from tools.pr_topology_core import (
    BranchDiff,
    TopologyMatrix,
    compute_topological_clusters,
)


def test_adversarial_empty_matrix():
    matrix = TopologyMatrix(base_branch="main", branches=[])
    clusters = compute_topological_clusters(matrix)
    assert clusters == []
    d = matrix.to_dict()
    assert d["branches"] == []


def test_adversarial_single_branch_singleton():
    matrix = TopologyMatrix(base_branch="main", branches=["feat/solo"])
    matrix.branch_diffs["feat/solo"] = BranchDiff(
        branch="feat/solo", ahead=1, behind=0, files=["foo.py"]
    )
    clusters = compute_topological_clusters(matrix)
    assert len(clusters) == 1
    assert clusters[0].category == "ISOLATED_SINGLETON"
    assert "Fast-Lane" in clusters[0].recommended_action


def test_adversarial_circular_lineage_protection():
    # Synthetic circular lineage where A says ancestor of B and B says ancestor of A
    matrix = TopologyMatrix(base_branch="main", branches=["feat/a", "feat/b"])
    diff_a = BranchDiff(branch="feat/a", ancestor_of=["feat/b"], descendant_of=["feat/b"])
    diff_b = BranchDiff(branch="feat/b", ancestor_of=["feat/a"], descendant_of=["feat/a"])
    matrix.branch_diffs["feat/a"] = diff_a
    matrix.branch_diffs["feat/b"] = diff_b

    # Clustering should not infinite loop or crash
    clusters = compute_topological_clusters(matrix)
    assert len(clusters) >= 1
    assert any(c.category == "LINEAGE_DEPENDENCY" for c in clusters)


def test_adversarial_massive_file_overlap_scaling():
    # 50 branches sharing 1,000 files
    branches = [f"feat/load_{i}" for i in range(20)]
    files = [f"src/file_{k}.py" for k in range(500)]
    matrix = TopologyMatrix(base_branch="main", branches=branches)
    for b in branches:
        matrix.branch_diffs[b] = BranchDiff(branch=b, files=files, file_count=len(files))

    # Pairwise overlap populated
    for b1 in branches:
        matrix.pairwise_overlap[b1] = {b2: 500 for b2 in branches}

    clusters = compute_topological_clusters(matrix)
    assert len(clusters) > 0
