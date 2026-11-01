import pytest
from pathlib import Path
from unittest.mock import patch

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


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payload - empty string, empty attendees, 40-char boundaries."""
    # Empty string fallback
    assert standardize_meeting_slug("") == "Meeting"
    assert standardize_meeting_slug("   ") == "Meeting"
    assert standardize_meeting_slug("", attendees=[]) == "Meeting"
    assert extract_slug_from_meeting_filename("") is None
    assert extract_slug_from_meeting_filename("    ") is None

    # Slug truncation at exactly max_len
    long_name = "Extremely_Long_Executive_Strategy_And_Architecture_Alignment_Session_2026"
    slug_40 = standardize_meeting_slug(long_name, max_len=40)
    assert len(slug_40) <= 40

    # Invariant: Slug does not end in trailing underscore
    assert not slug_40.endswith("_")


def test_adversarial_malformed_inputs():
    """Category 2: Malformed input - path traversal attempts, unusual attendee structures, malformed filenames."""
    # Path traversal attack in filename
    traversal_fn = "../../../etc/passwd/00_10-09-2026_Exploit_Attempt_master.wav"
    extracted = extract_slug_from_meeting_filename(traversal_fn)
    assert extracted == "Exploit_Attempt"
    # Ensure standardizer sanitizes any path-traversal tokens
    clean = standardize_meeting_slug(traversal_fn)
    assert "/" not in clean and "\\" not in clean and ".." not in clean

    # Malformed attendee types
    assert standardize_meeting_slug("Standup", attendees=[123, None, {}]) == "Standup"
    assert standardize_meeting_slug("Standup", attendees="not a valid json structure") == "Standup_Not"

    # Malformed filename lacking prefix or date
    assert extract_slug_from_meeting_filename("00_invalid_date_Standup_master.wav") is None
    assert extract_slug_from_meeting_filename("not_even_a_meeting.txt") is None


def test_adversarial_invariant_bypass_and_duplicate_worktree_prevention(tmp_path):
    """Category 3: Invariant bypass attempt - repeated ingestion with mutating rosters attempting to fork worktrees."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    wt_dir = repo_root / ".worktrees" / "live_standup_amit"
    meeting_dir = wt_dir / "output" / "proj_x" / "10-09-2026" / "Standup_Amit"
    meeting_dir.mkdir(parents=True)
    audio_wav = meeting_dir / "00_10-09-2026_Standup_Amit_master.wav"
    audio_wav.write_text("audio")

    # Attacker passes conflicting title and mutated attendee list to force a secondary worktree
    adversarial_meta = {
        "input_path": str(audio_wav),
        "meeting_name": "Totally_Different_Title_Standup",
        "attendees": ["Amit Ayer", "Nozim", "Speaker_Beta", "Speaker_Alpha", "NewAttendee1", "NewAttendee2"],
    }

    with patch("dual_pipeline.canonical_pipeline.lifecycle.ROOT", repo_root), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_live_transcription_worktree") as mock_ensure, \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-standup_amit"):

        # Execute routing
        target = calculate_target_path(
            output_dir="auto",
            proj="proj_x",
            date_fn="10-09-2026",
            slug="Standup_Amit_Nozim",
            no_subfolder=False,
            modality=None,
            meta=adversarial_meta,
        )

        # Invariant: Must NOT provision a secondary worktree
        mock_ensure.assert_not_called()
        # Invariant: Must bind strictly to existing worktree
        assert target == meeting_dir
        assert adversarial_meta["worktree_dir"] == str(wt_dir)
        assert adversarial_meta["worktree_branch"] == "feat/live-standup_amit"
        assert adversarial_meta["filename_slug"] == "Standup_Amit"
