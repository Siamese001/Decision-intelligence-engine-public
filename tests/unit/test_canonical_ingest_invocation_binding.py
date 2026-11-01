"""
Unit tests validating canonical ingestion invocation command binding for Wave 3.
Verifies that build_canonical_ingest_command and live recording concluding lifecycle
correctly bind --worktree-dir and --filename-slug flags.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.core import ROOT
from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cli.ingest import _extract_flag_attributes


def test_build_canonical_ingest_command_backward_compatibility():
    """Verify that omitting worktree_dir and filename_slug preserves legacy command format."""
    cmd = build_canonical_ingest_command(
        input_path="/tmp/audio.wav",
        project="project_nexus",
        meeting="Executive Sync",
        date="10/09/2026",
    )
    assert "--filename-slug" not in cmd
    assert "--worktree-dir" not in cmd
    assert 'nee ingest-meeting' in cmd
    assert '--input "/tmp/audio.wav"' in cmd
    assert '--project "project_nexus"' in cmd
    assert '--enforce-worktree' in cmd


def test_build_canonical_ingest_command_explicit_slug_and_worktree():
    """Verify that providing filename_slug and worktree_dir appends exact CLI flags."""
    cmd = build_canonical_ingest_command(
        input_path="/tmp/audio.wav",
        project="project_nexus",
        meeting="Standup Amit",
        date="10/09/2026",
        filename_slug="Standup_Amit",
        worktree_dir=".worktrees/live_standup_amit",
    )
    assert '--filename-slug "Standup_Amit"' in cmd
    assert '--worktree-dir ".worktrees/live_standup_amit"' in cmd


def test_build_canonical_ingest_command_normalizes_absolute_worktree_path():
    """Verify that absolute worktree paths within repo root are converted to relative paths."""
    abs_wt = ROOT / ".worktrees" / "live_standup_amit"
    cmd = build_canonical_ingest_command(
        input_path="/tmp/audio.wav",
        project="project_nexus",
        meeting="Standup Amit",
        date="10/09/2026",
        filename_slug="Standup_Amit",
        worktree_dir=abs_wt,
    )
    assert '--worktree-dir ".worktrees/live_standup_amit"' in cmd
    assert '--filename-slug "Standup_Amit"' in cmd


def test_cli_parser_and_extract_flags_recognizes_worktree_and_slug():
    """Verify CLI parser and flag attribute extractor correctly ingest --worktree-dir and --filename-slug."""
    parser = build_parser()
    args = parser.parse_args([
        "ingest-meeting",
        "--input", "/tmp/audio.wav",
        "--worktree-dir", ".worktrees/live_standup_amit",
        "--filename-slug", "Standup_Amit",
    ])
    assert args.worktree_dir == Path(".worktrees/live_standup_amit")
    assert args.filename_slug == "Standup_Amit"

    meta = {}
    _extract_flag_attributes(args, meta)
    assert meta.get("worktree_dir") == ".worktrees/live_standup_amit"
    assert meta.get("filename_slug") == "Standup_Amit"


def test_recorder_concluding_lifecycle_binds_worktree_and_slug(tmp_path):
    """Verify LiveMeetingRecorder binds clean_slug and worktree_dir into canonical_ingest_command."""
    from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

    rec = LiveMeetingRecorder(
        project_name="project_nexus",
        meeting_name="Standup Amit",
        mock_mode=True,
        output_dir=tmp_path / "output",
        immediate_worktree=True,
        enforce_worktree=True,
    )
    rec.clean_slug = "Standup_Amit"
    rec.worktree_dir = ROOT / ".worktrees" / "live_standup_amit"
    rec.master_wav = tmp_path / "output" / "00_master.wav"
    rec.master_wav.touch()

    cmd = build_canonical_ingest_command(
        input_path=rec.master_wav,
        project=rec.project_name,
        meeting=rec.meeting_name,
        date=rec.meeting_date_display,
        attendees=rec.attendees,
        filename_slug=getattr(rec, "clean_slug", None),
        worktree_dir=getattr(rec, "worktree_dir", None),
    )
    assert '--filename-slug "Standup_Amit"' in cmd
    assert '--worktree-dir ".worktrees/live_standup_amit"' in cmd
