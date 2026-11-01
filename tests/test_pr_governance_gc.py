"""
Unit tests for worktree garbage collector and branch retirement.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from tools.pr_governance_core.gc import inspect_worktrees_for_gc
from tools.retire_superseded_branch import retire_branch


def test_inspect_worktrees_for_gc():
    candidates = inspect_worktrees_for_gc()
    assert isinstance(candidates, list)
    for c in candidates:
        assert "path" in c
        assert "branch" in c
        assert "is_merged" in c
        assert "is_dirty" in c


def test_retire_branch_refuses_protected_main():
    with pytest.raises(ValueError, match="is protected"):
        retire_branch("main", dry_run=True)


def test_retire_branch_dry_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
        (root / "init.txt").write_text("ok\n", encoding="utf-8")
        subprocess.run(["git", "add", "init.txt"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True)
        subprocess.run(["git", "branch", "feat/my-feat"], cwd=root, check=True)

        receipt = retire_branch("feat/my-feat", dry_run=True, root=root)
        assert receipt["status"] == "SIMULATED"
        assert receipt["branch"] == "feat/my-feat"
        assert "refs/archive/feat/my-feat/" in receipt["archive_ref"]
