"""Adversarial and edge-case tests for Wave 1 runner bootstrap and receipt verification."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.hitl_resolution_runner_core.models import WaveReceipt, WaveSpec
from tools.hitl_resolution_runner_core.receipt import persist_wave_receipt
from tools.hitl_resolution_runner_core.verification import (
    validate_environment,
    validate_parent_receipt,
)


class TestHitlResolutionW1Adversarial(unittest.TestCase):
    """Adversarial stress suite targeting runner pre-flight, sandbox boundaries, and receipt locks."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name) / "sandbox"
        self.receipt_root = Path(self.tmp_dir.name) / "reports"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.receipt_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_adversarial_sandbox_violation_rejection(self) -> None:
        """Verifies that running in repo root or production output/ fails closed."""
        # 1. Reject primary repo root as runtime root
        valid, msg = validate_environment(self.repo_root, self.receipt_root, wave=1, repo_root=self.repo_root)
        self.assertFalse(valid)
        self.assertIn("cannot be the primary repository root", msg)

        # 2. Reject output/ directory as runtime root
        output_dir = self.repo_root / "output"
        valid, msg = validate_environment(output_dir, self.receipt_root, wave=1, repo_root=self.repo_root)
        self.assertFalse(valid)
        self.assertIn("cannot reside within production output/", msg)

    def test_adversarial_immutable_receipt_overwrite_rejection(self) -> None:
        """Verifies that an existing receipt cannot be overwritten (fail-closed append-only invariant)."""
        receipt = WaveReceipt(
            schema_version="1.0.0",
            wave=1,
            title="Wave 1 Test",
            run_id="run_001",
            plan_digest="0" * 64,
            spec_digest="1" * 64,
            code_tree_digest="2" * 64,
            runtime_root=str(self.runtime_root),
            receipt_root=str(self.receipt_root),
            parent_receipt_digest=None,
            command="run",
            started_at="2026-10-06T00:00:00Z",
            completed_at="2026-10-06T00:00:01Z",
            terminal_result="COMPLETE",
            exit_code=0,
            test_counts={"passed": 1},
            test_ids=["t1"],
            ci_profile_resolution={},
        )
        # First persistence succeeds
        target, _ = persist_wave_receipt(receipt, self.receipt_root)
        self.assertTrue(target.is_file())

        # Second persistence must raise FileExistsError
        with self.assertRaises(FileExistsError):
            persist_wave_receipt(receipt, self.receipt_root)

        # Environment validator must also detect conflict
        valid, msg = validate_environment(self.runtime_root, self.receipt_root, wave=1, repo_root=self.repo_root)
        self.assertFalse(valid)
        self.assertIn("already exists", msg)

    def test_adversarial_parent_receipt_validation_failures(self) -> None:
        """Verifies that missing, corrupt, or failed predecessor receipts block execution."""
        # 1. Missing parent receipt for Wave 2
        valid, digest, msg = validate_parent_receipt(self.receipt_root, parent_wave=1)
        self.assertFalse(valid)
        self.assertIn("Parent receipt missing", msg)

        # 2. Corrupt JSON parent receipt
        p_path = self.receipt_root / "wave_1_receipt.json"
        p_path.write_text("{corrupt json ...", encoding="utf-8")
        valid, digest, msg = validate_parent_receipt(self.receipt_root, parent_wave=1)
        self.assertFalse(valid)
        self.assertIn("Failed parsing parent receipt", msg)

        # 3. Parent receipt marked FAILED
        p_path.write_text(json.dumps({"terminal_result": "FAILED", "wave": 1}), encoding="utf-8")
        valid, digest, msg = validate_parent_receipt(self.receipt_root, parent_wave=1)
        self.assertFalse(valid)
        self.assertIn("expected COMPLETE", msg)

    def test_adversarial_malformed_wave_spec_handling(self) -> None:
        """Verifies robust validation of missing keys and invalid types in wave spec."""
        # Missing wave key
        with self.assertRaises(KeyError):
            WaveSpec.from_dict({"title": "No wave"})

        # Type conversion resilience
        spec = WaveSpec.from_dict({
            "wave": "2",
            "title": "Wave 2 String Wave",
            "test_files": ["tests/t1.py"],
            "required_min_pass_count": "5",
            "parent_wave": "1",
        })
        self.assertEqual(spec.wave, 2)
        self.assertEqual(spec.required_min_pass_count, 5)
        self.assertEqual(spec.parent_wave, 1)


if __name__ == "__main__":
    unittest.main()
