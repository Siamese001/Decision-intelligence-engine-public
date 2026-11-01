import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from dual_pipeline.meeting_paths import (
    extract_slug_from_meeting_filename,
    standardize_meeting_slug,
    _is_attendee_slug_redundant,
)
from dual_pipeline.canonical_pipeline.lifecycle import (
    _inherit_worktree_from_input_path,
    calculate_target_path,
    resolve_routing,
)


def test_extract_slug_from_meeting_filename():
    """Validates regex extraction of established meeting slugs from canonical filenames."""
    assert extract_slug_from_meeting_filename("00_10-09-2026_Standup_Amit_master.wav") == "Standup_Amit"
    assert extract_slug_from_meeting_filename("00_10-09-2026_Sprint_Planning_master_1.wav") == "Sprint_Planning"
    assert extract_slug_from_meeting_filename("01_10-09-2026_Weekly_Sync_live_transcript.md") == "Weekly_Sync"
    assert extract_slug_from_meeting_filename("00_10-09-2026_Project_Kickoff_segments.json") == "Project_Kickoff"
    assert extract_slug_from_meeting_filename("random_recording.wav") is None


def test_attendee_slug_redundancy_with_partial_token_overlap():
    """Validates that attendee tokens overlapping with scope prevent attendee appending."""
    assert _is_attendee_slug_redundant("Amit_Nozim", "Standup_Amit") is True
    assert _is_attendee_slug_redundant("Amit", "Standup_Amit") is True
    assert _is_attendee_slug_redundant("Sarah_Client", "Standup_Amit") is False


def test_standardize_meeting_slug_invariance_on_attendee_expansion():
    """Validates that slug remains stable even when attendees expand from 1 to 4."""
    # Step 1: Initial recording with 1 attendee
    initial_slug = standardize_meeting_slug("LENS Standup", attendees=["Amit Ayer"])
    assert initial_slug == "Standup_Amit"

    # Step 2: Canonical ingestion with 4 attendees, passing initial_slug
    expanded_attendees = ["Amit Ayer", "Nozim", "Speaker_Beta", "Speaker_Alpha"]
    ingest_slug_explicit = standardize_meeting_slug(
        "LENS Standup", attendees=expanded_attendees, initial_slug=initial_slug
    )
    assert ingest_slug_explicit == "Standup_Amit"

    # Step 3: Canonical ingestion passing audio filename
    ingest_slug_filename = standardize_meeting_slug(
        "00_10-09-2026_Standup_Amit_master.wav", attendees=expanded_attendees
    )
    assert ingest_slug_filename == "Standup_Amit"

    # Step 4: Canonical ingestion passing scope that already contains attendee token
    ingest_slug_overlap = standardize_meeting_slug(
        "Standup_Amit", attendees=expanded_attendees
    )
    assert ingest_slug_overlap == "Standup_Amit"


def test_inherit_worktree_from_input_path(tmp_path):
    """Validates inheritance of worktree directory and branch from input_path metadata."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    wt_dir = repo_root / ".worktrees" / "live_standup_amit"
    wt_dir.mkdir(parents=True)
    audio_file = wt_dir / "output" / "proj" / "10-09-2026" / "Standup_Amit" / "00_10-09-2026_Standup_Amit_master.wav"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_text("mock pcm")

    meta = {"input_path": str(audio_file)}
    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-standup_amit"):
        res = _inherit_worktree_from_input_path(meta, repo_root)
        assert res is not None
        res_dir, res_branch = res
        assert res_dir == wt_dir
        assert res_branch == "feat/live-standup_amit"


def test_calculate_target_path_inherits_worktree(tmp_path):
    """Validates calculate_target_path routes to existing worktree without reprovisioning."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    wt_dir = repo_root / ".worktrees" / "live_standup_amit"
    target_existing = wt_dir / "output" / "proj" / "10-09-2026" / "Standup_Amit"
    target_existing.mkdir(parents=True)
    audio_file = target_existing / "00_10-09-2026_Standup_Amit_master.wav"
    audio_file.write_text("mock")

    meta = {
        "input_path": str(audio_file),
        "meeting_name": "Standup",
        "attendees": ["Amit Ayer", "Nozim", "Speaker_Beta", "Speaker_Alpha"],
    }

    with patch("dual_pipeline.canonical_pipeline.lifecycle.ROOT", repo_root), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree") as mock_ensure, \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-standup_amit"):

        target = calculate_target_path(
            output_dir="auto",
            proj="proj",
            date_fn="10-09-2026",
            slug="Standup_Amit_Nozim",
            no_subfolder=False,
            modality=None,
            meta=meta,
        )

        # Ensure provisioner was NOT called
        mock_ensure.assert_not_called()
        # Verify target is inside inherited worktree
        assert target == target_existing
        assert meta["worktree_dir"] == str(wt_dir)
        assert meta["worktree_branch"] == "feat/live-standup_amit"
        assert meta["filename_slug"] == "Standup_Amit"
