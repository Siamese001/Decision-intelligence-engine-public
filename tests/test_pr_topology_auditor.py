"""
Unit tests for PR topology auditor matrix and clustering.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tools.pr_overlap_core import MergeOrderPlan, MergeStep, RebaseRecommendation
from tools.pr_topology_auditor import main, render_topology_report
from tools.pr_topology_core import (
    BranchDiff,
    TopologyMatrix,
    compute_topological_clusters,
)


def test_topology_matrix_initialization():
    matrix = TopologyMatrix(base_branch="main", branches=["feat/b1", "feat/b2"])
    assert matrix.base_branch == "main"
    assert len(matrix.branches) == 2
    d = matrix.to_dict()
    assert d["base_branch"] == "main"
    assert "feat/b1" in d["branches"]


def test_branch_diff_dataclass():
    diff = BranchDiff(branch="feat/test", ahead=2, behind=1, files=["a.py", "b.py"], file_count=2)
    assert diff.branch == "feat/test"
    assert diff.ahead == 2
    assert diff.file_count == 2
    assert not diff.is_output_only


def test_compute_topological_clusters_tombstone():
    matrix = TopologyMatrix(base_branch="main", branches=["feat/old", "feat/new"])
    matrix.branch_diffs["feat/old"] = BranchDiff(
        branch="feat/old", ahead=5, behind=20, files=["a.py", "b.py"]
    )
    matrix.branch_diffs["feat/new"] = BranchDiff(
        branch="feat/new", ahead=5, behind=0, files=["a.py", "b.py"]
    )
    matrix.superseded_candidates.append(
        {
            "older_branch": "feat/old",
            "newer_branch": "feat/new",
            "shared_file_count": 2,
        }
    )

    clusters = compute_topological_clusters(matrix)
    categories = [c.category for c in clusters]
    assert "SUPERSEDED_TOMBSTONE" in categories
    tombstone = [c for c in clusters if c.category == "SUPERSEDED_TOMBSTONE"][0]
    assert "feat/old" in tombstone.branches


def test_compute_topological_clusters_data_highway():
    matrix = TopologyMatrix(base_branch="main", branches=["feat/live-meeting"])
    matrix.branch_diffs["feat/live-meeting"] = BranchDiff(
        branch="feat/live-meeting",
        ahead=1,
        behind=0,
        files=["output/proj/01_rec.md", "output/proj/02_sum.md"],
        is_output_only=True,
    )
    clusters = compute_topological_clusters(matrix)
    assert any(c.category == "DATA_HIGHWAY" for c in clusters)


def test_render_topology_report_with_merge_plan():
    matrix = TopologyMatrix(base_branch="main", branches=["feat/b1", "feat/b2"])
    clusters = []

    step1 = MergeStep(
        step_index=1,
        branch="feat/b1",
        action="MERGE",
        conflicts_with_prior=[],
        conflict_count=0,
        recommended_command="git merge --no-ff feat/b1",
    )
    step2 = MergeStep(
        step_index=2,
        branch="feat/b2",
        action="MERGE",
        conflicts_with_prior=["feat/b1"],
        conflict_count=1,
        recommended_command="git merge --no-ff feat/b2",
    )
    rebase_rec = RebaseRecommendation(
        branch="feat/b2",
        target_base="feat/b1",
        upstream_branch="feat/b1",
        is_stacked=True,
        rebase_command="git rebase --onto feat/b1 main feat/b2",
        predicted_conflict_files=["x.py"],
    )

    plan = MergeOrderPlan(
        schema_version="urn:nee:pr-overlap-plan:1.0.0",
        repo_root="/test",
        base_ref="main",
        branches=["feat/b1", "feat/b2"],
        ordered_steps=[step1, step2],
        total_conflicts=1,
        alphabetical_conflicts=2,
        rebase_recommendations=[rebase_rec],
        search_strategy="exhaustive_pruned",
        repo_state_digest_before="digest123",
        repo_state_digest_after="digest123",
        zero_mutation_verified=True,
        execution_time_seconds=0.05,
    )

    report = render_topology_report(matrix, clusters, duration_sec=0.1, merge_plan=plan)
    assert "CONSOLIDATION & CHERRY-PICK PLAN" in report
    assert "urn:nee:pr-overlap-plan:1.0.0" in report
    assert "Contention Reduction:   50.0%" in report
    assert "Zero Mutation Verified: True" in report
    assert "[STACKED] feat/b2 onto feat/b1" in report
    assert "git rebase --onto feat/b1 main feat/b2" in report


def test_topology_auditor_receipt_and_metrics(tmp_path):
    receipt_file = tmp_path / "pr_overlap_test_receipt.json"

    with (
        patch("sys.argv", [
            "pr_topology_auditor.py",
            "--branches", "feat/b1", "feat/b2",
            "--receipt", str(receipt_file),
            "--no-simulate-merge",
        ]),
        patch(
            "tools.pr_topology_auditor.compute_topology_matrix",
            return_value=TopologyMatrix(base_branch="main", branches=["feat/b1", "feat/b2"]),
        ),
        patch("tools.pr_topology_auditor.compute_dispositions", return_value={}),
        patch("tools.pr_topology_auditor.compute_topological_clusters", return_value=[]),
    ):
        ret = main()
        assert ret == 0

    assert receipt_file.exists()
    data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert data["gate_status"] == "PASSED"
    assert data["terminal_outcome"] == "PASS"
    assert "metrics" in data
    assert data["metrics"]["branch_count"] == 2

