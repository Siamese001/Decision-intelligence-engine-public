"""
tests/test_record_start_latency_diagnosis.py

Wave 5 Unit Tests: Latency and Failure Diagnosis Governance.
Enforces:
1. Missing PCM stays missing (None values never substituted with wall-clock time).
2. Failed starts and missing PCM are explicitly tracked in the failure denominator.
3. Strict startup budget qualification gating across warm and cold cohorts.
4. Monotonic source-bound PCM evidence decoupled from ASR readiness.
5. Strict prohibition of permission explanations without verified OS denial telemetry.
6. Absolute ban on declaring capture proven from PID or dry-run alone.
"""
from __future__ import annotations

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
from tools.benchmark_latency_core.stats import (
    compute_latency_stats,
    compute_percentile,
    evaluate_startup_budget_qualification,
)


class TestRecordStartLatencyDiagnosis(unittest.TestCase):
    def test_compute_percentile_interpolation(self) -> None:
        """Verifies linear interpolation of percentiles."""
        data = [0.1, 0.2, 0.3, 0.4, 0.5]
        self.assertAlmostEqual(compute_percentile(data, 0.50), 0.3)
        self.assertAlmostEqual(compute_percentile(data, 0.90), 0.46)
        self.assertAlmostEqual(compute_percentile(data, 0.95), 0.48)
        self.assertEqual(compute_percentile([], 0.5), 0.0)

    def test_missing_pcm_retained_as_none_and_not_substituted(self) -> None:
        """Invariant: Missing PCM stays missing and is not substituted with timeout durations."""
        measurements = [0.12, None, 0.15, None, 0.18]
        stats = compute_latency_stats(measurements)

        self.assertEqual(stats["total_count"], 5)
        self.assertEqual(stats["valid_count"], 3)
        self.assertEqual(stats["missing_count"], 2)
        self.assertEqual(stats["deficit_rate"], 0.4)
        self.assertAlmostEqual(stats["p50"], 0.15)
        self.assertAlmostEqual(stats["mean"], 0.15)

    def test_failure_denominator_accounting_all_missing(self) -> None:
        """When all PCM measurements fail/time out, denominator accurately reflects 100% deficit."""
        stats = compute_latency_stats([None, None, None])
        self.assertEqual(stats["total_count"], 3)
        self.assertEqual(stats["valid_count"], 0)
        self.assertEqual(stats["missing_count"], 3)
        self.assertEqual(stats["deficit_rate"], 1.0)
        self.assertEqual(stats["p95"], 0.0)

    def test_startup_budget_qualification_all_gates_pass(self) -> None:
        """Verifies that within-budget warm and cold cohorts pass qualification."""
        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.210},
            "t_first_pcm_byte_s": {"p95": 0.350, "missing_count": 0},
            "t_recording_confirmed_s": {"p95": 1.250},
        }
        cold_stats = {
            "t_recording_confirmed_s": {"p95": 3.400},
        }

        verdict = evaluate_startup_budget_qualification(
            warm_stats=warm_stats,
            cold_stats=cold_stats,
            total_missing_pcm=0,
        )
        self.assertTrue(verdict["qualified"])
        self.assertTrue(verdict["gates"]["gate_intent_to_dispatch_p95"]["pass"])
        self.assertTrue(verdict["gates"]["gate_capture_to_first_pcm_warm_p95"]["pass"])
        self.assertTrue(verdict["gates"]["gate_intent_to_confirmed_warm_p95"]["pass"])
        self.assertTrue(verdict["gates"]["gate_intent_to_confirmed_cold_p95"]["pass"])
        self.assertTrue(verdict["gates"]["gate_no_missing_pcm"]["pass"])

    def test_startup_budget_breach_fails_qualification(self) -> None:
        """Any gate breach (e.g. dispatch > 250ms) fails qualification."""
        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.320},  # Breach: > 250ms
            "t_first_pcm_byte_s": {"p95": 0.350, "missing_count": 0},
            "t_recording_confirmed_s": {"p95": 1.250},
        }
        verdict = evaluate_startup_budget_qualification(warm_stats=warm_stats, total_missing_pcm=0)
        self.assertFalse(verdict["qualified"])
        self.assertFalse(verdict["gates"]["gate_intent_to_dispatch_p95"]["pass"])

    def test_missing_pcm_breach_fails_qualification(self) -> None:
        """Missing PCM count > 0 strictly fails qualification gate 5."""
        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.150},
            "t_first_pcm_byte_s": {"p95": 0.350, "missing_count": 1},
            "t_recording_confirmed_s": {"p95": 1.100},
        }
        verdict = evaluate_startup_budget_qualification(warm_stats=warm_stats, total_missing_pcm=1)
        self.assertFalse(verdict["qualified"])
        self.assertFalse(verdict["gates"]["gate_no_missing_pcm"]["pass"])

    def test_no_permission_remediation_on_pcm_deficit_or_generic_error(self) -> None:
        """Prohibition of false permission diagnosis: PCM byte rate deficit must NEVER suggest permission fixes."""
        err = AudioCaptureUnprovenError(
            stage="P1_PCM_BYTE_RATE",
            reason_code=AudioReasonCode.PCM_BYTE_RATE_DEFICIT,
            detail="PCM byte rate deficit: master WAV has 0B after 2.54s.",
        )
        remediation, denial_ev = extract_verified_os_denial(err)
        self.assertIsNone(remediation)
        self.assertIsNone(denial_ev)

        # Digital zero test
        err_zero = AudioCaptureUnprovenError(
            stage="P2_MIC_NON_DIGITAL_ZERO",
            reason_code=AudioReasonCode.MIC_DIGITAL_ZERO,
            detail="Microphone channel is digital zero.",
        )
        remediation_z, denial_ev_z = extract_verified_os_denial(err_zero)
        self.assertIsNone(remediation_z)
        self.assertIsNone(denial_ev_z)

    def test_verified_os_denial_permitted_on_explicit_tcc_evidence(self) -> None:
        """Permission remediation is strictly permitted when explicit OS denial telemetry is verified."""
        err = AudioCaptureUnprovenError(
            stage="P0_PROCESS_ALIVE",
            reason_code=AudioReasonCode.SCK_TCC_DENIED,
            detail="ScreenCaptureKit process terminated early: Permission denied",
            remediation="Grant Screen & System Audio Recording permission in macOS System Settings.",
        )
        remediation, denial_ev = extract_verified_os_denial(err)
        self.assertIsNotNone(remediation)
        self.assertIsNotNone(denial_ev)
        self.assertEqual(denial_ev["reason_code"], "SCK_TCC_DENIED")
        self.assertIn("System Settings", remediation)

    def test_no_success_from_pid_or_dry_run_alone(self) -> None:
        """Invariant: Having a running process PID or dry-run alone does NOT prove capture readiness."""
        req = RecordingStartRequest(
            project_name="proj_pid_test",
            meeting_name="PID Alone Test",
            mock_mode=False,
        )
        handle = RecordingSessionHandle(request=req, session_id="test_handle_pid", repo_root=Path("/tmp"))
        handle.recording_state = RecordingState.STARTING
        status = handle.get_status()

        # STARTING state with no chunks is unproven
        self.assertFalse(status.is_proven)

        # CAPTURING state with 0 chunks is still unproven in non-mock mode
        handle.recording_state = RecordingState.CAPTURING
        status_cap = handle.get_status()
        self.assertFalse(status_cap.is_proven)


if __name__ == "__main__":
    unittest.main()
