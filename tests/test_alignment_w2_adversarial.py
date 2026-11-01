"""tests/test_alignment_w2_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 2 Governance.
Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload:
   - Exact boundary equality (spread == 0.30000000) mandates operator review under '<=' inclusive trigger.
   - Single-candidate scenario (no second option) resolves autonomously without error.
   - Missing Wave 1 predecessor lock blocks Wave 2 gate execution fail-closed (Exit Code 1).
2. Malformed input:
   - NaN, Infinity, negative values, and non-numeric confidence inputs fail closed (mandating review).
   - Tampered policy snapshot content digest is rejected fail-closed.
   - Corrupted or tampered predecessor lock file is rejected fail-closed.
3. Invariant bypass attempt:
   - Mid-run policy snapshot mutation attempt raises ValueError (POLICY_MUTATION_PROHIBITED).
   - Overwrite attempt of existing Wave 2 receipt/lock without --allow-overwrite fails closed.
   - Open / unratified decision blocks Wave 2 gate execution with Exit Code 2.
4. Rollback & recovery:
   - Failed gate execution cleans up and leaves no destination lock or receipt.
   - Registry unbind operation releases bound run cleanly.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.l0.policy_snapshot import (
    ActiveRunPolicyRegistry,
    PolicySnapshot,
    create_policy_snapshot,
    verify_policy_snapshot_integrity,
)
from dual_pipeline.l0.receipts import verify_predecessor_lock


class TestAlignmentW2Adversarial(unittest.TestCase):
    """Adversarial and edge-case testing for Wave 2 policy authority controls."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"
        self.pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"
        self.parent_lock = REPO_ROOT / "reports" / "alignment" / "wave-1.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_exact_margin_equality_mandates_hitl(self) -> None:
        """Exact boundary equality (spread == 0.30) mandates HITL review under '<=' inclusive rule."""
        snapshot = create_policy_snapshot(ambiguity_margin=0.30, boundary_inclusivity="<=")

        # Exact hits: 0.80 - 0.50 = 0.30
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, 0.50), "Exact boundary equality must require HITL under '<='")
        self.assertTrue(snapshot.evaluate_ambiguity(0.95, 0.65), "Exact boundary equality must require HITL under '<='")

        # Just barely inside: 0.80 - 0.5000001 = 0.2999999 <= 0.30 -> HITL
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, 0.5000001))

        # Just barely outside: 0.8000001 - 0.50 = 0.3000001 > 0.30 -> Autonomous allowed
        self.assertFalse(snapshot.evaluate_ambiguity(0.8000001, 0.50))

    def test_boundary_single_candidate_resolves_autonomously(self) -> None:
        """A single candidate (second_confidence is None) resolves autonomously without error."""
        snapshot = create_policy_snapshot()
        self.assertFalse(snapshot.evaluate_ambiguity(0.90, None))
        self.assertFalse(snapshot.evaluate_ambiguity(0.65, None))

    def test_boundary_missing_prior_lock_blocks_wave2_gate(self) -> None:
        """Wave 2 execution without prior Wave 1 lock fails closed with Exit Code 1."""
        spec_content = {
            "wave_id": "Wave 2: Test",
            "wave_number": 2,
            "acceptance_criteria": ["Criteria"],
            "test_suites": ["tests/test_ci_gates.py"],
            "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
        }
        spec_path = self.work_dir / "bad_wave2_spec.yaml"
        spec_path.write_text(yaml.dump(spec_content), encoding="utf-8")

        # Without --require-lock
        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "2",
            "--spec", str(spec_path),
            "--receipt", str(self.work_dir / "r.json"),
            "--lock", str(self.work_dir / "l.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("requires prior lock from Wave 1", res.stdout)

    # ── Category 2: Malformed Input ───────────────────────────────────────────

    def test_malformed_confidence_inputs_fail_closed(self) -> None:
        """NaN, Inf, negative, and non-numeric inputs fail closed (mandating HITL review)."""
        snapshot = create_policy_snapshot()

        self.assertTrue(snapshot.evaluate_ambiguity(float("nan"), 0.50))
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, float("nan")))
        self.assertTrue(snapshot.evaluate_ambiguity(float("inf"), 0.50))
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, float("-inf")))
        self.assertTrue(snapshot.evaluate_ambiguity(-0.01, 0.50))
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, -0.05))
        self.assertTrue(snapshot.evaluate_ambiguity("invalid_str", 0.50))
        self.assertTrue(snapshot.evaluate_ambiguity(0.80, "invalid_str"))
        self.assertTrue(snapshot.evaluate_ambiguity(None, None))

    def test_malformed_policy_snapshot_digest_mismatch_detected(self) -> None:
        """Tampered snapshot parameters with mismatched digest fail integrity verification."""
        legit = create_policy_snapshot()
        # Create tampered copy with forged margin but original digest
        tampered = PolicySnapshot(
            policy_version=legit.policy_version,
            snapshot_id=legit.snapshot_id,
            created_at=legit.created_at,
            ambiguity_margin=0.10,  # tampered from 0.30
            boundary_inclusivity=legit.boundary_inclusivity,
            confidence_floor=legit.confidence_floor,
            entropy_ceiling=legit.entropy_ceiling,
            divergence_margin=legit.divergence_margin,
            named_domain_exceptions=legit.named_domain_exceptions,
            snapshot_digest=legit.snapshot_digest,
        )

        ok, reason = verify_policy_snapshot_integrity(tampered)
        self.assertFalse(ok)
        self.assertIn("digest mismatch", reason)

    def test_malformed_tampered_predecessor_lock_rejected(self) -> None:
        """Predecessor lock with forged lock_digest or altered parent wave is rejected."""
        tampered_lock = self.work_dir / "tampered_parent.lock"
        tampered_lock.write_text(json.dumps({
            "lock_id": "wave-1.lock",
            "wave_id": "Wave 1",
            "status": "LOCKED",
            "receipt_digest": "dummy_digest",
            "lock_digest": "forged_non_matching_lock_digest",
            "metadata": {"wave": 1},
        }), encoding="utf-8")

        ok, reason, _ = verify_predecessor_lock(tampered_lock, expected_parent_wave=1)
        self.assertFalse(ok)
        self.assertIn("digest mismatch", reason)

    # ── Category 3: Invariant Bypass Attempt ───────────────────────────────────

    def test_bypass_mid_run_policy_mutation_prohibited(self) -> None:
        """Active run rejects mutation attempts when attempting to bind different parameters."""
        registry = ActiveRunPolicyRegistry()
        snap_v1 = create_policy_snapshot(ambiguity_margin=0.30, snapshot_id="snap-v1")
        snap_mutated = create_policy_snapshot(ambiguity_margin=0.15, snapshot_id="snap-mutated")

        registry.bind_run_snapshot("active-run-abc", snap_v1)

        # Attempting to re-bind with different snapshot raises ValueError
        with self.assertRaises(ValueError) as ctx:
            registry.bind_run_snapshot("active-run-abc", snap_mutated)
        self.assertIn("POLICY_MUTATION_PROHIBITED", str(ctx.exception))

    def test_bypass_overwrite_existing_wave2_receipt_rejected(self) -> None:
        """Attempting to overwrite existing Wave 2 receipt without --allow-overwrite fails closed."""
        existing_receipt = self.work_dir / "wave-2.json"
        existing_receipt.write_text(json.dumps({"status": "PRIOR_RUN"}), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "2",
            "--spec", str(REPO_ROOT / "config" / "governance" / "alignment" / "wave-2.yaml"),
            "--receipt", str(existing_receipt),
            "--lock", str(self.work_dir / "wave-2.lock"),
            "--require-lock", str(self.parent_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("already exists", res.stdout)
        self.assertIn("Exclusive creation prohibits overwrites", res.stdout)

    def test_bypass_open_decision_halts_wave2_gate_exit_code_2(self) -> None:
        """Plan with an OPEN (unratified) decision for Wave 2 halts gate execution with Exit Code 2."""
        plan_with_open = self.work_dir / "plan_with_open.md"
        plan_with_open.write_text(
            "# Plan with Open Decision\n\n"
            "### Decision DECISION-TEST-OPEN: Open Review Item\n"
            "- **Wave**: 2\n"
            "- **Status**: OPEN\n"
            "- **Question**: Should we allow this change?\n"
            "- **Option A**: Yes\n"
            "- **Option B**: No\n",
            encoding="utf-8",
        )

        cmd = [
            sys.executable,
            str(self.pre_gate_script),
            "--wave", "2",
            "--plan-path", str(plan_with_open),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("DECISION-TEST-OPEN", res.stdout)
        self.assertIn("open decision", res.stdout.lower())

    # ── Category 4: Rollback & Recovery ───────────────────────────────────────

    def test_recovery_failed_gate_execution_leaves_no_artifacts(self) -> None:
        """When gate execution fails due to test failure, uncommitted locks and receipts are not created."""
        target_receipt = self.work_dir / "aborted_receipt.json"
        target_lock = self.work_dir / "aborted_lock.lock"

        bad_spec = self.work_dir / "bad_spec.yaml"
        bad_spec.write_text(yaml.dump({
            "wave_id": "Wave 2: Fail",
            "wave_number": 2,
            "acceptance_criteria": ["Criteria"],
            "test_suites": ["tests/nonexistent_test_suite_123.py"],
            "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
        }), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "2",
            "--spec", str(bad_spec),
            "--receipt", str(target_receipt),
            "--lock", str(target_lock),
            "--require-lock", str(self.parent_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertFalse(target_receipt.exists())
        self.assertFalse(target_lock.exists())

    def test_recovery_registry_unbind_cleans_up_state(self) -> None:
        """Unbinding a run from ActiveRunPolicyRegistry clears state cleanly."""
        registry = ActiveRunPolicyRegistry()
        snap = create_policy_snapshot()
        registry.bind_run_snapshot("ephemeral-run", snap)
        self.assertIsNotNone(registry.get_run_snapshot("ephemeral-run"))

        registry.unbind_run("ephemeral-run")
        self.assertIsNone(registry.get_run_snapshot("ephemeral-run"))


if __name__ == "__main__":
    unittest.main()
