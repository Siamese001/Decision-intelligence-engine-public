"""
tests/test_isolated_transcriber_prewarm.py

Unit tests for Wave 3: Apple Silicon Neural Engine Pre-Warming & Capture Process Decoupling.
1. Verifies non-blocking prewarm_async() initializes worker in background.
2. Verifies auto-recovery and worker restart after synthetic process termination.
3. Verifies audio spooling and capture continue uninterrupted even if worker crashes.
"""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.audio_utils import generate_synthetic_speech_chunk
from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
)


class TestIsolatedTranscriberPrewarm(unittest.TestCase):
    """Test suite for IsolatedMLXTranscriberClient pre-warming and fault tolerance."""

    def setUp(self):
        self.tmp_root = Path(tempfile.mkdtemp(prefix="test_prewarm_"))
        self.wav_path = self.tmp_root / "test_chunk.wav"
        generate_synthetic_speech_chunk(self.wav_path, duration_sec=2.0)

    def tearDown(self):
        if self.tmp_root.exists():
            shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_prewarm_async_spawns_worker_in_background(self):
        """prewarm_async() triggers worker initialization without blocking the caller."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        t0 = time.perf_counter()
        client.prewarm_async(timeout=10.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Method call itself must be fast (< 50ms)
        self.assertLess(elapsed_ms, 50.0, "prewarm_async should return non-blocking")

        try:
            # Worker should become active in the background
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if client.is_running:
                    break
                time.sleep(0.05)

            self.assertTrue(client.is_running, "Worker process did not start in background")
            self.assertIsNotNone(client.worker_pid)
        finally:
            client.stop()

    def test_transcribe_recovers_after_process_termination(self):
        """Client restarts worker automatically if worker process crashes or terminates."""
        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client.start(timeout=10.0)
        self.assertTrue(client.is_running)
        old_pid = client.worker_pid

        # Synthetically kill the worker process
        if client._process is not None:
            client._process.kill()
            client._process.join(timeout=2.0)

        self.assertFalse(client.is_running)

        try:
            # Transcribe call should detect dead process, restart it, and attempt transcription
            try:
                text = client.transcribe(self.wav_path, timeout=10.0)
                self.assertIsInstance(text, str)
            except RuntimeError as exc:
                self.assertIn("MOCK_ASR_PROHIBITED", str(exc))
            self.assertTrue(client.is_running)
            self.assertNotEqual(client.worker_pid, old_pid, "Worker PID should change after restart")
        finally:
            client.stop()

    def test_capture_continues_uninterrupted_during_worker_failure(self):
        """Audio spooler capture continues spooling audio chunks even if transcriber crashes."""
        spool_dir = self.tmp_root / "spool"
        spooler = InstantAudioSpooler.spawn_instant(
            spool_dir=spool_dir,
            segment_seconds=1,
            mock_mode=True,
        )

        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client.start(timeout=10.0)

        try:
            # Simulate worker death while capture is running
            if client._process is not None:
                client._process.kill()
                client._process.join(timeout=1.0)

            # Spooler must continue capturing chunks
            time.sleep(1.5)
            self.assertTrue(spooler.is_running, "Spooler should remain active despite worker crash")
            chunks = list(spool_dir.glob("chunk_*.wav"))
            self.assertGreaterEqual(len(chunks), 1, "Spooler should continue accumulating chunks")
        finally:
            spooler.stop()
            client.stop()

    def test_revoked_pty_stdin_stream_sanitization(self) -> None:
        """Verifies that a detached daemon whose controlling PTY master was revoked spawns MLX worker without init_sys_streams EBADF."""
        import os
        import pty
        import subprocess
        import sys

        master, slave = pty.openpty()
        log_file = self.tmp_root / "revoked_pty_service.log"
        ready_flag = self.tmp_root / "daemon_started.flag"
        # Note: Even if a legacy caller omitted stdin=DEVNULL so the daemon inherited the slave PTY,
        # _sanitize_standard_streams() / _ensure_valid_standard_streams() repairs revoked FD 0!
        leader_code = f"""
import fcntl, os, subprocess, sys, termios, time
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
log_fd = os.open({str(log_file)!r}, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
child_code = \"\"\"
import os, time
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import IsolatedMLXTranscriberClient
open({str(ready_flag)!r}, "w").write("1")
time.sleep(0.3)
client = IsolatedMLXTranscriberClient(mock_mode=True)
try:
    receipt = client.start(timeout=10.0)
    print("HANDSHAKE_STATUS:" + str(receipt.get("status")), flush=True)
finally:
    client.stop()
\"\"\"
subprocess.Popen(
    [sys.executable, "-c", child_code],
    stdout=log_fd,
    stderr=log_fd,
    start_new_session=True,
    close_fds=True,
)
os.close(log_fd)
while not os.path.exists({str(ready_flag)!r}):
    time.sleep(0.01)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", leader_code],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        os.close(slave)
        proc.wait(timeout=5.0)
        os.close(master)  # Revoke slave PTY vnode in kernel after daemon started

        deadline = time.monotonic() + 8.0
        content = ""
        while time.monotonic() < deadline:
            if log_file.exists():
                content = log_file.read_text(encoding="utf-8")
                if "HANDSHAKE_STATUS:READY" in content or "Fatal Python error" in content:
                    break
            time.sleep(0.1)

        self.assertNotIn("init_sys_streams", content)
        self.assertNotIn("Bad file descriptor", content)
        self.assertIn("HANDSHAKE_STATUS:READY", content)

    def test_handshake_fails_fast_on_early_worker_exit(self) -> None:
        """Verifies that _await_handshake detects dead worker in <0.5s instead of hanging for 60s."""
        from unittest.mock import MagicMock
        from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
            MLXInitError,
            MLXReasonCode,
        )

        client = IsolatedMLXTranscriberClient(mock_mode=True)
        client._output_q = client._ctx.Queue()
        dead_proc = MagicMock()
        dead_proc.is_alive.return_value = False
        dead_proc.exitcode = 1
        client._process = dead_proc

        t0 = time.monotonic()
        with self.assertRaises(MLXInitError) as ctx:
            client._await_handshake(timeout=60.0)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.5, "Dead worker handshake must fail fast (<0.5s), not wait 60s")
        self.assertEqual(ctx.exception.reason_code, MLXReasonCode.MLX_WORKER_DIED)
        self.assertEqual(ctx.exception.stage, "SPAWN")


if __name__ == "__main__":
    unittest.main()

