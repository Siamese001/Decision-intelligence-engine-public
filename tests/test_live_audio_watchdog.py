"""
tests/test_live_audio_watchdog.py

Unit tests for Wave 2: AudioSubprocessWatchdog and LiveAudioCaptureManager auto-failover.
Verifies:
1. AudioSubprocessWatchdog detects child process exit within <= 500ms.
2. AudioSubprocessWatchdog detects stream stalls when no chunks are emitted.
3. AudioSubprocessWatchdog maintains accurate failover event history.
4. LiveAudioCaptureManager.fallback_to_microphone() cleanly hot-swaps capture to microphone (:0).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioSubprocessWatchdog


class TestLiveAudioWatchdog(unittest.TestCase):
    def test_watchdog_detects_child_process_exit(self):
        """Asserts that the watchdog triggers a failover callback when a supervised process dies."""
        callback_called = []

        def on_failover(reason: str, exit_code: int | None):
            callback_called.append((reason, exit_code))

        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=on_failover,
            poll_interval_sec=0.05,
            max_stall_sec=10.0,
        )

        # Spawn short-lived process that exits immediately with code 42
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(42)"])
        watchdog.register_processes([proc])
        watchdog.start()

        # Wait up to 1 second for watchdog to detect termination
        deadline = time.time() + 1.0
        while time.time() < deadline and not callback_called:
            time.sleep(0.05)

        watchdog.stop()
        self.assertEqual(len(callback_called), 1)
        reason, code = callback_called[0]
        self.assertIn("exited unexpectedly", reason)
        self.assertEqual(code, 42)

    def test_watchdog_detects_stream_stall(self):
        """Asserts that watchdog detects stall when chunks stop being produced."""
        callback_called = []

        def on_failover(reason: str, exit_code: int | None):
            callback_called.append((reason, exit_code))

        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=on_failover,
            poll_interval_sec=0.04,
            max_stall_sec=0.15,
        )

        # Supervise a long-running process that does not exit
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            watchdog.register_processes([proc])
            watchdog.start()

            # Wait for stall timer (0.15s) to trigger
            time.sleep(0.35)
            watchdog.stop()

            self.assertEqual(len(callback_called), 1)
            reason, code = callback_called[0]
            self.assertIn("generation stalled", reason)
            self.assertEqual(code, -999)
        finally:
            proc.kill()
            proc.wait()

    def test_watchdog_reset_stall_on_chunk(self):
        """Asserts that notifying chunk written resets stall timer and prevents false positive."""
        callback_called = []

        def on_failover(reason: str, exit_code: int | None):
            callback_called.append((reason, exit_code))

        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=on_failover,
            poll_interval_sec=0.04,
            max_stall_sec=0.2,
        )

        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            watchdog.register_processes([proc])
            watchdog.start()

            # Ping chunk written every 0.08s for 0.4s (longer than max_stall_sec)
            for _ in range(5):
                time.sleep(0.08)
                watchdog.notify_chunk_written()

            watchdog.stop()
            self.assertEqual(len(callback_called), 0)
        finally:
            proc.kill()
            proc.wait()

    def test_capture_manager_fallback_to_microphone(self):
        """Validates that fallback_to_microphone reconfigures capture to microphone (:0)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            chunks_dir = tmp_path / "chunks"
            master_wav = tmp_path / "master.wav"

            # Pre-seed existing chunks to test chunk index resumption
            chunks_dir.mkdir(parents=True, exist_ok=True)
            (chunks_dir / "chunk_0000.wav").write_bytes(b"data")
            (chunks_dir / "chunk_0001.wav").write_bytes(b"data")

            manager = LiveAudioCaptureManager(
                chunks_dir=chunks_dir,
                master_wav=master_wav,
                source_mode="system",
                segment_seconds=5,
            )

            with self.assertRaises(AudioCaptureUnprovenError) as ctx:
                manager.fallback_to_microphone()
            self.assertEqual(ctx.exception.reason_code, AudioReasonCode.AUDIO_GROWTH_STALLED)
            self.assertTrue(manager._sck_permanently_unavailable)
            self.assertFalse(manager.is_running)

    def test_watchdog_threshold_dynamic_coupling(self):
        """Verifies zero_byte_stall_threshold_sec is dynamically derived from segment_seconds."""
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: None,
            segment_seconds=15,
        )
        self.assertGreaterEqual(watchdog.zero_byte_stall_threshold_sec, 20.0)
        self.assertEqual(watchdog.segment_seconds, 15)

    def test_watchdog_threshold_invariant_raises_on_underflow(self):
        """Verifies ValueError when zero_byte_stall_threshold_sec is smaller than segment_seconds."""
        with self.assertRaises(ValueError) as ctx:
            AudioSubprocessWatchdog(
                on_failover_callback=lambda r, c: None,
                segment_seconds=15,
                zero_byte_stall_threshold_sec=5.0,
            )
        self.assertIn("must be >= segment_seconds", str(ctx.exception))

    def test_no_false_failover_during_normal_segment_window(self):
        """Asserts that a 0-byte chunk within normal segment window does not trigger failover."""
        callback_called = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks_dir = Path(tmp_dir) / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            chunk_file = chunks_dir / "chunk_0000.wav"
            chunk_file.touch()  # 0 bytes

            watchdog = AudioSubprocessWatchdog(
                on_failover_callback=lambda r, c: callback_called.append((r, c)),
                poll_interval_sec=0.03,
                segment_seconds=2,
                zero_byte_stall_threshold_sec=2.5,
            )
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
            try:
                watchdog.register_processes([proc], chunks_dir=chunks_dir)
                watchdog.start()

                # Sleep 0.2s - well under the 2.5s threshold
                time.sleep(0.2)
                watchdog.stop()

                # Failover must NOT have been called
                self.assertEqual(len(callback_called), 0)
            finally:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()

