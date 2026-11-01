import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    teardown_empty_live_worktree,
    _worktree_has_audio_or_commits,
    _resolve_primary_repo_root,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def test_teardown_empty_live_worktree_success(tmp_path):
    """Verifies that an empty worktree is cleanly removed and its branch deleted."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    wt_dir = repo_root / ".worktrees" / "live_standup_amit"
    wt_dir.mkdir(parents=True)

    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git") as mock_git:
        mock_git.return_value = ""
        success = teardown_empty_live_worktree(
            worktree_dir=wt_dir,
            branch_name="feat/live-standup-amit",
            repo_root=repo_root,
        )
        assert success is True
        assert not wt_dir.exists()
        # Verify git worktree remove and branch -D were invoked
        invocations = [call[0][0] for call in mock_git.call_args_list]
        assert any("worktree" in cmd and "remove" in cmd for cmd in invocations)
        assert any("branch" in cmd and "-D" in cmd for cmd in invocations)


def test_teardown_preserves_worktree_with_audio(tmp_path):
    """Verifies that a worktree containing captured audio chunks is protected from teardown."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    wt_dir = repo_root / ".worktrees" / "live_standup_amit"
    wt_dir.mkdir(parents=True)
    audio_file = wt_dir / "chunk_000.wav"
    audio_file.write_bytes(b"\x00\x01" * 100)

    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git") as mock_git:
        success = teardown_empty_live_worktree(
            worktree_dir=wt_dir,
            branch_name="feat/live-standup-amit",
            repo_root=repo_root,
        )
        assert success is False
        assert wt_dir.exists()
        assert audio_file.exists()


def test_teardown_preserves_worktree_with_commits(tmp_path):
    """Verifies that a worktree branch with commits ahead of main is protected from teardown."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    wt_dir = repo_root / ".worktrees" / "live_standup_amit"
    wt_dir.mkdir(parents=True)

    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git") as mock_git:
        def fake_git(cmd, **kwargs):
            if cmd[:2] == ["log", "main..feat/live-standup-amit"]:
                return "abc1234 feat: captured notes"
            return ""
        mock_git.side_effect = fake_git

        success = teardown_empty_live_worktree(
            worktree_dir=wt_dir,
            branch_name="feat/live-standup-amit",
            repo_root=repo_root,
        )
        assert success is False
        assert wt_dir.exists()


def test_teardown_refuses_to_delete_root(tmp_path):
    """Verifies that teardown refuses to delete the primary repository or caller root."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    success = teardown_empty_live_worktree(
        worktree_dir=repo_root,
        branch_name="main",
        repo_root=repo_root,
    )
    assert success is False
    assert repo_root.exists()


def test_recorder_residual_chunks_no_microsecond_tag(tmp_path):
    """Verifies that existing residual chunks are archived in place without minting ms_tag branches."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    chunks_dir = (
        repo_root / ".worktrees" / "live_standup_amit" / "output" / "transcript_cache" / "sandbox" / "10-09-2026" / ".chunks_Standup_Amit"
    )
    chunks_dir.mkdir(parents=True)
    residual_chunk = chunks_dir / "chunk_000.wav"
    residual_chunk.write_bytes(b"\x00" * 50)

    with patch("dual_pipeline.live_meeting_recorder.ROOT", repo_root), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", return_value=""), \
         patch("dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree", return_value=False), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree", return_value=False), \
         patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder.materialize_worktree"):

        rec = LiveMeetingRecorder(
            project_name="sandbox",
            meeting_name="Standup_Amit",
            mock_mode=True,
            immediate_worktree=True,
            enforce_worktree=True,
        )

        # Invariant: branch name must not have microsecond suffix (e.g. -160532_943)
        assert not any(part.isdigit() and len(part) == 6 for part in rec.worktree_branch.split("_"))
        assert "_943" not in rec.worktree_branch
        assert rec.worktree_branch == "feat/live-standup_amit"

        # Verify residual chunk was moved to archive and not in active chunks
        assert not residual_chunk.exists()
        archives = list(chunks_dir.glob(".stale_*"))
        assert len(archives) == 1
        assert (archives[0] / "chunk_000.wav").exists()
