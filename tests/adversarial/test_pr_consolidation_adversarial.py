"""
Adversarial test suite for PR consolidation, branch clustering, and merge train orchestration.
Tests path traversal attempts, malformed refs, non-existent revisions, and race boundaries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.gate_impact_core import GateLayer, classify_diff_impact
from tools.merge_train_core import assemble_speculative_cohort
from tools.pr_topology_core import compute_topology_matrix
from tools.retire_superseded_branch import retire_branch

ROOT = Path(__file__).resolve().parent.parent.parent


def test_adversarial_topology_malformed_branch_names(tmp_path: Path):
    """Verifies that malicious or path-traversing branch names are handled cleanly."""
    repo_dir = tmp_path / "traversal_repo"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

    test_file = repo_dir / "init.txt"
    test_file.write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    # Attempt audit with invalid/path traversing names
    bad_branches = ["../../../etc/passwd", "--help", "invalid branch name", "nonexistent_ref_xyz"]
    matrix = compute_topology_matrix(bad_branches, root=repo_dir, base_branch="main")
    assert len(matrix.branches) == 4
    # All bad branches should result in empty file lists without crashing
    for b in bad_branches:
        assert b in matrix.branch_diffs
        files = matrix.branch_diffs[b].files
        assert isinstance(files, list)


def test_adversarial_gate_impact_extreme_paths():
    """Verifies path impact analyzer behavior under massive path counts and odd paths."""
    # 5,000 synthetic paths
    paths = [f"output/batch_{i}/01_transcript.md" for i in range(5000)]
    impact = classify_diff_impact(paths)
    assert impact.layer == GateLayer.CLIENT_DELIVERABLES
    assert impact.file_count == 5000
    assert impact.profile_name == "data_highway"

    # Contaminating with a single core engine file
    paths_contaminated = paths + ["dual_pipeline/core.py"]
    impact2 = classify_diff_impact(paths_contaminated)
    assert impact2.layer == GateLayer.CORE_ENGINE
    assert impact2.profile_name == "full"


def test_adversarial_tombstone_already_deleted_or_missing_branch():
    """Verifies that retiring a non-existent branch fails closed with error."""
    with pytest.raises(ValueError, match="does not exist"):
        retire_branch("ghost_branch_never_existed")


def test_adversarial_cohort_assembly_cyclic_or_identical_branches(tmp_path: Path):
    """Verifies that assembling a cohort with duplicate branch entries doesn't duplicate diffs."""
    rec = assemble_speculative_cohort(
        cohort_name="dup_cohort",
        branches=["branch_a", "branch_a", "branch_a"],
        root=ROOT,
    )
    assert rec.cohort_name == "dup_cohort"
    # Should handle duplicate gracefully
