"""
End-to-End lifecycle test for PR Overlap Planner (Wave 4 / Unified Wave 7).

Executes tools/pr_overlap_audit.py plan across the multi-branch fixture repository,
validating full CLI lifecycle, receipt persistence, and zero repository mutation.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pr_overlap"


class TestPROverlapPlannerE2E(unittest.TestCase):
    """E2E lifecycle test verifying CLI order planner and zero-mutation receipts."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_full_pr_overlap_plan_e2e(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            receipt_path = Path(tmp_dir) / "wave4_receipt.json"

            # 1. Record repo state digest before CLI execution
            proc_before_refs = subprocess.run(
                ["git", "for-each-ref"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            )
            proc_before_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            )

            # 2. Execute CLI plan command
            cmd = [
                sys.executable,
                str(ROOT / "tools" / "pr_overlap_audit.py"),
                "plan",
                "--fixture",
                str(FIXTURE_DIR),
                "--base",
                "main",
                "--receipt",
                str(receipt_path),
                "--json",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, f"pr_overlap_audit plan failed: {proc.stderr}")

            # 3. Validate JSON output
            plan_data = json.loads(proc.stdout)
            self.assertEqual(plan_data["schema_version"], "urn:nee:pr-overlap-plan:1.0.0")
            self.assertTrue(plan_data["zero_mutation_verified"])
            self.assertLessEqual(plan_data["total_conflicts"], plan_data["alphabetical_conflicts"])
            self.assertGreater(len(plan_data["ordered_steps"]), 0)

            # Check stacked branch recommendation
            stacked_recs = [r for r in plan_data["rebase_recommendations"] if r["is_stacked"]]
            self.assertGreater(len(stacked_recs), 0)
            self.assertIn("feat/theta-stacked-on-beta", [r["branch"] for r in stacked_recs])

            # 4. Validate Receipt on disk
            self.assertTrue(receipt_path.exists())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["schema_version"], "urn:nee:pr-overlap-plan:1.0.0")
            self.assertEqual(receipt["terminal_outcome"], "PASS")
            self.assertTrue(receipt["zero_mutation_verified"])
            self.assertEqual(receipt["gate_status"], "PASSED")

            # 5. Verify Zero Repository Mutation
            proc_after_refs = subprocess.run(
                ["git", "for-each-ref"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            )
            proc_after_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertEqual(proc_before_refs.stdout, proc_after_refs.stdout)
            self.assertEqual(proc_before_status.stdout, proc_after_status.stdout)


if __name__ == "__main__":
    unittest.main()
