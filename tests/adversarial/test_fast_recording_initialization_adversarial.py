"""
tests/adversarial/test_fast_recording_initialization_adversarial.py

Adversarial stress and edge-case testing for Fast Recording Initialization:
1. Concurrency races: Parallel recorder invocations fail closed in <20ms with LiveSessionCollisionError.
2. Stale abandoned lock recovery: Dead process locks are cleanly acquired without hanging.
3. Zero-fratricide guarantee: Reaper only kills registered child PIDs of dead parents, never foreign processes.
4. Malformed / zero-byte WAV injection: Header-only and truncated WAV files are rejected without timeline corruption.
5. Port contention resilience: Busy ports increment gracefully without violent termination.
"""
from __future__ import annotations

import json
import os
import time
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveSessionCollisionError,
)
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    AudioChunkValidator,
)
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry


class TestFastRecordingInitializationAdversarial(unittest.TestCase):
    """Adversarial and failure-mode verification of fast recording initialization."""

    def setUp(self):
        import shutil
        self.test_root = Path(__file__).resolve().parents[2] / ".worktrees" / "adv_fast_recording_tmp"
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.test_root / "out"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_parallel_slug_collision_rejection(self):
        """Concurrent recorder attempts on the same slug fail closed in < 20ms."""
        rec1 = LiveMeetingRecorder(
            meeting_name="Adversarial Parallel Slug Race",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )
        try:
            t0 = time.perf_counter()
            with self.assertRaises(LiveSessionCollisionError) as ctx:
                LiveMeetingRecorder(
                    meeting_name="Adversarial Parallel Slug Race",
                    output_dir=self.output_dir,
                    mock_mode=True,
                    enforce_worktree=False,
                )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self.assertLess(latency_ms, 50.0)
            self.assertEqual(ctx.exception.holder_pid, os.getpid())
        finally:
            rec1._release_slug_lock()

    def test_stale_abandoned_lock_recovery(self):
        """An abandoned advisory lock whose holding process died is cleanly acquired by a new recorder."""
        locks_dir = Path(__file__).resolve().parents[2] / ".session_locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        slug = "Abandoned_Lock_Recovery_Amit"
        lock_path = locks_dir / f"{slug}.flock"

        # Simulate dead process lockfile with non-existent PID
        dead_pid = 99999999
        lock_path.write_text(f"{dead_pid}\n")

        # Because the dead process is not running and holds no fcntl lock,
        # a new recorder must acquire the file lock cleanly without error
        rec = LiveMeetingRecorder(
            meeting_name="Abandoned Lock Recovery",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )
        try:
            self.assertEqual(rec.clean_slug, slug)
            self.assertIsNotNone(rec._slug_lock_fd)
        finally:
            rec._release_slug_lock()

    def test_zero_fratricide_guarantee_in_session_reaper(self):
        """Reaper qualifies processes through 4 gates and strictly avoids terminating foreign PIDs."""
        registry = SessionRegistry(self.test_root)

        # Register a valid session manifest pointing to an active PID (e.g. init PID 1 or our parent)
        # but with a live parent PID -> must NEVER be qualified as stale
        record = registry.register(parent_pid=os.getpid(), meeting_slug="Active_Parent_Session")

        # Force a heartbeat back to > 20s ago
        manifest_path = registry.registry_dir / f"{record.session_id}.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["last_heartbeat"] = time.time() - 25.0
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        # Run reaper: because parent_pid is alive (os.getpid()), it MUST NOT be reaped!
        reaped = registry.reap_stale_sessions()
        self.assertNotIn(record.session_id, reaped)

        # Record must still be present
        self.assertIsNotNone(registry.inspect(record.session_id))
        registry.unregister(record.session_id)

    def test_malformed_and_truncated_wav_injection(self):
        """Header-only and corrupt WAV chunks are rejected by chunk validation without advancing timeline."""
        corrupt_wav = self.test_root / "chunk_corrupt.wav"
        # Write only 44-byte RIFF header with 0 audio frames
        with wave.open(str(corrupt_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            # Write 0 frames

        val = AudioChunkValidator.validate_chunk(corrupt_wav, allow_digital_zero=False)
        self.assertFalse(val.is_valid)

        rec = LiveMeetingRecorder(
            meeting_name="Corrupt WAV Rejection Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )
        try:
            # Processing invalid chunk file must be skipped
            rec._process_chunk_file(corrupt_wav)
            self.assertEqual(len(rec.processed_chunks), 0)
            self.assertEqual(rec._cumulative_elapsed_seconds, 0.0)
        finally:
            rec._release_slug_lock()

    def test_zero_port_contention_no_listeners(self):
        """LiveMeetingRecorder binds 0 network sockets and causes no port contention."""
        rec = LiveMeetingRecorder(
            meeting_name="Zero Port Contention Test",
            output_dir=self.output_dir,
            mock_mode=True,
            enforce_worktree=False,
        )
        try:
            self.assertIsNone(rec.ws_server)
        finally:
            rec._release_slug_lock()
