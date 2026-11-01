"""Unit tests for Phase 2: Copilot session discovery, shadow registry bypassing,
canonical path resolution, and readiness states.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.cli.copilot_session import (
    _read_turns_from_jsonl,
    _select_session,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
    LiveBufferReader,
    _parse_jsonl_turns,
)
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)


def test_shadow_registry_bypassing(tmp_path: Path):
    """An empty registry beneath a runtime root must not shadow a valid recorder session."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()

    # Active session registered in canonical repo root
    canon_reg = SessionRegistry(repo_root)
    manifest = canon_reg.register(
        session_id="session-active-123",
        meeting_slug="project_discussion",
        output_dir="output/project_discussion",
        capture_state="CAPTURING",
        asr_state="READY",
        context_state="FRESH",
        copilot_state="READY",
    )

    # Empty shadow registry beneath .local/dual_pipeline
    runtime_root = repo_root / ".local" / "dual_pipeline"
    shadow_reg_dir = runtime_root / ".session_registry"
    shadow_reg_dir.mkdir(parents=True)

    # Reader initialized pointing to the runtime_root
    reader = LiveBufferReader(runtime_root)
    active = reader.get_active_session()

    assert active is not None
    assert active.session_id == "session-active-123"
    assert active.copilot_state == "READY"


def test_symlink_canonical_path_resolution(tmp_path: Path):
    """Symlinked repository paths must resolve to physical canonical directories."""
    real_repo = tmp_path / "real_repo"
    real_repo.mkdir()
    (real_repo / ".git").mkdir()

    symlink_repo = tmp_path / "symlink_repo"
    os.symlink(real_repo, symlink_repo)

    reg = SessionRegistry(real_repo)
    manifest = reg.register(
        session_id="session-symlink-456",
        meeting_slug="symlink_meeting",
        output_dir="output/symlink_meeting",
    )

    # Read from symlink path
    reader = LiveBufferReader(symlink_repo)
    found = reader.get_session("session-symlink-456")
    assert found is not None
    assert found.session_id == manifest.session_id


def test_explicit_session_discovery_across_worktrees(tmp_path: Path):
    """Explicit session ID lookup must search parent and child worktree registries deterministically."""
    primary_repo = tmp_path / "primary"
    primary_repo.mkdir()
    (primary_repo / ".git").mkdir()

    wt_repo = primary_repo / ".worktrees" / "feature_branch"
    wt_repo.mkdir(parents=True)
    (wt_repo / ".git").write_text("gitdir: primary", encoding="utf-8")

    # Session registered in worktree
    wt_reg = SessionRegistry(wt_repo)
    manifest = wt_reg.register(
        session_id="session-wt-789",
        meeting_slug="wt_meeting",
        output_dir="output/wt_meeting",
    )

    # Reader querying from primary repo
    reader = LiveBufferReader(primary_repo)
    found = reader.get_session("session-wt-789")
    assert found is not None
    assert found.session_id == "session-wt-789"
    assert found.meeting_slug == "wt_meeting"


def test_partial_jsonl_turn_preservation(tmp_path: Path):
    """Preserves complete structured turns when a concurrent append leaves an incomplete final record."""
    segments_file = tmp_path / "segments.jsonl"
    turn1 = {"speaker": "Alice", "normalized": "Hello team", "timestamp": "00:00:01", "start_sec": 1.0}
    turn2 = {"speaker": "Bob", "normalized": "Reviewing system architecture", "timestamp": "00:00:05", "start_sec": 5.0}

    # Write 2 valid lines, then a truncated 3rd line
    raw_content = (
        json.dumps(turn1) + "\n" +
        json.dumps(turn2) + "\n" +
        '{"speaker": "Charlie", "normalized": "Mid-write incomp'  # Truncated
    )
    segments_file.write_text(raw_content, encoding="utf-8")

    turns = _parse_jsonl_turns(segments_file)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Alice"
    assert turns[0]["text"] == "Hello team"
    assert turns[1]["speaker"] == "Bob"
    assert turns[1]["text"] == "Reviewing system architecture"


def test_session_readiness_state_machine(tmp_path: Path):
    """Verifies orthogonal readiness states publish and update cleanly in session manifest."""
    reg = SessionRegistry(tmp_path)
    manifest = reg.register(
        session_id="session-state-test",
        meeting_slug="state_meeting",
        capture_state="STARTING",
        asr_state="WARMING",
        context_state="INITIALIZING",
        copilot_state="NOT_READY",
    )
    assert manifest.capture_state == "STARTING"
    assert manifest.asr_state == "WARMING"
    assert manifest.context_state == "INITIALIZING"
    assert manifest.copilot_state == "NOT_READY"

    # Advance state via heartbeat
    reg.heartbeat(
        session_id="session-state-test",
        capture_state="CAPTURING",
        asr_state="READY",
        context_state="FRESH",
        copilot_state="READY",
        force=True,
    )

    updated = reg.inspect("session-state-test")
    assert updated is not None
    assert updated.capture_state == "CAPTURING"
    assert updated.asr_state == "READY"
    assert updated.context_state == "FRESH"
    assert updated.copilot_state == "READY"


def test_initializing_session_does_not_default_to_repo_root(tmp_path: Path):
    """When output_dir is not yet set in an initializing manifest, _select_session returns output_dir=None."""
    reg = SessionRegistry(tmp_path)
    reg.register(
        session_id="session-init-only",
        meeting_slug="init_meeting",
        output_dir=None,
        copilot_state="NOT_READY",
    )

    reader = LiveBufferReader(tmp_path)
    mock_args = MagicMock()
    mock_args.meeting_dir = None
    mock_args.session = "session-init-only"

    out_dir, manifest, slug = _select_session(mock_args, reader)
    assert manifest is not None
    assert out_dir is None
    assert slug == "init_meeting"


def test_audio_capture_state_reporting(tmp_path: Path):
    """LiveAudioCaptureManager exposes capture_state and get_lifecycle_status."""
    from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager

    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    master_wav = tmp_path / "master.wav"

    mgr = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="auto",
        mock_mode=True,
    )

    assert mgr.capture_state == "STOPPED"
    status = mgr.get_lifecycle_status()
    assert status["capture_state"] == "STOPPED"
    assert status["is_running"] is False
    assert status["has_startup_failure"] is False

    mgr.start(run_proof=False)
    assert mgr.capture_state == "CAPTURING"
    assert mgr.get_lifecycle_status()["capture_state"] == "CAPTURING"

    mgr.stop()
    assert mgr.capture_state == "STOPPED"
