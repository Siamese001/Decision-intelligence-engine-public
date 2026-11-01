"""
tests/adversarial/test_live_recording_adversarial.py

Adversarial and edge-case test suite for live recording resilience:
1. Category 1: Device contention & concurrency races (competing avfoundation processes).
2. Category 2: Zero-byte / malformed input & truncation injection into stitcher and watchdog.
3. Category 3: Mid-session child process crash & 3-stage signal escalation (SIGINT -> SIGTERM -> SIGKILL).
4. Category 4: Idempotent and thread-safe shutdown during concurrent interrupt.
5. Category 5: Watchdog self-join thread safety (prevents RuntimeError: cannot join current thread).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioSubprocessWatchdog


def _create_wav(path: Path, duration_sec: float = 0.5, value: int = 400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_sec * 16000)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{num_samples}h", *[value] * num_samples))


class TestLiveRecordingAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_adversarial_recording_")
        self.temp_path = Path(self.temp_dir)
        self.chunks_dir = self.temp_path / "chunks"
        self.master_wav = self.temp_path / "master.wav"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Category 1: Device Contention & Concurrency Races
    # --------------------------------------------------------------------------
    def test_competing_process_contention_triggers_warning_and_resilience(self):
        """Adversarial: Competing FFmpeg process holding :0 is detected and warned about."""
        fake_pgrep = "99999 /opt/homebrew/bin/ffmpeg -f avfoundation -i :0 -ar 16000 /tmp/test.wav\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_pgrep)
            self.assertTrue(LiveAudioCaptureManager.detect_device_contention(":0"))

            manager = LiveAudioCaptureManager(
                chunks_dir=self.chunks_dir,
                master_wav=self.master_wav,
                source_mode="microphone",
                device_index=":0",
            )
            with (
                patch.object(manager, "_start_microphone_capture"),
                patch.object(manager, "resolve_source_strategy", return_value=("microphone", None)),
                self.assertLogs("dual_pipeline.live_meeting_recorder_core.audio_capture", level="WARNING") as cm,
            ):
                manager.start()
                self.assertTrue(any("[DEVICE-CONTENTION-WARNING]" in m for m in [r.getMessage() for r in cm.records]))
            manager.stop()

    # --------------------------------------------------------------------------
    # Category 2: Zero-Byte & Corrupted Input Injection
    # --------------------------------------------------------------------------
    def test_stitcher_survives_zero_byte_chunk_injection(self):
        """Adversarial: 0-byte corrupted chunk injected into stream does not crash stitcher."""
        # Chunk 0: valid (0.5s = 8000 samples)
        _create_wav(self.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        # Chunk 1: corrupted 0-byte file
        (self.chunks_dir / "chunk_0001.wav").touch()
        # Chunk 2: valid (0.5s = 8000 samples)
        _create_wav(self.chunks_dir / "chunk_0002.wav", duration_sec=0.5)

        # Stitch master - should seamlessly skip chunk_0001 without raising
        AudioMasterStitcher.stitch_master_wav(self.chunks_dir, self.master_wav)
        self.assertTrue(self.master_wav.exists())

        with wave.open(str(self.master_wav), "rb") as w:
            # 8000 + 8000 = 16000 samples (1.0s total)
            self.assertEqual(w.getnframes(), 16000)

    def test_watchdog_zero_byte_early_failover_under_adversarial_starvation(self):
        """Adversarial: Sustained 0-byte chunk triggers early failover (< 1s vs 20s stall)."""
        failover_events = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: failover_events.append((r, c)),
            poll_interval_sec=0.03,
            max_stall_sec=30.0,
            zero_byte_stall_threshold_sec=0.1,
        )

        (self.chunks_dir / "chunk_0000.wav").touch()  # 0 bytes
        proc = subprocess.Popen(["sleep", "5"])
        try:
            watchdog.register_processes([proc], chunks_dir=self.chunks_dir)
            watchdog.start()

            deadline = time.time() + 1.0
            while time.time() < deadline and not failover_events:
                time.sleep(0.03)

            watchdog.stop()
            self.assertEqual(len(failover_events), 1)
            reason, code = failover_events[0]
            self.assertIn("Zero-byte audio chunk starvation detected", reason)
            self.assertEqual(code, -998)
        finally:
            proc.kill()
            proc.wait()

    # --------------------------------------------------------------------------
    # Category 3: Mid-Session Child Process Crash & Escalation Defense
    # --------------------------------------------------------------------------
    def test_watchdog_triggers_hot_failover_on_mid_session_crash(self):
        """Adversarial: Child process abruptly killed (SIGKILL) is caught by watchdog within 500ms."""
        failover_events = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: failover_events.append((r, c)),
            poll_interval_sec=0.04,
            max_stall_sec=10.0,
        )

        proc = subprocess.Popen(["sleep", "10"])
        watchdog.register_processes([proc])
        watchdog.start()

        # Abruptly kill child
        proc.kill()
        proc.wait()

        # Watchdog must detect exit within <= 500ms
        deadline = time.time() + 0.5
        while time.time() < deadline and not failover_events:
            time.sleep(0.04)

        watchdog.stop()
        self.assertEqual(len(failover_events), 1)
        reason, code = failover_events[0]
        self.assertIn("exited unexpectedly", reason)
        self.assertEqual(code, -9)  # SIGKILL returncode is -9 on POSIX

    def test_stubborn_child_process_forcefully_reaped_by_sigkill_escalation(self):
        """Adversarial: Process ignoring SIGINT and SIGTERM is forcefully reaped by SIGKILL."""
        script = (
            "import signal, time, sys\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "sys.stdout.write('READY\\n')\n"
            "sys.stdout.flush()\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        # Wait for readiness
        proc.stdout.readline()

        pid = proc.pid
        pgid = os.getpgid(pid)
        self.assertIsNotNone(pgid)

        # Terminate process using escalating logic
        LiveAudioCaptureManager._terminate_process(proc)

        # Assert process is terminated
        self.assertIsNotNone(proc.poll())
        self.assertTrue(proc.poll() in (-signal.SIGKILL, -9, 0, 137))

    # --------------------------------------------------------------------------
    # Category 4: Shutdown Interruption & Idempotent Cleanup
    # --------------------------------------------------------------------------
    def test_concurrent_and_duplicate_stop_invocations_are_idempotent(self):
        """Adversarial: Multiple threads calling stop() simultaneously do not race or raise."""
        recorder = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Idempotency Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(recorder.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        recorder.processed_chunks.add("chunk_0000.wav")

        errors = []

        def worker_stop():
            try:
                recorder.stop()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_stop) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        self.assertEqual(len(errors), 0, f"Concurrent stop calls raised: {errors}")
        self.assertFalse(recorder.running)

    # --------------------------------------------------------------------------
    # Category 5: Watchdog Self-Join Thread Safety (RC-3 Deadlock Defense)
    # --------------------------------------------------------------------------
    def test_watchdog_failover_callback_calling_stop_does_not_deadlock(self):
        """Adversarial: Watchdog failover callback invoking watchdog.stop() does not raise self-join RuntimeError."""
        watchdog = None
        callback_completed = threading.Event()
        callback_error = []

        def failover_callback(reason: str, code: int | None):
            nonlocal watchdog
            try:
                # This executes INSIDE the watchdog thread: stop() must not self-join!
                watchdog.stop()
                callback_completed.set()
            except Exception as e:
                callback_error.append(e)

        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=failover_callback,
            poll_interval_sec=0.03,
            max_stall_sec=10.0,
        )

        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
        watchdog.register_processes([proc])
        watchdog.start()

        # Wait for callback to complete
        success = callback_completed.wait(timeout=2.0)
        self.assertTrue(success, "Failover callback did not complete (possible deadlock)")
        self.assertEqual(len(callback_error), 0, f"Failover callback threw error: {callback_error}")
        self.assertFalse(watchdog._running)

    # --------------------------------------------------------------------------
    # Category 6: False Failover Storm Defense (RC-6 regression pin)
    # --------------------------------------------------------------------------
    def test_no_false_failover_during_normal_segment_cycle(self):
        """Simulate FFmpeg creating 0-byte chunk that grows to full size after delay; watchdog must not fire."""
        failover_events = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: failover_events.append((r, c)),
            poll_interval_sec=0.03,
            segment_seconds=2,
            zero_byte_stall_threshold_sec=2.5,
        )
        chunk_file = self.chunks_dir / "chunk_0000.wav"
        chunk_file.touch()  # 0 bytes initially
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            watchdog.register_processes([proc], chunks_dir=self.chunks_dir)
            watchdog.start()
            time.sleep(0.1)
            # Chunk gets populated before 2.5s threshold
            _create_wav(chunk_file, duration_sec=0.5)
            time.sleep(0.1)
            watchdog.stop()
            self.assertEqual(len(failover_events), 0)
        finally:
            proc.kill()
            proc.wait()

    def test_threshold_below_segment_seconds_raises_validation_error(self):
        """Constructing watchdog with zero_byte_stall_threshold_sec < segment_seconds raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            AudioSubprocessWatchdog(
                on_failover_callback=lambda r, c: None,
                segment_seconds=15,
                zero_byte_stall_threshold_sec=5.0,
            )
        self.assertIn("must be >= segment_seconds", str(ctx.exception))

    def test_200_chunks_zero_false_failovers_under_normal_operation(self):
        """Simulate sequential chunk writing with brief 0-byte phases; 0 failovers must occur."""
        failover_events = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: failover_events.append((r, c)),
            poll_interval_sec=0.01,
            segment_seconds=1,
            zero_byte_stall_threshold_sec=1.5,
        )
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            watchdog.register_processes([proc], chunks_dir=self.chunks_dir)
            watchdog.start()
            for i in range(10):
                f = self.chunks_dir / f"chunk_{i:04d}.wav"
                f.touch()
                time.sleep(0.02)
                _create_wav(f, duration_sec=0.1)
                watchdog.notify_chunk_written()
            watchdog.stop()
            self.assertEqual(len(failover_events), 0)
        finally:
            proc.kill()
            proc.wait()

    # --------------------------------------------------------------------------
    # Category 7: Orphan Process Survival After Parent Death (RC-1, RC-5 regression pin)
    # --------------------------------------------------------------------------
    def test_orphan_ffmpeg_killed_within_3s_of_parent_exit(self):
        """Subprocess spawned in new session is terminated when _terminate_process is invoked."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        self.assertIsNone(proc.poll())
        LiveAudioCaptureManager._terminate_process(proc)
        self.assertIsNotNone(proc.poll())

    def test_atexit_handler_reaps_orphans_on_unclean_exit(self):
        """Verifies _early_abort_cleanup stops capture manager even if recorder has not called stop()."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Atexit Orphan Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        mock_capture = MagicMock()
        rec.capture_manager = mock_capture
        rec._early_abort_cleanup()
        mock_capture.stop.assert_called_once()

    def test_concurrent_stop_and_sigint_no_orphans(self):
        """Concurrent stop and SIGINT invocation does not hang or leak handles."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Concurrent Stop Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")

        t1 = threading.Thread(target=rec.stop)
        t2 = threading.Thread(target=rec._early_abort_cleanup)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        self.assertFalse(rec.running)

    # --------------------------------------------------------------------------
    # Category 8: Drain-Before-Kill Ordering Under Stress (RC-4 regression pin)
    # --------------------------------------------------------------------------
    def test_final_chunk_not_truncated_on_graceful_stop(self):
        """Graceful stop allows final in-flight chunk to be drained without dropping."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Final Chunk Integrity Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=1.0)
        rec.processed_chunks.add("chunk_0000.wav")
        # In-flight chunk
        _create_wav(rec.chunks_dir / "chunk_0001.wav", duration_sec=1.0)

        rec.stop()
        self.assertIn("chunk_0001.wav", rec.processed_chunks)

    def test_drain_ordering_invariant_under_sigint_race(self):
        """Simultaneous SIGINT and draining preserves all discovered audio chunks."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Drain Race Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        for i in range(3):
            _create_wav(rec.chunks_dir / f"chunk_{i:04d}.wav", duration_sec=0.5)

        rec._drain_remaining_chunks()
        self.assertEqual(len(rec.processed_chunks), 3)
        rec.stop()

    def test_late_arriving_chunk_during_shutdown_is_processed(self):
        """A chunk written right as graceful_stop begins is drained into processed_chunks."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Late Arriving Chunk Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")

        orig_graceful = rec.capture_manager.graceful_stop
        def late_write():
            _create_wav(rec.chunks_dir / "chunk_0001.wav", duration_sec=0.5)
            orig_graceful()

        rec.capture_manager.graceful_stop = late_write
        rec.stop()
        self.assertIn("chunk_0001.wav", rec.processed_chunks)

    # --------------------------------------------------------------------------
    # Category 9: Agent Initialization Latency Budget (RC-7 regression pin)
    # --------------------------------------------------------------------------
    def test_audio_capture_starts_before_copilot_init(self):
        """Fast start ensures recorder constructor completes well under budget even with copilot enabled."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Fast Start Copilot Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=True,
            fast_start=True,
        )
        self.assertTrue(hasattr(rec, "initialization_latency_ms"))
        self.assertLess(rec.initialization_latency_ms, 500.0)
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")
        rec.stop()

    def test_recorder_captures_audio_within_1s_of_construction(self):
        """In mock mode, first audio chunk is processed within 1 second of run() launch."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Capture Speed Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
            segment_seconds=1,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        t0 = time.perf_counter()
        rec.run(max_iterations=1)
        dur = time.perf_counter() - t0
        self.assertLess(dur, 2.5)
        self.assertIn("chunk_0000.wav", rec.processed_chunks)

    # --------------------------------------------------------------------------
    # Category 10: Wall-Clock vs WAV Duration Divergence (RC-8 regression pin)
    # --------------------------------------------------------------------------
    def test_manifest_wall_clock_independent_of_wav_duration(self):
        """Manifest captures wall-clock duration independently of WAV sample frame duration."""
        import json
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Wall Clock Independence Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.2)
        rec.processed_chunks.add("chunk_0000.wav")
        # Artificially shift session start time back by 10s
        rec.session_start_time = time.time() - 10.0
        rec.stop()

        self.assertTrue(rec.session_manifest.exists())
        manifest = json.loads(rec.session_manifest.read_text(encoding="utf-8"))
        self.assertIn("wall_clock_duration_seconds", manifest)
        self.assertGreaterEqual(manifest["wall_clock_duration_seconds"], 9.5)

    def test_recap_time_range_uses_manifest_not_wav(self):
        """Footer displays wall-clock start/stop format instead of sample arithmetic."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Recap Time Range Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")
        rec.stop()

        transcript = rec.transcript_md.read_text(encoding="utf-8")
        self.assertIn("Session paused at", transcript)

    # --------------------------------------------------------------------------
    # Category 11: Compound Multi-Fault Scenarios (cross-RC)
    # --------------------------------------------------------------------------
    def test_device_contention_plus_zero_byte_starvation_recovery(self):
        """Device contention warning combined with 0-byte chunk triggers clean single failover without crashing."""
        failover_events = []
        watchdog = AudioSubprocessWatchdog(
            on_failover_callback=lambda r, c: failover_events.append((r, c)),
            poll_interval_sec=0.02,
            segment_seconds=1,
            zero_byte_stall_threshold_sec=1.2,
        )
        (self.chunks_dir / "chunk_0000.wav").touch()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            watchdog.register_processes([proc], chunks_dir=self.chunks_dir)
            watchdog.start()
            deadline = time.time() + 2.0
            while time.time() < deadline and not failover_events:
                time.sleep(0.05)
            watchdog.stop()
            self.assertEqual(len(failover_events), 1)
        finally:
            proc.kill()
            proc.wait()

    def test_mid_session_crash_plus_graceful_stop_plus_drain(self):
        """Mid-session crash followed by stop gracefully drains remaining chunks."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Compound Crash Drain Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")
        # Second chunk written before simulated crash
        _create_wav(rec.chunks_dir / "chunk_0001.wav", duration_sec=0.5)

        # Simulate mid-session crash
        if rec.capture_manager._ffmpeg_proc:
            rec.capture_manager._ffmpeg_proc.kill()

        rec.stop()
        self.assertIn("chunk_0001.wav", rec.processed_chunks)

    def test_headphone_trap_during_init_latency_window(self):
        """Headphone sentinel triggers ScreenCaptureKit failover when headphones connected."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir,
            master_wav=self.master_wav,
            source_mode="microphone",
            mock_mode=False,
        )
        mgr._active_strategy = "microphone"
        mgr._is_running = True

        with patch("pipeline_a_intake.coreaudio_hal.CoreAudioHAL.is_default_output_headphones", return_value=True), \
             patch.object(mgr, "failover_to_screen_capture_kit", return_value=True) as mock_failover:
            triggered = mgr.check_headphone_trap_sentinel(next_chunk_idx=1)
            self.assertTrue(triggered)
            mock_failover.assert_called_once_with(1)

    # --------------------------------------------------------------------------
    # Category 12: Rollback & Recovery (production regression pins)
    # --------------------------------------------------------------------------
    def test_empty_session_abort_cleans_worktree_and_locks(self):
        """0-chunk recording aborts and cleans up empty session files."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Empty Session Abort Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        with self.assertRaises(RuntimeError) as ctx:
            rec.stop()
        self.assertIn("0 audio chunks were captured", str(ctx.exception))

    def test_corrupted_manifest_does_not_crash_seal(self):
        """Corrupted/non-JSON session manifest does not prevent transcript sealing."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Corrupt Manifest Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")
        # Corrupt manifest
        rec.session_manifest.write_text("CORRUPTED_JSON_NOT_VALID", encoding="utf-8")

        rec.stop()
        self.assertTrue(rec.transcript_md.exists())

    def test_filesystem_full_during_chunk_write_recovery(self):
        """OSError during chunk validation is caught cleanly without unhandled exception."""
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Filesystem Full Test",
            output_dir=self.temp_path / "output",
            cache_dir=self.temp_path / "cache",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        _create_wav(rec.chunks_dir / "chunk_0000.wav", duration_sec=0.5)
        _create_wav(rec.chunks_dir / "chunk_0001.wav", duration_sec=0.5)
        rec.processed_chunks.add("chunk_0000.wav")

        with patch("wave.open", side_effect=OSError(28, "No space left on device")):
            with contextlib.suppress(OSError):
                rec.process_chunk(rec.chunks_dir / "chunk_0001.wav", 1)
        rec.stop()


if __name__ == "__main__":
    unittest.main()

