"""End-to-End Verification of E2E Runtime Authenticity Linter and Gap Auditor.

Verifies:
1. True runtime E2E tests executing production functions pass linting cleanly.
2. Static receipt-only test files are deterministically caught and flagged with ERR_E2E_RECEIPT_ONLY.
3. Source-grep test files are deterministically caught and flagged with ERR_E2E_SOURCE_GREP_ONLY.
4. Core-mock stubbed test files are caught and flagged with ERR_E2E_CORE_STUBBED_MOCK.
5. TestingGapAuditor discovers all recursive production packages and computes authentic E2E breakdown metrics.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestE2ERuntimeAuthenticityE2E(unittest.TestCase):
    """End-to-end tests for E2E runtime authenticity validation in an ephemeral sandbox."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="e2e_authenticity_")
        self.sandbox = Path(self.temp_dir.name)
        (self.sandbox / "tests" / "end_to_end").mkdir(parents=True, exist_ok=True)
        (self.sandbox / "dual_pipeline" / "canonical_pipeline").mkdir(parents=True, exist_ok=True)
        (self.sandbox / "tools").mkdir(parents=True, exist_ok=True)
        (self.sandbox / "reports").mkdir(parents=True, exist_ok=True)

        # Create production modules
        (self.sandbox / "dual_pipeline" / "canonical_pipeline" / "stage_runner.py").write_text(
            "def run_stage(x: int) -> int:\n    return x * 2\n",
            encoding="utf-8",
        )
        (self.sandbox / "tools" / "helper.py").write_text(
            "def format_result(val: int) -> str:\n    return f'val={val}'\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_authentic_runtime_test_passes_linter(self) -> None:
        """Verifies a true runtime E2E test executing production code passes with 0 violations."""
        real_test = self.sandbox / "tests" / "end_to_end" / "test_real_e2e.py"
        real_test.write_text(
            "import unittest\n"
            "from dual_pipeline.canonical_pipeline.stage_runner import run_stage\n"
            "class RealTest(unittest.TestCase):\n"
            "    def test_run(self):\n"
            "        self.assertEqual(run_stage(21), 42)\n",
            encoding="utf-8",
        )

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        violations = audit_test_file(real_test, self.sandbox)
        self.assertEqual(violations, [], f"Expected 0 violations, got {violations}")

    def test_receipt_only_test_is_flagged(self) -> None:
        """Verifies a test reading only reports/*.json is caught with ERR_E2E_RECEIPT_ONLY."""
        receipt_test = self.sandbox / "tests" / "end_to_end" / "test_receipt_e2e.py"
        receipt_test.write_text(
            "import json, unittest\n"
            "from pathlib import Path\n"
            "class ReceiptTest(unittest.TestCase):\n"
            "    def test_receipt(self):\n"
            "        data = json.loads(Path('reports/summary.json').read_text())\n"
            "        self.assertEqual(data['status'], 'PASS')\n",
            encoding="utf-8",
        )

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        violations = audit_test_file(receipt_test, self.sandbox)
        self.assertTrue(
            any(code == "ERR_E2E_RECEIPT_ONLY" for _, code, _ in violations),
            f"Expected ERR_E2E_RECEIPT_ONLY, got {violations}",
        )

    def test_source_grep_test_is_flagged(self) -> None:
        """Verifies a test solely grepping source text is caught with ERR_E2E_SOURCE_GREP_ONLY."""
        grep_test = self.sandbox / "tests" / "end_to_end" / "test_grep_e2e.py"
        grep_test.write_text(
            "import unittest\n"
            "from pathlib import Path\n"
            "class GrepTest(unittest.TestCase):\n"
            "    def test_grep(self):\n"
            "        txt = Path('dual_pipeline/foo.py').read_text()\n"
            "        self.assertIn('class Foo', txt)\n",
            encoding="utf-8",
        )

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        violations = audit_test_file(grep_test, self.sandbox)
        self.assertTrue(
            any(code == "ERR_E2E_SOURCE_GREP_ONLY" for _, code, _ in violations),
            f"Expected ERR_E2E_SOURCE_GREP_ONLY, got {violations}",
        )

    def test_core_mock_test_is_flagged(self) -> None:
        """Verifies a test patching core execution entry points is caught with ERR_E2E_CORE_STUBBED_MOCK."""
        mock_test = self.sandbox / "tests" / "end_to_end" / "test_mock_e2e.py"
        mock_test.write_text(
            "import unittest\n"
            "from unittest.mock import patch\n"
            "from dual_pipeline.canonical_pipeline.stage_runner import run_stage\n"
            "class MockTest(unittest.TestCase):\n"
            "    @patch('run_fast_bootstrap')\n"
            "    def test_stubbed(self, m):\n"
            "        m.return_value = '/tmp/fake'\n"
            "        self.assertEqual(run_stage(1), 2)\n",
            encoding="utf-8",
        )

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        violations = audit_test_file(mock_test, self.sandbox)
        self.assertTrue(
            any(code == "ERR_E2E_CORE_STUBBED_MOCK" for _, code, _ in violations),
            f"Expected ERR_E2E_CORE_STUBBED_MOCK, got {violations}",
        )

    def test_auditor_scan_and_summary_breakdown(self) -> None:
        """Verifies TestingGapAuditor discovers nested production packages and records authentic E2E counts."""
        # Add 1 real runtime E2E test and 1 receipt-only E2E test
        (self.sandbox / "tests" / "end_to_end" / "test_auth_e2e.py").write_text(
            "import unittest\n"
            "from dual_pipeline.canonical_pipeline.stage_runner import run_stage\n"
            "class AuthTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertEqual(run_stage(5), 10)\n",
            encoding="utf-8",
        )
        (self.sandbox / "tests" / "end_to_end" / "test_fake_e2e.py").write_text(
            "import json, unittest\n"
            "from pathlib import Path\n"
            "class FakeTest(unittest.TestCase):\n"
            "    def test_fake(self):\n"
            "        data = json.loads(Path('reports/summary.json').read_text())\n",
            encoding="utf-8",
        )

        from tools.audit_testing_gaps import TestingGapAuditor

        auditor = TestingGapAuditor(repo_root=self.sandbox)
        auditor.scan()
        summary = auditor.compute_overall_summary()

        self.assertGreaterEqual(summary["total_production_modules"], 2)
        self.assertEqual(summary["true_runtime_e2e_count"], 1)
        self.assertEqual(summary["receipt_only_e2e_count"], 1)


if __name__ == "__main__":
    unittest.main()
