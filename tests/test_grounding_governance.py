"""tests/test_grounding_governance.py

Qualification suite for Wave 5: Staged Git, readiness and policy governance integration.
Asserts that:
1. Staged ungrounded content fails even if unstaged working tree copy is modified/grounded.
2. Tampered PASS receipts or forged canonical digests are rejected fail-closed.
3. Clean staged payloads and manifests pass verification.
4. Legacy 29-gate receipts lacking Grounded Delivery verification are rejected by _matches_receipt.
5. The complete 30-gate set is required for promotion qualification.
6. Passing --skip-readiness-run without an authentic passing receipt fails closed.
7. tools/readiness_core._build_stages defines 30 stages with stage 30 bound to Grounded Delivery verification.
8. governance/policy_registry.yaml harmonizes all 4 grounding invariant families.
9. AGENTS.md complies with <=12KB size limit and contains all required sentinels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    DeliveryReceipt,
    compute_rfc8259_digest,
)
from tools.generate_agents_index import load_policy_registry
from tools.pr_governance_core.inspection import _matches_receipt
from tools.pr_governance_core.lifecycle import _execute_or_skip_readiness
from tools.readiness_core import _build_stages
from tools.validate_staged_grounding import validate_staged_grounding


class TestGroundingGovernance(unittest.TestCase):
    """Test suite for staged Git grounding and readiness qualification governance."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir_obj.name)

        # Initialize temporary git repository
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "tester@example.com"],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Governance Tester"],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
        )

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    def test_staged_ungrounded_content_fails_even_if_unstaged_working_copy_is_grounded(self) -> None:
        """Verify ungrounded staged content fails even when the working tree file has been modified."""
        # 1. Create and stage an ungrounded payload
        staged_content = "This is ungrounded staged content with fabricated facts."
        payload_file = self.repo_root / "delivery_payload.txt"
        payload_file.write_text(staged_content, encoding="utf-8")

        # 2. Manifest expects a DIFFERENT (grounded) hash
        grounded_content = "This is verified grounded content supported by graph assertions."
        expected_digest = hashlib.sha256(grounded_content.encode("utf-8")).hexdigest()

        manifest = ClaimManifest(
            manifest_id="MNF-GOV-001",
            payload_digest=expected_digest,
            runtime_root="/tmp/runtime",
            graph_generation_id="GEN-001",
            graph_sha256="d" * 64,
            claims=[],
            extensions={"payload.path": "delivery_payload.txt"},
        )
        manifest_file = self.repo_root / "claim_manifest.json"
        manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

        # Stage both files into git index
        subprocess.run(["git", "add", "delivery_payload.txt", "claim_manifest.json"], cwd=self.repo_root, check=True)

        # 3. Modify working copy of delivery_payload.txt to match the manifest (UNSTAGED)
        payload_file.write_text(grounded_content, encoding="utf-8")

        # 4. Verify that working tree matches manifest on disk
        on_disk_sha = hashlib.sha256(payload_file.read_bytes()).hexdigest()
        self.assertEqual(on_disk_sha, expected_digest)

        # 5. But staged git index still has ungrounded bytes -> validate_staged_grounding MUST FAIL
        rc, violations = validate_staged_grounding(self.repo_root, staged=True)
        self.assertEqual(rc, 1, "Expected staged verification to fail closed due to staged index mismatch")
        self.assertTrue(
            any("staged index mismatch" in v for v in violations),
            f"Expected 'staged index mismatch' violation, got: {violations}",
        )

    def test_tampered_pass_receipt_fails(self) -> None:
        """Verify DeliveryReceipt with status CERTIFIED but contradiction fails closed."""
        receipt = DeliveryReceipt(
            receipt_id="RCP-TAMPER-001",
            manifest_id="MNF-001",
            manifest_digest="a" * 64,
            payload_digest="b" * 64,
            runtime_root="/tmp/runtime",
            graph_generation_id="GEN-001",
            graph_sha256="c" * 64,
            status="CERTIFIED",
            reason_code="GROUNDED_SUPPORTED",
            total_claims_count=1,
            supported_claims_count=1,
            contradicted_claims_count=1,  # Contradiction in certified receipt!
            delivery_surface="EMAIL_BODY",
            audience="CLIENT_SAFE",
            word_count=50,
        )
        receipt_file = self.repo_root / "delivery_receipt.json"
        receipt_file.write_text(json.dumps(receipt.to_dict(), indent=2), encoding="utf-8")
        subprocess.run(["git", "add", "delivery_receipt.json"], cwd=self.repo_root, check=True)

        rc, violations = validate_staged_grounding(self.repo_root, staged=True)
        self.assertEqual(rc, 1)
        self.assertTrue(any("Tampered DeliveryReceipt" in v for v in violations))

    def test_clean_staged_payload_and_manifest_passes(self) -> None:
        """Verify matching staged payload and manifest pass verification."""
        content = "Verified clean delivery payload content."
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        payload_file = self.repo_root / "clean_payload.txt"
        payload_file.write_text(content, encoding="utf-8")

        manifest = ClaimManifest(
            manifest_id="MNF-CLEAN-001",
            payload_digest=digest,
            runtime_root=str(self.repo_root),
            graph_generation_id="GEN-CLEAN",
            graph_sha256="c" * 64,
            claims=[],
            extensions={"payload.path": "clean_payload.txt"},
        )
        manifest_file = self.repo_root / "clean_manifest.json"
        manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

        subprocess.run(["git", "add", "clean_payload.txt", "clean_manifest.json"], cwd=self.repo_root, check=True)

        rc, violations = validate_staged_grounding(self.repo_root, staged=True)
        self.assertEqual(rc, 0)
        self.assertEqual(violations, [])

    def test_legacy_29_gate_receipt_rejected_by_matches_receipt(self) -> None:
        """Verify legacy readiness receipts lacking the 30th Grounded Delivery gate fail matching."""
        stages = [
            {"stage_index": i, "stage_name": f"Linter Stage {i}", "status": "PASS"}
            for i in range(1, 30)
        ]
        data = {
            "branch": "feat/legacy-branch",
            "head_sha": "abc123456789",
            "terminal_outcome": "PASS",
            "total_stages": 29,
            "stages_evaluated": 29,
            "stages": stages,
        }
        # Missing stage 30 Grounded Delivery
        matched = _matches_receipt(data, "feat/legacy-branch", "abc123456789")
        self.assertFalse(matched, "Legacy 29-gate receipt must be rejected")

    def test_30_gate_receipt_missing_grounding_stage_rejected(self) -> None:
        """Verify 30-gate receipt missing the specific Grounded Delivery stage is rejected."""
        stages = [
            {"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"}
            for i in range(1, 31)
        ]
        data = {
            "branch": "feat/test",
            "head_sha": "sha123",
            "terminal_outcome": "PASS",
            "total_stages": 30,
            "stages": stages,
        }
        matched = _matches_receipt(data, "feat/test", "sha123")
        self.assertFalse(matched)

    def test_complete_30_gate_receipt_accepted(self) -> None:
        """Verify valid 30-gate receipt containing Grounded Delivery stage is accepted."""
        stages = [
            {"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"}
            for i in range(1, 30)
        ]
        stages.append({
            "stage_index": 30,
            "stage_name": "Grounded Delivery & Claim Manifest Verification",
            "status": "PASS",
        })
        data = {
            "branch": "feat/current",
            "head_sha": "sha456",
            "terminal_outcome": "PASS",
            "total_stages": 30,
            "stages": stages,
        }
        matched = _matches_receipt(data, "feat/current", "sha456")
        self.assertTrue(matched, "Valid 30-gate receipt must be accepted")

    def test_skip_readiness_run_without_valid_receipt_fails_closed(self) -> None:
        """Verify --skip-readiness-run flag cannot bypass execution when valid receipt is absent."""
        args = argparse.Namespace(
            skip_readiness_run=True,
            full=False,
            force_readiness=False,
        )
        info = {
            "readiness_receipt_pass": False,
        }
        passed = _execute_or_skip_readiness(
            branch="feat/bypass-attempt",
            target_cwd=self.repo_root,
            info=info,
            args=args,
            py="python",
        )
        self.assertFalse(passed, "--skip-readiness-run must fail closed when receipt is absent or invalid")

    def test_readiness_core_build_stages_has_30_gates(self) -> None:
        """Verify _build_stages creates at least 30 gates with stage 30 as Grounded Delivery verification."""
        stages = _build_stages(py="python", target_files=[], test_modules=[])
        self.assertGreaterEqual(len(stages), 30, f"Expected at least 30 stages in _build_stages, got {len(stages)}")
        stage_30_name, stage_30_cmd = stages[29]
        self.assertEqual(stage_30_name, "Grounded Delivery & Claim Manifest Verification")
        self.assertTrue(any("validate_staged_grounding.py" in str(c) for c in stage_30_cmd))

    def test_policy_registry_has_four_grounding_invariant_families(self) -> None:
        """Verify all 4 grounding invariant policies are registered in governance/policy_registry.yaml."""
        root = Path(__file__).resolve().parents[1]
        registry = load_policy_registry(root)
        policies = registry.get("policies", [])
        policy_ids = {p.get("id") for p in policies}

        expected_ids = {
            "INV-GROUNDING-MANDATORY-ASSERTION",
            "INV-GROUNDING-STRICT-METRICS",
            "INV-GROUNDING-DELIVERY-IMMUTABILITY",
            "INV-GROUNDING-SAFE-FAILURE-RESPONSE",
        }
        for expected in expected_ids:
            self.assertIn(expected, policy_ids, f"Policy {expected} missing from policy_registry.yaml")

    def test_agents_md_satisfies_size_and_sentinels(self) -> None:
        """Verify AGENTS.md satisfies <=12KB size ceiling and contains runtime evidence guard."""
        root = Path(__file__).resolve().parents[1]
        agents_file = root / "AGENTS.md"
        content = agents_file.read_text(encoding="utf-8")
        byte_size = len(content.encode("utf-8"))
        self.assertLessEqual(byte_size, 12288, f"AGENTS.md size {byte_size} exceeds 12288 bytes")
        self.assertIn("<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->", content)
        self.assertIn("<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->", content)


if __name__ == "__main__":
    unittest.main()
