"""
Adversarial test suite for bifurcated worktree reconciliation and pruning under e2e-and-edge-case-coverage.md.
Covers boundary empty trees, corruption prevention, unlinked branches, and multi-pair isolation.
"""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.reconcile_live_worktrees import (
    coalesce_live_worktree_pair,
    detect_bifurcated_pairs,
    migrate_artifacts,
)


def _setup_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(repo_dir), check=True)
    (repo_dir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)


def test_boundary_empty_or_unrelated_worktrees(tmp_path):
    """Boundary: Empty worktrees directory or completely unrelated worktrees yield 0 pairs."""
    wt_dir = tmp_path / ".worktrees"
    wt_dir.mkdir()

    # Empty directory
    assert detect_bifurcated_pairs(tmp_path) == []

    # Unrelated worktrees
    (wt_dir / "live_project_alpha").mkdir()
    (wt_dir / "live_project_beta").mkdir()
    (wt_dir / "feat_something_else").mkdir()

    assert detect_bifurcated_pairs(tmp_path) == []


def test_corruption_during_migration_aborts_without_pruning(tmp_path):
    """Invariant: If file copy corrupts data, reconciliation must abort and NEVER prune source."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _setup_git_repo(repo_root)

    wt_dir = repo_root / ".worktrees"
    wt_dir.mkdir()

    wt_source = wt_dir / "live_standup_amit"
    wt_target = wt_dir / "live_standup_amit_nozim"

    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_amit", str(wt_source), "main"], cwd=str(repo_root), check=True, capture_output=True)
    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_amit_nozim", str(wt_target), "main"], cwd=str(repo_root), check=True, capture_output=True)

    source_out = wt_source / "output" / "proj" / "date" / "slug"
    source_out.mkdir(parents=True)
    (source_out / "00_master.wav").write_text("CORRECT_SOURCE_AUDIO")

    # Simulate hash corruption during copy
    with patch("shutil.copy2", side_effect=lambda src, dst: Path(dst).write_text("CORRUPTED_BYTES")):
        with pytest.raises(ValueError, match="Hash mismatch"):
            coalesce_live_worktree_pair(wt_source, wt_target, repo_root, dry_run=False)

    # Source must NOT have been pruned!
    assert wt_source.exists()
    assert (source_out / "00_master.wav").exists()
    branch_check = subprocess.run(["git", "branch", "--list", "feat/live-standup_amit"], cwd=str(repo_root), capture_output=True, text=True)
    assert "feat/live-standup_amit" in branch_check.stdout


def test_multi_pair_isolation(tmp_path):
    """Multi-pair: Verify that multiple distinct bifurcated sessions are resolved independently."""
    wt_dir = tmp_path / ".worktrees"
    wt_dir.mkdir()

    # Session 1: standup_amit
    (wt_dir / "live_standup_amit").mkdir()
    (wt_dir / "live_standup_amit_nozim").mkdir()

    # Session 2: sprint_planning
    (wt_dir / "live_sprint_planning").mkdir()
    (wt_dir / "live_sprint_planning_client").mkdir()

    pairs = detect_bifurcated_pairs(tmp_path)
    assert len(pairs) == 2

    session1 = {p[0].name for p in pairs if "standup" in p[0].name} | {p[1].name for p in pairs if "standup" in p[1].name}
    assert session1 == {"live_standup_amit", "live_standup_amit_nozim"}

    session2 = {p[0].name for p in pairs if "sprint" in p[0].name} | {p[1].name for p in pairs if "sprint" in p[1].name}
    assert session2 == {"live_sprint_planning", "live_sprint_planning_client"}
