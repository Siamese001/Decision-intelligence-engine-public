import argparse
import pytest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.ingest import _handle_dry_run_ingest, build_canonical_metadata
from dual_pipeline.canonical_pipeline.lifecycle import resolve_routing, calculate_target_path


def test_worktree_slug_inheritance_lifecycle_e2e(tmp_path):
    """End-to-end test simulating complete canonical ingestion invocation with audio inside worktree."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    worktrees_dir = repo_root / ".worktrees"
    worktrees_dir.mkdir()

    # Step 1: Simulate active live recording worktree
    wt_dir = worktrees_dir / "live_standup_amit"
    meeting_output_dir = wt_dir / "output" / "project_lens" / "10-09-2026" / "Standup_Amit"
    meeting_output_dir.mkdir(parents=True)
    audio_file = meeting_output_dir / "00_10-09-2026_Standup_Amit_master.wav"
    audio_file.write_text("RIFF....WAVEfmt ....data....")

    # Step 2: Formulate CLI args for ingestion with expanded attendees roster
    args = argparse.Namespace(
        input=str(audio_file),
        source=None,
        project="project_lens",
        meeting="Standup",
        date="10-09-2026",
        attendees='["Amit Ayer", "Nozim", "Speaker_Beta", "Speaker_Alpha"]',
        enforce_worktree=True,
        dry_run=True,
        package_only=True,
        output_dir="auto",
        provider="llm",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        directive_id="directive-test-01",
    )

    metadata = build_canonical_metadata(args)
    assert metadata["input_path"] == str(audio_file)
    assert len(metadata["attendees"]) == 4

    # Step 3: Run dry-run ingest with mocked git branch return
    with patch("dual_pipeline.core.ROOT", repo_root), \
         patch("dual_pipeline.canonical_pipeline.lifecycle.ROOT", repo_root), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree") as mock_ensure, \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-standup_amit"):

        dry_run_result = _handle_dry_run_ingest(
            args=args,
            input_val=str(audio_file),
            metadata=metadata,
            enforce_wt=True,
            directive_id="directive-test-01",
            reviewer="operator",
            should_mutate=False,
            eff_model="gpt-5.6-sol",
            reasoning_effort="high",
        )

        assert dry_run_result["status"] == "PASS"
        assert dry_run_result["worktree_dir"] == str(wt_dir)
        assert dry_run_result["worktree_branch"] == "feat/live-standup_amit"
        mock_ensure.assert_not_called()

        # Step 4: Resolve routing and target path as the pipeline would
        out_target = resolve_routing(
            meta=metadata,
            output_dir="auto",
        )

        assert out_target == meeting_output_dir
        assert metadata["filename_slug"] == "Standup_Amit"
        assert metadata["worktree_dir"] == str(wt_dir)
        assert metadata["worktree_branch"] == "feat/live-standup_amit"
        mock_ensure.assert_not_called()
