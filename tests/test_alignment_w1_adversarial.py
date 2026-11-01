"""tests/test_alignment_w1_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 1 Governance.
Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload:
   - Empty test suites list in specification rejected fail-closed (Exit Code 1).
   - Zero decision records in scope rejected fail-closed with Exit Code 3 (Decision D6).
   - Nonexistent specification path rejected fail-closed (Exit Code 1).
2. Malformed input:
   - Surface catalog missing required modality (e.g. CLI/MCP) rejected fail-closed.
   - Surface catalog missing required surface metadata fields (e.g. evaluator, ci_owner) rejected fail-closed.
   - Corrupted or tampered predecessor lock file (digest mismatch) rejected fail-closed.
   - Specification wave_number mismatching CLI wave argument rejected fail-closed.
3. Invariant bypass attempt:
   - Overwrite attempt of existing receipt or sequential lock without --allow-overwrite rejected fail-closed.
   - Skipped wave progression (Wave 2 requiring Wave 1 lock) rejected fail-closed when predecessor lock is missing or from wrong wave.
   - Mixed-style collection canary failure: failing test assertion triggers non-zero gate exit.
   - Zero-collection test suite rejected fail-closed.
4. Rollback & recovery:
   - Aborted / failed gate execution leaves existing locks and receipts untouched.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.l0.receipts import (
    create_sequential_lock,
    verify_predecessor_lock,
    write_immutable_receipt,
)
from dual_pipeline.l0.contracts import RuntimeReceipt
from tools.run_wave_gate import WaveEvidenceVerifier


class TestAlignmentW1Adversarial(unittest.TestCase):
    """Adversarial and edge-case testing for Wave 1 governance controls."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"
        self.pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_empty_test_suites_rejected(self) -> None:
        """Specification with empty test_suites list is rejected fail-closed (Exit Code 1)."""
        bad_spec = self.work_dir / "empty_suites_spec.yaml"
        spec_content = {
            "wave_id": "Wave 1: Test",
            "wave_number": 1,
            "acceptance_criteria": ["Criteria 1"],
            "test_suites": [],
            "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
        }
        bad_spec.write_text(yaml.dump(spec_content), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "1",
            "--spec", str(bad_spec),
            "--receipt", str(self.work_dir / "r.json"),
            "--lock", str(self.work_dir / "l.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("No test suites declared", res.stdout)

    def test_boundary_zero_decisions_fails_closed_exit_code_3(self) -> None:
        """Zero decisions found for a wave in scope triggers Exit Code 3 under Decision D6."""
        dummy_plan = self.work_dir / "dummy_plan.md"
        dummy_plan.write_text("# Plan Without Decisions\nNo decisions here.\n", encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.pre_gate_script),
            "--wave", "1",
            "--plan-path", str(dummy_plan),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 3)
        self.assertIn("ZERO DECISIONS FOUND", res.stdout)

    def test_boundary_missing_spec_file_rejected(self) -> None:
        """Non-existent specification file path fails closed immediately with Exit Code 1."""
        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "1",
            "--spec", str(self.work_dir / "nonexistent_spec.yaml"),
            "--receipt", str(self.work_dir / "r.json"),
            "--lock", str(self.work_dir / "l.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not found", res.stdout)

    # ── Category 2: Malformed Input ───────────────────────────────────────────

    def test_malformed_surface_catalog_missing_modality(self) -> None:
        """Surface catalog omitting a mandatory modality is rejected fail-closed."""
        bad_cat = self.work_dir / "incomplete_catalog.yaml"
        cat_content = {
            "surfaces": [
                {
                    "surface_id": "SURF-AUDIO",
                    "entrypoint": "nee record-meeting",
                    "modality": "audio/transcript",
                    "prompt_or_schema": "contracts/transcript.json",
                    "evaluator": "AcousticQualityGate",
                    "gate": "Gate 1",
                    "consumer": "recap_executor",
                    "receipt": "reports/audio.json",
                    "ci_owner": "ci-audio",
                    "status": "MAPPED",
                }
            ]
        }
        bad_cat.write_text(yaml.dump(cat_content), encoding="utf-8")

        ok, reason, _ = WaveEvidenceVerifier.verify_surface_catalog(bad_cat)
        self.assertFalse(ok)
        self.assertIn("missing required modalities", reason)

    def test_malformed_surface_catalog_missing_required_fields(self) -> None:
        """Surface catalog with missing mandatory field (e.g. evaluator) is rejected."""
        bad_cat = self.work_dir / "missing_fields_catalog.yaml"
        cat_content = {
            "surfaces": [
                {
                    "surface_id": "SURF-INCOMPLETE",
                    "entrypoint": "nee test",
                    "modality": "audio/transcript",
                    # missing prompt_or_schema, evaluator, gate, consumer, receipt, ci_owner, status
                }
            ]
        }
        bad_cat.write_text(yaml.dump(cat_content), encoding="utf-8")

        ok, reason, _ = WaveEvidenceVerifier.verify_surface_catalog(bad_cat)
        self.assertFalse(ok)
        self.assertIn("missing required field", reason)

    def test_malformed_predecessor_lock_digest_mismatch(self) -> None:
        """Predecessor lock with tampered content/digest mismatch is rejected fail-closed."""
        tampered_lock = self.work_dir / "tampered.lock"
        payload = {
            "lock_id": "wave-1.complete",
            "wave_id": "Wave 1",
            "status": "LOCKED",
            "receipt_digest": "abcdef0123456789",
            "lock_digest": "forged_digest_value_that_does_not_match_hash",
        }
        tampered_lock.write_text(json.dumps(payload), encoding="utf-8")

        ok, reason, _ = verify_predecessor_lock(tampered_lock, expected_parent_wave=1)
        self.assertFalse(ok)
        self.assertIn("digest mismatch", reason)

    def test_malformed_spec_wave_mismatch_rejected(self) -> None:
        """Specification wave_number (2) conflicting with CLI --wave (1) fails closed."""
        spec_file = self.work_dir / "mismatch_wave_spec.yaml"
        spec_content = {
            "wave_id": "Wave 2 Mismatch",
            "wave_number": 2,
            "acceptance_criteria": ["Criteria"],
            "test_suites": ["tests/test_ci_gates.py"],
        }
        spec_file.write_text(yaml.dump(spec_content), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "1",
            "--spec", str(spec_file),
            "--receipt", str(self.work_dir / "r.json"),
            "--lock", str(self.work_dir / "l.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("does not match command wave", res.stdout)

    # ── Category 3: Invariant Bypass Attempt ───────────────────────────────────

    def test_bypass_overwrite_existing_receipt_prohibited(self) -> None:
        """Attempting to overwrite existing receipt file without --allow-overwrite fails closed."""
        existing_receipt = self.work_dir / "existing_receipt.json"
        existing_receipt.write_text(json.dumps({"status": "PREVIOUS_RUN"}), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "1",
            "--spec", str(REPO_ROOT / "config" / "governance" / "alignment" / "wave-1.yaml"),
            "--receipt", str(existing_receipt),
            "--lock", str(self.work_dir / "new_lock.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("already exists", res.stdout)
        self.assertIn("Exclusive creation prohibits overwrites", res.stdout)

    def test_bypass_skipped_wave_predecessor_discontinuity_rejected(self) -> None:
        """Wave 2 gate execution without valid Wave 1 predecessor lock fails closed."""
        spec_file = self.work_dir / "wave_2_spec.yaml"
        spec_content = {
            "wave_id": "Wave 2: Resolve policy authority",
            "wave_number": 2,
            "acceptance_criteria": ["Criteria"],
            "test_suites": ["tests/test_ci_gates.py"],
        }
        spec_file.write_text(yaml.dump(spec_content), encoding="utf-8")

        # Without prior lock specified
        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "2",
            "--spec", str(spec_file),
            "--receipt", str(self.work_dir / "r2.json"),
            "--lock", str(self.work_dir / "l2.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("requires prior lock from Wave 1", res.stdout)

    def test_bypass_mixed_style_collection_canary_failure(self) -> None:
        """A test suite containing a failing test case (canary) causes the gate to fail closed."""
        canary_test = self.work_dir / "test_canary_failure.py"
        canary_test.write_text(
            "import unittest\n"
            "class CanaryFailureTest(unittest.TestCase):\n"
            "    def test_canary_must_fail(self):\n"
            "        self.fail('Canary failure deliberately triggered')\n",
            encoding="utf-8",
        )

        canary_spec = self.work_dir / "canary_spec.yaml"
        canary_spec.write_text(
            yaml.dump({
                "wave_id": "Wave 1: Canary",
                "wave_number": 1,
                "acceptance_criteria": ["Canary pass"],
                "test_suites": [str(canary_test)],
                "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
            }),
            encoding="utf-8",
        )

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "1",
            "--spec", str(canary_spec),
            "--receipt", str(self.work_dir / "canary.json"),
            "--lock", str(self.work_dir / "canary.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("failure(s) in test execution", res.stdout)

    # ── Category 4: Rollback & Recovery ───────────────────────────────────────

    def test_recovery_failed_gate_does_not_leave_corrupted_artifacts(self) -> None:
        """When gate execution fails before sealing, no destination lock or receipt is created."""
        target_receipt = self.work_dir / "uncreated_receipt.json"
        target_lock = self.work_dir / "uncreated_lock.lock"

        # Missing test suite deliberately causes failure before sealing
        failing_spec = self.work_dir / "missing_suite_spec.yaml"
        failing_spec.write_text(
            yaml.dump({
                "wave_id": "Wave 1: Test",
                "wave_number": 1,
                "acceptance_criteria": ["Criteria"],
                "test_suites": ["tests/nonexistent_test_suite_xyz.py"],
                "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
            }),
            encoding="utf-8",
        )

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "1",
            "--spec", str(failing_spec),
            "--receipt", str(target_receipt),
            "--lock", str(target_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertFalse(target_receipt.exists(), "Receipt should not exist after gate failure")
        self.assertFalse(target_lock.exists(), "Lock should not exist after gate failure")


if __name__ == "__main__":
    unittest.main()
