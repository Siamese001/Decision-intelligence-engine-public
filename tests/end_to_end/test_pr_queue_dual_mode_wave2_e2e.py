"""Wave 2 lifecycle checks for truthful queue facts in a disposable Git repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

from tools.pr_governance_core.queue_snapshot import collect_queue_snapshot


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=10).stdout.strip()


def test_snapshot_measures_branch_sync_diff_and_dirty_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Queue Test")
    _git(root, "config", "user.email", "queue-test@example.invalid")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "chore: base")
    _git(root, "switch", "-c", "feat/cost")
    changed = root / "dual_pipeline/token_optimization/calls.py"
    changed.parent.mkdir(parents=True)
    changed.write_text("def fewer_calls(): return 1\n", encoding="utf-8")
    _git(root, "add", "dual_pipeline/token_optimization/calls.py")
    _git(root, "commit", "-m", "perf: reduce paid calls")

    snapshot = collect_queue_snapshot(root)
    branch = next(item for item in snapshot.branches if item.branch == "feat/cost")
    assert (branch.ahead, branch.behind, branch.clean_merge) == (1, 0, True)
    assert branch.changed_files == ["dual_pipeline/token_optimization/calls.py"]
    assert branch.is_dirty is False

    (root / "draft.txt").write_text("uncommitted work\n", encoding="utf-8")
    dirty_snapshot = collect_queue_snapshot(root)
    dirty_branch = next(item for item in dirty_snapshot.branches if item.branch == "feat/cost")
    assert dirty_branch.is_dirty is True
    assert dirty_snapshot.completeness_status == "COMPLETE"
