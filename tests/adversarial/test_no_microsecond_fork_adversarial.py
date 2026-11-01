import pytest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    teardown_empty_live_worktree,
    _resolve_primary_repo_root,
    _worktree_has_audio_or_commits,
)


def test_adversarial_boundary_payloads(tmp_path):
    """Category 1: Boundary payload tests (None, empty, 0-byte, non-existent)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    # None and empty worktree dir fail safely without crashing
    assert teardown_empty_live_worktree(None, "feat/live-test", repo_root) is False
    assert teardown_empty_live_worktree(Path(""), "feat/live-test", repo_root) is False

    # Non-existent worktree returns True because it is already absent
    non_existent = repo_root / ".worktrees" / "live_does_not_exist"
    assert teardown_empty_live_worktree(non_existent, "feat/live-non-existent", repo_root) is True

    # 0-byte audio file does not count as meaningful audio, allowing clean teardown
    empty_wt = repo_root / ".worktrees" / "live_empty_wt"
    empty_wt.mkdir(parents=True)
    zero_wav = empty_wt / "chunk_000.wav"
    zero_wav.touch()
    assert _worktree_has_audio_or_commits(empty_wt, "feat/live-empty", repo_root) is False


def test_adversarial_root_and_caller_teardown_bypass(tmp_path):
    """Category 2: Invariant bypass attempt (attempting to delete root, caller worktree, or main)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    caller_wt = repo_root / ".worktrees" / "active_caller_worktree"
    caller_wt.mkdir(parents=True)

    # Attempting to tear down the repo root itself must fail closed
    assert teardown_empty_live_worktree(repo_root, "main", repo_root) is False
    assert repo_root.exists()

    # Attempting to tear down the caller root must fail closed
    assert teardown_empty_live_worktree(caller_wt, "feat/live-caller", repo_root=caller_wt) is False
    assert caller_wt.exists()

    # Attempting to delete non-live branches like main or master must not invoke git branch -D
    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git") as mock_git:
        empty_wt = repo_root / ".worktrees" / "live_test_wt"
        empty_wt.mkdir(parents=True)
        teardown_empty_live_worktree(empty_wt, "main", repo_root)
        branch_d_calls = [
            c[0][0] for c in mock_git.call_args_list
            if "branch" in c[0][0] and "-D" in c[0][0]
        ]
        assert len(branch_d_calls) == 0


def test_adversarial_concurrent_teardown_race(tmp_path):
    """Category 3: Concurrency and race testing across multiple teardown attempts."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    wt_dir = repo_root / ".worktrees" / "live_race_test"
    wt_dir.mkdir(parents=True)

    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", return_value=""):
        # First teardown succeeds
        assert teardown_empty_live_worktree(wt_dir, "feat/live-race", repo_root) is True
        assert not wt_dir.exists()

        # Second sequential/concurrent teardown of already deleted directory succeeds idempotently
        assert teardown_empty_live_worktree(wt_dir, "feat/live-race", repo_root) is True
