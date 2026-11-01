"""
Wave 1 E2E Test: Recorder Lifecycle, Identity Rebind, SIGINT/--detach Stop, and Sidecar Repatriation.

Verifies:
1. Pre-CLI fast bootstrap skips parent pre-spool on --detach and preserves segment_seconds=15 default.
2. InstantAudioSpooler adoption retains _captured_pid and terminates child capture cleanly on stop().
3. Anonymous start -> resolve_identity provisions an authentic git worktree on audio/<slug>.
4. Repatriation copies master WAV, referenced_artifacts/*, speaker_map.json, speaker_hints.json,
   copilot_answers.jsonl, and .voiceprints.json with SHA-256 verification before teardown.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
    is_bootstrap_eligible,
    resolve_bootstrap_spooler,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import (
    SpoolStatus,
    hydrate_spooler_from_manifest,
)
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import halt_capture_before_drain
from tools.pr_governance_core.audio_repatriation import repatriate_worktree_audio_artifacts
from nee import _is_record_start_intent


def _write_pcm16_wav(path: Path, duration_sec: float = 1.0, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    samples = (np.sin(2 * np.pi * 440.0 * t) * 12000).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def _init_sandbox_git_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, env=env, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, env=env, check=True)
    subprocess.run(["git", "config", "user.name", "Test Operator"], cwd=repo_dir, env=env, check=True)
    (repo_dir / "README.md").write_text("# Sandbox Repo\n", encoding="utf-8")
    (repo_dir / ".githooks").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".venv").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".local").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, env=env, check=True, capture_output=True)
    return repo_dir


def test_detach_skips_parent_bootstrap_and_preserves_15s_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies --detach bypasses parent fast bootstrap and segment_seconds defaults to 15s."""
    assert _is_record_start_intent(["record", "--project", "TestProj", "--meeting", "Sync"]) is True
    assert _is_record_start_intent(["record", "--project", "TestProj", "--meeting", "Sync", "--detach"]) is False
    assert is_bootstrap_eligible(["record", "--project", "TestProj", "--detach"]) is False

    spool_dir = tmp_path / "spool_default_15"
    spool_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEE_BOOTSTRAP_SPOOL_DIR", str(spool_dir))
    monkeypatch.setenv("NEE_BOOTSTRAP_SESSION_ID", "sess_15s")
    spooler = resolve_bootstrap_spooler(explicit_spooler=None, dry_run=False, mock_mode=True)
    assert spooler is not None
    assert spooler.segment_seconds == 15


def test_adopted_spooler_retains_pid_and_terminates_on_stop(tmp_path: Path) -> None:
    """Verifies cross-process manifest hydration and adopted spooler stop kill the child PID."""
    spool_dir = tmp_path / "spool_proc"
    spool_dir.mkdir(parents=True, exist_ok=True)

    # Spawn a real lightweight child process sleeping until signaled
    child = subprocess.Popen(
        [sys.executable, "-c", "import time, signal; signal.signal(signal.SIGINT, lambda *a: exit(0)); time.sleep(30)"],
        start_new_session=True,
    )
    try:
        manifest_path = spool_dir / "spool_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "session_id": "test_hydrate",
                    "spool_dir": str(spool_dir),
                    "started_at_iso": "2026-10-29T10:00:00",
                    "started_at_monotonic": time.monotonic(),
                    "pid": child.pid,
                    "device_index": ":0",
                    "segment_seconds": 15,
                    "status": "UNKNOWN_LEGACY_STATUS",
                }
            ),
            encoding="utf-8",
        )

        # Hydrate manifest with unknown status -> must resolve to SpoolStatus.ACTIVE_SPOOLING (not AttributeError)
        pid, alive, status, _, _, _ = hydrate_spooler_from_manifest(manifest_path)
        assert pid == child.pid
        assert alive is True
        assert status == SpoolStatus.ACTIVE_SPOOLING

        spooler = InstantAudioSpooler(spool_dir=spool_dir, segment_seconds=15, mock_mode=False, session_id="test_hydrate")
        spooler.mark_adopted()

        class _DummyRecorder:
            def __init__(self, sp: InstantAudioSpooler) -> None:
                self.running = True
                self.mock_mode = True
                self.capture_manager = None
                self.ffmpeg_proc = None
                self.spooler = sp

        dummy = _DummyRecorder(spooler)
        halt_capture_before_drain(dummy)

        # Wait up to 2s for child to exit
        child.wait(timeout=2.0)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=1.0)


