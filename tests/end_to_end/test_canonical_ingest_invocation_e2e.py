"""
End-to-End test suite for canonical ingestion invocation binding.
Validates the full lifecycle from command emission through CLI execution and target path routing.
"""
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.canonical_pipeline.lifecycle import resolve_routing
from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.cli.ingest import (
    _assert_no_speaker_collapse,
    _resolve_ingest_rbac_authorization,
    _validate_canonical_input,
    build_canonical_metadata,
)
from dual_pipeline.cli.parser import build_parser


def test_canonical_ingest_invocation_e2e(tmp_path):
    """
    Simulates the complete end-to-end flow:
    1. A live meeting recorder concludes in .worktrees/live_standup_amit.
    2. It formats the canonical command with --worktree-dir, --filename-slug, and --time.
    3. The command is parsed against the production CLI parser with zero unrecognized flags.
    4. Ingestion detects the existing worktree (from both repo_root and inside the worktree),
       locks the slug, defers RBAC approval to HITL Invariant 9, skips binary WAV in collapse check,
       and resolves routing without creating any secondary branches or nested worktrees.
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    worktrees_dir = repo_root / ".worktrees"
    worktrees_dir.mkdir()

    wt_dir = worktrees_dir / "live_standup_amit"
    meeting_output_dir = wt_dir / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit"
    meeting_output_dir.mkdir(parents=True)
    master_wav = meeting_output_dir / "00_10-09-2026_Standup_Amit_master.wav"
    master_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32)

    cmd_str = build_canonical_ingest_command(
        input_path=master_wav,
        project="project_nexus",
        meeting="Standup Amit",
        date="10-09-2026",
        attendees=["Amit Ayer", "Nozim", "Speaker_Beta", "Speaker_Alpha"],
        reviewer="operator",
        filename_slug="Standup_Amit",
        worktree_dir=wt_dir,
        time="10:34 AM - 11:07 AM ET",
    )

    assert '--filename-slug "Standup_Amit"' in cmd_str
    assert f'--worktree-dir "{wt_dir}"' in cmd_str
    assert '--time "10:34 AM - 11:07 AM ET"' in cmd_str
    assert "--mutate-graph" not in cmd_str
    assert "--enforce-worktree" in cmd_str

    cli_tokens = shlex.split(cmd_str)[1:]
    parser = build_parser()
    args = parser.parse_args(cli_tokens)

    assert args.worktree_dir == wt_dir
    assert args.filename_slug == "Standup_Amit"
    assert args.time == "10:34 AM - 11:07 AM ET"

    validated_input = _validate_canonical_input(args)
    assert validated_input == master_wav

    metadata = build_canonical_metadata(args)
    assert metadata["worktree_dir"] == str(wt_dir)
    assert metadata["filename_slug"] == "Standup_Amit"
    assert metadata["time"] == "10:34 AM - 11:07 AM ET"

    _resolve_ingest_rbac_authorization(args, args.reviewer, args.rationale, metadata)
    assert metadata["rbac_auth"]["auth_type"] == "DEFERRED_HITL_APPROVAL"
    assert metadata["rbac_auth"]["awaiting_interactive_approval"] is True
    assert "approval_receipt" not in metadata

    _assert_no_speaker_collapse(str(master_wav), metadata)

    with patch("dual_pipeline.core.ROOT", repo_root), \
         patch("dual_pipeline.canonical_pipeline.lifecycle.ROOT", repo_root), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.ensure_ingest_worktree") as mock_ensure, \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-standup_amit"):

        resolved_path = resolve_routing(
            meta=metadata,
            output_dir="auto",
        )

        assert resolved_path == meeting_output_dir
        assert metadata["filename_slug"] == "Standup_Amit"
        assert metadata["worktree_dir"] == str(wt_dir)
        assert metadata["worktree_branch"] == "feat/live-standup_amit"
        mock_ensure.assert_not_called()

    worktree_entries = list(worktrees_dir.iterdir())
    assert len(worktree_entries) == 1
    assert worktree_entries[0].name == "live_standup_amit"
    assert not (wt_dir / ".worktrees").exists()

