"""
tests/adversarial/test_copilot_engagement_wave2_adversarial.py

Adversarial and edge-case tests for Wave 2 Copilot engagement identity binding.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (null, whitespace, mixed delimiters, punctuation)
2. Malformed input (corrupted manifest files in .session_registry, invalid JSON, missing fields)
3. Invariant bypass attempt (mismatched meeting_dir and project, session_id/project conflict)
4. Concurrency & conflict (multiple active sessions across engagements, fail-closed resolution)
"""
from __future__ import annotations

import argparse
import json
import os
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


# ==============================================================================
# 1. Boundary Payload Category
# ==============================================================================
def test_adversarial_boundary_payload_engagement_normalization() -> None:
    """Verifies edge case handling in normalize_engagement_id with irregular payloads."""
    # None and empty
    assert normalize_engagement_id(None) is None
    assert normalize_engagement_id("") is None
    assert normalize_engagement_id("   \t\n  ") is None

    # Reserved null indicators
    assert normalize_engagement_id("none") is None
    assert normalize_engagement_id("None") is None
    assert normalize_engagement_id("null") is None
    assert normalize_engagement_id("unknown") is None

    # Delimiter variants
    assert normalize_engagement_id("  PROJECT:jarvis  ") == "project:jarvis"
    assert normalize_engagement_id("project:JARVIS") == "project:jarvis"
    assert normalize_engagement_id("Jarvis-Core-Alpha") == "project:jarvis_core_alpha"
    assert normalize_engagement_id("Jarvis  Core__Beta") == "project:jarvis__core__beta"

    # Display name edge cases
    assert canonical_display_name(None) == "UNKNOWN"
    assert canonical_display_name("") == "UNKNOWN"
    assert canonical_display_name("unknown") == "UNKNOWN"
    assert canonical_display_name("project:apex_global_lens_v2") == "apex_global_LENS_V2"


# ==============================================================================
# 2. Malformed Input Category
# ==============================================================================
def test_adversarial_malformed_session_manifest_files(tmp_path: Path) -> None:
    """
    Verifies that malformed, truncated, or non-JSON files in .session_registry/
    do not crash the buffer reader or corrupt active session enumeration.
    """
    reg_dir = tmp_path / ".session_registry"
    reg_dir.mkdir(parents=True, exist_ok=True)

    # 1. Zero-byte file
    (reg_dir / "zero_byte.json").write_text("", encoding="utf-8")

    # 2. Truncated JSON
    (reg_dir / "truncated.json").write_text('{"session_id": "abc', encoding="utf-8")

    # 3. Valid JSON but wrong schema (missing required fields)
    (reg_dir / "wrong_schema.json").write_text(
        json.dumps({"some_unrelated_key": 123}), encoding="utf-8"
    )

    # 4. Valid manifest
    valid_reg = SessionRegistry(tmp_path)
    valid_m = valid_reg.register(
        session_id="valid-session-123",
        meeting_slug="Valid_Meeting",
        engagement_name="JARVIS",
        parent_pid=os.getpid(),
    )

    reader = LiveBufferReader(tmp_path)
    active = reader.list_active_sessions()
    # Malformed files must be safely ignored without throwing exceptions
    assert len(active) == 1
    assert active[0].session_id == valid_m.session_id


# ==============================================================================
# 3. Invariant Bypass Attempt Category
# ==============================================================================
def test_adversarial_invariant_bypass_mismatched_meeting_dir_and_project(tmp_path: Path) -> None:
    """
    Adversarial test: An operator attempts to pass a meeting directory belonging to LENS,
    but simultaneously specifies --project jarvis.
    The system must fail closed with 'conflicting_engagement' and refuse to construct context.
    """
    repo_root = tmp_path / "repo"
    lens_dir = repo_root / "output" / "01_lens_meeting"
    lens_dir.mkdir(parents=True, exist_ok=True)

    reg = SessionRegistry(repo_root)
    lens_manifest = reg.register(
        session_id="lens-session-bypass-attempt",
        meeting_slug="01_lens_meeting",
        engagement_name="LENS",
        parent_pid=os.getpid(),
        output_dir="output/01_lens_meeting",
    )

    reader = LiveBufferReader(repo_root)

    # Attempt to access LENS meeting directory while claiming project is JARVIS
    args = argparse.Namespace(
        runtime_root=repo_root,
        meeting_dir=str(lens_dir),
        project="jarvis",
        active=True,
    )

    out_dir, ctx_model, manifest, slug = _resolve_session_and_context_model(args, reader=reader)
    assert slug == "conflicting_engagement"
    assert manifest is None
    assert out_dir is None
    assert ctx_model.focus_turns() == []


def test_adversarial_invariant_bypass_mismatched_session_id_and_project(tmp_path: Path) -> None:
    """
    Adversarial test: An operator passes a specific session_id belonging to LENS,
    but specifies --project jarvis.
    The system must fail closed with 'conflicting_engagement'.
    """
    repo_root = tmp_path / "repo"
    reg = SessionRegistry(repo_root)
    lens_manifest = reg.register(
        session_id="lens-session-id-target",
        meeting_slug="Lens_Architecture",
        engagement_name="LENS",
        parent_pid=os.getpid(),
    )

    reader = LiveBufferReader(repo_root)
    args = argparse.Namespace(
        runtime_root=repo_root,
        session="lens-session-id-target",
        project="jarvis",
    )

    out_dir, ctx_model, manifest, slug = _resolve_session_and_context_model(args, reader=reader)
    assert slug == "conflicting_engagement"
    assert manifest is None
    assert out_dir is None


# ==============================================================================
# 4. Concurrency & Ambiguity Conflict Category
# ==============================================================================
def test_adversarial_three_concurrent_engagements_fail_closed_without_scope(tmp_path: Path) -> None:
    """
    Adversarial test: 3 concurrent active sessions across distinct engagements
    (JARVIS, LENS, SHERPA).
    An unguided request must detect the three-way conflict and fail closed.
    """
    repo_root = tmp_path / "repo"
    reg = SessionRegistry(repo_root)

    t0 = time.time()
    for i, name in enumerate(["JARVIS", "LENS", "SHERPA"]):
        m = reg.register(
            session_id=f"session-tri-{name.lower()}",
            meeting_slug=f"Meeting_{name}",
            engagement_name=name,
            parent_pid=os.getpid(),
        )
        m.last_heartbeat = t0 + (i * 10)
        (repo_root / ".session_registry" / f"{m.session_id}.json").write_text(
            json.dumps(m.to_dict()), encoding="utf-8"
        )

    reader = LiveBufferReader(repo_root)
    all_active = reader.list_active_sessions()
    assert len(all_active) == 3

    conflicts = detect_engagement_conflicts(all_active)
    assert conflicts is not None
    assert conflicts == {"project:jarvis", "project:lens", "project:sherpa"}

    args = argparse.Namespace(runtime_root=repo_root)
    out_dir, ctx, manifest, slug = _resolve_session_and_context_model(args, reader=reader)
    assert slug == "conflicting_engagement"
    assert manifest is None
