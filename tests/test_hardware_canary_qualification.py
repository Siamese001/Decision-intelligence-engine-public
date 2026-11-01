"""
tests/test_hardware_canary_qualification.py

Wave 6 Unit Tests: Host & Hardware Qualification Canary.
Enforces:
1. Complete host environment telemetry (Apple Silicon, Darwin, CoreAudio HAL, SCK authorization).
2. Distinct mock and representative hardware evidence tiers.
3. Model comparison parity matrix across Gemini Pro and Flash profiles.
4. Zero formal chat replies on speaker hints during qualification.
5. Strict failure denominator accounting and preceding receipt chaining.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.hardware_canary_core.models import (
    CanaryTopologyResult,
    ModelComparisonRecord,
    evaluate_canary_qualification,
    inspect_host_hardware,
)
from tools.qualify_hardware_canary import (
    replay_model_comparison_matrix,
    run_mock_topology_benchmark,
    verify_preceding_receipts,
)


class TestHardwareCanaryQualification(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_host_hardware_inspection_structure(self) -> None:
        """Verifies host environment inspection extracts valid Apple Silicon and CoreAudio metadata."""
        meta = inspect_host_hardware()
        self.assertIsNotNone(meta.platform_name)
        self.assertIsNotNone(meta.darwin_version)
        self.assertIsNotNone(meta.architecture)
        self.assertIsNotNone(meta.python_version)
        self.assertIsInstance(meta.is_apple_silicon, bool)
        self.assertIsInstance(meta.sck_tap_authorized, bool)
        self.assertIsInstance(meta.metal_inference_available, bool)
        self.assertIsInstance(meta.audio_devices, list)

    def test_mock_topology_benchmark_execution(self) -> None:
        """Verifies mock topology executes warm and cold cohorts with distinct evidence tagging."""
        res = run_mock_topology_benchmark(warm_samples=5, cold_samples=2)
        self.assertEqual(res.topology_name, "mock_isolated")
        self.assertEqual(res.warm_samples_requested, 5)
        self.assertEqual(res.cold_samples_requested, 2)
        self.assertEqual(res.total_attempts, 7)
        self.assertEqual(res.successful_starts, 7)
        self.assertEqual(res.failed_starts, 0)
        self.assertEqual(res.missing_pcm_count, 0)
        self.assertTrue(res.is_hardware_distinct)
        self.assertTrue(res.qualified)
        self.assertLess(res.p95_dispatch_s, 0.250)
        self.assertLess(res.p95_first_pcm_s, 0.500)
        self.assertLess(res.p95_confirmed_warm_s, 1.500)
        self.assertLess(res.p95_confirmed_cold_s, 5.000)

    def test_model_comparison_parity_matrix(self) -> None:
        """Verifies model comparison replaying start and hint tools across model tiers."""
        records = replay_model_comparison_matrix(self.repo_root)
        self.assertEqual(len(records), 2)

        for r in records:
            self.assertLess(r.dispatch_latency_ms, 250.0)
            self.assertTrue(r.silent_hint_intake_success)
            self.assertEqual(r.chat_replies_emitted, 0)  # Zero chat replies!
            self.assertTrue(r.capture_ownership_retained)

    def test_evaluate_canary_qualification_pass(self) -> None:
        """Verifies overall canary qualification passes when all gates and comparisons succeed."""
        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=30,
            cold_samples_requested=10,
            total_attempts=40,
            successful_starts=40,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.010,
            p95_first_pcm_s=0.015,
            p95_confirmed_warm_s=0.300,
            p95_confirmed_cold_s=0.900,
            is_hardware_distinct=True,
            qualified=True,
        )
        mc = [
            ModelComparisonRecord(
                model_id="pro",
                dispatch_latency_ms=15.0,
                silent_hint_intake_success=True,
                chat_replies_emitted=0,
                capture_ownership_retained=True,
            ),
            ModelComparisonRecord(
                model_id="flash",
                dispatch_latency_ms=12.0,
                silent_hint_intake_success=True,
                chat_replies_emitted=0,
                capture_ownership_retained=True,
            ),
        ]
        ok = evaluate_canary_qualification([top], mc, preceding_receipts_ok=True)
        self.assertTrue(ok)

    def test_evaluate_canary_qualification_fails_on_missing_preceding_receipts(self) -> None:
        """Verifies qualification fails if preceding receipts are not sealed."""
        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=5,
            cold_samples_requested=2,
            total_attempts=7,
            successful_starts=7,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.010,
            p95_first_pcm_s=0.015,
            p95_confirmed_warm_s=0.300,
            p95_confirmed_cold_s=0.900,
            is_hardware_distinct=True,
            qualified=True,
        )
        ok = evaluate_canary_qualification([top], [], preceding_receipts_ok=False)
        self.assertFalse(ok)

    def test_evaluate_canary_qualification_fails_on_chat_reply(self) -> None:
        """Verifies qualification fails if any model emits a chat reply to a speaker hint."""
        top = CanaryTopologyResult(
            topology_name="mock_isolated",
            warm_samples_requested=5,
            cold_samples_requested=2,
            total_attempts=7,
            successful_starts=7,
            failed_starts=0,
            missing_pcm_count=0,
            p95_dispatch_s=0.010,
            p95_first_pcm_s=0.015,
            p95_confirmed_warm_s=0.300,
            p95_confirmed_cold_s=0.900,
            is_hardware_distinct=True,
            qualified=True,
        )
        mc = [
            ModelComparisonRecord(
                model_id="pro",
                dispatch_latency_ms=15.0,
                silent_hint_intake_success=True,
                chat_replies_emitted=1,  # Defect: emitted chat reply!
                capture_ownership_retained=True,
            )
        ]
        ok = evaluate_canary_qualification([top], mc, preceding_receipts_ok=True)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
