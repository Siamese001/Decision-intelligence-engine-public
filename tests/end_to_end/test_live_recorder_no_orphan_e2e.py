"""
tests/end_to_end/test_live_recorder_no_orphan_e2e.py

End-to-end verification that LiveMeetingRecorder never creates unregistered orphan
directories under .worktrees/:
1. Start -> abort lifecycle: abort with 0 chunks cleans up and leaves 0 orphans in .worktrees/.
2. Start -> record -> stop lifecycle: provisions an authentic git worktree with .git.
3. Provisioning failure fallback: when git worktree creation fails, falls back cleanly to
   main repository output paths without leaving any unregistered .worktrees/ folders.
"""
from __future__ import annotations

import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    is_authentic_git_worktree,
)
from tools.pr_governance_core.worktree_inventory import build_worktree_inventory


def create_mock_wav(path: Path, duration_sec: float = 0.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        n_samples = int(16000 * duration_sec)
        samples = bytearray()
        for i in range(n_samples):
            val = int(3000 * math.sin(2 * math.pi * 440 * i / 16000))
            samples.extend(struct.pack("<h", val))
        wf.writeframes(samples)
    return path


class TestLiveRecorderNoOrphanE2E(unittest.TestCase):
    """E2E test suite ensuring zero orphan directories under .worktrees/."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "rec@example.com"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Recorder Tester"], cwd=self.repo, check=True, capture_output=True)

        (self.repo / "README.md").write_text("# Main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

        self.wt_dir = self.repo / ".worktrees"
        self.wt_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_abort_leaves_zero_orphans(self) -> None:
        """A recorder started and immediately aborted leaves 0 directories in .worktrees/."""
        with patch("dual_pipeline.live_meeting_recorder.ROOT", self.repo):
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Early Abort Meeting",
                mock_mode=True,
                enforce_worktree=True,
                output_dir="auto",
                enable_ws=False,
                enable_copilot=False,
            )

            # Abort before any chunks are captured
            try:
                recorder.stop()
            except RuntimeError as e:
                self.assertIn("0 audio chunks were captured", str(e))

        inv = build_worktree_inventory(self.repo)
        self.assertEqual(inv["counts"]["orphan_directories"], 0, f"Found orphans: {inv['worktrees']}")
        self.assertEqual(inv["counts"]["total_worktrees_children"], 0)

    def test_start_record_stop_creates_authentic_worktree(self) -> None:
        """A recorded session materializes an authentic git worktree (with .git), not an unmanaged orphan."""
        with patch("dual_pipeline.live_meeting_recorder.ROOT", self.repo):
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Complete Valid Session",
                mock_mode=True,
                enforce_worktree=True,
                output_dir="auto",
                enable_ws=False,
                enable_copilot=False,
            )

            # Feed a valid chunk
            chunk_file = recorder.chunks_dir / "chunk_00000.wav"
            create_mock_wav(chunk_file)

            # Process chunk to trigger materialization before write
            recorder.process_chunk(chunk_file, 0)
            self.assertTrue(recorder._worktree_materialized)
            self.assertIsNotNone(recorder.worktree_dir)
            self.assertTrue(is_authentic_git_worktree(recorder.worktree_dir))

            recorder.stop()

        inv = build_worktree_inventory(self.repo)
        # Should be a registered worktree, NOT an orphan
        self.assertEqual(inv["counts"]["orphan_directories"], 0)
        self.assertEqual(inv["counts"]["registered"], 1)

    def test_provisioning_failure_fallback_leaves_no_orphan(self) -> None:
        """When git worktree provisioning fails, recorder falls back to main-root path with 0 orphans."""
        with patch("dual_pipeline.live_meeting_recorder.ROOT", self.repo), \
             patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", side_effect=RuntimeError("git failure")):
            recorder = LiveMeetingRecorder(
                project_name="project_nexus",
                meeting_name="Failing Provision Session",
                mock_mode=True,
                enforce_worktree=True,
                output_dir="auto",
                enable_ws=False,
                enable_copilot=False,
            )

            chunk_file = recorder.chunks_dir / "chunk_00000.wav"
            create_mock_wav(chunk_file)
            recorder.process_chunk(chunk_file, 0)

            # Worktree provisioning failed, fell back to main-root paths
            self.assertIsNone(recorder.worktree_dir)
            self.assertEqual(list(self.wt_dir.iterdir()), [])

        inv = build_worktree_inventory(self.repo)
        self.assertEqual(inv["counts"]["orphan_directories"], 0)

    def test_detached_service_spawn_survives_pty_revocation_e2e(self) -> None:
        """Detached RecordingServiceOwner spawn sets stdin=DEVNULL and sanitizes standard streams."""
        import os
        import pty
        from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
            RecordingStartRequest,
        )
        from dual_pipeline.live_meeting_recorder_core.recording_service_owner import (
            RecordingServiceOwner,
        )

        # Verify RecordingServiceOwner.spawn_session_process spawns daemon with DEVNULL stdin and survives
        sessions_dir = self.repo / ".local" / "sessions"
        owner = RecordingServiceOwner(sessions_root=sessions_dir)
        session_id = "rec_test_pty_e2e"

        req = RecordingStartRequest(
            project_name="project_nexus",
            meeting_name="PTY E2E Session",
            attendees=["Alice", "Bob"],
            output_dir=str(self.repo / "output"),
            mock_mode=True,
        )
        record = owner.spawn_session_process(
            request=req,
            session_id=session_id,
        )
        self.assertNotEqual(record.pid, os.getpid())
        try:
            stored = owner.get_session_record(session_id)
            self.assertIsNotNone(stored)
        finally:
            owner.stop_session(session_id)

        # Also verify end-to-end in a child process that _sanitize_standard_streams()
        # repairs a revoked PTY stdin before IsolatedMLXTranscriberClient spawns
        master, slave = pty.openpty()
        ready_file = self.repo / "pty_child_ready.flag"
        result_file = self.repo / "pty_child_result.txt"
        child_code = f"""
import fcntl, os, pathlib, sys, termios, time
sys.path.insert(0, {repr(str(REPO_ROOT))})
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import _sanitize_standard_streams
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import IsolatedMLXTranscriberClient

try:
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
except Exception:
    pass

pathlib.Path({repr(str(ready_file))}).write_text("ready")
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    try:
        os.fstat(0)
        time.sleep(0.02)
    except OSError:
        break

_sanitize_standard_streams()
client = IsolatedMLXTranscriberClient(mock_mode=True)
client.start(timeout=10.0)
client.stop()
pathlib.Path({repr(str(result_file))}).write_text("PASS")
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        try:
            import time
            deadline = time.monotonic() + 5.0
            while not ready_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            os.close(master)
            _, stderr_bytes = proc.communicate(timeout=15.0)
            self.assertEqual(proc.returncode, 0, f"Child failed: {stderr_bytes.decode('utf-8', errors='replace')}")
            self.assertTrue(result_file.exists())
            self.assertEqual(result_file.read_text(), "PASS")
        finally:
            try:
                os.close(master)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

