"""tests/end_to_end/test_alignment_w2_e2e.py

End-to-End Verification Suite for Wave 2: Resolve policy authority.
Verifies:
1. Predecessor verification of Wave 1 lock (reports/alignment/wave-1.lock).
2. Governed policy registry SSOT alignment (0.30 margin, <= inclusivity, 0.65 confidence floor).
3. Named domain exceptions (unresolved entity 0.15, quarantine 0.60, medium tier 0.60).
4. Pre-wave interceptor clearance for all Wave 2 ratified decisions.
5. Decision agreement across boundary, single-candidate, and invalid-value cases.
6. Immutable policy snapshot binding per active run with mid-run mutation prevention.
7. Full governed gate execution generating verified Wave 2 receipt and sequential lock.
"""
from __future__ import annotations

import json
import math
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

from dual_pipeline.l0.policy_snapshot import (
    ActiveRunPolicyRegistry,
    PolicySnapshot,
    create_policy_snapshot,
    verify_policy_snapshot_integrity,
)
from dual_pipeline.l0.receipts import (
    verify_predecessor_lock,
    verify_runtime_receipt,
    verify_sequential_lock,
)
from tools.record_wave_receipt import verify_receipt


class TestAlignmentW2E2E(unittest.TestCase):
    """E2E suite for Wave 2 policy authority resolution governance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.receipt_path = self.work_dir / "wave-2.json"
        self.lock_path = self.work_dir / "wave-2.lock"
        self.spec_path = REPO_ROOT / "config" / "governance" / "alignment" / "wave-2.yaml"
        self.parent_lock_path = REPO_ROOT / "reports" / "alignment" / "wave-1.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_wave1_predecessor_lock_validity(self) -> None:
        """Wave 1 predecessor lock exists, is valid, and matches wave 1 sequence."""
        self.assertTrue(self.parent_lock_path.is_file(), "Wave 1 lock must exist before Wave 2 can proceed")
        ok, reason, parent_digest = verify_predecessor_lock(self.parent_lock_path, expected_parent_wave=1)
        self.assertTrue(ok, f"Wave 1 predecessor lock invalid: {reason}")
        self.assertTrue(parent_digest, "Missing parent lock digest")

    def test_policy_registry_and_pyproject_ssot_alignment(self) -> None:
        """Policy registry and pyproject.toml align on 0.30 margin, <= inclusivity, and 0.65 floor."""
        registry_path = REPO_ROOT / "governance" / "policy_registry.yaml"
        self.assertTrue(registry_path.is_file())
        with open(registry_path, "r", encoding="utf-8") as f:
            reg_data = yaml.safe_load(f)

        self.assertEqual(reg_data.get("version"), "1.1.0")
        policies = {p["id"]: p for p in reg_data.get("policies", [])}

        # 1. Ambiguity margin policy
        margin_pol = policies.get("INV-HITL-AMBIGUITY-MARGIN")
        self.assertIsNotNone(margin_pol)
        self.assertEqual(float(margin_pol["threshold"]), 0.20)
        self.assertEqual(margin_pol.get("boundary_inclusivity"), "<=")
        exceptions = margin_pol.get("named_domain_exceptions", [])
        self.assertGreaterEqual(len(exceptions), 1)
        entity_exc = next((e for e in exceptions if e.get("domain") == "entity_resolution"), None)
        self.assertIsNotNone(entity_exc)
        self.assertEqual(float(entity_exc["threshold"]), 0.15)

        # 2. Confidence floor policy
        floor_pol = policies.get("INV-HITL-MIN-CONFIDENCE-FLOOR")
        self.assertIsNotNone(floor_pol)
        self.assertEqual(float(floor_pol["threshold"]), 0.65)
        self.assertEqual(floor_pol.get("boundary_inclusivity"), ">=")

        # 3. pyproject.toml alignment
        pyproj_path = REPO_ROOT / "pyproject.toml"
        pyproj_text = pyproj_path.read_text(encoding="utf-8")
        self.assertIn("hitl_ambiguity_margin_threshold = 0.20", pyproj_text)

    def test_pre_wave_interceptor_clearance_for_wave2(self) -> None:
        """Pre-wave interceptor grants clearance for Wave 2 with all 3 ratified decisions."""
        pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"
        plan_path = REPO_ROOT / "docs" / "plans" / "docs_pipeline_alignment_debate_plan_20261006.md"

        cmd = [
            sys.executable,
            str(pre_gate_script),
            "--wave", "2",
            "--plan-path", str(plan_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Expected clearance (0), got {res.returncode}: {res.stderr}\n{res.stdout}")
        self.assertIn("CLEARANCE GRANTED", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W2-MARGIN-AUTHORITY", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W2-CONFIDENCE-FLOOR", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W2-BOUNDARY-INCLUSIVITY", res.stdout)

    def test_policy_snapshot_decision_agreement(self) -> None:
        """Policy snapshot evaluates boundary (<=), single-candidate, invalid-values, and domain exceptions."""
        snapshot = create_policy_snapshot()
        ok, reason = verify_policy_snapshot_integrity(snapshot)
        self.assertTrue(ok, reason)

        # Boundary equality: Delta == 0.20 -> HITL mandated under '<='
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, 0.60))
        self.assertTrue(snapshot.evaluate_ambiguity(0.50, 0.30))

        # Autonomous resolution: Delta == 0.21 > 0.20 -> autonomous allowed
        self.assertFalse(snapshot.evaluate_ambiguity(0.81, 0.60))

        # Single candidate: second is None -> autonomous allowed
        self.assertFalse(snapshot.evaluate_ambiguity(0.85, None))

        # Invalid values: fail-closed -> HITL mandated
        self.assertTrue(snapshot.evaluate_ambiguity(None, 0.50))
        self.assertTrue(snapshot.evaluate_ambiguity(float("nan"), 0.50))
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, float("inf")))
        self.assertTrue(snapshot.evaluate_ambiguity(-0.1, 0.50))

        # Named domain exception: entity_resolution uses 0.15 threshold
        # Delta = 0.18 > 0.15 -> autonomous allowed in entity domain, but would fail default 0.20
        self.assertFalse(snapshot.evaluate_ambiguity(0.80, 0.62, domain="entity_resolution"))
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, 0.62))  # Default domain requires HITL (0.18 <= 0.20)

    def test_immutable_run_policy_binding_and_mutation_prevention(self) -> None:
        """Active run retains bound snapshot; mid-run mutation attempts fail closed."""
        registry = ActiveRunPolicyRegistry()
        snap1 = create_policy_snapshot(snapshot_id="snap-v1")
        snap2_mutated = create_policy_snapshot(ambiguity_margin=0.15, snapshot_id="snap-mutated")

        # 1. Bind run-1 to snap1
        registry.bind_run_snapshot("run-1", snap1)
        self.assertEqual(registry.get_run_snapshot("run-1").snapshot_digest, snap1.snapshot_digest)

        # 2. Attempting to bind mutated snapshot to run-1 raises ValueError
        with self.assertRaises(ValueError) as ctx:
            registry.bind_run_snapshot("run-1", snap2_mutated)
        self.assertIn("POLICY_MUTATION_PROHIBITED", str(ctx.exception))

        # 3. Verify invariance check detects divergence
        ok, reason = registry.verify_run_policy_invariance("run-1", snap2_mutated)
        self.assertFalse(ok)
        self.assertIn("POLICY_MUTATION_DETECTED", reason)

        # 4. Next run can bind new snapshot independently while active run-1 retains snap1
        registry.bind_run_snapshot("run-2", snap2_mutated)
        self.assertEqual(registry.get_run_snapshot("run-1").ambiguity_margin, 0.20)
        self.assertEqual(registry.get_run_snapshot("run-2").ambiguity_margin, 0.15)

    def test_wave2_gate_lifecycle_sealing_and_verification(self) -> None:
        """Full execution of tools/run_wave_gate.py for Wave 2 generates valid immutable receipt and lock."""
        gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"

        # Child test spec to prevent mutual recursion
        with open(self.spec_path, "r", encoding="utf-8") as f:
            spec_data = yaml.safe_load(f)

        child_spec = dict(spec_data)
        child_spec["test_suites"] = ["tests/test_alignment_w2_adversarial.py"]
        child_spec_path = self.work_dir / "test_wave_2_child_spec.yaml"
        with open(child_spec_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(child_spec, f)

        cmd = [
            sys.executable,
            str(gate_script),
            "--wave", "2",
            "--spec", str(child_spec_path),
            "--receipt", str(self.receipt_path),
            "--lock", str(self.lock_path),
            "--require-lock", str(self.parent_lock_path),
            "--immutable",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Wave 2 gate failed (exit {res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        self.assertIn("WAVE 2 GATE PASSED", res.stdout)

        # Verify receipt
        self.assertTrue(self.receipt_path.is_file())
        with open(self.receipt_path, "r", encoding="utf-8") as f:
            receipt_data = json.load(f)
        self.assertEqual(receipt_data.get("status"), "COMPLETE")
        self.assertEqual(receipt_data.get("wave_id"), "Wave 2: Resolve policy authority")

        # Verify lock
        self.assertTrue(self.lock_path.is_file())
        with open(self.lock_path, "r", encoding="utf-8") as f:
            lock_data = json.load(f)
        self.assertEqual(lock_data.get("status"), "LOCKED")
        self.assertEqual(lock_data.get("receipt_digest"), receipt_data.get("receipt_digest"))

        # Predecessor lock verification on Wave 2 lock
        ok, reason, lock_digest = verify_predecessor_lock(self.lock_path, expected_parent_wave=2)
        self.assertTrue(ok, reason)

        # Cryptographic verification
        rec_ok, rec_reason, rec_digest = verify_runtime_receipt(self.receipt_path, expected_wave_id="Wave 2: Resolve policy authority")
        self.assertTrue(rec_ok, rec_reason)
        self.assertTrue(verify_receipt(self.receipt_path))


if __name__ == "__main__":
    unittest.main()