def test_recorder_e2e_lifecycle_and_lossless_sidecar_repatriation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full E2E test of anonymous start, identity resolution, worktree registration, and sidecar repatriation."""
    repo_root = _init_sandbox_git_repo(tmp_path / "repo")
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(lock_dir))
    monkeypatch.setattr("dual_pipeline.live_meeting_recorder.ROOT", repo_root)

    rec = LiveMeetingRecorder(
        project_name=None,
        meeting_name=None,
        mock_mode=True,
        isolated_process=False,
        enforce_worktree=True,
        fast_start=True,
    )
    try:
        assert rec.is_anonymous is True
        rec.resolve_identity(
            project_name="AcmeCloud",
            meeting_name="Architecture Review",
            attendees=["Alice Smith", "Bob Jones"],
            meeting_date="2026-10-29",
        )
        assert rec.is_anonymous is False
        assert rec.worktree_dir is not None
        assert rec.worktree_dir.exists()
        assert (rec.worktree_dir / ".git").exists()

        # Verify git worktree list registers the new audio/<slug> worktree
        wt_out = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"], cwd=repo_root, text=True
        )
        assert str(rec.worktree_dir.resolve()) in wt_out

        # Populate master WAV and all critical sidecars inside the worktree
        _write_pcm16_wav(rec.master_wav, duration_sec=2.0)
        chunk0 = rec.chunks_dir / "chunk_00000.wav"
        _write_pcm16_wav(chunk0, duration_sec=2.0)
        rec.processed_chunks.add(chunk0.name)

        rec.stop()

        sidecars_to_create = {
            rec.output_dir / "referenced_artifacts" / "deck.pdf": b"%PDF-1.4 mock slide deck content",
            rec.output_dir / f"{rec.clean_slug.lower()}_speaker_map.json": json.dumps({"SPEAKER_00": "Alice Smith"}).encode(),
            rec.output_dir / "speaker_hints.json": json.dumps([{"speaker_name": "Alice Smith", "quote": "hello"}]).encode(),
            rec.output_dir / "copilot_answers.jsonl": b'{"query":"status","answer":"on track"}\n',
            rec.cache_dir / f"{rec.master_wav.stem}.voiceprints.json": json.dumps({"SPEAKER_00": [0.1] * 256}).encode(),
        }
        expected_targets = {}
        for path, raw_bytes in sidecars_to_create.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw_bytes)
            rel_from_output = path.resolve().relative_to((rec.worktree_dir / "output").resolve())
            dst_target = repo_root / "output" / "transcript_cache" / rel_from_output
            expected_targets[dst_target] = hashlib.sha256(raw_bytes).hexdigest()

        # Repatriate from worktree into primary repo root before teardown
        repat = repatriate_worktree_audio_artifacts(rec.worktree_dir, repo_root)
        assert repat.success is True, f"Repatriation failed: {repat.error}"

        # Verify every sidecar survived into primary_root/output/transcript_cache with matching SHA-256
        for dst_path, exp_sha in expected_targets.items():
            assert dst_path.is_file(), f"Sidecar {dst_path.name} was lost during repatriation at {dst_path}!"
            actual_sha = hashlib.sha256(dst_path.read_bytes()).hexdigest()
            assert actual_sha == exp_sha, f"SHA-256 mismatch for {dst_path.name}"
    finally:
        rec._release_slug_lock()
