"""
tests/test_lens_tokenomics_wave0_baseline.py

Unit tests for Wave 0: Project LENS Tokenomics & Spend Optimization Baseline.
Validates the empirical baseline metrics, invariant checks, and receipt construction.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.audit_lens_tokenomics_baseline import (
    EMPIRICAL_LENS_BASELINE,
    build_wave0_receipt,
    compute_tokenomics_baseline,
    verify_baseline_invariants,
)


class LensTokenomicsWave0BaselineTests(unittest.TestCase):
    def test_01_empirical_baseline_structure_and_metrics(self) -> None:
        """Verify the empirical baseline contains required incident intervals and counts."""
        baseline = dict(EMPIRICAL_LENS_BASELINE)
        self.assertIn("session_interval", baseline)
        self.assertIn("pipeline_attempts", baseline)
        self.assertIn("token_expenditures", baseline)
        self.assertIn("target_reductions", baseline)

        sess = baseline["session_interval"]
        self.assertEqual(sess["wall_clock_minutes"], 197.0)
        self.assertAlmostEqual(sess["completed_pipeline_minutes"], 81.66, places=2)
        self.assertAlmostEqual(sess["unaccounted_host_overhead_minutes"], 109.07, places=2)
        self.assertAlmostEqual(sess["unaccounted_overhead_pct"], 55.37, places=2)

        attempts = baseline["pipeline_attempts"]
        self.assertEqual(attempts["total_explicit_runs"], 36)
        self.assertEqual(attempts["failed_runs"], 31)
        self.assertEqual(attempts["completed_runs"], 5)
        self.assertAlmostEqual(attempts["failure_rate_pct"], 86.11, places=2)
        self.assertEqual(attempts["doomed_pre_l2_attempts"], 37)

    def test_02_token_expenditure_and_reasoning_breakdown(self) -> None:
        """Verify token expenditure counts across host, L2, micro-judge, and retries."""
        tokens = EMPIRICAL_LENS_BASELINE["token_expenditures"]
        self.assertEqual(tokens["host_total_tokens"], 261_000_000)
        self.assertEqual(tokens["host_cache_read_tokens"], 251_000_000)
        self.assertEqual(tokens["host_steps_count"], 1797)
        self.assertEqual(tokens["wasted_retry_tokens"], 820_000)
        self.assertEqual(tokens["stage03_graph_extraction_tokens"], 468_500)
        self.assertEqual(tokens["stage03_reasoning_tokens"], 222_000)
        self.assertEqual(tokens["micro_judge_tokens"], 361_600)
        self.assertEqual(tokens["micro_judge_reasoning_tokens"], 123_000)

    def test_03_verify_baseline_invariants_pass_on_valid_data(self) -> None:
        """Verify baseline invariants pass on empirical baseline data."""
        valid, errors = verify_baseline_invariants(EMPIRICAL_LENS_BASELINE)
        self.assertTrue(valid, f"Expected invariants to pass, but got: {errors}")
        self.assertEqual(len(errors), 0)

    def test_04_verify_baseline_invariants_fail_on_tampered_metrics(self) -> None:
        """Verify baseline invariants fail closed on invalid or regressed metrics."""
        tampered = {
            "session_interval": {"wall_clock_minutes": 100.0},
            "pipeline_attempts": {"total_explicit_runs": 10, "failed_runs": 2},
            "token_expenditures": {
                "host_total_tokens": 1_000_000,
                "wasted_retry_tokens": 10_000,
                "stage03_graph_extraction_tokens": 50_000,
                "micro_judge_tokens": 20_000,
            },
        }
        valid, errors = verify_baseline_invariants(tampered)
        self.assertFalse(valid)
        self.assertGreaterEqual(len(errors), 5)

    def test_05_build_wave0_receipt_payload_integrity(self) -> None:
        """Verify receipt structure and deliverable declarations."""
        baseline = compute_tokenomics_baseline()
        receipt = build_wave0_receipt(baseline)
        self.assertEqual(receipt["wave"], 0)
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertEqual(receipt["terminal_outcome"], "PASS")
        self.assertEqual(receipt["branch"], "feat/lens-tokenomics-optimization")
        self.assertIn("execution_command", receipt)
        self.assertIn("deliverables", receipt)
        self.assertGreaterEqual(len(receipt["deliverables"]), 5)


if __name__ == "__main__":
    unittest.main()
