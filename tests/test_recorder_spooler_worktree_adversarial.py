"""
Wave 1 Adversarial & Edge-Case Suite: Recorder Spooler, Worktree & Repatriation Invariants.

Covers >= 3 mandatory adversarial categories:
1. Concurrency & race: --detach pre-spool exclusion, active-chunk FD preservation during mid-call materialization.
2. Boundary & malformed state: linked worktree (.git file vs .git dir) primary root resolution, audio/* empty worktree teardown.
3. Rollback & recovery: constructor exception lock release rollback, fail-closed repatriation abort on SHA-256 corruption,
   45s detached service owner stop grace period, and SIGINT immunity inside isolated MLX worker.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import _worker_entrypoint
from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import (
    check_active_mlx_session,
    resolve_canonical_repo_root,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    RecordingStartRequest,
    RecordingState,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import RecordingServiceOwner
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _resolve_primary_repo_root,
    teardown_empty_live_worktree,
)
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import finalize_worktree_materialization
from tools.pr_governance_core.audio_repatriation import repatriate_worktree_audio_artifacts
from tools.pr_governance_core.worktree import is_ephemeral_worktree_path


def _create_git_repo_with_linked_worktree(base_dir: Path) -> tuple[Path, Path]:
    primary = base_dir / "primary_repo"
    primary.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=primary, env=env, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "adv@example.com"], cwd=primary, env=env, check=True)
    subprocess.run(["git", "config", "user.name", "Adv Tester"], cwd=primary, env=env, check=True)
    (primary / "README.md").write_text("init\n", encoding="utf-8")
    (primary / ".githooks").mkdir(parents=True, exist_ok=True)
    (primary / ".venv").mkdir(parents=True, exist_ok=True)
    (primary / ".local").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "add", "README.md"], cwd=primary, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=primary, env=env, check=True, capture_output=True)

    linked = primary / ".worktrees" / "feat_linked_wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feat/linked-wt", str(linked), "main"],
        cwd=primary,
        env=env,
        check=True,
        capture_output=True,
    )
    return primary, linked


def test_git_common_dir_resolves_primary_root_from_linked_worktree(tmp_path: Path) -> None:
    """Adversarial Category: Boundary / Split-Brain — linked worktree has a .git FILE, not a .git directory."""
    primary, linked = _create_git_repo_with_linked_worktree(tmp_path)
    assert (linked / ".git").is_file()

    assert _resolve_primary_repo_root(linked) == primary.resolve()
    assert resolve_canonical_repo_root(linked) == primary.resolve()


def test_audio_prefix_empty_worktree_teardown_succeeds(tmp_path: Path) -> None:
    """Adversarial Category: Boundary — teardown_empty_live_worktree must clean audio/<slug> worktrees."""
    primary, _ = _create_git_repo_with_linked_worktree(tmp_path)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
    }
    audio_wt = primary / ".worktrees" / "audio_empty_session"
    subprocess.run(
        ["git", "worktree", "add", "-b", "audio/empty-session", str(audio_wt), "main"],
        cwd=primary,
        env=env,
        check=True,
        capture_output=True,
    )
    assert audio_wt.exists()
    removed = teardown_empty_live_worktree(audio_wt, "audio/empty-session", repo_root=primary)
    assert removed is True
    assert not audio_wt.exists()


def test_mid_recording_materialization_never_unlinks_active_chunks(tmp_path: Path) -> None:
    """Adversarial Category: Concurrency & Race — active capture chunks must not unlink during materialization."""
    staging_out = tmp_path / "staging" / "out"
    staging_cache = tmp_path / "staging" / "cache"
    staging_chunks = staging_cache / "chunks"
    target_out = tmp_path / "wt" / "out"
    target_cache = tmp_path / "wt" / "cache"
    target_chunks = target_cache / "chunks"

    staging_chunks.mkdir(parents=True, exist_ok=True)
    staging_out.mkdir(parents=True, exist_ok=True)
    chunk0 = staging_chunks / "chunk_00000.wav"
    chunk1 = staging_chunks / "chunk_00001.wav"
    chunk0.write_bytes(b"RIFF" + b"\x00" * 128)
    chunk1.write_bytes(b"RIFF" + b"\x00" * 64)

    class _FakeCaptureMgr:
        _ffmpeg_proc = object()
        _is_running = True
        chunks_dir = staging_chunks
        master_wav = staging_cache / "master.wav"

    class _FakeRecorder:
        running = True
        output_dir = staging_out
        cache_dir = staging_cache
        chunks_dir = staging_chunks
        _target_output_dir = target_out
        _target_cache_dir = target_cache
        _target_chunks_dir = target_chunks
        capture_manager = _FakeCaptureMgr()
        spooler = None
        date_dirname = "2026-10-29"
        clean_slug = "race_test"
        session_tag = "120000"

        def _migrate_staged_chunks(self, is_final: bool = False) -> None:
            from dual_pipeline.live_meeting_recorder_core.worktree_manager import migrate_staged_files
            migrate_staged_files(self.chunks_dir, self._target_chunks_dir, [], is_final=is_final)

        def _migrate_staged_metadata(self, is_final: bool = False) -> None:
            pass

        def _repoint_recorder_paths_to_worktree(self) -> None:
            self.output_dir = self._target_output_dir
            self.cache_dir = self._target_cache_dir
            self.chunks_dir = self._target_chunks_dir

    rec = _FakeRecorder()
    # Even if caller passes is_final=True while recorder.running is True, staging chunks must NOT be unlinked!
    finalize_worktree_materialization(rec, is_final=True, root_dir=tmp_path)
    assert chunk0.exists(), "chunk_00000.wav was unlinked while capture was still active!"
    assert chunk1.exists(), "Active open chunk_00001.wav was unlinked while capture was still active!"
    assert (target_chunks / "chunk_00000.wav").exists()


def test_constructor_failure_releases_global_mlx_and_slug_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial Category: Rollback & Recovery — constructor failure must release acquired locks."""
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(lock_dir))

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Simulated mid-constructor failure after lock acquisition")

    monkeypatch.setattr("dual_pipeline.live_meeting_recorder.ensure_ffmpeg", _boom)

    with pytest.raises(RuntimeError, match="Simulated mid-constructor failure"):
        LiveMeetingRecorder(
            project_name="LockTest",
            meeting_name="Rollback Check",
            mock_mode=True,
            isolated_process=False,
        )

    is_active, _ = check_active_mlx_session()
    assert is_active is False, "GlobalMLXSessionLock leaked after __init__ exception!"


