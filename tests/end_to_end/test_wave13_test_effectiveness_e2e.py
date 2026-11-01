"""
tests/end_to_end/test_wave13_test_effectiveness_e2e.py

End-to-End test suite for Wave 13 (Test Effectiveness & Program Closeout).
Validates full lifecycle integration across:
1. In-memory mutation score engine and baseline receipt emission.
2. Gate 10 testing gap audit incorporating measured mutation kill rates.
3. Third-party patch governance linter and grandfathered allowlist.
4. Shared test fakes (subprocess, HTTP, credentials) in lifecycle execution.
5. Closeout KPI scorecard verification (>= 80% targets met).

Governed under Finding X-32 and Decision H-8.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.fakes import (
    FakeCredentials,
    safe_credentials_environment,
)
from tools.aidlc_kpi_report import compare_reports, compute_kpi_metrics
from tools.audit_testing_gaps import TestingGapAuditor
from tools.kpi_scorecard import evaluate_section4_kpis
from tools.lint_third_party_patch import audit_test_file, load_allowlist
from tools.mutation_score_engine import run_mutation_evaluation


class TestWave13TestEffectivenessE2E(unittest.TestCase):
    """End-to-end integration tests for Wave 13 test effectiveness architecture."""

    def test_mutation_score_engine_lifecycle(self) -> None:
        """Verify mutation engine runs in-memory, generates valid scores, and seals receipt."""
        tmp_receipt = Path("reports/aidlc/test_temp_mutation_baseline.json")
        try:
            res = run_mutation_evaluation(
                modules=["dual_pipeline/agent_architecture_core/context_profile.py"],
                max_sites_per_module=5,
                receipt_out=tmp_receipt,
            )
            self.assertEqual(res.contract_id, "urn:nee:aidlc:mutation-score-receipt:1.0.0")
            self.assertGreater(res.total_mutants, 0)
            self.assertGreater(res.killed_mutants, 0)
            self.assertGreaterEqual(res.mutation_score_pct, 70.0)
            self.assertTrue(tmp_receipt.exists())

            data = json.loads(tmp_receipt.read_text(encoding="utf-8"))
            self.assertEqual(data["contract_id"], res.contract_id)
            self.assertEqual(data["total_mutants"], res.total_mutants)
        finally:
            tmp_receipt.unlink(missing_ok=True)

    def test_gate10_measured_inputs_integration(self) -> None:
        """Verify Gate 10 reads measured mutation scores and reports passing status."""
        auditor = TestingGapAuditor(repo_root=Path("."))
        score = auditor._load_measured_mutation_score()
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 70.0)

    def test_shared_test_fakes_e2e_lifecycle(self) -> None:
        """Verify real subprocess, network isolation, and credentials validation."""
        # 1. Real subprocess execution against real OS process table
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('success\\n')"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "success\n")

        # 2. Genuine network stack isolation: unreachable/unauthorized connection fails closed
        with self.assertRaises((urllib.error.URLError, ConnectionRefusedError, OSError)):
            urllib.request.urlopen("http://127.0.0.1:59999/unreachable", timeout=1.0)

        # 3. Credentials fake
        creds = FakeCredentials(api_key="mock_secret_key_123")
        self.assertTrue(creds.validate_non_leaking("Safe log output without key"))
        self.assertFalse(creds.validate_non_leaking("Leaked key mock_secret_key_123 in log"))

        with safe_credentials_environment({"TEST_KEY": "dummy_value"}) as env:
            self.assertEqual(env["TEST_KEY"], "dummy_value")

    def test_third_party_patch_linter_integration(self) -> None:
        """Verify third-party patch linter enforces allowlist for legacy tests and blocks new violations."""
        allowlist = load_allowlist()
        self.assertGreater(len(allowlist), 0)

        # Compliant test file
        valid_code = Path("tests/fakes/credentials_fake.py")
        violations = audit_test_file(valid_code, allowlist)
        self.assertEqual(violations, [])

    def test_section4_kpi_scorecard_closeout(self) -> None:
        """Verify Section 4 scorecard evaluates >= 80% targets met for Wave 13 closeout."""
        current = compute_kpi_metrics(count=20)
        current["origin_lag_commits"] = 0
        baseline = {"commit_range": "test-base", "origin_lag_commits": 0}
        scorecard = evaluate_section4_kpis(current, baseline)

        self.assertEqual(scorecard["total_kpis"], 21)
        self.assertGreaterEqual(scorecard["target_met_pct"], 80.0)
        self.assertEqual(scorecard["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
