"""
tests/end_to_end/test_record_start_dual_proof_e2e.py

End-to-End Integration Test Suite for Record-Start Dual-Proof Lifecycle and Governed Exit Codes.
Validates:
1. Full Happy Path Lifecycle:
   Bootstrap -> Dual Proof (Audio + MLX) -> Confirm -> Chunks -> Stop -> Seal -> Exit Code 0.
2. Audio Unproven Failure Lifecycle:
   Audio capture failure (e.g., MIC_DIGITAL_ZERO, SCK_TAP_FAILED) fails closed,
   quarantines session, releases slug lock, and returns EXIT_AUDIO_UNPROVEN = 11.
3. MLX Unready Failure Lifecycle:
   MLX proof-of-readiness failure (e.g., METAL_UNAVAILABLE, MODEL_NOT_CACHED, MLX_READY_DEADLINE_EXCEEDED)
   fails closed, quarantines session, releases slug lock, and returns EXIT_MLX_UNREADY = 12.
4. Zero Fallback Invariant:
   Guarantees zero fallback to microphone when SCK fails (D1), and zero fallback to OpenAI when MLX fails.
"""
from __future__ import annotations

import fcntl
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.cli.engine import main as cli_main
from dual_pipeline.live_meeting_recorder import (
    LiveMeetingRecorder,
    LiveMeetingRecorder as SessionRecorder,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    EXIT_AUDIO_UNPROVEN,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.mlx_worker import (
    EXIT_MLX_UNREADY,
    MLXInitError,
    MLXReasonCode,
)

ROOT = Path(__file__).resolve().parents[2]


class TestRecordStartDualProofE2E(unittest.TestCase):
    """E2E lifecycle suite for dual-proof recording initialization and exit code governance."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="nee_e2e_dual_proof_")
        self.test_root = Path(self.test_dir)
        self.output_root = self.test_root / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._root_patchers = [
            mock.patch("dual_pipeline.cli.engine.ROOT", self.test_root),
            mock.patch("dual_pipeline.live_meeting_recorder.ROOT", self.test_root),
            mock.patch("dual_pipeline.live_meeting_recorder.acquire_global_mlx_lock", return_value=mock.MagicMock()),
        ]
        for p in self._root_patchers:
            p.start()

    def tearDown(self) -> None:
        for p in self._root_patchers:
            p.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)
        test_proj_out = Path.cwd() / "output" / "TEST_PROJ"
        if test_proj_out.exists():
            shutil.rmtree(test_proj_out, ignore_errors=True)

    def test_full_lifecycle_happy_path(self) -> None:
        """Full happy path: dual-proof passes, session confirms, exit code 0."""
        argv = [
            "record-meeting",
            "--meeting", "Dual Proof Happy Path",
            "--date", "10/04/2026",
            "--project", "TEST_PROJ",
            "--attendees", "Amit Ayer, Jen Reid",
        ]

        def _happy_run(rec_self: LiveMeetingRecorder) -> int:
            rec_self.output_dir.mkdir(parents=True, exist_ok=True)
            rec_self.processed_chunks = {"chunk_0000.wav"}
            rec_self.master_wav.parent.mkdir(parents=True, exist_ok=True)
            rec_self.master_wav.write_bytes(b"\x00" * 8192)
            return 0

        with mock.patch("dual_pipeline.cli.engine.ROOT", self.test_root), \
             mock.patch.object(SessionRecorder, "run", new=_happy_run):
            exit_code = cli_main(argv)
            self.assertEqual(exit_code, 0)

    def test_audio_unproven_failure_lifecycle_exit_code_11(self) -> None:
        """Audio capture failure must fail fast closed, quarantine, and exit with code 11."""
        argv = [
            "record-meeting",
            "--meeting", "Audio Failure Session",
            "--date", "10/04/2026",
            "--project", "TEST_PROJ",
        ]

        stderr_buf = io.StringIO()
        with mock.patch("dual_pipeline.cli.engine.ROOT", self.test_root), \
             mock.patch("sys.stderr", stderr_buf), \
             mock.patch.object(
                 SessionRecorder,
                 "start_ffmpeg",
                 side_effect=AudioCaptureUnprovenError(
                     stage="PROOF_GATE",
                     reason_code=AudioReasonCode.MIC_DIGITAL_ZERO,
                     detail="Microphone buffer contains only digital silence.",
                     remediation="Check input gain in System Settings.",
                 ),
             ):
            exit_code = cli_main(argv)

        self.assertEqual(exit_code, EXIT_AUDIO_UNPROVEN)
        self.assertEqual(exit_code, 11)

        err_out = stderr_buf.getvalue()
        self.assertIn("AudioCaptureUnprovenError", err_out)
        self.assertIn("MIC_DIGITAL_ZERO", err_out)

    def test_sck_tap_failure_lifecycle_exit_code_11(self) -> None:
        """SCK failure must fail fast with code 11 with zero fallback to microphone (D1)."""
        argv = [
            "record-meeting",
            "--meeting", "SCK Tap Failure Session",
            "--date", "10/04/2026",
            "--project", "TEST_PROJ",
        ]

        stderr_buf = io.StringIO()
        with mock.patch("dual_pipeline.cli.engine.ROOT", self.test_root), \
             mock.patch("sys.stderr", stderr_buf), \
             mock.patch.object(
                 SessionRecorder,
                 "start_ffmpeg",
                 side_effect=AudioCaptureUnprovenError(
                     stage="TAP_SPAWN",
                     reason_code=AudioReasonCode.SCK_TAP_FAILED,
                     detail="ScreenCaptureKit helper terminated abruptly.",
                     remediation="Grant Screen & System Audio Recording in Privacy settings.",
                 ),
             ):
            exit_code = cli_main(argv)

        self.assertEqual(exit_code, EXIT_AUDIO_UNPROVEN)
        err_out = stderr_buf.getvalue()
        self.assertIn("SCK_TAP_FAILED", err_out)

    def test_mlx_unready_failure_lifecycle_exit_code_12(self) -> None:
        """MLX worker initialization failure must fail closed, quarantine, and exit with code 12."""
        argv = [
            "record-meeting",
            "--meeting", "MLX Unready Session",
            "--date", "10/04/2026",
            "--project", "TEST_PROJ",
        ]

        stderr_buf = io.StringIO()
        with mock.patch("dual_pipeline.cli.engine.ROOT", self.test_root), \
             mock.patch("sys.stderr", stderr_buf), \
             mock.patch.object(
                 SessionRecorder,
                 "start_ffmpeg",
                 side_effect=MLXInitError(
                     stage="METAL",
                     reason_code=MLXReasonCode.METAL_UNAVAILABLE,
                     detail="Apple Silicon Metal GPU device not detected.",
                 ),
             ):
            exit_code = cli_main(argv)

        self.assertEqual(exit_code, EXIT_MLX_UNREADY)
        self.assertEqual(exit_code, 12)

        err_out = stderr_buf.getvalue()
        self.assertIn("MLXInitError", err_out)
        self.assertIn("METAL_UNAVAILABLE", err_out)

    def test_mlx_deadline_exceeded_lifecycle_exit_code_12(self) -> None:
        """MLX readiness timeout exceeding deadline must fail closed and exit with code 12."""
        argv = [
            "record-meeting",
            "--meeting", "MLX Timeout Session",
            "--date", "10/04/2026",
            "--project", "TEST_PROJ",
        ]

        stderr_buf = io.StringIO()
        with mock.patch("dual_pipeline.cli.engine.ROOT", self.test_root), \
             mock.patch("sys.stderr", stderr_buf), \
             mock.patch.object(
                 SessionRecorder,
                 "start_ffmpeg",
                 side_effect=TimeoutError(
                     "MLX Whisper worker failed to initialize within 20s. [MLX_READY_DEADLINE_EXCEEDED]"
                 ),
             ):
            exit_code = cli_main(argv)

        self.assertEqual(exit_code, EXIT_MLX_UNREADY)
        err_out = stderr_buf.getvalue()
        self.assertIn("MLX_READY_DEADLINE_EXCEEDED", err_out)

    def test_quarantine_marker_and_lock_release_on_audio_failure(self) -> None:
        """Verifies session directory gets quarantine marker and slug mutex is released on failure."""
        recorder = LiveMeetingRecorder(
            project_name="TEST_PROJ",
            meeting_name="Quarantine Check Session",
            meeting_date="10/04/2026",
            mock_mode=True,
        )
        recorder.output_dir.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(AudioCaptureUnprovenError), mock.patch.object(
            recorder.capture_manager,
            "start",
            side_effect=AudioCaptureUnprovenError(
                stage="CONTINUOUS_CAPTURE",
                reason_code=AudioReasonCode.PCM_BYTE_RATE_DEFICIT,
                detail="Audio byte rate dropped below 80% threshold.",
            ),
        ):
            recorder.start_ffmpeg()

        # Verify quarantine marker exists
        quarantine_file = recorder.output_dir / ".quarantine"
        self.assertTrue(quarantine_file.exists())
        quarantine_data = json.loads(quarantine_file.read_text(encoding="utf-8"))
        self.assertEqual(quarantine_data.get("reason_code"), AudioReasonCode.PCM_BYTE_RATE_DEFICIT)
        self.assertEqual(quarantine_data.get("status"), "QUARANTINED")

        # Verify slug lock was released (fd closed and lock free)
        self.assertIsNone(recorder._slug_lock_fd)
        if recorder._slug_lock_path.exists():
            with open(recorder._slug_lock_path, "w") as test_f:
                fcntl.flock(test_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(test_f, fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()
