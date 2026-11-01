"""
End-to-End Test for PR Queue Branch Column Width and RCA Verification.

Executes tools/pr_queue_render_probe.py via subprocess to verify:
1. RC2 word-break collapse reproduction on current 12-column table
2. Winning variant achieves >= 28 chars/line at 1000px viewport
3. Receipt output conforms to governance schema with PASS outcome
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestPRQueueBranchWidthE2E(unittest.TestCase):
    def test_pr_queue_render_probe_e2e(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            receipt_path = f.name

        try:
            cmd = [
                sys.executable,
                str(ROOT / "tools" / "pr_queue_render_probe.py"),
                "--viewports",
                "600,800,1000,1400",
                "--receipt",
                receipt_path,
                "--assert-branch-min-chars",
                "28",
            ]
            res = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Probe command failed with code {res.returncode}.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}",
            )

            # Validate receipt content
            with open(receipt_path, "r", encoding="utf-8") as f:
                receipt = json.load(f)

            self.assertEqual(receipt.get("terminal_outcome"), "PASS")
            rca = receipt.get("rca_verdicts", {})
            self.assertEqual(rca.get("rc1_no_width_contract"), "VERIFIED")
            self.assertIn(rca.get("rc2_branch_word_break_collapse"), ("VERIFIED", "OBSERVED_MARGINAL"))
            self.assertEqual(rca.get("rc3_w_tier_row_flood"), "VERIFIED")
            self.assertEqual(rca.get("rc4_lost_padding_fix"), "VERIFIED")
            self.assertEqual(rca.get("rc5_rule_contradiction"), "VERIFIED")

            # Check winning variant achieves >= 28 chars at 1000px
            recom = receipt.get("recommendation", {})
            self.assertGreaterEqual(recom.get("branch_chars_at_1000px", 0), 28)

            # Check that current 12-col layout was measured
            measurements = receipt.get("measurements", [])
            self.assertTrue(len(measurements) >= 4)
            current_1000 = [
                m for m in measurements if "current" in m["variant_name"] and m["viewport_width_px"] == 1000
            ]
            self.assertTrue(len(current_1000) > 0)

        finally:
            p = Path(receipt_path)
            if p.exists():
                p.unlink()


if __name__ == "__main__":
    unittest.main()
