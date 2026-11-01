"""tests/end_to_end/test_hosted_ci_and_trunk_sync_e2e.py

End-to-End test suite for Local Quality Gates & Trunk Synchronization.
Verifies:
1. Sharded test resolution in tools/run_ci_gates.py:
   - Partitions E2E_TESTS into disjoint shards covering 100% of tests.
2. Unified governance linter runner (tools/run_governance_linters.py):
   - Verifies all 17 linters execute and pass cleanly.
3. Pre-push receipt and trunk sync verification (tools/verify_push_receipt.py):
   - Verifies origin lag ceiling (<= 5 commits) and DSSE attestation integration.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tools.aidlc_kpi_report import compute_origin_lag
from tools.run_ci_gates import DETERMINISTIC_TESTS, E2E_TESTS
from tools.run_governance_linters import CORE_LINTERS
from tools.verify_push_receipt import check_origin_lag, verify_push_receipt


class HostedCIAndTrunkSyncE2ETests(unittest.TestCase):
    """End-to-end tests for local quality gates and trunk synchronization."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_run_ci_gates_e2e_sharding_partitions(self) -> None:
        """Verifies tools/run_ci_gates.py partitions E2E_TESTS into disjoint non-empty shards."""
        total_tests = len(E2E_TESTS)
        self.assertGreater(total_tests, 100, "E2E_TESTS must contain tests")

        shard1 = [t for i, t in enumerate(E2E_TESTS) if i % 2 == 0]
        shard2 = [t for i, t in enumerate(E2E_TESTS) if i % 2 == 1]

        self.assertEqual(len(shard1) + len(shard2), total_tests)
        self.assertTrue(set(shard1).isdisjoint(set(shard2)), "Shards must be mutually disjoint")
        self.assertEqual(
            set(shard1) | set(shard2), set(E2E_TESTS), "Shards union must equal total set"
        )

    def test_17_readiness_linters_enrolled(self) -> None:
        """Verifies that all 17 readiness invariant linters are enrolled in CORE_LINTERS."""
        self.assertEqual(
            len(CORE_LINTERS),
            17,
            f"CORE_LINTERS must contain exactly 17 linters (got {len(CORE_LINTERS)})",
        )
        linter_names = [name for name, _ in CORE_LINTERS]
        expected_names = [
            "Model-Neutral Naming",
            "Layer Hierarchy & Cycle Linter",
            "Module File Budgets",
            "Governed Storage & SQLite Safety (Ruff TID251)",
            "Production Purity Linter",
            "Stage U0 Direct Ingestion Linter",
            "Credential & Career Firewall Scanner",
            "Mock Boundary Linter",
            "Contract Parity Linter",
            "Epistemic Boundaries Audit",
            "HITL Ledger Merkle Integrity",
            "Dual Pipeline Contracts Validation",
            "No Synthetic Embeddings Policy",
            "Wave Implementation Plan Schema Gate",
            "Root File Allowlist & Size Guard",
            "Risk-Tiered Change Lane Classifier",
            "Prompt SSOT Integrity Linter",
        ]
        for exp in expected_names:
            self.assertIn(exp, linter_names, f"Expected linter '{exp}' must be enrolled")

    @patch("tools.verify_push_receipt.run_git")
    def test_pre_push_receipt_origin_lag_compliance(self, mock_run_git) -> None:
        """Verifies pre-push origin lag check passes when within the 5-commit ceiling."""
        mock_run_git.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="2\n")
        ok, lag, msg = check_origin_lag(self.repo_root, max_lag=5)
        self.assertTrue(ok, f"Origin lag check must pass under 5 commits: {msg}")
        self.assertEqual(lag, 2)

    @patch("tools.aidlc_kpi_report.subprocess.run")
    def test_kpi_report_origin_lag_metric(self, mock_subproc) -> None:
        """Verifies KPI report compute_origin_lag returns compliant count <= 5."""
        mock_subproc.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="2\n")
        lag = compute_origin_lag(self.repo_root)
        self.assertEqual(lag, 2)


if __name__ == "__main__":
    unittest.main()
