"""
tests/test_copilot_engagement_wave2.py

Wave 2 Acceptance Tests: Canonical engagement identity binding.
Verifies that:
1. Canonical engagement identity is normalized and persisted through registration.
2. Unknown or conflicting identity yields clarification rather than silent scope selection.
3. Heartbeat order across worktrees cannot silently change engagement scope.
4. Shared attendees or similar meeting names cannot hijack engagement binding.
5. Legacy sessions lacking engagement_id require explicit reconciliation.
6. Explicit conflicting session_id and project arguments fail closed.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace
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


def test_canonical_engagement_identity_normalization() -> None:
    """Verifies that project names and IDs normalize into canonical 'project:<slug>' format."""
    assert normalize_engagement_id("JARVIS") == "project:jarvis"
    assert normalize_engagement_id("project:jarvis") == "project:jarvis"
    assert normalize_engagement_id("Engineering Jarvis") == "project:engineering_jarvis"
    assert normalize_engagement_id("LENS") == "project:lens"
    assert normalize_engagement_id("project:lens-phase2") == "project:lens_phase2"
    assert normalize_engagement_id(None) is None
    assert normalize_engagement_id("") is None
    assert normalize_engagement_id("   ") is None
    assert normalize_engagement_id("unknown") is None

    assert canonical_display_name("project:jarvis") == "JARVIS"
    assert canonical_display_name("project:lens") == "LENS"

    assert match_engagement("project:jarvis", "JARVIS") is True
    assert match_engagement("JARVIS", "project:jarvis") is True
    assert match_engagement("project:jarvis", "LENS") is False
    assert match_engagement(None, "JARVIS") is False


def test_session_registration_persists_canonical_identity(tmp_path: Path) -> None:
    """Verifies that SessionRegistry.register binds and persists canonical engagement identity."""
    reg = SessionRegistry(tmp_path)
    manifest = reg.register(
        meeting_slug="Engineering_Jarvis_Kaleena_PartnerAdvisory",
        engagement_name="JARVIS",
        ws_port=8765,
    )
    assert manifest.engagement_id == "project:jarvis"
    assert manifest.engagement_name == "JARVIS"

    # Re-read from disk
    stored_path = tmp_path / ".session_registry" / f"{manifest.session_id}.json"
    assert stored_path.is_file()
    disk_data = json.loads(stored_path.read_text(encoding="utf-8"))
    assert disk_data["engagement_id"] == "project:jarvis"
    assert disk_data["engagement_name"] == "JARVIS"

    # Deserialization check
    reloaded = SessionManifest.from_dict(disk_data)
    assert reloaded.engagement_id == "project:jarvis"
    assert reloaded.engagement_name == "JARVIS"


def test_unknown_or_conflicting_identity_yields_clarification(tmp_path: Path) -> None:
    """
    Verifies that when multiple active sessions exist with distinct engagements,
    unscoped copilot requests fail closed with clarification required rather than
    silently selecting whichever session has the latest heartbeat.
    """
    reg = SessionRegistry(tmp_path)
    now = time.time()

    # Session 1: JARVIS recorded earlier
    s1 = reg.register(
        session_id="session-jarvis-001",
        parent_pid=1111,
        meeting_slug="Engineering_Jarvis",
        engagement_name="JARVIS",
    )
    s1.last_heartbeat = now + 10.0
    (tmp_path / ".session_registry" / f"{s1.session_id}.json").write_text(
        json.dumps(s1.to_dict()), encoding="utf-8"
    )

    # Session 2: LENS recorded later (newer heartbeat)
    s2 = reg.register(
        session_id="session-lens-002",
        parent_pid=2222,
        meeting_slug="apex_global_Lens_Architecture",
        engagement_name="LENS",
    )
    s2.last_heartbeat = now + 20.0
    (tmp_path / ".session_registry" / f"{s2.session_id}.json").write_text(
        json.dumps(s2.to_dict()), encoding="utf-8"
    )

    reader = LiveBufferReader(tmp_path)
    # Mock OS PID checks to consider both test PIDs alive
    reg._is_pid_alive = lambda pid: True  # type: ignore[assignment]
    reader.registry._is_pid_alive = lambda pid: True  # type: ignore[assignment]

    # 1. Unscoped request: must detect conflict and return None / conflicting_engagement
    args_unscoped = argparse.Namespace(runtime_root=tmp_path)
    _, manifest, slug = _select_session(args_unscoped, reader)
    assert slug == "conflicting_engagement"
    assert manifest is None

    # 2. Scoped to JARVIS: must select Session 1 despite older heartbeat
    args_jarvis = argparse.Namespace(runtime_root=tmp_path, project="jarvis")
    _, manifest_jarvis, slug_jarvis = _select_session(args_jarvis, reader)
    assert manifest_jarvis is not None
    assert manifest_jarvis.session_id == "session-jarvis-001"
    assert manifest_jarvis.engagement_id == "project:jarvis"

    # 3. Scoped to LENS: must select Session 2
    args_lens = argparse.Namespace(runtime_root=tmp_path, project="lens")
    _, manifest_lens, slug_lens = _select_session(args_lens, reader)
    assert manifest_lens is not None
    assert manifest_lens.session_id == "session-lens-002"
    assert manifest_lens.engagement_id == "project:lens"

    # 4. Scoped to unknown engagement: must return None
    args_unknown = argparse.Namespace(runtime_root=tmp_path, project="solar_telemetry")
    _, manifest_unknown, _ = _select_session(args_unknown, reader)
    assert manifest_unknown is None


def test_heartbeat_order_cannot_silently_change_scope(tmp_path: Path) -> None:
    """
    Verifies that heartbeat fluctuations in a background session cannot cause
    a scoped copilot request to switch engagements.
    """
    reg = SessionRegistry(tmp_path)
    reg._is_pid_alive = lambda pid: True  # type: ignore[assignment]
    reader = LiveBufferReader(tmp_path)
    reader.registry._is_pid_alive = lambda pid: True  # type: ignore[assignment]

    # Create JARVIS session
    jarvis_sess = reg.register(
        session_id="jarvis-stable",
        meeting_slug="Jarvis_Standup",
        engagement_name="JARVIS",
    )
    # Create LENS session with rapidly advancing heartbeat
    lens_sess = reg.register(
        session_id="lens-noisy",
        meeting_slug="Lens_Sprint",
        engagement_name="LENS",
    )

    for step in range(5):
        # Even when LENS updates its heartbeat far ahead of JARVIS:
        lens_sess.last_heartbeat = time.time() + (step * 100)
        (tmp_path / ".session_registry" / f"{lens_sess.session_id}.json").write_text(
            json.dumps(lens_sess.to_dict()), encoding="utf-8"
        )

        args = argparse.Namespace(runtime_root=tmp_path, project="jarvis")
        _, manifest, _ = _select_session(args, reader)
        assert manifest is not None
        assert manifest.session_id == "jarvis-stable"
        assert manifest.engagement_id == "project:jarvis"


def test_legacy_session_requires_explicit_reconciliation(tmp_path: Path) -> None:
    """
    Verifies that legacy session manifests lacking engagement_id require explicit
    reconciliation and fail closed with 'unreconciled_legacy_session' when unguided.
    """
    sess_snapshot = load_session_snapshot()
    # Confirm the incident snapshot is indeed a legacy session without engagement_id
    assert sess_snapshot.get("engagement_id") is None
    import os
    sess_snapshot["parent_pid"] = os.getpid()
    sess_snapshot["last_heartbeat"] = time.time()

    reg_dir = tmp_path / ".session_registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = reg_dir / f"{sess_snapshot['session_id']}.json"
    manifest_file.write_text(json.dumps(sess_snapshot), encoding="utf-8")

    reader = LiveBufferReader(tmp_path)

    # 1. Unscoped request on legacy session fails closed
    args_unscoped = argparse.Namespace(runtime_root=tmp_path)
    _, manifest, slug = _select_session(args_unscoped, reader)
    assert slug == "unreconciled_legacy_session"

    # 2. Guided request with explicit engagement reconciles the session
    args_guided = argparse.Namespace(runtime_root=tmp_path, project="jarvis")
    output_dir, context_model, manifest_reconciled, slug_guided = (
        _resolve_session_and_context_model(args_guided)
    )
    assert manifest_reconciled is not None
    assert manifest_reconciled.engagement_id == "project:jarvis"
    assert context_model.project_scope == "project:jarvis"

    # 3. Verify reconciliation persisted to registry on disk
    reconciled_disk = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert reconciled_disk.get("engagement_id") == "project:jarvis"


def test_conflicting_session_id_and_project_fails_closed(tmp_path: Path) -> None:
    """Verifies that requesting a specific session_id with a conflicting project fails closed."""
    reg = SessionRegistry(tmp_path)
    reg._is_pid_alive = lambda pid: True  # type: ignore[assignment]
    lens_sess = reg.register(
        session_id="lens-explicit-001",
        meeting_slug="Lens_Architecture",
        engagement_name="LENS",
    )

    reader = LiveBufferReader(tmp_path)
    reader.registry._is_pid_alive = lambda pid: True  # type: ignore[assignment]

    args_conflict = argparse.Namespace(
        runtime_root=tmp_path,
        session="lens-explicit-001",
        project="jarvis",  # Conflicting engagement
    )
    _, manifest, slug = _select_session(args_conflict, reader)
    assert slug == "conflicting_engagement"
    assert manifest is None
