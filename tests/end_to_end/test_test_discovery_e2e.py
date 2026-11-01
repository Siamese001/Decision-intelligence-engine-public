#!/usr/bin/env python3
"""End-to-End Test for Wave 5 Automated Test Discovery, Selection & Resolution Engine.

Exercises:
1. Full test discovery across tests directory.
2. Category resolution: deterministic, e2e, bge with strict precedence.
3. Decentralized manifest integration.
4. Quarantine registry integration and isolation.
5. Parity report generation and schema compliance (>= 95% coverage, 100% base parity).
6. Modular run_ci_gates interface verification (LOC <= 400).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.ci_gates_core.base_tests import (
    BASE_BGE_TESTS,
    BASE_DETERMINISTIC_TESTS,
    BASE_E2E_TESTS,
)
from tools.test_discovery_core import (
    discover_all_test_files,
    generate_resolution_report,
    get_quarantined_modules,
    load_quarantined_tests,
    resolve_all_tests,
)

ROOT = Path(__file__).resolve().parents[2]


class TestDiscoveryE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="test_discovery_e2e_")
        self.report_path = Path(self.tmp_dir) / "resolution_report.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_discover_all_test_files_e2e(self) -> None:
        """Verify discover_all_test_files discovers all tests in repo."""
        all_tests = discover_all_test_files(ROOT)
        self.assertGreater(len(all_tests), 1000)
        mods = [m for m, _ in all_tests]
        self.assertIn("tests.end_to_end.test_aidlc_kpi_report_e2e", mods)
        self.assertIn("tests.test_ci_gates", mods)

    def test_02_resolve_all_tests_with_precedence_and_parity_e2e(self) -> None:
        """Verify resolve_all_tests achieves 100% parity with historical base sets."""
        det, e2e, bge = resolve_all_tests(
            base_deterministic=BASE_DETERMINISTIC_TESTS,
            base_e2e=BASE_E2E_TESTS,
            base_bge=BASE_BGE_TESTS,
            repo_root=ROOT,
        )

        all_resolved = set(det) | set(e2e) | set(bge)
        quarantined = get_quarantined_modules()

        # Strict disjoint sets
        self.assertEqual(len(set(det) & set(e2e)), 0)
        self.assertEqual(len(set(det) & set(bge)), 0)
        self.assertEqual(len(set(e2e) & set(bge)), 0)

        # Parity: every historical base test must be accounted for
        for t in BASE_DETERMINISTIC_TESTS:
            self.assertTrue(t in all_resolved or t in quarantined, f"Base det test missing: {t}")
        for t in BASE_E2E_TESTS:
            self.assertTrue(t in all_resolved or t in quarantined, f"Base e2e test missing: {t}")
        for t in BASE_BGE_TESTS:
            self.assertTrue(t in all_resolved or t in quarantined, f"Base bge test missing: {t}")

    def test_03_resolution_report_generation_e2e(self) -> None:
        """Verify generate_resolution_report meets acceptance criteria."""
        report = generate_resolution_report(
            base_deterministic=BASE_DETERMINISTIC_TESTS,
            base_e2e=BASE_E2E_TESTS,
            base_bge=BASE_BGE_TESTS,
            out_path=self.report_path,
            repo_root=ROOT,
        )

        self.assertTrue(self.report_path.is_file())
        self.assertEqual(report["schema_version"], "1.0.0")
        self.assertEqual(report["report_type"], "aidlc-wave-5-test-resolution-report")
        self.assertTrue(report["parity_passed"])
        self.assertEqual(len(report["missing_from_resolved"]), 0)
        self.assertGreaterEqual(report["coverage_percentage"], 95.0)
        self.assertTrue(report["coverage_threshold_met"])

    def test_04_run_ci_gates_loc_budget_under_400_e2e(self) -> None:
        """Verify tools/run_ci_gates.py strictly complies with <= 400 LOC budget."""
        ci_gates_path = ROOT / "tools" / "run_ci_gates.py"
        self.assertTrue(ci_gates_path.is_file())
        lines = ci_gates_path.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(
            len(lines),
            400,
            f"tools/run_ci_gates.py exceeds 400 LOC limit: {len(lines)} lines",
        )

    def test_05_quarantined_tests_registry_validation_e2e(self) -> None:
        """Verify quarantined tests registry contains valid structure and active entries."""
        quarantined = load_quarantined_tests()
        self.assertGreater(len(quarantined), 0)
        for mod, meta in quarantined.items():
            self.assertEqual(meta["module"], mod)
            self.assertIn("reason", meta)
            self.assertIn("expires_at", meta)


if __name__ == "__main__":
    unittest.main()
