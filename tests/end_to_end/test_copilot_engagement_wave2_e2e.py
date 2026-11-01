"""
tests/end_to_end/test_copilot_engagement_wave2_e2e.py

End-to-End acceptance tests for Wave 2:
Verifies the complete lifecycle of canonical engagement identity binding:
1. Session registration under LiveMeetingRecorder / SessionRegistry with canonical identity.
2. Disk persistence in .session_registry/ and candidate discovery across candidate registries.
3. Copilot session resolution and context model construction with scoped project binding.
4. Multi-worktree concurrent session isolation: LENS vs JARVIS.
5. End-to-end legacy session reconciliation with persisted manifest update.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.cli.copilot_session import (
    _resolve_session_and_context_model,
    _select_session,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
    LiveBufferReader,
)
from dual_pipeline.live_meeting_recorder_core.session_identity import (
    canonical_display_name,
    detect_engagement_conflicts,
    match_engagement,
    normalize_engagement_id,
    reconcile_legacy_session,
)
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)
from tests.fixtures.copilot_engagement import load_session_snapshot


def test_e2e_session_registration_and_scoped_resolution_lifecycle(tmp_path: Path) -> None:
    """
    E2E Lifecycle:
    Registers a new meeting session with explicit engagement, verifies disk persistence,
    reads it back via LiveBufferReader, and resolves copilot context model.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    registry = SessionRegistry(repo_root)
    # Register session for JARVIS engagement
    manifest = registry.register(
        meeting_slug="Jarvis_Architecture_Sync",
        engagement_name="JARVIS",
        parent_pid=os.getpid(),
        ws_port=8765,
    )
    assert manifest.engagement_id == "project:jarvis"
    assert manifest.engagement_name == "JARVIS"

    # Verify manifest file exists on disk
    manifest_file = repo_root / ".session_registry" / f"{manifest.session_id}.json"
    assert manifest_file.is_file()
    disk_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert disk_data["engagement_id"] == "project:jarvis"
    assert disk_data["engagement_name"] == "JARVIS"

    # Reader discovers session
    reader = LiveBufferReader(repo_root)
    active_sessions = reader.list_active_sessions()
    assert len(active_sessions) == 1
    assert active_sessions[0].session_id == manifest.session_id

    # Resolve copilot session with matching engagement
    args = argparse.Namespace(runtime_root=repo_root, project="jarvis")
    output_dir, context_model, resolved_manifest, slug = _resolve_session_and_context_model(
        args, reader=reader
    )
    assert resolved_manifest is not None
    assert resolved_manifest.session_id == manifest.session_id
    assert resolved_manifest.engagement_id == "project:jarvis"
    assert context_model.project_scope == "project:jarvis"
    assert slug == "Jarvis_Architecture_Sync"


def test_e2e_multi_engagement_isolation_across_concurrent_sessions(tmp_path: Path) -> None:
    """
    E2E Multi-Engagement Isolation:
    Two active sessions exist concurrently (JARVIS and LENS).
    Verifies that:
    1. Unscoped copilot requests fail closed to prevent silent cross-engagement hijacking.
    2. JARVIS scoped request selects only the JARVIS session regardless of heartbeat.
    3. LENS scoped request selects only the LENS session.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    registry = SessionRegistry(repo_root)

    t0 = time.time()
    # JARVIS session created first
    jarvis_manifest = registry.register(
        session_id="e2e-jarvis-session-001",
        meeting_slug="Engineering_Jarvis",
        engagement_name="JARVIS",
        parent_pid=os.getpid(),
    )
    jarvis_manifest.last_heartbeat = t0
    (repo_root / ".session_registry" / f"{jarvis_manifest.session_id}.json").write_text(
        json.dumps(jarvis_manifest.to_dict()), encoding="utf-8"
    )

    # LENS session created second with later heartbeat
    lens_manifest = registry.register(
        session_id="e2e-lens-session-002",
        meeting_slug="apex_global_Lens_Review",
        engagement_name="LENS",
        parent_pid=os.getpid(),
    )
    lens_manifest.last_heartbeat = t0 + 100.0
    (repo_root / ".session_registry" / f"{lens_manifest.session_id}.json").write_text(
        json.dumps(lens_manifest.to_dict()), encoding="utf-8"
    )

    reader = LiveBufferReader(repo_root)

    # 1. Unscoped copilot request fails closed
    args_unscoped = argparse.Namespace(runtime_root=repo_root)
    _, _, resolved_unscoped, slug_unscoped = _resolve_session_and_context_model(
        args_unscoped, reader=reader
    )
    assert slug_unscoped == "conflicting_engagement"
    assert resolved_unscoped is None

    # 2. Scoped to JARVIS binds solely to JARVIS
    args_jarvis = argparse.Namespace(runtime_root=repo_root, project="jarvis")
    _, context_jarvis, resolved_jarvis, slug_jarvis = _resolve_session_and_context_model(
        args_jarvis, reader=reader
    )
    assert resolved_jarvis is not None
    assert resolved_jarvis.session_id == "e2e-jarvis-session-001"
    assert resolved_jarvis.engagement_id == "project:jarvis"
    assert context_jarvis.project_scope == "project:jarvis"

    # 3. Scoped to LENS binds solely to LENS
    args_lens = argparse.Namespace(runtime_root=repo_root, project="lens")
    _, context_lens, resolved_lens, slug_lens = _resolve_session_and_context_model(
        args_lens, reader=reader
    )
    assert resolved_lens is not None
    assert resolved_lens.session_id == "e2e-lens-session-002"
    assert resolved_lens.engagement_id == "project:lens"
    assert context_lens.project_scope == "project:lens"


def test_e2e_legacy_session_reconciliation_and_persistence(tmp_path: Path) -> None:
    """
    E2E Legacy Migration:
    Takes an unmigrated legacy session manifest snapshot (from the incident),
    places it in .session_registry/, confirms that an unscoped request fails closed,
    reconciles it via explicit --project jarvis, and confirms on-disk persistence.
    """
    repo_root = tmp_path / "legacy_repo"
    reg_dir = repo_root / ".session_registry"
    reg_dir.mkdir(parents=True, exist_ok=True)

    snapshot = load_session_snapshot()
    assert snapshot.get("engagement_id") is None
    snapshot["parent_pid"] = os.getpid()
    snapshot["last_heartbeat"] = time.time()

    sid = snapshot["session_id"]
    manifest_file = reg_dir / f"{sid}.json"
    manifest_file.write_text(json.dumps(snapshot), encoding="utf-8")

    reader = LiveBufferReader(repo_root)

    # Step 1: Unscoped fails closed
    args_unscoped = argparse.Namespace(runtime_root=repo_root)
    _, _, unmigrated, slug_unscoped = _resolve_session_and_context_model(
        args_unscoped, reader=reader
    )
    assert slug_unscoped == "unreconciled_legacy_session"

    # Step 2: Guided request reconciles and writes back to disk
    args_guided = argparse.Namespace(runtime_root=repo_root, project="jarvis")
    _, ctx_guided, reconciled, slug_guided = _resolve_session_and_context_model(
        args_guided, reader=reader
    )
    assert reconciled is not None
    assert reconciled.engagement_id == "project:jarvis"
    assert reconciled.engagement_name == "JARVIS"
    assert ctx_guided.project_scope == "project:jarvis"

    # Step 3: Verify disk file was updated
    disk_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert disk_data["engagement_id"] == "project:jarvis"
    assert disk_data["engagement_name"] == "JARVIS"
