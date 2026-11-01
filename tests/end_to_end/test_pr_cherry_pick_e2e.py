"""
End-to-End test suite for PR Cherry-Pick Assessor CLI (Wave 5 / Unified Wave 8).
Validates full CLI invocation, receipt emission, JSON schema compliance,
and terminal summary formatting.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pr_overlap"
AUDIT_SCRIPT = Path(__file__).resolve().parent.parent.parent / "tools" / "pr_overlap_audit.py"


class TestPRCherryPickE2E(unittest.TestCase):
    """End-to-End execution tests for pr_overlap_audit.py cherry-pick."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_cli_cherry_pick_clean_branch_receipt_emission(self):
        """Validates end-to-end CLI run emitting receipt for a clean branch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            receipt_file = Path(tmp_dir) / "wave5_receipt.json"
            output_file = Path(tmp_dir) / "wave5_plan.json"

            cmd = [
                sys.executable,
                str(AUDIT_SCRIPT),
                "cherry-pick",
                "--fixture", str(FIXTURE_DIR),
                "--branch", "feat/cherry-clean",
                "--receipt", str(receipt_file),
                "--output", str(output_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, f"CLI invocation failed: {proc.stderr}")
            self.assertIn("PR Cherry-Pick Plan", proc.stdout)
            self.assertIn("PICK_CLEAN", proc.stdout)
            self.assertIn("git cherry-pick", proc.stdout)

            # Validate receipt content
            self.assertTrue(receipt_file.exists())
            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["schema_version"], "urn:nee:pr-cherry-pick-plan:1.0.0")
            self.assertEqual(receipt_data["terminal_outcome"], "PASS")
            self.assertEqual(receipt_data["gate_status"], "PASSED")
            self.assertTrue(receipt_data["zero_mutation_verified"])
            self.assertTrue(receipt_data["scratch_agreement_verified"])
            self.assertEqual(receipt_data["extract_set_count"], 1)

    def test_cli_cherry_pick_json_output_mode(self):
        """Validates JSON output mode to stdout."""
        cmd = [
            sys.executable,
            str(AUDIT_SCRIPT),
            "cherry-pick",
            "--fixture", str(FIXTURE_DIR),
            "--branch", "feat/cherry-prereq",
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["schema_version"], "urn:nee:pr-cherry-pick-plan:1.0.0")
        self.assertEqual(data["total_commits"], 2)
        self.assertEqual(len(data["extract_set"]), 2)
        self.assertTrue(data["scratch_agreement_verified"])


if __name__ == "__main__":
    unittest.main()
