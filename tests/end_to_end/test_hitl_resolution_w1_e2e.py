"""End-to-end tests for Wave 1: Policy truth reconciliation and runner bootstrap."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_divergence_margin_threshold,
    needs_operator_decision,
)
from tools.hitl_resolution_runner_core.models import WaveReceipt, WaveSpec
from tools.hitl_resolution_runner_core.receipt import persist_wave_receipt
from tools.hitl_resolution_runner_core.verification import (
    compute_file_digest,
    validate_environment,
    validate_parent_receipt,
)


class TestHitlResolutionW1E2E(unittest.TestCase):
    """E2E suite verifying baseline policy snapshot, characterization, and runner bootstrap."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name) / "runtime"
        self.receipt_root = Path(self.tmp_dir.name) / "reports"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.receipt_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_policy_reconciliation_record_integrity(self) -> None:
        """Verifies policy_reconciliation_record.json aligns with active runtime and registry snapshot."""
        rec_path = self.repo_root / "governance" / "policy_reconciliation_record.json"
        self.assertTrue(rec_path.is_file(), "governance/policy_reconciliation_record.json must exist")

        data = json.loads(rec_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema_version"), "1.0.0")

        summary = data.get("reconciliation_summary", {})
        self.assertEqual(summary.get("effective_runtime_ambiguity_threshold"), 0.30)
        self.assertEqual(summary.get("registry_declared_ambiguity_threshold"), 0.15)
        self.assertEqual(summary.get("effective_divergence_threshold"), 0.20)

        # Verify active accessor matches effective runtime snapshot
        self.assertEqual(get_hitl_ambiguity_margin_threshold(), 0.30)
        self.assertEqual(get_hitl_divergence_margin_threshold(), 0.20)

    def test_reproduce_incident_candidate_characterization(self) -> None:
        """Reproduces 0.95 vs 0.52 candidate: spread 0.43 strictly exceeds 0.30 threshold."""
        c_top = 0.95
        c_second = 0.52

        # 1. Accessor level
        is_ambiguous = needs_operator_decision(c_top, c_second, threshold=0.30)
        self.assertFalse(is_ambiguous, "0.95 vs 0.52 (43% gap) must not require operator decision under 30% rule")

        # 2. MarginEvaluator level
        eval_res = MarginEvaluator.evaluate(
            [{"confidence": c_top}, {"confidence": c_second}],
            threshold=0.30,
            confidence_floor=0.65,
        )
        self.assertFalse(eval_res["surface_hitl"])
        self.assertTrue(eval_res["decision_resolved_autonomously"])
        self.assertEqual(eval_res["margin"], 0.43)
        self.assertFalse(eval_res["confidence_floor_breached"])

    def test_boundary_and_ambiguous_conditions(self) -> None:
        """Verifies boundary condition (spread == 0.30) and ambiguous condition (spread < 0.30)."""
        # Exact boundary: 0.90 vs 0.60 -> gap = 0.30 (inclusive boundary requires review)
        self.assertTrue(needs_operator_decision(0.90, 0.60, threshold=0.30))
        eval_boundary = MarginEvaluator.evaluate(
            [{"confidence": 0.90}, {"confidence": 0.60}],
            threshold=0.30,
            confidence_floor=0.65,
        )
        self.assertTrue(eval_boundary["surface_hitl"])
        self.assertFalse(eval_boundary["decision_resolved_autonomously"])

        # Ambiguous condition: 0.90 vs 0.65 -> gap = 0.25 <= 0.30
        self.assertTrue(needs_operator_decision(0.90, 0.65, threshold=0.30))
        eval_amb = MarginEvaluator.evaluate(
            [{"confidence": 0.90}, {"confidence": 0.65}],
            threshold=0.30,
            confidence_floor=0.65,
        )
        self.assertTrue(eval_amb["surface_hitl"])
        self.assertFalse(eval_amb["decision_resolved_autonomously"])

    def test_manifest_discovery_and_wave_spec_contracts(self) -> None:
        """Verifies that all seven wave manifests exist and conform to WaveSpec schema."""
        for w in range(1, 8):
            spec_file = self.repo_root / "config" / "testing" / "hitl_resolution" / f"wave-{w}.json"
            self.assertTrue(spec_file.is_file(), f"wave-{w}.json must exist")

            data = json.loads(spec_file.read_text(encoding="utf-8"))
            spec = WaveSpec.from_dict(data)
            self.assertEqual(spec.wave, w)
            self.assertGreater(len(spec.test_files), 0)
            self.assertGreater(len(spec.e2e_files), 0)
            self.assertGreater(len(spec.adversarial_files), 0)
            if w == 1:
                self.assertIsNone(spec.parent_wave)
            else:
                self.assertEqual(spec.parent_wave, w - 1)

    def test_runner_receipt_creation_and_chain_verification(self) -> None:
        """Verifies WaveReceipt lifecycle, exclusive persistence, and parent receipt chaining."""
        # 1. Environment validation
        valid, msg = validate_environment(self.runtime_root, self.receipt_root, wave=1, repo_root=self.repo_root)
        self.assertTrue(valid, msg)

        # 2. Wave 1 parent check (should be valid without parent)
        p_valid, p_digest, p_msg = validate_parent_receipt(self.receipt_root, parent_wave=None)
        self.assertTrue(p_valid)
        self.assertIsNone(p_digest)

        # 3. Create synthetic Wave 1 receipt
        receipt_w1 = WaveReceipt(
            schema_version="1.0.0",
            wave=1,
            title="Establish policy truth and trustworthy verification",
            run_id="test_run_w1",
            plan_digest="a" * 64,
            spec_digest="b" * 64,
            code_tree_digest="c" * 64,
            runtime_root=str(self.runtime_root),
            receipt_root=str(self.receipt_root),
            parent_receipt_digest=None,
            command="run_hitl_resolution_wave.py --wave 1",
            started_at="2026-10-06T00:00:00Z",
            completed_at="2026-10-06T00:00:10Z",
            terminal_result="COMPLETE",
            exit_code=0,
            test_counts={"total_collected": 5, "passed": 5, "failed": 0, "errors": 0, "skipped": 0, "xfail": 0, "xpass": 0},
            test_ids=["test1", "test2", "test3", "test4", "test5"],
            ci_profile_resolution={"all_enrolled": True},
        )
        r_path, r_digest = persist_wave_receipt(receipt_w1, self.receipt_root)
        self.assertTrue(r_path.is_file())
        self.assertEqual(len(r_digest), 64)

        # 4. Wave 2 parent check against Wave 1 receipt
        p2_valid, p2_digest, p2_msg = validate_parent_receipt(self.receipt_root, parent_wave=1)
        self.assertTrue(p2_valid, p2_msg)
        self.assertEqual(p2_digest, r_digest)


if __name__ == "__main__":
    unittest.main()
