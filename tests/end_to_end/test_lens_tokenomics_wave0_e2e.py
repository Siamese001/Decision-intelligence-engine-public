"""
tests/end_to_end/test_lens_tokenomics_wave0_e2e.py

End-to-end lifecycle test for Wave 0: Project LENS Tokenomics Baseline.
Verifies programmatic execution of baseline audit, receipt emission into isolated
temporary directory, and plan governance schema compliance.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.audit_lens_tokenomics_baseline import (
    build_wave0_receipt,
    compute_tokenomics_baseline,
    verify_baseline_invariants,
)
from tools.validate_implementation_plan import validate_plan_content

ROOT = Path(__file__).resolve().parents[2]


class LensTokenomicsWave0E2ETests(unittest.TestCase):
    def test_01_full_wave0_audit_and_receipt_lifecycle(self) -> None:
        """Run full audit pipeline and verify receipt serialization to disk."""
        with tempfile.TemporaryDirectory(prefix="lens_tokenomics_test_") as tmpdir:
            receipt_file = Path(tmpdir) / "wave0_receipt.json"

            # 1. Compute baseline
            baseline = compute_tokenomics_baseline()
            valid, errors = verify_baseline_invariants(baseline)
            self.assertTrue(valid, f"Baseline invariant failure: {errors}")

            # 2. Build receipt
            summary = {"total": 15, "passed": 15, "failed": 0, "duration_sec": 0.42}
            receipt = build_wave0_receipt(baseline, test_summary=summary)

            # 3. Write to temporary disk
            with open(receipt_file, "w", encoding="utf-8") as f:
                json.dump(receipt, f, indent=2)

            self.assertTrue(receipt_file.exists())
            with open(receipt_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            self.assertEqual(loaded["wave"], 0)
            self.assertEqual(loaded["terminal_outcome"], "PASS")
            self.assertEqual(loaded["test_summary"]["passed"], 15)

    def test_02_implementation_plan_governance_e2e(self) -> None:
        """Verify the authored implementation plan passes strict governance validation."""
        plan_path = ROOT / "docs/plans/feat_lens_tokenomics_optimization_plan.md"
        self.assertTrue(plan_path.exists(), f"Missing plan at {plan_path}")

        with open(plan_path, "r", encoding="utf-8") as f:
            content = f.read()

        is_compliant, errors = validate_plan_content(content, filename=plan_path.name)
        self.assertTrue(is_compliant, f"Plan governance check failed: {errors}")
        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
