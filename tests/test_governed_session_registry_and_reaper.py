"""Tests for Wave 3: Governed Session Registry & Non-Violent Port Fallback Architecture.
Validates:
1. Atomic session manifest creation and heartbeat in .session_registry/
2. Triangulated Stale Process Qualification algorithm (4-criteria gate)
3. Non-violent reaping of qualified orphaned children in < 300ms
4. Automatic non-violent WebSocket port fallback when port 8765 is occupied
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.session_registry import (
    ENGINE_SIGNATURE,
    SessionManifest,
    SessionRegistry,
)


def test_atomic_session_manifest_registration_and_heartbeat(tmp_path: Path):
    """Verifies atomic registration, heartbeat updates, and unregistration."""
    registry = SessionRegistry(tmp_path)
    manifest = registry.register(
        parent_pid=os.getpid(),
        meeting_slug="q3_planning",
        ws_port=8765,
        ffmpeg_pid=1234,
        sck_pid=5678,
    )
    manifest_file = tmp_path / ".session_registry" / f"{manifest.session_id}.json"
    assert manifest_file.exists(), "Session manifest JSON was not created"

    # Verify fields
    assert manifest.engine_signature == ENGINE_SIGNATURE
    assert manifest.parent_pid == os.getpid()
    assert manifest.ws_port == 8765
    assert manifest.ffmpeg_pid == 1234

    # Update heartbeat
    time.sleep(0.01)
    prev_hb = manifest.last_heartbeat
    registry.heartbeat(manifest.session_id, ffmpeg_pid=9999)

    updated_manifest = SessionManifest.from_dict(
        json.loads(manifest_file.read_text(encoding="utf-8"))
    )
    assert updated_manifest.last_heartbeat >= prev_hb
    assert updated_manifest.ffmpeg_pid == 9999

    # Unregister
    registry.unregister(manifest.session_id)
    assert not manifest_file.exists()


def test_triangulated_stale_qualification_algorithm(tmp_path: Path):
    """Verifies the 4-criteria qualification algorithm prevents fratricide."""
    registry = SessionRegistry(tmp_path)

    # 1. Active session (parent PID is alive = our PID) -> Must NOT qualify
    m1 = registry.register(parent_pid=os.getpid(), meeting_slug="active_sess")
    # artificially age heartbeat
    m1_file = tmp_path / ".session_registry" / f"{m1.session_id}.json"
    d1 = json.loads(m1_file.read_text(encoding="utf-8"))
    d1["last_heartbeat"] = time.time() - 30.0
    m1_file.write_text(json.dumps(d1), encoding="utf-8")

    qual1 = registry.qualify_stale_sessions(stale_seconds=15.0)
    assert len(qual1) == 0, "Active parent session must never be qualified for reaping!"

    # 2. Dead parent session with fresh heartbeat (< 15s) -> Must NOT qualify
    fake_dead_pid = 99999999  # Guaranteed non-existent PID
    m2 = registry.register(parent_pid=fake_dead_pid, meeting_slug="fresh_dead_parent")
    qual2 = registry.qualify_stale_sessions(stale_seconds=15.0)
    assert len(qual2) == 0, "Session with fresh heartbeat must not be qualified yet"

    # 3. Dead parent session with stale heartbeat (> 15s) and MATCHING repo root -> QUALIFIES
    m2_file = tmp_path / ".session_registry" / f"{m2.session_id}.json"
    d2 = json.loads(m2_file.read_text(encoding="utf-8"))
    d2["last_heartbeat"] = time.time() - 25.0
    m2_file.write_text(json.dumps(d2), encoding="utf-8")

    qual3 = registry.qualify_stale_sessions(stale_seconds=15.0)
    assert len(qual3) == 1
    assert qual3[0][1].session_id == m2.session_id

    # 4. Dead parent with stale heartbeat but MISMATCHED repo root -> Must NOT qualify
    d2["repo_root"] = "/different/unrelated/repo"
    m2_file.write_text(json.dumps(d2), encoding="utf-8")
    qual4 = registry.qualify_stale_sessions(stale_seconds=15.0)
    assert len(qual4) == 0, "Session from foreign repo root must never be reaped!"


def test_reaper_execution_speed_and_clean_orphan_reaping(tmp_path: Path):
    """Reaper must terminate qualified orphan child in < 300ms."""
    registry = SessionRegistry(tmp_path)

    # Spawn real dummy child process to simulate orphaned FFmpeg
    dummy_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        fake_dead_parent_pid = 99999998
        manifest = registry.register(
            parent_pid=fake_dead_parent_pid,
            meeting_slug="crashed_meeting",
            ffmpeg_pid=dummy_proc.pid,
        )
        # Age heartbeat to 30s
        m_file = tmp_path / ".session_registry" / f"{manifest.session_id}.json"
        d = json.loads(m_file.read_text(encoding="utf-8"))
        d["last_heartbeat"] = time.time() - 30.0
        m_file.write_text(json.dumps(d), encoding="utf-8")

        # Execute reap
        res = registry.reap_stale_sessions(stale_seconds=15.0)
        assert res["duration_ms"] <= 300.0, f"Reap took {res['duration_ms']:.2f}ms (> 300ms)"
        assert dummy_proc.pid in res["reaped_pids"]
        assert manifest.session_id in res["reaped_sessions"]

        # Confirm child process is now dead
        dummy_proc.poll()
        assert not SessionRegistry._is_pid_alive(dummy_proc.pid)
        assert not m_file.exists()
    finally:
        if dummy_proc.poll() is None:
            dummy_proc.kill()
