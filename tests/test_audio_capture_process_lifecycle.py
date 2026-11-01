"""Unit and integration tests for audio capture process lifecycle, PGID isolation, and forced termination."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
)
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    _ACTIVE_RECORDERS,
    _cleanup_active_recorders,
)


class TestAudioCaptureProcessLifecycle(unittest.TestCase):
    """Validates process-group isolation and termination cascades."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_terminate_process_clean_exit(self) -> None:
        """Verifies clean termination of a subprocess in a new session."""
        _mgr = LiveAudioCaptureManager(
            chunks_dir=self.tmp_path / "chunks",
            master_wav=self.tmp_path / "master.wav",
            source_mode="mock",
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertIsNone(proc.poll())
        pid = proc.pid
        pgid = os.getpgid(pid)
        self.assertEqual(pgid, pid)

        LiveAudioCaptureManager._terminate_process(proc)
        self.assertIsNotNone(proc.poll())

    def test_terminate_process_escalation_to_sigkill(self) -> None:
        """Verifies escalation to SIGKILL when process ignores SIGTERM."""
        _mgr = LiveAudioCaptureManager(
            chunks_dir=self.tmp_path / "chunks",
            master_wav=self.tmp_path / "master.wav",
            source_mode="mock",
        )
        # Process ignores SIGTERM
        code = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "sys.stdout.write('READY\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(10)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.assertIsNotNone(proc.stdout)
        self.assertEqual(proc.stdout.readline().strip(), "READY")
        self.assertIsNone(proc.poll())

        LiveAudioCaptureManager._terminate_process(proc)

        self.assertIsNotNone(proc.poll())
        self.assertIn(proc.returncode, (-signal.SIGKILL, -9, 9))

    def test_stop_processes_clears_handles(self) -> None:
        """Verifies stop_processes terminates all tracked processes and resets state."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.tmp_path / "chunks",
            master_wav=self.tmp_path / "master.wav",
            source_mode="mock",
        )
        p1 = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        p2 = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        mgr._sck_proc = p1
        mgr._ffmpeg_proc = p2

        mgr.stop_processes()

        self.assertIsNone(mgr._sck_proc)
        self.assertIsNone(mgr._ffmpeg_proc)
        self.assertIsNotNone(p1.poll())
        self.assertIsNotNone(p2.poll())

    def test_destructor_safety_net(self) -> None:
        """Verifies __del__ triggers stop_processes without raising."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.tmp_path / "chunks",
            master_wav=self.tmp_path / "master.wav",
            source_mode="mock",
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        mgr._ffmpeg_proc = proc
        mgr.__del__()
        self.assertIsNotNone(proc.poll())

    def test_atexit_cleanup_handler(self) -> None:
        """Verifies _cleanup_active_recorders stops processes for active recorders."""
        mock_rec = MagicMock()
        mock_rec.running = True
        mock_rec.capture_manager = MagicMock()

        _ACTIVE_RECORDERS.add(mock_rec)
        _cleanup_active_recorders()

        mock_rec.capture_manager.stop_processes.assert_called_once()

    def test_atexit_cleanup_when_running_is_false_adversarial(self) -> None:
        """Adversarial test: Verifies cleanup when recorder.stop() set running=False before crashing."""
        mock_rec = MagicMock()
        mock_rec.running = False  # Set to False early in stop() before stitch or diarization crash
        mock_rec.capture_manager = MagicMock()

        _ACTIVE_RECORDERS.add(mock_rec)
        _cleanup_active_recorders()

        # Must still call stop_processes even if running is False
        mock_rec.capture_manager.stop_processes.assert_called_once()

    def test_sck_process_failure_terminates_ffmpeg_group(self) -> None:
        """Adversarial test: Verifies SCK failure invokes _terminate_process on FFmpeg instead of leaking."""
        mgr = LiveAudioCaptureManager(
            chunks_dir=self.tmp_path / "chunks",
            master_wav=self.tmp_path / "master.wav",
            source_mode="mock",
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        mgr._ffmpeg_proc = proc
        sck_dead = MagicMock()
        sck_dead.poll.return_value = 1
        sck_dead.stderr = None
        mgr._sck_proc = sck_dead

        # Simulate SCK probation check failure
        mgr._detected_headphones = False
        with self.assertRaises(AudioCaptureUnprovenError):
            mgr._verify_sck_process()

        # The original proc must have been killed by _terminate_process
        self.assertIsNotNone(proc.poll())
        # Clean up any fallback capture process launched during failover
        mgr.stop_processes()
        self.assertIsNone(mgr._ffmpeg_proc)

    def test_reap_descendants_kills_orphaned_grandchild(self) -> None:
        """Verifies _reap_descendants aggressively terminates nested child processes."""
        code = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "sys.stdout.write(f'{p.pid}\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.assertIsNotNone(parent.stdout)
        child_pid_str = parent.stdout.readline().strip()
        self.assertTrue(child_pid_str.isdigit())
        child_pid = int(child_pid_str)

        LiveAudioCaptureManager._terminate_process(parent)
        self.assertIsNotNone(parent.poll())

        # Check that grandchild process was reaped
        try:
            os.kill(child_pid, 0)
            is_alive = True
        except (ProcessLookupError, OSError):
            is_alive = False
        self.assertFalse(is_alive, f"Child PID {child_pid} survived termination!")

    def test_early_abort_cleanup_stops_capture_manager(self) -> None:
        """Verifies _early_abort_cleanup calls stop on capture_manager."""
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
        rec = LiveMeetingRecorder(
            project_name="project_nexus",
            meeting_name="Early Abort Test",
            mock_mode=True,
            enable_ws=False,
            enable_copilot=False,
        )
        mock_capture = MagicMock()
        rec.capture_manager = mock_capture

        rec._early_abort_cleanup()
        mock_capture.stop.assert_called_once()

        import os
        import shutil
        if os.path.exists(rec.output_dir):
            shutil.rmtree(rec.output_dir)


if __name__ == "__main__":
    unittest.main()

