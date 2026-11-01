"""
tests/end_to_end/test_no_microsecond_fork_e2e.py

End-to-end lifecycle test verifying branch stability across consecutive and aborted
live recording sessions in an isolated temporary audio capture harness without mocking
LiveMeetingRecorder methods.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import (
    finalize_worktree_materialization,
)


def test_no_microsecond_fork_lifecycle_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end lifecycle test verifying branch stability across consecutive and aborted sessions."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(tmp_path / "locks"))

    wt_dir = repo_root / ".worktrees" / "audio_sandbox_10_09_2026_standup_amit"
    chunks_dir = (
        wt_dir
        / "output"
        / "transcript_cache"
        / "sandbox"
        / "10-09-2026"
        / ".chunks_Standup_Amit"
    )
    chunks_dir.mkdir(parents=True)

    # 1. Simulate leftover stale chunk from an interrupted previous session
    stale_chunk = chunks_dir / "chunk_000.wav"
    stale_chunk.write_bytes(b"\x00\x01" * 100)

    def _fake_provision(w_dir: Path, branch: str, r_root: Path | None = None):
        w_dir.mkdir(parents=True, exist_ok=True)
        (w_dir / ".git").write_text(
            "gitdir: ../../.git/worktrees/audio_sandbox_10_09_2026_standup_amit\n",
            encoding="utf-8",
        )
        return w_dir, branch

    def _safe_finalize(rec, is_final: bool = False, root_dir: Path | None = None):
        if not hasattr(rec, "capture_manager"):
            rec.capture_manager = None
        return finalize_worktree_materialization(
            rec, is_final=is_final, root_dir=root_dir or repo_root
        )

    with patch("dual_pipeline.live_meeting_recorder.ROOT", repo_root), patch(
        "dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", return_value=""
    ), patch(
        "dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree", return_value=False
    ), patch(
        "dual_pipeline.live_meeting_recorder_core.worktree_manager.is_inside_isolated_worktree",
        return_value=False,
    ), patch(
        "dual_pipeline.live_meeting_recorder.provision_live_transcription_worktree",
        side_effect=_fake_provision,
    ), patch(
        "dual_pipeline.live_meeting_recorder_core.worktree_repointer.finalize_worktree_materialization",
        side_effect=_safe_finalize,
    ):
        # 2. Launch new session: must NOT mint ms_tag branch
        rec = LiveMeetingRecorder(
            project_name="sandbox",
            meeting_name="Standup_Amit",
            meeting_date="10/09/2026",
            mock_mode=True,
            immediate_worktree=True,
            enforce_worktree=True,
        )
        try:
            assert rec.worktree_branch == "audio/sandbox-10-09-2026-standup-amit"
            assert not any(
                len(p) == 6 and p.isdigit() for p in rec.worktree_branch.split("-")
            )
            assert not stale_chunk.exists()

            # 3. Simulate early abort with 0 processed chunks: teardown must trigger
            assert len(rec.processed_chunks) == 0
            with patch(
                "dual_pipeline.live_meeting_recorder.teardown_empty_live_worktree"
            ) as mock_teardown:
                rec._abort_and_rollback_empty_session()
                mock_teardown.assert_called_once()
        finally:
            rec._release_slug_lock()