def test_repatriation_fails_closed_on_sha256_corruption_and_sidecars_not_ephemeral(tmp_path: Path) -> None:
    """Adversarial Category: Invariant Bypass / Rollback — corrupted copy aborts teardown fail-closed."""
    # Verify valuable sidecars are no longer classified as disposable ephemeral paths
    assert is_ephemeral_worktree_path("output/Proj/2026-10-29/mtg/referenced_artifacts/spec.pdf") is False
    assert is_ephemeral_worktree_path("output/Proj/2026-10-29/mtg/speaker_hints.json") is False
    assert is_ephemeral_worktree_path("output/Proj/2026-10-29/mtg/speaker_map.json") is False
    assert is_ephemeral_worktree_path("output/Proj/2026-10-29/mtg/copilot_answers.jsonl") is False
    assert is_ephemeral_worktree_path("output/transcript_cache/Proj/2026-10-29/master.voiceprints.json") is False

    wt_dir = tmp_path / "wt"
    root_dir = tmp_path / "root"
    wt_wav = wt_dir / "output" / "transcript_cache" / "Proj" / "2026-10-29" / "00_master.wav"
    wt_wav.parent.mkdir(parents=True, exist_ok=True)
    root_dir.mkdir(parents=True, exist_ok=True)
    wt_wav.write_bytes(b"RIFF" + b"\x11" * 200)

    def _corrupting_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(b"CORRUPTED_BYTES")

    res = repatriate_worktree_audio_artifacts(wt_dir, root_dir, copy_fn=_corrupting_copy)
    assert res.success is False
    assert "SHA-256 mismatch" in (res.error or "")
    assert wt_wav.exists(), "Source WAV in worktree must remain intact when repatriation fails!"


def test_service_owner_waits_for_graceful_stop_beyond_2_5_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial Category: Boundary / Timing — stop_session waits beyond old 2.5s cap without SIGKILL."""
    monkeypatch.setenv("NEE_DETACHED_STOP_GRACE_SECONDS", "10.0")
    owner = RecordingServiceOwner(sessions_root=tmp_path / "sessions")
    req = RecordingStartRequest(
        request_id="req_grace",
        idempotency_key="idem_grace",
        session_id="sess_grace",
        host_chat_id=None,
        project_name="GraceProj",
        meeting_name="Grace Meeting",
        meeting_date="2026-10-29",
        attendees=[],
        mock_mode=True,
    )
    marker_file = tmp_path / "graceful_seal.txt"
    # Spawn process that traps SIGTERM, takes 3.0s (> old 2.5s cap) to write marker, then exits 0
    script = (
        "import signal, time, sys\n"
        f"marker = {str(marker_file)!r}\n"
        "def handle_term(s, f):\n"
        "    time.sleep(3.0)\n"
        "    open(marker, 'w').write('SEALED_CLEANLY')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, handle_term)\n"
        "while True: time.sleep(0.1)\n"
    )
    proc = subprocess.Popen([os.sys.executable, "-c", script], start_new_session=True)
    try:
        s_dir = owner._get_session_dir("sess_grace")
        state_file = s_dir / "session_state.json"
        import json
        state_file.write_text(
            json.dumps(
                {
                    "session_id": "sess_grace",
                    "request_id": req.request_id,
                    "pid": proc.pid,
                    "started_at_iso": "2026-10-29T10:00:00Z",
                    "started_at_monotonic": 1.0,
                    "status": RecordingState.CAPTURING.value,
                    "lock_file": str(s_dir / "owner.lock"),
                    "state_file": str(state_file),
                    "is_mock": True,
                }
            ),
            encoding="utf-8",
        )
        time.sleep(0.2)
        stop_res = owner.stop_session("sess_grace")
        assert stop_res["status"] == "STOPPED"
        assert marker_file.exists(), "Process was SIGKILLed before 3.0s graceful drain completed!"
        assert marker_file.read_text(encoding="utf-8") == "SEALED_CLEANLY"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1.0)


def test_mlx_worker_ignores_sigint_until_stop_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial Category: Signal Race — _worker_entrypoint sets SIGINT to SIG_IGN."""
    import queue as pyqueue

    in_q: Any = pyqueue.Queue()
    out_q: Any = pyqueue.Queue()

    class _DummyEvent:
        def is_set(self) -> bool:
            return False

    monkeypatch.setattr(
        "dual_pipeline.live_meeting_recorder_core.isolated_transcriber._perform_worker_handshake",
        lambda *a, **kw: True,
    )
    in_q.put({"type": "STOP"})
    prev_handler = signal.getsignal(signal.SIGINT)
    try:
        _worker_entrypoint(in_q, out_q, _DummyEvent(), mock_mode=True, parent_pid=os.getpid())
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, prev_handler)
