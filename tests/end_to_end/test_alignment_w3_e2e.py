"""tests/end_to_end/test_alignment_w3_e2e.py

End-to-End Verification Suite for Wave 3: Align generation and grading contracts.
Verifies:
1. Predecessor verification of Wave 2 lock (reports/alignment/wave-2.lock).
2. SSOT alignment on Action & Governance Ledger as Mandatory Baseline Core across:
   - Generator prompt (02_executive_recap.md)
   - Judge prompt (EXECUTIVE_RECAP_JUDGE_PROMPT.md)
   - Declarative contract (OUTPUT2_CONTRACT)
   - Layer 0 PromptContract (CANONICAL_OUTPUT2_PROMPT_CONTRACT)
3. Owner rules alignment: Single named individual DRI mandate with zero 'Unassigned' tolerance.
4. Contract parity verification across Outputs 1, 2, and 3.
5. Assembled bundle fingerprinting producing deterministic cryptographic signatures.
6. Pre-wave interceptor clearance for all Wave 3 ratified decisions.
7. Full governed gate execution generating verified Wave 3 receipt and sequential lock.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.l0.prompt_contract import (
    CANONICAL_OUTPUT1_PROMPT_CONTRACT,
    CANONICAL_OUTPUT2_PROMPT_CONTRACT,
    CANONICAL_OUTPUT3_PROMPT_CONTRACT,
    CANONICAL_PROMPT_CONTRACTS,
    canonical_obligation_digest,
    compute_assembled_bundle_fingerprint,
    validate_owner_string,
    verify_contract_parity,
)
from dual_pipeline.l0.receipts import (
    verify_predecessor_lock,
    verify_runtime_receipt,
    verify_sequential_lock,
)
from dual_pipeline.prompts.contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
    SectionRequirement,
)
from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
from tools.record_wave_receipt import verify_receipt


class TestAlignmentW3E2E(unittest.TestCase):
    """E2E suite for Wave 3 prompt & judge contract alignment governance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.receipt_path = self.work_dir / "wave-3.json"
        self.lock_path = self.work_dir / "wave-3.lock"
        self.spec_path = REPO_ROOT / "config" / "governance" / "alignment" / "wave-3.yaml"
        self.parent_lock_path = REPO_ROOT / "reports" / "alignment" / "wave-2.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_wave2_predecessor_lock_validity(self) -> None:
        """Wave 2 predecessor lock exists, is valid, and matches wave 2 sequence."""
        self.assertTrue(self.parent_lock_path.is_file(), "Wave 2 lock must exist before Wave 3 can proceed")
        ok, reason, parent_digest = verify_predecessor_lock(self.parent_lock_path, expected_parent_wave=2)
        self.assertTrue(ok, f"Wave 2 predecessor lock invalid: {reason}")
        self.assertTrue(parent_digest, "Missing parent lock digest")

    def test_recap_ledger_mandatory_baseline_core_alignment(self) -> None:
        """Action & Governance Ledger is aligned as Mandatory Baseline Core across all surfaces."""
        # 1. Check prompt markdown
        recap_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP")
        self.assertIn("MANDATORY BASELINE CORE: Action & Governance Ledger", recap_prompt)
        self.assertNotIn("CONDITIONAL EXTENSION SPECIFICATIONS\n\n### Action & Governance Ledger", recap_prompt)

        # 2. Check judge prompt markdown
        judge_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_RECAP")
        self.assertIn("Action & Governance Ledger (Mandatory Baseline Core)", judge_prompt)
        self.assertIn("mandatory baseline section across 100% of recaps", judge_prompt)
        self.assertNotIn("(or explicit 'Unassigned')", judge_prompt)

        # 3. Check declarative Output 2 contract
        for arch in MeetingArchetype:
            topo = OUTPUT2_CONTRACT.get_section_topology(arch)
            self.assertEqual(
                topo.get("Action & Governance Ledger"),
                SectionRequirement.MANDATORY,
                f"Action Ledger must be MANDATORY for {arch.value}",
            )

        # 4. Check L0 PromptContract
        contract = CANONICAL_OUTPUT2_PROMPT_CONTRACT
        self.assertIn("Action & Governance Ledger", contract.required_sections)
        self.assertNotIn("Action & Governance Ledger", contract.conditional_sections)

    def test_owner_rules_strict_single_named_dri_mandate(self) -> None:
        """Owner rules enforce single named individual DRI and disallow placeholder/collective owners."""
        contract = CANONICAL_OUTPUT2_PROMPT_CONTRACT
        self.assertTrue(contract.owner_rules.get("disallow_unassigned"))
        self.assertTrue(contract.owner_rules.get("require_single_named_dri"))
        self.assertTrue(contract.owner_rules.get("disallow_collective"))

        # Valid single named DRIs
        ok, err = validate_owner_string("Alice Chen", contract)
        self.assertTrue(ok, f"Valid DRI rejected: {err}")
        ok, err = validate_owner_string("Bob Smith (Lead Architect)", contract)
        self.assertTrue(ok, f"Valid DRI with role rejected: {err}")

        # Invalid placeholder owners
        for placeholder in ("Unassigned", "Pending", "TBD", "PartnerAdvisory / NEE", "Team", "All"):
            ok, err = validate_owner_string(placeholder, contract)
            self.assertFalse(ok, f"Placeholder '{placeholder}' must be rejected")
            self.assertIsNotNone(err)

        # Invalid multiple / collective owners
        for multi in ("Alice, Bob", "Alice / Bob", "Alice & Bob", "Alice and Bob"):
            ok, err = validate_owner_string(multi, contract)
            self.assertFalse(ok, f"Multiple owners '{multi}' must be rejected")
            self.assertIn("multiple or collective", err.lower())

    def test_contract_parity_across_outputs_1_to_3(self) -> None:
        """Contract parity holds across all OutputContracts and L0 PromptContracts."""
        for out_id in (1, 2, 3):
            contract = CANONICAL_PROMPT_CONTRACTS[out_id]
            ok, issues = contract.validate_integrity()
            self.assertTrue(ok, f"Output {out_id} integrity failed: {issues}")

            # Verify parity with itself
            parity_ok, defects = verify_contract_parity(contract, contract)
            self.assertTrue(parity_ok, f"Output {out_id} parity defects: {defects}")

    def test_assembled_bundle_fingerprinting_deterministic(self) -> None:
        """Assembled bundle fingerprint is deterministic across prompt components."""
        p1 = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
        p2 = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages" / "02_architecture_deep_dive_override.md"
        p3 = REPO_ROOT / "governance" / "decision_intelligence" / "EXECUTIVE_RECAP_JUDGE_PROMPT.md"

        fp1 = compute_assembled_bundle_fingerprint([p1, p2, p3])
        fp2 = compute_assembled_bundle_fingerprint([p1, p2, p3])
        self.assertEqual(fp1, fp2, "Bundle fingerprint must be strictly deterministic")
        self.assertEqual(len(fp1), 64, "Bundle fingerprint must be 64-character SHA-256 hex string")

    def test_pre_wave_interceptor_clearance_for_wave3(self) -> None:
        """Pre-wave interceptor grants clearance for Wave 3 with ratified decisions."""
        pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"
        self.assertTrue(pre_gate_script.is_file())

        cmd = [
            sys.executable,
            str(pre_gate_script),
            "--wave", "3",
            "--plan-path", "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
            "--ledger-path", "hitl_decision_ledger.json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            res.returncode, 0,
            f"Pre-wave decision interceptor failed (exit {res.returncode}):\n{res.stdout}\n{res.stderr}",
        )
        self.assertIn("CLEARANCE GRANTED", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W3-RECAP-LEDGER-STATUS", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W3-OWNER-RULES", res.stdout)

    def test_full_wave3_gate_runner_execution(self) -> None:
        """Wave 3 gate runs successfully via child spec and seals receipt and sequential lock."""
        gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"

        with open(self.spec_path, "r", encoding="utf-8") as f:
            spec_data = yaml.safe_load(f)

        child_spec = dict(spec_data)
        child_spec["test_suites"] = ["tests/test_alignment_w3_adversarial.py"]
        child_spec_path = self.work_dir / "test_wave_3_child_spec.yaml"
        with open(child_spec_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(child_spec, f)

        cmd = [
            sys.executable,
            str(gate_script),
            "--wave", "3",
            "--spec", str(child_spec_path),
            "--receipt", str(self.receipt_path),
            "--lock", str(self.lock_path),
            "--require-lock", str(self.parent_lock_path),
            "--immutable",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            res.returncode, 0,
            f"Wave 3 gate failed (exit {res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}",
        )
        self.assertIn("WAVE 3 GATE PASSED", res.stdout)

        # Verify receipt
        self.assertTrue(self.receipt_path.is_file())
        with open(self.receipt_path, "r", encoding="utf-8") as f:
            receipt_data = json.load(f)
        self.assertEqual(receipt_data.get("status"), "COMPLETE")
        self.assertEqual(receipt_data.get("wave_id"), "Wave 3: Align generation and grading contracts")

        # Verify lock
        self.assertTrue(self.lock_path.is_file())
        with open(self.lock_path, "r", encoding="utf-8") as f:
            lock_data = json.load(f)
        self.assertEqual(lock_data.get("status"), "LOCKED")
        self.assertEqual(lock_data.get("receipt_digest"), receipt_data.get("receipt_digest"))

        # Predecessor lock verification on Wave 3 lock
        ok, reason, lock_digest = verify_predecessor_lock(self.lock_path, expected_parent_wave=3)
        self.assertTrue(ok, reason)

        # Cryptographic verification
        rec_ok, rec_reason, rec_digest = verify_runtime_receipt(
            self.receipt_path,
            expected_wave_id="Wave 3: Align generation and grading contracts",
        )
        self.assertTrue(rec_ok, rec_reason)
        self.assertTrue(verify_receipt(self.receipt_path))


if __name__ == "__main__":
    unittest.main()
