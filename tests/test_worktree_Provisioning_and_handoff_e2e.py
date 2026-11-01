"""
End-to-End test suite for Wave 2: Canonical CLI Contract, Worktree Inheritance, & RBAC Approval Gate.
Verifies BUG-07 through BUG-10 across primary repo root and isolated live worktree execution.
"""
from __future__ import annotations

import shlex
import struct
import wave
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.canonical_pipeline.lifecycle import resolve_routing
from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.cli.ingest import (
    _assert_no_speaker_collapse,
    _resolve_ingest_rbac_authorization,
    _validate_canonical_input,
    build_canonical_metadata,
)
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def _write_pcm_wav(path: Path, n_samples: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([250] * n_samples)))


def test_recorder_emits_valid_cli_command_with_time_and_no_mutate_graph(tmp_path: Path) -> None:
    """BUG-07: LiveMeetingRecorder stop lifecycle emits --time and omits unrecognized --mutate-graph."""
    rec = LiveMeetingRecorder(
        project_name="project_lens",
        meeting_name="Ed / Amit",
        meeting_date="10-16-2026",
        attendees="Eduardo More, Amit Ayer",
        output_dir=tmp_path / "output",
        mock_mode=True,
    )
    try:
        c0 = rec.chunks_dir / "chunk_00000.wav"
        _write_pcm_wav(c0, 16000)
        rec.process_chunk(c0, 0)
        rec.stop()

        cmd = rec.canonical_ingest_command
        assert "--mutate-graph" not in cmd
        assert "--time " in cmd
        assert "--reviewer" not in cmd
        assert "--rationale" not in cmd

        parser = build_parser()
        args = parser.parse_args(shlex.split(cmd)[1:])
        assert args.project == "project_lens"
        assert args.meeting == "Ed / Amit"
        assert args.time is not None and "ET" in args.time
    finally:
        rec._release_slug_lock()


def test_handoff_from_repo_root_and_inside_live_worktree_without_nesting(tmp_path: Path) -> None:
    """BUG-08, BUG-09, BUG-10: Emitted command resolves input and inherits live_<slug> from both roots."""
    repo_root = tmp_path / "repo"
    wt_dir = repo_root / ".worktrees" / "live_10_16_2026_ed_amit"
    cache_dir = wt_dir / "output" / "transcript_cache" / "project_lens" / "10-16-2026"
    meeting_dir = wt_dir / "output" / "project_lens" / "10-16-2026" / "10-16-2026_Ed_Amit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    meeting_dir.mkdir(parents=True, exist_ok=True)
    (wt_dir / ".git").write_text("gitdir: /tmp/fake", encoding="utf-8")

    master_wav = cache_dir / "00_10-16-2026_Ed_Amit_master.wav"
    _write_pcm_wav(master_wav, 16000)

    rel_input = Path("output/project_lens/10-16-2026/10-16-2026_Ed_Amit/00_10-16-2026_Ed_Amit_master.wav")
    cmd = build_canonical_ingest_command(
        input_path=rel_input,
        project="project_lens",
        meeting="Ed / Amit",
        date="10-16-2026",
        attendees="Eduardo More, Amit Ayer",
        reviewer="operator",
        filename_slug="10-16-2026_Ed_Amit",
        worktree_dir=Path(".worktrees/live_10_16_2026_ed_amit"),
        time="10:34 AM - 11:07 AM ET",
    )

    parser = build_parser()
    args = parser.parse_args(shlex.split(cmd)[1:])

    # 1. Execute from repo_root where rel_input does not exist directly, resolved via worktree_dir discovery
    with patch("pathlib.Path.cwd", return_value=repo_root), \
         patch("dual_pipeline.core.ROOT", repo_root), \
         patch("dual_pipeline.canonical_pipeline.lifecycle.ROOT", repo_root), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-10-16-2026-ed-amit"):
        resolved_in = _validate_canonical_input(args)
        assert resolved_in.resolve() == master_wav.resolve()

        meta = build_canonical_metadata(args)
        _resolve_ingest_rbac_authorization(args, getattr(args, "reviewer", None), getattr(args, "rationale", None), meta)
        assert meta["rbac_auth"]["auth_type"] == "DEFERRED_HITL_APPROVAL"
        assert "approval_receipt" not in meta

        _assert_no_speaker_collapse(str(resolved_in), meta)
        target_from_root = resolve_routing(meta=meta, output_dir="auto")
        assert target_from_root.resolve() == meeting_dir.resolve()

    # 2. Execute from inside wt_dir itself: must NOT create .worktrees/live_.../.worktrees/live_...
    args_inside = parser.parse_args(shlex.split(cmd)[1:])
    args_inside.input = str(master_wav)
    with patch("pathlib.Path.cwd", return_value=wt_dir), \
         patch("dual_pipeline.core.ROOT", wt_dir), \
         patch("dual_pipeline.canonical_pipeline.lifecycle.ROOT", wt_dir), \
         patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value="feat/live-10-16-2026-ed-amit"):
        meta_inside = build_canonical_metadata(args_inside)
        target_inside = resolve_routing(meta=meta_inside, output_dir="auto")
        assert target_inside.resolve() == meeting_dir.resolve()
        assert not (wt_dir / ".worktrees").exists()
        assert len(list((repo_root / ".worktrees").iterdir())) == 1
