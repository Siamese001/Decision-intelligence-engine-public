"""
tests/test_record_start_latency_adversarial.py

Wave 5 Adversarial & Edge-Case Test Suite: Latency & Failure Diagnosis Governance.
Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Exact production regression: Pin Gemini Pro zero-byte failure (exit 11) -> No Terminal handoff, verified_os_denial=False.
2. Invariant bypass attempt: Rejection of unverified permission claims from generic audio exceptions.
3. Rollback & recovery: Session-scoped process recovery without broad process kills (pkill -f ffmpeg).
4. Boundary payload: Slow source open (>2s) -> missing PCM stays None.
5. Malformed input: Corrupted probe JSON and missing markers.
6. Concurrency & race: Cohort isolation between warm and cold runs.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    ASRState,
    DiarizationState,
    RecordingServiceAdapter,
    RecordingSessionHandle,
    RecordingStartRequest,
    RecordingState,
    extract_verified_os_denial,
)
from tools.benchmark_latency_core.probe import run_isolated_probe_cycle
from tools.benchmark_latency_core.stats import (
    compute_latency_stats,
    evaluate_startup_budget_qualification,
)


class TestRecordStartLatencyAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self.adapter = RecordingServiceAdapter(repo_root=self.repo_root)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_adv_exact_production_regression_zero_bytes_exit_11(self) -> None:
        """Exact production regression: Gemini Pro zero-byte exit 11 must NOT claim permission denied.

        In the incident, Pro observed 0 bytes after 2.54s with exit code 11 and falsely diagnosed
        missing microphone permissions, instructing the user to run Terminal.
        Verify that this condition reports PCM_BYTE_RATE_DEFICIT, verified_os_denial=False,
        remediation=None, and does not delegate to Terminal.
        """
        err = AudioCaptureUnprovenError(
            stage="P1_PCM_BYTE_RATE",
            reason_code=AudioReasonCode.PCM_BYTE_RATE_DEFICIT,
            detail="PCM byte rate deficit: master WAV has 0B after 2.54s (0.0 B/s vs expected 64000.0 B/s, exit 11).",
            remediation=None,
        )
        remediation, denial_ev = extract_verified_os_denial(err)
        self.assertIsNone(remediation)
        self.assertIsNone(denial_ev)

        req = RecordingStartRequest(
            project_name="proj_rca",
            meeting_name="Incident Replay Session",
            mock_mode=False,
        )

        with patch("dual_pipeline.live_meeting_recorder.LiveMeetingRecorder") as mock_rec_cls:
            mock_rec = MagicMock()
            mock_rec.start_ffmpeg.side_effect = err
            mock_rec_cls.return_value = mock_rec

            self.adapter.start_recording(req)

            deadline = time.time() + 2.0
            status = None
            while time.time() < deadline:
                status = self.adapter.get_status(req.request_id)
                if status and status.recording_state == RecordingState.FAILED:
                    break
                time.sleep(0.05)

            self.assertIsNotNone(status)
            self.assertEqual(status.recording_state, RecordingState.FAILED)
            self.assertFalse(status.verified_os_denial)
            self.assertIsNone(status.remediation)
            self.assertNotIn("Terminal", str(status.remediation))
            self.assertNotIn("Terminal", str(status.error))

    def test_adv_invariant_bypass_spoofed_permission_string(self) -> None:
        """Invariant bypass attempt: Generic error mentioning AVFoundation or SCK must not bypass denial gate."""
        generic_err = RuntimeError("AVFoundation device buffer overflow while opening stream")
        remediation, denial_ev = extract_verified_os_denial(generic_err)
        self.assertIsNone(remediation)
        self.assertIsNone(denial_ev)

    def test_adv_rollback_and_session_scoped_recovery_no_broad_kill(self) -> None:
        """Rollback & Concurrency: Recovering Session 1 must not terminate or disrupt Session 2 (no pkill)."""
        req1 = RecordingStartRequest(session_id="session_target_1", project_name="p1", meeting_name="Session 1", mock_mode=True)
        req2 = RecordingStartRequest(session_id="session_target_2", project_name="p2", meeting_name="Session 2", mock_mode=True)

        resp1 = self.adapter.start_recording(req1)
        resp2 = self.adapter.start_recording(req2)

        handle1 = self.adapter._sessions.get("session_target_1")
        handle2 = self.adapter._sessions.get("session_target_2")
        self.assertIsNotNone(handle1)
        self.assertIsNotNone(handle2)

        # Mock mock_proc on both
        mock_proc1 = MagicMock()
        mock_proc1.pid = 99111
        mock_proc2 = MagicMock()
        mock_proc2.pid = 99222

        handle1.recorder = MagicMock()
        handle1.recorder.ffmpeg_proc = mock_proc1
        handle2.recorder = MagicMock()
        handle2.recorder.ffmpeg_proc = mock_proc2

        # Recover only Session 1
        recovery_report = self.adapter.recover_session("session_target_1")
        self.assertIsNotNone(recovery_report)
        self.assertEqual(recovery_report["session_id"], "session_target_1")
        self.assertIn(99111, recovery_report["reaped_pids"])

        # Session 1 state should be FAILED
        status1 = self.adapter.get_status("session_target_1")
        self.assertEqual(status1.recording_state, RecordingState.FAILED)

        # Session 2 must remain completely intact and unaffected
        status2 = self.adapter.get_status("session_target_2")
        self.assertIn(status2.recording_state, (RecordingState.STARTING, RecordingState.CAPTURING))
        handle2.recorder._early_abort_cleanup.assert_not_called()

    def test_adv_boundary_slow_source_open_retains_missing_pcm(self) -> None:
        """Boundary payload: When source open times out and no PCM arrives, t_first_pcm stays None."""
        probe_res = {
            "cohort": "warm",
            "run_index": 1,
            "status": "PASS",
            "t_cli_import_s": 0.05,
            "t_capture_spawn_s": 0.12,
            "t_first_pcm_byte_s": None,  # Timed out waiting for PCM!
            "t_first_nonzero_sample_s": None,
            "t_recorder_init_s": 0.02,
            "t_mlx_ready_s": 0.01,
            "t_recording_confirmed_s": 4.15,
            "has_pcm": False,
        }

        stats = compute_latency_stats([probe_res["t_first_pcm_byte_s"]])
        self.assertEqual(stats["missing_count"], 1)
        self.assertEqual(stats["valid_count"], 0)
        self.assertEqual(stats["deficit_rate"], 1.0)

        # Qualification fails due to missing PCM
        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.120},
            "t_first_pcm_byte_s": stats,
            "t_recording_confirmed_s": {"p95": 4.150},
        }
        qualification = evaluate_startup_budget_qualification(
            warm_stats=warm_stats,
            total_missing_pcm=1,
        )
        self.assertFalse(qualification["qualified"])
        self.assertFalse(qualification["gates"]["gate_no_missing_pcm"]["pass"])

    def test_adv_malformed_probe_output_handled_gracefully(self) -> None:
        """Malformed input: Probe returning corrupted or missing JSON markers fails cleanly."""
        res = run_isolated_probe_cycle(
            python_bin="/usr/bin/false",
            repo_root=self.repo_root,
            mode="mock",
            project="p_malformed",
            meeting_title="Malformed Probe",
            run_idx=1,
            timeout_sec=5.0,
        )
        self.assertEqual(res["status"], "FAIL")
        self.assertIsNone(res["t_first_pcm_byte_s"])
        self.assertFalse(res["has_pcm"])

    def test_adv_concurrency_cohort_isolation(self) -> None:
        """Concurrency / cohort isolation: Slow cold starts must not pollute warm cohort p95."""
        warm_pcm = [0.15, 0.18, 0.20, 0.22]
        cold_pcm = [3.80]

        warm_stats = compute_latency_stats(warm_pcm)
        cold_stats = compute_latency_stats(cold_pcm)

        self.assertLess(warm_stats["p95"], 0.25)
        self.assertGreater(cold_stats["p95"], 3.0)


if __name__ == "__main__":
    unittest.main()
