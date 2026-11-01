"""End-to-End test suite for D02 Architecture A/B Benchmark Harness.

Verifies:
- Execution of comparative A/B evaluation across Arm A, Arm B, and Arm C.
- Accurate calculation of confidence levels, p50/p95 latencies, word-floor pass rates, and token economy.
- Mathematical rigor of ambiguity margin calculation (Delta = c_top - c_second < 15%).
- Durable emission of comparison receipt with SHA256 integrity hash.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.benchmark_d02_architectures import run_ab_comparison


class D02ArchitectureABBenchmarkE2ETests(unittest.TestCase):
    """End-to-end tests for Deliverable 02 A/B architectural evaluation."""

    def test_e2e_ab_comparison_receipt_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "test_ab_comparison.json"
            receipt = run_ab_comparison(live=False, output_path=str(out_path))

            self.assertTrue(out_path.exists())
            self.assertIn("receipt_sha256", receipt)
            self.assertEqual(receipt["schema_version"], "urn:nee:d02:architecture-ab-comparison:1.0.0")

            # Verify arms
            metrics = receipt["metrics_by_arm"]
            self.assertIn("ARM_A", metrics)
            self.assertIn("ARM_B", metrics)
            self.assertIn("ARM_C", metrics)

            arm_a = metrics["ARM_A"]
            arm_b = metrics["ARM_B"]
            arm_c = metrics["ARM_C"]

            # Latency: Arm A is fastest (p50 ~ 7.2s vs Arm B 17.5s)
            self.assertLess(arm_a["p50_latency_sec"], arm_b["p50_latency_sec"])
            self.assertLess(arm_a["p50_latency_sec"], arm_c["p50_latency_sec"])

            # Word-floor pass rate: Arm A & C achieve >95%, Arm B drops to ~71%
            self.assertGreater(arm_a["word_floor_pass_rate"], 0.95)
            self.assertGreater(arm_c["word_floor_pass_rate"], 0.95)
            self.assertLess(arm_b["word_floor_pass_rate"], 0.80)

            # Billable token economy under caching: Arm A requires only 291 billable prompt tokens
            self.assertEqual(arm_a["billable_prompt_tokens"], 291)
            self.assertEqual(arm_b["billable_prompt_tokens"], 97)

            # Coherence: Arm C is 100%, Arm A is 99.2%
            self.assertEqual(arm_c["coherence_pass_rate"], 1.0)
            self.assertGreater(arm_a["coherence_pass_rate"], 0.98)

            # HITL evaluation summary
            summary = receipt["evaluation_summary"]
            self.assertEqual(summary["recommended_arm"], "ARM_A")
            self.assertEqual(summary["calibrated_confidence_top"], 0.88)
            self.assertEqual(summary["calibrated_confidence_second"], 0.82)
            self.assertAlmostEqual(summary["confidence_margin_delta"], 0.06, places=4)
            # Delta 0.06 < 0.15 => HITL gating must be triggered
            self.assertTrue(summary["hitl_gating_triggered"])


if __name__ == "__main__":
    unittest.main()
