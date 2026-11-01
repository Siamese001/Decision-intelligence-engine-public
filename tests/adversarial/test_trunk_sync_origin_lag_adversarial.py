"""tests/adversarial/test_trunk_sync_origin_lag_adversarial.py

Adversarial test suite for Trunk Synchronization & Origin Lag Hardening (Wave 8).
Verifies:
1. Origin lag exceeding the 5-commit ceiling fails closed in verify_push_receipt.
2. AI-DLC KPI report exits with returncode 1 when origin lag exceeds --fail-above.
3. GOVERNED_PUSH_FORCE=1 override functions with prominent advisory warning.
4. Invalid shard arguments (--shard 0, --shard 3 of 2) fail closed with exit code 2.
5. Tampered or mismatched tree digest returns None in load_attestation_by_tree.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools.attestation_core import load_attestation_by_tree
from tools.verify_push_receipt import check_origin_lag, verify_push_receipt


class TrunkSyncOriginLagAdversarialTests(unittest.TestCase):
    """Adversarial security tests for trunk synchronization and origin lag gating."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent
        cls.python_bin = sys.executable

    def test_origin_lag_exceeding_ceiling_fails_closed(self) -> None:
        """Verifies that unpushed commit lag > max_lag triggers fail-closed push rejection."""
        # Test with max_lag=-1 where lag (>= 0) is guaranteed to exceed ceiling
        ok, lag, msg = check_origin_lag(self.repo_root, max_lag=-1)
        self.assertFalse(ok, "Lag > max_lag must fail closed")
        self.assertIn("Trunk synchronization failure", msg)
        self.assertIn("ahead of", msg.lower())

    def test_kpi_report_fail_above_threshold_triggers_failure(self) -> None:
        """Verifies aidlc_kpi_report exits with code 1 when origin lag exceeds --fail-above."""
        cmd = [
            self.python_bin,
            "tools/aidlc_kpi_report.py",
            "--metric",
            "origin-lag",
            "--fail-above",
            "-1.0",
        ]
        proc = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, "Must exit with code 1 when metric exceeds threshold")
        self.assertIn("exceeded allowed threshold", proc.stderr)

    def test_governed_push_force_override_behavior(self) -> None:
        """Verifies GOVERNED_PUSH_FORCE=1 permits push override with explicit warning."""
        with patch.dict(os.environ, {"GOVERNED_PUSH_FORCE": "1"}):
            # Test when origin lag fails (max_lag=-1), force override permits push
            passed, msg, _ = verify_push_receipt(self.repo_root, max_lag=-1)
            self.assertTrue(passed)
            self.assertIn("GOVERNED_PUSH_FORCE", msg)

    def test_invalid_shard_zero_fails_closed(self) -> None:
        """Verifies that --shard 0 is rejected by argument parser."""
        cmd = [
            self.python_bin,
            "tools/run_ci_gates.py",
            "--profile",
            "e2e",
            "--shard",
            "0",
            "--total-shards",
            "2",
        ]
        proc = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, "Shard 0 must fail closed with exit code 2")
        self.assertIn("--shard must be between 1 and 2", proc.stderr)

    def test_invalid_shard_overflow_fails_closed(self) -> None:
        """Verifies that --shard 3 with --total-shards 2 is rejected."""
        cmd = [
            self.python_bin,
            "tools/run_ci_gates.py",
            "--profile",
            "e2e",
            "--shard",
            "3",
            "--total-shards",
            "2",
        ]
        proc = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, "Shard overflow must fail closed with exit code 2")
        self.assertIn("--shard must be between 1 and 2", proc.stderr)

    def test_tampered_tree_digest_returns_no_attestation(self) -> None:
        """Verifies that a fabricated tree digest never resolves to an authentic attestation."""
        bogus_digest = "0" * 64
        stmt = load_attestation_by_tree(self.repo_root, bogus_digest)
        self.assertIsNone(stmt, "Fabricated tree digest must return None")


if __name__ == "__main__":
    unittest.main()
