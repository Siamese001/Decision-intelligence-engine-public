"""
tests/adversarial/test_watchdog_sentinel_oscillation_adversarial.py

Adversarial Stress & Hardening Test Suite for Audio Watchdog & Sentinel Oscillation:
1. Dynamic Threshold Derivation: Asserts starvation threshold strictly exceeds segment_seconds (e.g. 20s for 15s segments).
2. Startup Grace Period: Asserts zero-byte chunks during initial 8s do not trigger false starvation.
3. Sentinel Hysteresis & Cooldown: Asserts headphone sentinel enforces cooldown to prevent ping-pong.
4. Circuit Breaker Latching: Asserts 3 rapid failovers trip circuit breaker and latch permanently to mic.
5. Concurrent Failover Serialization: Asserts state lock serializes racing failover invocations safely.
6. Process Reaping & Pipe Hygiene: Asserts subprocess termination cleanly closes pipes without leaks.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import (
    AudioSubprocessWatchdog,
)


class TestWatchdogSentinelOscillationAdversarial(unittest.TestCase):
    """Adversarial suite validating watchdog thresholds, sentinel debouncing, and circuit breaker."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.chunks_dir = self.tmp_path / "chunks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.master_wav = self.tmp_path / "master.wav"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_dynamic_threshold_derivation_from_segment_seconds(self):
        """Watchdog starvation threshold MUST exceed segment duration (max(10s, segment_seconds + 5s))."""
        # 15s segments -> 20.0s threshold
        wd15 = AudioSubprocessWatchdog(on_failover_callback=lambda r, c: None, segment_seconds=15.0)
        self.assertEqual(wd15.zero_byte_stall_threshold_sec, 20.0)

        # 5s segments -> 10.0s threshold (min floor)
        wd5 = AudioSubprocessWatchdog(on_failover_callback=lambda r, c: None, segment_seconds=5.0)
        self.assertEqual(wd5.zero_byte_stall_threshold_sec, 10.0)

        # 30s segments -> 35.0s threshold
        wd30 = AudioSubprocessWatchdog(on_failover_callback=lambda r, c: None, segment_seconds=30.0)
        self.assertEqual(wd30.zero_byte_stall_threshold_sec, 35.0)

        # Explicit override respected
        wd_override = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: None, segment_seconds=15.0, zero_byte_stall_threshold_sec=42.0
        )
        self.assertEqual(wd_override.zero_byte_stall_threshold_sec, 42.0)

    def test_startup_grace_period_suppresses_early_zero_byte_starvation(self):
        """Simulates 0-byte chunk_0000.wav during initial 5s; asserts watchdog suppresses starvation."""
        chunk0 = self.chunks_dir / "chunk_0000.wav"
        chunk0.touch()  # 0 bytes

        failover_called = []
        wd = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: failover_called.append((r, c)),
            segment_seconds=15.0,
            startup_grace_period_sec=8.0,
        )
        wd.register_processes([], self.chunks_dir)

        # Immediate check at t=0s into startup: must be None
        reason, code = wd._check_zero_byte_starvation()
        self.assertIsNone(reason)
        self.assertIsNone(code)
        self.assertEqual(len(failover_called), 0)

    def test_sentinel_hysteresis_cooldown_prevents_immediate_retrigger(self):
        """Asserts that headphone sentinel enforces a cooldown period after a failover."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="microphone",
        )
        mgr._is_running = True
        mgr._active_strategy = "microphone"
        # Simulate recent failover at t = now - 5s (within 30s cooldown)
        mgr._last_failover_time = time.time() - 5.0
        mgr._sck_failure_count = 1

        with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_default_output_headphones", return_value=True), \
             patch.object(mgr, "failover_to_screen_capture_kit") as mock_failover:

            triggered = mgr.check_headphone_trap_sentinel()
            self.assertFalse(triggered, "Sentinel should have been suppressed by active cooldown")
            mock_failover.assert_not_called()

    def test_circuit_breaker_latches_degraded_under_adversarial_ping_pong(self):
        """Injects rapid consecutive failovers; asserts circuit breaker trips and latches permanently to mic."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="auto",
        )
        mgr._is_running = True

        with patch.object(mgr, "_start_microphone_capture"):
            # Trigger 3 rapid failovers
            mgr.fallback_to_microphone(reason="Test failover 1")
            self.assertFalse(mgr._sck_permanently_unavailable)

            mgr.fallback_to_microphone(reason="Test failover 2")
            self.assertFalse(mgr._sck_permanently_unavailable)

            # 3rd failover within 60s window must trip circuit breaker
            mgr.fallback_to_microphone(reason="Test failover 3")
            self.assertTrue(mgr._sck_permanently_unavailable, "Circuit breaker should have tripped on 3rd failover")

            # Subsequent sentinel check must be completely blocked
            with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_default_output_headphones", return_value=True):
                triggered = mgr.check_headphone_trap_sentinel()
                self.assertFalse(triggered)

    def test_concurrent_failover_calls_are_serialized_and_idempotent(self):
        """Concurrently invokes fallback_to_microphone from 10 threads; asserts state safety without deadlock."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="auto",
        )
        mgr._is_running = True

        results = []
        threads = []

        def worker():
            res = mgr.fallback_to_microphone(reason="Concurrent failover test")
            results.append(res)

        with patch.object(LiveAudioCaptureManager, "_start_microphone_capture"):
            for _ in range(10):
                t = threading.Thread(target=worker)
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=2.0)

        # All threads completed without deadlocks
        self.assertEqual(len(results), 10)
        self.assertEqual(mgr.active_strategy, "microphone")

    def test_process_reaping_cleans_pipes_and_leaves_no_zombies(self):
        """Asserts that _terminate_process closes stdin/stdout/stderr pipes and reaps processes cleanly."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time, sys; sys.stdin.read(); time.sleep(10)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIsNone(proc.poll())
        self.assertFalse(proc.stdin.closed)

        LiveAudioCaptureManager._terminate_process(proc)

        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(proc.stderr.closed)
        self.assertIsNotNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
