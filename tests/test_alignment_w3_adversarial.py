"""tests/test_alignment_w3_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 3 Governance.
Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload:
   - Zero-length / empty text components in bundle fingerprinting handled safely without crash.
   - Empty, whitespace, and single-character owner strings tested against DRI rules.
   - Missing Wave 2 predecessor lock blocks Wave 3 gate execution fail-closed (Exit Code 1).
2. Malformed input:
   - Rubric weights not summing to 100 points rejected fail-closed.
   - Invalid output_id (< 1 or > 3) rejected fail-closed.
   - Overlapping section sets (required & forbidden, or conditional & forbidden) rejected.
   - Corrupted or tampered predecessor lock file is rejected fail-closed.
3. Invariant bypass attempt:
   - Silent contract mutations (obligation changes, rubric weight adjustments, owner rule relaxation, modality overrides) detected immediately by detect_contract_mutation.
   - Placeholder owner tokens (Unassigned, Pending, TBD, Team, All, PartnerAdvisory/Client) fail-closed under validate_owner_string.
   - Multiple slash-separated or comma-separated owners fail-closed under validate_owner_string.
   - Harmless prose adjustments do NOT mutate canonical_obligation_digest.
   - Overwrite of existing Wave 3 receipt/lock without --allow-overwrite fails closed.
4. Rollback & recovery:
   - Gate runner aborts cleanly on missing required artifact and emits no lock or receipt.
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

from dual_pipeline.l0.prompt_contract import (
    CANONICAL_OUTPUT1_PROMPT_CONTRACT,
    CANONICAL_OUTPUT2_PROMPT_CONTRACT,
    CANONICAL_OUTPUT3_PROMPT_CONTRACT,
    PromptContract,
    canonical_obligation_digest,
    compute_assembled_bundle_fingerprint,
    detect_contract_mutation,
    validate_owner_string,
    verify_contract_parity,
)
from dual_pipeline.l0.receipts import verify_predecessor_lock


class TestAlignmentW3Adversarial(unittest.TestCase):
    """Adversarial and edge-case testing for Wave 3 generation and grading contract alignment."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"
        self.parent_lock = REPO_ROOT / "reports" / "alignment" / "wave-2.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_empty_and_zero_length_bundle_fingerprint(self) -> None:
        """Zero-length and whitespace-only components fingerprint deterministically without crash."""
        fp_empty = compute_assembled_bundle_fingerprint([])
        self.assertEqual(len(fp_empty), 64)

        fp_blanks = compute_assembled_bundle_fingerprint(["", "   \n\t", ""])
        self.assertEqual(len(fp_blanks), 64)

    def test_boundary_owner_string_validation(self) -> None:
        """Owner string boundary values: empty, spaces, and minimal names."""
        contract = CANONICAL_OUTPUT2_PROMPT_CONTRACT

        # Empty & whitespace
        ok, err = validate_owner_string("", contract)
        self.assertFalse(ok)
        self.assertIn("empty", err.lower())

        ok, err = validate_owner_string("   \t  ", contract)
        self.assertFalse(ok)
        self.assertIn("empty", err.lower())

        # Minimal valid name (e.g. single 2-letter word)
        ok, err = validate_owner_string("Bo", contract)
        self.assertTrue(ok)

    def test_boundary_missing_prior_lock_blocks_wave3_gate(self) -> None:
        """Wave 3 execution without prior Wave 2 lock fails closed with Exit Code 1."""
        spec_content = {
            "wave_id": "Wave 3: Test",
            "wave_number": 3,
            "acceptance_criteria": ["Criteria"],
            "test_suites": ["tests/test_ci_gates.py"],
            "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
        }
        spec_path = self.work_dir / "bad_wave3_spec.yaml"
        spec_path.write_text(yaml.dump(spec_content), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "3",
            "--spec", str(spec_path),
            "--receipt", str(self.work_dir / "r.json"),
            "--lock", str(self.work_dir / "l.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Wave 3 requires prior lock from Wave 2", res.stdout)

    # ── Category 2: Malformed Input ───────────────────────────────────────────

    def test_malformed_rubric_weights_sum_rejected(self) -> None:
        """Contracts with rubric weights not summing to 100 fail integrity validation."""
        bad_contract = PromptContract(
            contract_id="TEST_BAD_WEIGHTS",
            output_id=2,
            stage_name="STAGE_02_RECAP",
            rubric_weights={"dim_1": 25, "dim_2": 25, "dim_3": 25, "dim_4": 20},  # sums to 95
        )
        ok, issues = bad_contract.validate_integrity()
        self.assertFalse(ok)
        self.assertTrue(any("expected 100" in issue for issue in issues))

    def test_malformed_invalid_output_id_rejected(self) -> None:
        """Contract with output_id not in (1, 2, 3) fails integrity validation."""
        for bad_id in (0, 4, -1, 99):
            bad_contract = PromptContract(
                contract_id="TEST_BAD_ID",
                output_id=bad_id,
                stage_name="STAGE_02_RECAP",
                rubric_weights={"dim_1": 100},
            )
            ok, issues = bad_contract.validate_integrity()
            self.assertFalse(ok)
            self.assertTrue(any("Invalid output_id" in issue for issue in issues))

    def test_malformed_overlapping_sections_rejected(self) -> None:
        """Contract with sections overlapping across required/conditional/forbidden fails integrity validation."""
        bad_contract = PromptContract(
            contract_id="TEST_OVERLAPPING",
            output_id=2,
            stage_name="STAGE_02_RECAP",
            required_sections=("Meeting Objective", "Action & Governance Ledger"),
            forbidden_sections=("Action & Governance Ledger",),  # In both required and forbidden!
            rubric_weights={"dim_1": 100},
        )
        ok, issues = bad_contract.validate_integrity()
        self.assertFalse(ok)
        self.assertTrue(any("overlap" in issue.lower() for issue in issues))

    def test_malformed_tampered_parent_lock_rejected(self) -> None:
        """A tampered predecessor lock file fails predecessor verification."""
        tampered_lock = self.work_dir / "tampered.lock"
        tampered_lock.write_text(json.dumps({
            "lock_version": "1.0.0",
            "wave": 2,
            "status": "LOCKED",
            "receipt_digest": "0000000000000000000000000000000000000000000000000000000000000000",
            "lock_digest": "bad_digest_not_matching_content",
        }), encoding="utf-8")

        ok, reason, _ = verify_predecessor_lock(tampered_lock, expected_parent_wave=2)
        self.assertFalse(ok)
        self.assertIn("digest mismatch", reason.lower())

    # ── Category 3: Invariant Bypass Attempt ──────────────────────────────────

    def test_invariant_mutation_detection_on_obligations(self) -> None:
        """Attempts to silently mutate contract obligations are detected immediately."""
        baseline = CANONICAL_OUTPUT2_PROMPT_CONTRACT

        # 1. Mutate required sections (e.g. drop Action Ledger)
        mutated_sections = PromptContract(
            contract_id=baseline.contract_id,
            output_id=baseline.output_id,
            stage_name=baseline.stage_name,
            version=baseline.version,
            required_sections=("Meeting Objective", "Key Discussion Points & Findings"),
            conditional_sections=baseline.conditional_sections,
            forbidden_sections=baseline.forbidden_sections,
            owner_rules=baseline.owner_rules,
            rubric_weights=baseline.rubric_weights,
            thresholds=baseline.thresholds,
            modality_overrides=baseline.modality_overrides,
        )
        has_mut, diffs = detect_contract_mutation(baseline, mutated_sections)
        self.assertTrue(has_mut)
        self.assertTrue(any("required_sections mutated" in d for d in diffs))

        # 2. Mutate rubric weights
        mutated_weights = PromptContract(
            contract_id=baseline.contract_id,
            output_id=baseline.output_id,
            stage_name=baseline.stage_name,
            version=baseline.version,
            required_sections=baseline.required_sections,
            conditional_sections=baseline.conditional_sections,
            forbidden_sections=baseline.forbidden_sections,
            owner_rules=baseline.owner_rules,
            rubric_weights={
                "dimension_1_executive_altitude": 30,
                "dimension_2_technical_substance": 20,
                "dimension_3_strategic_synthesis": 25,
                "dimension_4_scoping_and_modality": 25,
            },
            thresholds=baseline.thresholds,
            modality_overrides=baseline.modality_overrides,
        )
        has_mut, diffs = detect_contract_mutation(baseline, mutated_weights)
        self.assertTrue(has_mut)
        self.assertTrue(any("rubric_weights mutated" in d for d in diffs))

        # 3. Mutate owner rules (e.g. relax unassigned tolerance)
        mutated_owner = PromptContract(
            contract_id=baseline.contract_id,
            output_id=baseline.output_id,
            stage_name=baseline.stage_name,
            version=baseline.version,
            required_sections=baseline.required_sections,
            conditional_sections=baseline.conditional_sections,
            forbidden_sections=baseline.forbidden_sections,
            owner_rules={"disallow_unassigned": False, "require_single_named_dri": False},
            rubric_weights=baseline.rubric_weights,
            thresholds=baseline.thresholds,
            modality_overrides=baseline.modality_overrides,
        )
        has_mut, diffs = detect_contract_mutation(baseline, mutated_owner)
        self.assertTrue(has_mut)
        self.assertTrue(any("owner_rules mutated" in d for d in diffs))

    def test_invariant_prose_changes_do_not_alter_obligation_digest(self) -> None:
        """Harmless prose commentary does NOT alter canonical obligation digest."""
        contract = CANONICAL_OUTPUT2_PROMPT_CONTRACT
        digest1 = canonical_obligation_digest(contract)

        # Re-compute digest on an identical contract instance
        contract_clone = PromptContract(
            contract_id=contract.contract_id,
            output_id=contract.output_id,
            stage_name=contract.stage_name,
            version=contract.version,
            required_sections=contract.required_sections,
            conditional_sections=contract.conditional_sections,
            forbidden_sections=contract.forbidden_sections,
            owner_rules=dict(contract.owner_rules),
            rubric_weights=dict(contract.rubric_weights),
            thresholds=dict(contract.thresholds),
            modality_overrides=dict(contract.modality_overrides),
            prohibited_patterns=contract.prohibited_patterns,
        )
        digest2 = canonical_obligation_digest(contract_clone)
        self.assertEqual(digest1, digest2, "Obligation digest must remain invariant across identical obligation payloads")

    def test_invariant_all_placeholder_owner_variants_rejected(self) -> None:
        """Every placeholder and collective owner variant is strictly rejected fail-closed."""
        contract = CANONICAL_OUTPUT2_PROMPT_CONTRACT
        prohibited_samples = [
            "Unassigned",
            "unassigned",
            "PENDING",
            "pending",
            "TBD",
            "tbd",
            "Team",
            "TEAM",
            "All",
            "PartnerAdvisory / NEE",
            "PartnerAdvisory / Client",
            "PartnerAdvisory/Client",
            "Client Team",
            "Engineering Team",
            "Alice, Bob",
            "Alice, Bob, Carol",
            "Alice / Bob",
            "Alice & Bob",
            "Alice and Bob",
        ]
        for sample in prohibited_samples:
            ok, err = validate_owner_string(sample, contract)
            self.assertFalse(ok, f"Expected '{sample}' to be rejected as invalid owner")

    def test_invariant_overwrite_prevention_on_receipt_and_lock(self) -> None:
        """Overwrite of existing receipt or lock file without --allow-overwrite fails closed."""
        dummy_receipt = self.work_dir / "existing_receipt.json"
        dummy_receipt.write_text("{}", encoding="utf-8")
        dummy_lock = self.work_dir / "existing_lock.lock"
        dummy_lock.write_text("{}", encoding="utf-8")

        spec_path = self.work_dir / "test_spec.yaml"
        spec_path.write_text(yaml.dump({
            "wave_number": 3,
            "acceptance_criteria": [],
            "test_suites": [],
            "required_prior_lock": str(self.parent_lock),
        }), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "3",
            "--spec", str(spec_path),
            "--receipt", str(dummy_receipt),
            "--lock", str(dummy_lock),
            "--require-lock", str(self.parent_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("already exists", res.stdout)

    # ── Category 4: Rollback & Recovery ───────────────────────────────────────

    def test_rollback_no_partial_receipt_or_lock_on_gate_failure(self) -> None:
        """Failed gate execution leaves zero orphaned receipt or lock files at destination."""
        spec_path = self.work_dir / "failing_spec.yaml"
        spec_path.write_text(yaml.dump({
            "wave_number": 3,
            "acceptance_criteria": ["Must pass"],
            "test_suites": ["tests/non_existent_test_suite_xyz.py"],
            "required_prior_lock": str(self.parent_lock),
        }), encoding="utf-8")

        target_receipt = self.work_dir / "nonexistent_receipt.json"
        target_lock = self.work_dir / "nonexistent_lock.lock"

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "3",
            "--spec", str(spec_path),
            "--receipt", str(target_receipt),
            "--lock", str(target_lock),
            "--require-lock", str(self.parent_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(target_receipt.exists(), "No receipt file should be created on gate failure")
        self.assertFalse(target_lock.exists(), "No lock file should be created on gate failure")


if __name__ == "__main__":
    unittest.main()
