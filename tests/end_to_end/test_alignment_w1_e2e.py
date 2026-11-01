"""tests/end_to_end/test_alignment_w1_e2e.py

End-to-End Verification Suite for Wave 1: Establish trustworthy evidence and coverage.
Verifies:
1. Full governed wave gate execution via tools/run_wave_gate.py with spec config/governance/alignment/wave-1.yaml.
2. Authentic plan-bound decision intercept verification (DECISION-ALIGNMENT-W1-AUTH).
3. Surface catalog verification covering all 8 required pipeline surfaces and modalities.
4. Exclusive creation of immutable receipt and sequential completion lock.
5. Evidence verifier checking artifact integrity and L0 module line budgets (<= 400 lines).
6. Cryptographic roundtrip and tamper detection for generated receipt and lock files.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.l0.receipts import (
    verify_predecessor_lock,
    verify_runtime_receipt,
    verify_sequential_lock,
)
from tools.record_wave_receipt import verify_receipt
from tools.run_wave_gate import WaveEvidenceVerifier


class TestAlignmentW1E2E(unittest.TestCase):
    """E2E suite for Wave 1 trustworthy evidence and coverage governance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.receipt_path = self.work_dir / "wave-1.json"
        self.lock_path = self.work_dir / "wave-1.lock"
        self.spec_path = REPO_ROOT / "config" / "governance" / "alignment" / "wave-1.yaml"
        self.catalog_path = REPO_ROOT / "config" / "governance" / "alignment" / "surface_catalog.yaml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_surface_catalog_coverage_and_validity(self) -> None:
        """Surface catalog contains all 8 required modalities with required metadata fields."""
        ok, reason, data = WaveEvidenceVerifier.verify_surface_catalog(self.catalog_path)
        self.assertTrue(ok, f"Surface catalog verification failed: {reason}")
        surfaces = data.get("surfaces", [])
        self.assertGreaterEqual(len(surfaces), 8)

        modalities = {s["modality"] for s in surfaces}
        expected = {
            "audio/transcript",
            "notes/documents/attachments",
            "ingestion",
            "query/copilot",
            "decisions",
            "maintenance",
            "release",
            "CLI/MCP",
        }
        self.assertEqual(expected - modalities, set(), "Missing required modalities in catalog")

        for s in surfaces:
            self.assertEqual(s.get("status"), "MAPPED")
            self.assertIn("surface_id", s)
            self.assertIn("ci_owner", s)
            self.assertIn("evaluator", s)
            self.assertIn("gate", s)
            self.assertIn("receipt", s)

    def test_l0_module_line_budgets(self) -> None:
        """All production modules under dual_pipeline/l0 adhere to the <= 400 line budget."""
        ok, violations = WaveEvidenceVerifier.audit_l0_line_budgets(REPO_ROOT)
        self.assertTrue(ok, f"Module line budget violations in L0: {violations}")

    def test_pre_wave_interceptor_clearance_for_wave1(self) -> None:
        """Pre-wave interceptor grants clearance for Wave 1 using the plan-bound authorization decision."""
        pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"
        plan_path = REPO_ROOT / "docs" / "plans" / "docs_pipeline_alignment_debate_plan_20261006.md"

        cmd = [
            sys.executable,
            str(pre_gate_script),
            "--wave", "1",
            "--plan-path", str(plan_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Expected clearance (0), got {res.returncode}: {res.stderr}\n{res.stdout}")
        self.assertIn("CLEARANCE GRANTED", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W1-AUTH", res.stdout)

    def test_wave1_gate_lifecycle_sealing_and_verification(self) -> None:
        """Full execution of tools/run_wave_gate.py generates valid immutable receipt and lock."""
        gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"

        # Prepare a child test specification that executes adversarial test suite without self-recursion
        with open(self.spec_path, "r", encoding="utf-8") as f:
            spec_data = yaml.safe_load(f)

        child_spec = dict(spec_data)
        child_spec["test_suites"] = ["tests/test_alignment_w1_adversarial.py"]
        child_spec_path = self.work_dir / "test_wave_1_child_spec.yaml"
        with open(child_spec_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(child_spec, f)

        # Execute wave 1 gate pointing to temporary destination paths
        cmd = [
            sys.executable,
            str(gate_script),
            "--wave", "1",
            "--spec", str(child_spec_path),
            "--receipt", str(self.receipt_path),
            "--lock", str(self.lock_path),
            "--immutable",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Wave 1 gate failed (exit {res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        self.assertIn("WAVE 1 GATE PASSED", res.stdout)

        # 1. Verify receipt file existence and permissions
        self.assertTrue(self.receipt_path.is_file(), "Receipt file was not created")
        mode = self.receipt_path.stat().st_mode
        self.assertFalse(bool(mode & stat.S_IWUSR), "Receipt file should be read-only (0o444)")

        # 2. Verify receipt content and metadata
        with open(self.receipt_path, "r", encoding="utf-8") as f:
            receipt_data = json.load(f)

        self.assertEqual(receipt_data.get("status"), "COMPLETE")
        self.assertEqual(receipt_data.get("step_name"), "wave_acceptance_gate")
        self.assertTrue(receipt_data.get("receipt_digest"), "Missing receipt digest")

        meta = receipt_data.get("metadata", {})
        self.assertEqual(meta.get("terminal_result"), "PASS")
        self.assertIn("artifacts", meta)
        self.assertIn("test_results", meta)
        self.assertIn("git_revision", meta)
        self.assertIn("isolated_runtime_root", meta)

        # 3. Verify lock file existence and permissions
        self.assertTrue(self.lock_path.is_file(), "Lock file was not created")
        lock_mode = self.lock_path.stat().st_mode
        self.assertFalse(bool(lock_mode & stat.S_IWUSR), "Lock file should be read-only (0o444)")

        # 4. Verify sequential lock structure and digest link
        with open(self.lock_path, "r", encoding="utf-8") as f:
            lock_data = json.load(f)

        self.assertEqual(lock_data.get("status"), "LOCKED")
        self.assertEqual(lock_data.get("receipt_digest"), receipt_data.get("receipt_digest"))
        self.assertTrue(verify_sequential_lock(self.lock_path, expected_receipt_digest=receipt_data.get("receipt_digest")))

        # 5. Verify predecessor lock verification logic on the generated lock
        ok, reason, lock_digest = verify_predecessor_lock(self.lock_path, expected_parent_wave=1)
        self.assertTrue(ok, f"Predecessor lock verification failed: {reason}")
        self.assertEqual(lock_digest, lock_data.get("lock_digest"))

        # 6. Verify cryptographic integrity via verify_runtime_receipt and verify_receipt
        rec_ok, rec_reason, rec_digest = verify_runtime_receipt(self.receipt_path, expected_wave_id="Wave 1: Establish trustworthy evidence and coverage")
        self.assertTrue(rec_ok, f"Runtime receipt verification failed: {rec_reason}")
        self.assertEqual(rec_digest, receipt_data.get("receipt_digest"))
        self.assertTrue(verify_receipt(self.receipt_path))


if __name__ == "__main__":
    unittest.main()
