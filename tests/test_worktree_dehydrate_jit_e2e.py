"""
End-to-end tests for Wave 3 worktree dehydration, JIT rehydration, and creation cap enforcement.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from tools.pr_governance_core.worktree import (
    _auto_prune_and_enforce_cap_unlocked,
    cmd_dehydrate,
    cmd_finish,
)


def test_cmd_dehydrate_and_finish_deleted_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)

    (repo / "README.md").write_text("main", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # Provision a worktree manually
    wt_dir = repo / ".worktrees" / "feat_test_dehydrate"
    subprocess.run(["git", "worktree", "add", str(wt_dir), "-b", "feat/test-dehydrate", "main"], cwd=repo, check=True)

    # Monkeypatch ROOT to repo
    import tools.pr_governance_core.worktree as wt_module
    monkeypatch.setattr(wt_module, "ROOT", repo)

    # Dehydrate worktree
    args = argparse.Namespace(branch="feat/test-dehydrate")
    rc = cmd_dehydrate(args)
    assert rc == 0
    assert not wt_dir.exists()

    # Verify branch still exists in git refs
    br_out = subprocess.run(["git", "branch", "--list", "feat/test-dehydrate"], cwd=repo, capture_output=True, text=True, check=True)
    assert "feat/test-dehydrate" in br_out.stdout

    # Now run finish on dehydrated branch with delete_branch=True
    finish_args = argparse.Namespace(branch="feat/test-dehydrate", force=True, delete_branch=True)
    f_rc = cmd_finish(finish_args)
    assert f_rc == 0

    # Verify branch is deleted cleanly
    br_after = subprocess.run(["git", "branch", "--list", "feat/test-dehydrate"], cwd=repo, capture_output=True, text=True, check=True)
    assert not br_after.stdout.strip()


def test_auto_prune_cap_enforcement(tmp_path: Path) -> None:
    repo = tmp_path / "repo_cap"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)

    (repo / "README.md").write_text("main", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # Provision 30 worktrees
    for i in range(1, 31):
        wt = repo / ".worktrees" / f"feat_worktree_{i}"
        subprocess.run(["git", "worktree", "add", str(wt), "-b", f"feat/worktree-{i}", "main"], cwd=repo, check=True)

    err = _auto_prune_and_enforce_cap_unlocked("feat/new-worktree", root=repo)
    assert err is not None
    assert "Active worktree cap reached" in err
