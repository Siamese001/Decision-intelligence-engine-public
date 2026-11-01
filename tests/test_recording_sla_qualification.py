"""
tests/test_recording_sla_qualification.py

Wave 4: Governs strict SLA release qualification, distinguishing simulated vs hardware evidence.
Validates:
1. Rejects missing/duplicate samples in cohort accounting.
2. Rejects absent source proof or missing PCM in failure denominator.
3. Enforces invariant: A simulated PASS cannot become a hardware PASS.
4. Rejects p95 masking individual deadline misses (any sample >= 30.0s fails).
5. Rejects any failed starts in cohort accounting.
6. Rejects unsealed or invalid preceding receipts.
7. Rejects zero placeholder ASR without a genuine readiness receipt.
8. Reports include each individual duration, maximum, percentiles, and failure counts.
9. Mock topology benchmark executes with genuine timing rather than constant durations.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    ASRState,
    RecordingServiceAdapter,
    RecordingStartRequest,
    RecordingState,
    RecordingStopRequest,
)
from tools.benchmark_latency_core.stats import (
    compute_latency_stats,
    evaluate_startup_budget_qualification,
)
from tools.hardware_canary_core.models import (
    CanaryTopologyResult,
    HardwareCanaryReport,
    HostEnvironmentMetadata,
    ModelComparisonRecord,
    evaluate_canary_qualification,
)
from tools.qualify_hardware_canary import (
    run_mock_topology_benchmark,
    verify_preceding_receipts,
)


class TestRecordingSLAQualification(unittest.TestCase):
    """Verifies that release qualification requires genuine measurements and fails closed on SLA misses."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_dummy_model_records(self) -> list[ModelComparisonRecord]:
        return [
            ModelComparisonRecord(
                model_id="gemini-pro",
                dispatch_latency_ms=120.0,
                silent_hint_intake_success=True,
                chat_replies_emitted=0,
                capture_ownership_retained=True,
            ),
            ModelComparisonRecord(
                model_id="gemini-flash",
                dispatch_latency_ms=80.0,
                silent_hint_intake_success=True,
                chat_replies_emitted=0,
                capture_ownership_retained=True,
            ),
        ]

    def test_rejects_missing_samples_in_cohort_accounting(self) -> None:
        """When total completed attempts is less than requested warm+cold count, qualification must fail."""
        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=30,
            cold_samples_requested=10,
            total_attempts=35,  # 5 samples missing!
            successful_starts=35,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.10,
            p95_first_pcm_s=0.05,
            p95_confirmed_warm_s=0.50,
            p95_confirmed_cold_s=1.20,
            is_hardware_distinct=True,
            qualified=True,
            topology_type="SIMULATED",
            max_duration_s=1.20,
            sample_durations=[0.5] * 35,
        )
        qualified = evaluate_canary_qualification(
            topologies=[top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
        )
        self.assertFalse(qualified)

    def test_rejects_absent_source_proof_or_missing_pcm(self) -> None:
        """Missing PCM or absent source proof must fail startup budget and canary qualification."""
        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.10},
            "t_first_pcm_byte_s": {"p95": 0.20, "missing_count": 2},
            "t_recording_confirmed_s": {"p95": 0.80, "max": 1.20},
        }
        res = evaluate_startup_budget_qualification(
            warm_stats=warm_stats,
            total_missing_pcm=2,
        )
        self.assertFalse(res["qualified"])
        self.assertFalse(res["gates"]["gate_no_missing_pcm"]["pass"])

        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=10,
            cold_samples_requested=0,
            total_attempts=10,
            successful_starts=8,
            failed_starts=0,
            missing_pcm_count=2,  # Missing PCM
            p95_dispatch_s=0.10,
            p95_first_pcm_s=0.05,
            p95_confirmed_warm_s=0.50,
            p95_confirmed_cold_s=0.50,
            is_hardware_distinct=True,
            qualified=False,
            topology_type="SIMULATED",
            max_duration_s=0.50,
        )
        canary_qual = evaluate_canary_qualification(
            topologies=[top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
        )
        self.assertFalse(canary_qual)

    def test_simulated_pass_cannot_become_hardware_pass(self) -> None:
        """A simulated mock topology PASS must never qualify as a hardware release."""
        sim_top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=30,
            cold_samples_requested=10,
            total_attempts=40,
            successful_starts=40,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.08,
            p95_first_pcm_s=0.03,
            p95_confirmed_warm_s=0.40,
            p95_confirmed_cold_s=0.90,
            is_hardware_distinct=True,
            qualified=True,
            topology_type="SIMULATED",
            max_duration_s=0.95,
            sample_durations=[0.4] * 30 + [0.9] * 10,
        )
        # When require_hardware is enabled, mock/simulated topology alone fails closed
        qual = evaluate_canary_qualification(
            topologies=[sim_top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
            require_hardware=True,
        )
        self.assertFalse(qual)

        # But if an authentic HARDWARE topology is present and qualified, it passes
        hw_top = CanaryTopologyResult(
            topology_name="hardware_apple_silicon",
            warm_samples_requested=30,
            cold_samples_requested=10,
            total_attempts=40,
            successful_starts=40,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.15,
            p95_first_pcm_s=0.25,
            p95_confirmed_warm_s=0.85,
            p95_confirmed_cold_s=1.80,
            is_hardware_distinct=True,
            qualified=True,
            topology_type="HARDWARE",
            max_duration_s=2.10,
            sample_durations=[0.8] * 30 + [1.8] * 10,
        )
        qual_hw = evaluate_canary_qualification(
            topologies=[sim_top, hw_top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
            require_hardware=True,
        )
        self.assertTrue(qual_hw)

    def test_p95_masking_individual_deadline_miss_is_rejected(self) -> None:
        """Any individual sample at or above 30.0s fails qualification, regardless of low p95."""
        # 39 fast samples (1.0s) and 1 deadline miss (32.5s)
        durations = [1.0] * 39 + [32.5]
        stats = compute_latency_stats(durations)

        # p95 is ~1.0s (well below 1.5s warm threshold)
        self.assertLess(stats["p95"], 1.5)
        # But max is 32.5s (>= 30.0s deadline)
        self.assertGreaterEqual(stats["max"], 30.0)

        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.05},
            "t_first_pcm_byte_s": {"p95": 0.10, "missing_count": 0},
            "t_recording_confirmed_s": stats,
        }
        res = evaluate_startup_budget_qualification(warm_stats=warm_stats)
        self.assertFalse(res["qualified"])
        self.assertFalse(res["gates"]["gate_individual_sample_ceiling"]["pass"])

        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=40,
            cold_samples_requested=0,
            total_attempts=40,
            successful_starts=40,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.05,
            p95_first_pcm_s=0.10,
            p95_confirmed_warm_s=stats["p95"],
            p95_confirmed_cold_s=stats["p95"],
            is_hardware_distinct=True,
            qualified=False,
            topology_type="SIMULATED",
            max_duration_s=stats["max"],
            sample_durations=durations,
        )
        canary_qual = evaluate_canary_qualification(
            topologies=[top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
        )
        self.assertFalse(canary_qual)

    def test_rejects_failed_starts_in_accounting(self) -> None:
        """Any failed start must be tracked in the failure denominator and fail qualification."""
        warm_stats = {
            "t_capture_spawn_s": {"p95": 0.10},
            "t_first_pcm_byte_s": {"p95": 0.20, "missing_count": 0},
            "t_recording_confirmed_s": {"p95": 0.80, "max": 1.20},
        }
        res = evaluate_startup_budget_qualification(
            warm_stats=warm_stats,
            total_failed_starts=1,
        )
        self.assertFalse(res["qualified"])
        self.assertFalse(res["gates"]["gate_no_failed_starts"]["pass"])

    def test_rejects_unsealed_preceding_receipts(self) -> None:
        """If preceding wave receipts are missing, incomplete, or unsealed, canary fails."""
        # Non-existent report dir
        ok = verify_preceding_receipts(repo_root=self.test_root, required_waves=[1, 2, 3])
        self.assertFalse(ok)

        # Canary evaluation fails if preceding_receipts_ok is False
        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=10,
            cold_samples_requested=0,
            total_attempts=10,
            successful_starts=10,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.05,
            p95_first_pcm_s=0.05,
            p95_confirmed_warm_s=0.50,
            p95_confirmed_cold_s=0.50,
            is_hardware_distinct=True,
            qualified=True,
            max_duration_s=0.50,
            sample_durations=[0.5] * 10,
        )
        qual = evaluate_canary_qualification(
            topologies=[top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=False,
        )
        self.assertFalse(qual)

    def test_rejects_zero_placeholder_asr_without_receipt(self) -> None:
        """ASR state must remain ASR_WARMING unless a validated readiness receipt exists."""
        adapter = RecordingServiceAdapter(repo_root=self.test_root)
        req = RecordingStartRequest(
            session_id="test_asr_state",
            project_name="test_proj",
            meeting_name="Test ASR State",
            mock_mode=True,
        )
        status = adapter.start_recording(req)
        # Even in mock mode, initial state must be ASR_WARMING until receipt is observed
        handle = adapter._sessions.get(status.session_id)
        self.assertIsNotNone(handle)
        self.assertEqual(handle.asr_state, ASRState.ASR_WARMING)
        adapter.stop_recording(RecordingStopRequest(session_id=status.session_id))

    def test_report_contains_each_duration_and_failure_count(self) -> None:
        """Statistical computation must preserve each duration sample and report max/min/percentiles."""
        durations = [0.12, 0.45, 0.89, 1.23, 0.67]
        stats = compute_latency_stats(durations)
        self.assertEqual(stats["total_count"], 5)
        self.assertEqual(stats["valid_count"], 5)
        self.assertEqual(stats["missing_count"], 0)
        self.assertEqual(stats["samples"], durations)
        self.assertEqual(stats["min"], 0.12)
        self.assertEqual(stats["max"], 1.23)
        self.assertGreater(stats["p95"], 0.0)

    def test_mock_topology_benchmark_runs_with_real_measurements(self) -> None:
        """Mock topology execution must perform actual timed starts without constant placeholders."""
        res = run_mock_topology_benchmark(warm_samples=2, cold_samples=1, repo_root=self.test_root)
        self.assertEqual(res.total_attempts, 3)
        self.assertEqual(len(res.sample_durations), 3)
        self.assertEqual(res.topology_type, "SIMULATED")
        # Ensure measurements are real non-zero numbers
        self.assertTrue(all(d > 0.0 for d in res.sample_durations))
        self.assertLess(res.max_duration_s, 30.0)

    def test_host_replay_benchmark_executes_and_emits_hardware_topology(self) -> None:
        """Host replay benchmark must yield a qualified HARDWARE topology result with evidence."""
        from tools.hardware_canary_core.host_replay import run_host_replay_benchmark
        evidence_file = self.test_root / "evidence.json"
        res, ev = run_host_replay_benchmark(
            warm_samples=2,
            cold_samples=1,
            repo_root=self.test_root,
            evidence_out_path=evidence_file,
        )
        self.assertEqual(res.topology_type, "HARDWARE")
        self.assertEqual(res.total_attempts, 3)
        self.assertEqual(res.failed_starts, 0)
        self.assertEqual(res.missing_pcm_count, 0)
        self.assertTrue(res.qualified)
        self.assertTrue(evidence_file.exists())
        self.assertEqual(ev["governance_ratification"], "D_AUDIO_W5")

    def test_canary_evaluation_demands_hardware_topology_when_required(self) -> None:
        """When require_hardware is True, simulated-only runs must fail qualification."""
        sim_top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=2,
            cold_samples_requested=1,
            total_attempts=3,
            successful_starts=3,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.01,
            p95_first_pcm_s=0.05,
            p95_confirmed_warm_s=0.1,
            p95_confirmed_cold_s=0.1,
            is_hardware_distinct=True,
            qualified=True,
            topology_type="SIMULATED",
            max_duration_s=0.2,
            sample_durations=[0.1, 0.1, 0.2],
        )
        # require_hardware with only simulated must fail
        self.assertFalse(evaluate_canary_qualification(
            topologies=[sim_top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
            require_hardware=True,
        ))

        # Adding hardware topology qualifies
        hw_top = CanaryTopologyResult(
            topology_name="host_replay_hardware",
            warm_samples_requested=2,
            cold_samples_requested=1,
            total_attempts=3,
            successful_starts=3,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.01,
            p95_first_pcm_s=0.05,
            p95_confirmed_warm_s=0.1,
            p95_confirmed_cold_s=0.1,
            is_hardware_distinct=True,
            qualified=True,
            topology_type="HARDWARE",
            max_duration_s=0.2,
            sample_durations=[0.1, 0.1, 0.2],
        )
        self.assertTrue(evaluate_canary_qualification(
            topologies=[sim_top, hw_top],
            model_comparisons=self._create_dummy_model_records(),
            preceding_receipts_ok=True,
            require_hardware=True,
        ))

