"""
tests/test_audio_device_contention.py

Unit tests for Wave 4:
1. LiveAudioCaptureManager.detect_device_contention() detects competing FFmpeg processes.
2. LiveAudioCaptureManager.start() emits [DEVICE-CONTENTION-WARNING] on contention.
3. AudioSubprocessWatchdog early failover on zero-byte chunk starvation (< 5s vs 20s stall).
4. Zero-byte chunk recovery when size increases.
5. Configurable stall thresholds in AudioSubprocessWatchdog.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioSubprocessWatchdog


class TestAudioDeviceContention(unittest.TestCase):
    def test_detect_device_contention_positive(self):
        """Asserts that competing FFmpeg processes using avfoundation are detected."""
        fake_pgrep_output = (
            "12345 /opt/homebrew/bin/ffmpeg -f avfoundation -i :0 -ar 16000 output.wav\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_pgrep_output)
            self.assertTrue(LiveAudioCaptureManager.detect_device_contention(":0"))

    def test_detect_device_contention_negative_when_no_avfoundation(self):
        """Asserts that unrelated FFmpeg or non-avfoundation processes do not trigger contention."""
        fake_pgrep_output = (
            "12345 /opt/homebrew/bin/ffmpeg -i input.mp4 -vn -c:a copy output.aac\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_pgrep_output)
            self.assertFalse(LiveAudioCaptureManager.detect_device_contention(":0"))

    def test_detect_device_contention_negative_when_no_processes(self):
        """Asserts that pgrep returning non-zero returncode results in False."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            self.assertFalse(LiveAudioCaptureManager.detect_device_contention(":0"))

    def test_detect_device_contention_ignores_self_pid(self):
        """Asserts that the current process's own PID is excluded from contention detection."""
        my_pid = os.getpid()
        fake_pgrep_output = (
            f"{my_pid} /opt/homebrew/bin/ffmpeg -f avfoundation -i :0 output.wav\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_pgrep_output)
            self.assertFalse(LiveAudioCaptureManager.detect_device_contention(":0"))

    def test_detect_device_contention_safe_on_exception(self):
        """Asserts that OS or subprocess exceptions return False rather than raising."""
        with patch("subprocess.run", side_effect=OSError("pgrep not found")):
            self.assertFalse(LiveAudioCaptureManager.detect_device_contention(":0"))

    def test_start_emits_contention_warning(self):
        """Asserts that start() logs [DEVICE-CONTENTION-WARNING] when contention is detected."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            chunks_dir = p / "chunks"
            master_wav = p / "master.wav"

            manager = LiveAudioCaptureManager(
                chunks_dir=chunks_dir,
                master_wav=master_wav,
                source_mode="microphone",
                device_index=":0",
                segment_seconds=5,
            )

            with (
                patch.object(manager, "detect_device_contention", return_value=True),
                patch.object(manager, "_start_microphone_capture"),
                patch.object(manager, "resolve_source_strategy", return_value=("microphone", None)),
                patch("dual_pipeline.live_meeting_recorder_core.audio_capture.AudioCaptureProofVerifier.run_full_start_proof", return_value=MagicMock()),
                self.assertLogs("dual_pipeline.live_meeting_recorder_core.audio_capture", level="WARNING") as cm,
            ):
                manager.start()
                self.assertTrue(
                    any("[DEVICE-CONTENTION-WARNING]" in record.getMessage() for record in cm.records),
                    f"Expected [DEVICE-CONTENTION-WARNING] in logs: {[r.getMessage() for r in cm.records]}",
                )
            manager.stop()


class TestWatchdogZeroByteEarlyFailover(unittest.TestCase):
    def test_zero_byte_chunk_starvation_triggers_early_failover(self):
        """Asserts that a 0-byte chunk remaining 0 bytes for >= threshold triggers failover with code -998."""
        callback_called = []

        def on_failover(reason: str, exit_code: int | None):
            callback_called.append((reason, exit_code))

        with tempfile.TemporaryDirectory() as td:
            chunks_dir = Path(td)
            chunk_file = chunks_dir / "chunk_0000.wav"
            chunk_file.touch()  # 0 bytes

            watchdog = AudioSubprocessWatchdog(
                on_failover_callback=on_failover,
                poll_interval_sec=0.04,
                max_stall_sec=20.0,
                zero_byte_stall_threshold_sec=0.15,
            )

            # Supervise a running dummy process so it doesn't fail on process exit
            proc = subprocess.Popen(["sleep", "5"])
            try:
                watchdog.register_processes([proc], chunks_dir=chunks_dir)
                watchdog.start()

                # Wait up to 1.0s for the 0.15s threshold to trigger
                deadline = time.time() + 1.0
                while time.time() < deadline and not callback_called:
                    time.sleep(0.04)

                watchdog.stop()

                self.assertEqual(len(callback_called), 1)
                reason, code = callback_called[0]
                self.assertIn("Zero-byte audio chunk starvation detected", reason)
                self.assertIn("chunk_0000.wav", reason)
                self.assertEqual(code, -998)
            finally:
                proc.kill()
                proc.wait()

    def test_zero_byte_chunk_recovery_resets_stall(self):
        """Asserts that when a chunk grows > 0 bytes before threshold, no failover occurs."""
        callback_called = []

        def on_failover(reason: str, exit_code: int | None):
            callback_called.append((reason, exit_code))

        with tempfile.TemporaryDirectory() as td:
            chunks_dir = Path(td)
            chunk_file = chunks_dir / "chunk_0000.wav"
            chunk_file.touch()

            watchdog = AudioSubprocessWatchdog(
                on_failover_callback=on_failover,
                poll_interval_sec=0.03,
                max_stall_sec=5.0,
                zero_byte_stall_threshold_sec=0.25,
            )

            proc = subprocess.Popen(["sleep", "5"])
            try:
                watchdog.register_processes([proc], chunks_dir=chunks_dir)
                watchdog.start()

                # Grow the chunk before the 0.25s threshold expires
                time.sleep(0.08)
                chunk_file.write_bytes(b"\x00" * 1024)
                watchdog.notify_chunk_written()

                # Sleep past what would have been the 0.25s mark
                time.sleep(0.25)
                watchdog.stop()

                # Callback should not have been called
                self.assertEqual(len(callback_called), 0)
            finally:
                proc.kill()
                proc.wait()

    def test_configurable_stall_thresholds(self):
        """Asserts that max_stall_sec and zero_byte_stall_threshold_sec are configurable."""
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: None,
            max_stall_sec=12.5,
            zero_byte_stall_threshold_sec=3.5,
        )
        self.assertEqual(watchdog.max_stall_sec, 12.5)
        self.assertEqual(watchdog.zero_byte_stall_threshold_sec, 3.5)


if __name__ == "__main__":
    unittest.main()
