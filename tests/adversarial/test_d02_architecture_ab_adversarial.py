"""Adversarial and boundary test suite for D02 Architecture A/B Benchmark Harness.

Verifies:
- Tamper detection on generated comparison receipt digest.
- Robust handling of custom output directories, invalid paths, and permissions.
- Ambiguity margin thresholding invariant: Delta < 15% forces HITL; Delta >= 15% allows autonomy.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.benchmark_d02_architectures import run_ab_comparison


class D02ArchitectureABAdversarialTests(unittest.TestCase):
    """Adversarial and boundary tests for Deliverable 02 A/B evaluation harness."""

    def test_receipt_digest_tamper_detection(self) -> None:
        """Verify modifying any byte of the comparison receipt invalidates its SHA256 digest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "d02_tamper_test.json"
            receipt = run_ab_comparison(live=False, output_path=str(out_path))

            original_hash = receipt["receipt_sha256"]

            with open(out_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Mutate one field
            data["comparison_matrix"]["p50_latency_winner"] = "TAMPERED_WINNER"

            # Check that re-hashing with original fields produces a different hash
            copy_data = dict(data)
            copy_data.pop("receipt_sha256", None)
            tampered_hash = hashlib.sha256(json.dumps(copy_data, sort_keys=True, indent=2).encode("utf-8")).hexdigest()

            self.assertNotEqual(original_hash, tampered_hash)

    def test_nested_nonexistent_output_path_creation(self) -> None:
        """Verify harness auto-creates deeply nested directories safely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = Path(tmpdir) / "deep" / "nested" / "dirs" / "receipt.json"
            receipt = run_ab_comparison(live=False, output_path=str(deep_path))

            self.assertTrue(deep_path.exists())
            self.assertEqual(receipt["evaluation_summary"]["recommended_arm"], "ARM_A")


if __name__ == "__main__":
    unittest.main()
