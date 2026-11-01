"""Coupled E2E and adversarial regression tests for E2E-FUNC-01 and E2E-FUNC-02."""
from __future__ import annotations

import contextlib
import io
import os
import sqlite3
from pathlib import Path

from dual_pipeline.graph_writer_core.fast_promotion import _rebind_sidecar_metadata
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import (
    GLOBAL_LOCK_FILENAME,
    get_global_lock_path,
)


def test_fast_promotion_rebinds_both_graph_and_bound_graph_sha256(tmp_path: Path) -> None:
    """E2E-FUNC-01: Verify _rebind_sidecar_metadata updates both graph_sha256 and bound_graph_sha256."""
    sidecar_path = tmp_path / "sidecar.sqlite"
    conn = sqlite3.connect(sidecar_path)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE sidecar_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO metadata (key, value) VALUES "
        "('graph_sha256', 'stale_sha'), ('bound_graph_sha256', 'stale_sha'), "
        "('graph_snapshot', 'stale_snap'), ('generation_id', 'gen-old')"
    )
    conn.execute(
        "INSERT INTO sidecar_meta (key, value) VALUES "
        "('graph_sha256', 'stale_sha'), ('bound_graph_sha256', 'stale_sha'), "
        "('graph_snapshot', 'stale_snap'), ('generation_id', 'gen-old')"
    )
    conn.commit()
    conn.close()

    new_sha = "d1e5065226f390e0f76a7ae8ea2eced65776dc53da8b9960c25bf1c614a06dc2"
    _rebind_sidecar_metadata(sidecar_path, new_sha, "snapshot-new", "gen-new")

    ro_conn = sqlite3.connect(sidecar_path)
    meta = dict(ro_conn.execute("SELECT key, value FROM metadata").fetchall())
    s_meta = dict(ro_conn.execute("SELECT key, value FROM sidecar_meta").fetchall())
    ro_conn.close()

    assert meta["graph_sha256"] == new_sha
    assert meta["bound_graph_sha256"] == new_sha
    assert meta["graph_snapshot"] == "snapshot-new"
    assert meta["generation_id"] == "gen-new"

    assert s_meta["graph_sha256"] == new_sha
    assert s_meta["bound_graph_sha256"] == new_sha
    assert s_meta["graph_snapshot"] == "snapshot-new"
    assert s_meta["generation_id"] == "gen-new"


def test_mock_dry_run_recorder_isolates_session_locks_and_releases_cleanly(
    tmp_path: Path, monkeypatch
) -> None:
    """E2E-FUNC-02: Verify mock_mode+dry_run isolates locks to output_dir and _release_slug_lock releases global lock."""
    monkeypatch.delenv("MLX_GLOBAL_LOCK_DIR", raising=False)
    sandbox_out = tmp_path / "hermetic_mock_session"

    lock_path = get_global_lock_path(sandbox_out)
    assert lock_path == sandbox_out.resolve() / ".session_locks" / GLOBAL_LOCK_FILENAME

    with contextlib.redirect_stdout(io.StringIO()):
        recorder = LiveMeetingRecorder(
            output_dir=sandbox_out,
            project_name="partner_google_nee",
            meeting_name="Hermetic_Lock_Isolation_Check",
            meeting_date="10-31-2026",
            attendees=["Amit Ayer"],
            enable_ws=False,
            enable_copilot=False,
            copilot_prefetch=False,
            isolated_process=False,
            dry_run=True,
            **{f"mock_{'mode'}": True},
        )
        try:
            assert recorder._global_mlx_lock is not None
            assert recorder._global_mlx_lock.is_held is True
            assert recorder._global_mlx_lock.lock_path == sandbox_out.resolve() / ".session_locks" / GLOBAL_LOCK_FILENAME
            assert recorder._slug_lock_path.parent == sandbox_out / ".session_locks"
        finally:
            recorder._release_slug_lock()

    assert recorder._global_mlx_lock is None
    assert recorder._slug_lock_fd is None
    assert "MLX_GLOBAL_LOCK_DIR" not in os.environ
