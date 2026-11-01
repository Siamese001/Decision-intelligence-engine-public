"""
tests/adversarial/test_document_ingestion_qualification_adversarial.py

Wave 6 Adversarial and Edge-Case Tests:
Covers adversarial boundary, missing receipts, budget exhaustion, and shortfall honesty:
1. Malformed or missing benchmark manifest handling.
2. Invariant bypass: forged or missing predecessor receipts fail closed.
3. Deadline and budget exhaustion cutoff prevention of infinite loops.
4. Honest shortfall reporting when performance targets are missed.
5. Corrupted or negative latency observations handling.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.document_acceptance_core.benchmark_runner import (
    calculate_distribution_metrics,
    execute_bounded_benchmark,
    validate_benchmark_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


class Wave6AdversarialTests(unittest.TestCase):
    def test_missing_or_malformed_manifest_fails_closed(self) -> None:
        """Verify that non-existent or malformed manifests fail closed."""
        with self.assertRaises(FileNotFoundError):
            validate_benchmark_manifest(Path("/nonexistent/manifest.json"), ROOT)

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_manifest = Path(tmpdir) / "bad_manifest.json"
            bad_manifest.write_text(json.dumps({"manifest_id": "incomplete"}))
            with self.assertRaises(ValueError) as ctx:
                validate_benchmark_manifest(bad_manifest, ROOT)
            self.assertIn("missing required key", str(ctx.exception).lower())

    def test_missing_or_forged_predecessor_receipt_rejected(self) -> None:
        """Verify that a manifest referencing an unverified or forged predecessor is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            forged_manifest = Path(tmpdir) / "forged_manifest.json"
            payload = {
                "manifest_id": "forged",
                "approved_run_count": 5,
                "total_deadline_seconds": 30.0,
                "sources": ["documents/project_lens/project-scope-items-for-review.docx"],
                "preceding_receipts": {
                    "wave_4": "reports/lens_runtime/wave-4-complete.json",
                    "wave_5": "reports/lens_runtime/nonexistent-wave-5.json",
                },
                "performance_targets": {"warm_median_seconds": 180.0},
            }
            forged_manifest.write_text(json.dumps(payload))
            with self.assertRaises(FileNotFoundError):
                validate_benchmark_manifest(forged_manifest, ROOT)

    def test_deadline_budget_exhaustion_cutoff(self) -> None:
        """Verify that deadline exhaustion terminates benchmark loop cleanly without hanging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "fast_deadline_manifest.json"
            out_benchmark = Path(tmpdir) / "out.json"
            payload = {
                "manifest_id": "fast-deadline",
                "approved_run_count": 1000,
                "total_deadline_seconds": 0.0001,  # immediate deadline
                "sources": ["documents/project_lens/project-scope-items-for-review.docx"],
                "preceding_receipts": {
                    "wave_4": "reports/lens_runtime/wave-4-complete.json",
                    "wave_5": "reports/lens_runtime/wave-5.json",
                },
                "performance_targets": {"warm_median_seconds": 180.0},
            }
            manifest_path.write_text(json.dumps(payload))
            result = execute_bounded_benchmark(manifest_path, out_benchmark, ROOT)
            # Should have terminated well before 1000 runs
            self.assertLess(len(result["observations"]), 5)

    def test_honest_shortfall_reporting_when_target_missed(self) -> None:
        """Verify that if performance target is not met, outcome is FAIL, never a relaxed PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "impossible_target_manifest.json"
            out_benchmark = Path(tmpdir) / "out.json"
            payload = {
                "manifest_id": "impossible-target",
                "approved_run_count": 5,
                "total_deadline_seconds": 30.0,
                "sources": ["documents/project_lens/project-scope-items-for-review.docx"],
                "preceding_receipts": {
                    "wave_4": "reports/lens_runtime/wave-4-complete.json",
                    "wave_5": "reports/lens_runtime/wave-5.json",
                },
                # Unattainable targets
                "performance_targets": {
                    "warm_median_seconds": 0.0000001,
                    "warm_p95_seconds": 0.0000001,
                    "cold_p95_seconds": 0.0000001,
                },
            }
            manifest_path.write_text(json.dumps(payload))
            result = execute_bounded_benchmark(manifest_path, out_benchmark, ROOT)
            self.assertFalse(result["performance_targets_met"])
            self.assertEqual(result["terminal_outcome"], "FAIL")

    def test_empty_observations_distribution_metrics_handling(self) -> None:
        """Verify that empty observations calculate safe zero-values without ZeroDivisionError."""
        metrics = calculate_distribution_metrics([])
        self.assertEqual(metrics["sample_count"], 0)
        self.assertEqual(metrics["cold"]["count"], 0)
        self.assertEqual(metrics["warm"]["median"], 0.0)
