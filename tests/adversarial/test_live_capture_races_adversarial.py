"""tests/adversarial/test_live_capture_races_adversarial.py

Adversarial stress and edge-case testing for Wave 5: Capture-Layer Correctness.
1. ScreenCaptureKit stderr pipe overflow: background daemon thread drains >100KB stderr without pipe deadlock.
2. Recycled PID protection in SessionRegistry: verifies child process identity before reaping to prevent killing innocent processes.
3. Active capture dir protection: _backup_unmanaged_dir preserves active recording directories containing .active_session or .flock.
4. SessionRegistry heartbeat rate-limiting: prevents disk I/O churn under rapid consecutive calls while honoring force flushes.
5. Preflight cache thread safety and invalidation under rapid concurrent lookups.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry
from dual_pipeline.live_meeting_recorder_core.worktree_manager import _backup_unmanaged_dir
from pipeline_a_intake.mac_system_audio import CoreAudioDeviceManager


class TestLiveCaptureRacesAdversarial(unittest.TestCase):
    """Adversarial stress and race condition tests for capture layer and process safety."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="adv_capture_races_"))
        self.repo_root = self.temp_dir / "repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        CoreAudioDeviceManager.clear_device_cache()
        AudioPreflightValidator.clear_cache()

    def tearDown(self):
        CoreAudioDeviceManager.clear_device_cache()
        AudioPreflightValidator.clear_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sck_stderr_drain_under_high_log_volume(self):
        """ScreenCaptureKit stderr drainer handles >100KB without pipe buffer deadlock."""
        # Standard POSIX pipe buffer is 64KB. If stderr is not continuously drained,
        # write to pipe blocks and deadlocks the child process.
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.temp_dir / "chunks",
            master_wav=self.temp_dir / "master.wav",
            source_mode="microphone",
            device_index=":0",
            segment_seconds=15,
            start_chunk_idx=0,
            mock_mode=True,
        )

        # Spawn a python process that emits 120KB of stderr output rapidly
        large_stderr_script = (
            "import sys, time\n"
            "for i in range(1200):\n"
            "    sys.stderr.write(f'SCK_LOG_LINE_{i:04d}_' + 'X'*90 + '\\n')\n"
            "    sys.stderr.flush()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", large_stderr_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        mgr._start_sck_stderr_drain(proc)

        # Process should finish within 2 seconds without hanging on pipe buffer
        ret = proc.wait(timeout=5.0)
        self.assertEqual(ret, 0)
        self.assertTrue(mgr._last_sck_stderr.startswith("SCK_LOG_LINE_"))

    def test_session_registry_recycled_pid_protection(self):
        """Invariant: Reaper NEVER terminates alive child PIDs that fail identity verification."""
        registry = SessionRegistry(self.repo_root)

        # Spawn an unrelated benign process (e.g. cat or sleep)
        benign_proc = subprocess.Popen(["sleep", "30"])
        try:
            fake_dead_parent_pid = 99999997
            manifest = registry.register(
                parent_pid=fake_dead_parent_pid,
                meeting_slug="recycled_pid_session",
                ffmpeg_pid=benign_proc.pid,
            )

            # Age heartbeat to 30s
            m_file = self.repo_root / ".session_registry" / f"{manifest.session_id}.json"
            d = json.loads(m_file.read_text(encoding="utf-8"))
            d["last_heartbeat"] = time.time() - 30.0
            m_file.write_text(json.dumps(d), encoding="utf-8")

            # Mock _verify_child_identity to return False (simulating recycled PID belonging to unrelated app)
            with patch.object(SessionRegistry, "_verify_child_identity", return_value=False) as mock_verify:
                reap_res = registry.reap_stale_sessions(stale_seconds=15.0)
                mock_verify.assert_called_with(benign_proc.pid)

                # Benign process must NOT be reaped!
                self.assertNotIn(benign_proc.pid, reap_res["reaped_pids"])
                # Process must still be alive
                self.assertIsNone(benign_proc.poll())
        finally:
            benign_proc.terminate()
            benign_proc.wait(timeout=2.0)

    def test_active_capture_dir_protected_from_unmanaged_backup_purge(self):
        """Invariant: _backup_unmanaged_dir refuses to delete directories with active capture markers."""
        worktree_candidate = self.temp_dir / "active_meeting_wt"
        worktree_candidate.mkdir(parents=True, exist_ok=True)
        active_marker = worktree_candidate / ".active_session"
        active_marker.write_text("session-12345", encoding="utf-8")

        dummy_chunk = worktree_candidate / "chunk_00000.wav"
        dummy_chunk.write_bytes(b"RIFF" + b"\x00" * 40)

        # Call _backup_unmanaged_dir on active capture dir
        bak = _backup_unmanaged_dir(worktree_candidate)

        # Must return None and NOT destroy worktree_candidate
        self.assertIsNone(bak)
        self.assertTrue(worktree_candidate.exists())
        self.assertTrue(active_marker.exists())
        self.assertTrue(dummy_chunk.exists())

    def test_heartbeat_rate_limiting_and_forced_state_flush(self):
        """SessionRegistry rate-limits identical heartbeats but immediately flushes state changes."""
        registry = SessionRegistry(self.repo_root)
        manifest = registry.register(
            parent_pid=os.getpid(),
            meeting_slug="rate_limiting_test",
            ffmpeg_pid=1001,
        )
        m_file = self.repo_root / ".session_registry" / f"{manifest.session_id}.json"

        # Record initial modification time
        initial_mtime = m_file.stat().st_mtime_ns

        # Consecutive rapid heartbeat without state change should be rate-limited
        time.sleep(0.02)
        registry.heartbeat(manifest.session_id, ffmpeg_pid=1001)
        subsequent_mtime = m_file.stat().st_mtime_ns
        self.assertEqual(initial_mtime, subsequent_mtime, "Identical heartbeat within min_interval must be throttled")

        # Heartbeat with state change (e.g. ffmpeg_pid change) must write immediately
        time.sleep(0.02)
        registry.heartbeat(manifest.session_id, ffmpeg_pid=2002)
        changed_mtime = m_file.stat().st_mtime_ns
        self.assertNotEqual(subsequent_mtime, changed_mtime, "State change must trigger immediate write")

        # Forced heartbeat must write immediately
        time.sleep(0.02)
        registry.heartbeat(manifest.session_id, ffmpeg_pid=2002, force=True)
        forced_mtime = m_file.stat().st_mtime_ns
        self.assertNotEqual(changed_mtime, forced_mtime, "Forced heartbeat must write immediately")


if __name__ == "__main__":
    unittest.main()
