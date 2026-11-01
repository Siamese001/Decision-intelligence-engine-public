"""Adversarial & Edge-Case Verification for E2E Runtime Authenticity.

Categories Covered:
1. Boundary payload: 0-byte test files, empty test files, tests with no methods.
2. Malformed input: Syntax errors, unparseable ASTs, invalid JSON in disk cache.
3. Concurrency & race: Multi-threaded concurrent auditor scans with shared cache.
4. Invariant bypass attempt: Disguised receipt reader with dynamic string concatenation.
5. Rollback & recovery: Graceful fallback and self-healing when cache file is corrupted.
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestE2ERuntimeAuthenticityAdversarial(unittest.TestCase):
    """Adversarial and boundary stress tests for E2E runtime authenticity validation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="e2e_auth_adv_")
        self.sandbox = Path(self.temp_dir.name)
        (self.sandbox / "tests" / "end_to_end").mkdir(parents=True, exist_ok=True)
        (self.sandbox / "dual_pipeline").mkdir(parents=True, exist_ok=True)
        (self.sandbox / "reports").mkdir(parents=True, exist_ok=True)

        (self.sandbox / "dual_pipeline" / "core_mod.py").write_text(
            "def compute_val(v: int) -> int:\n    return v + 1\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_boundary_zero_byte_and_empty_test_files(self) -> None:
        """Boundary: Verifies 0-byte and empty files do not crash the linter or auditor."""
        zero_file = self.sandbox / "tests" / "end_to_end" / "test_zero_byte_e2e.py"
        zero_file.write_bytes(b"")

        empty_file = self.sandbox / "tests" / "end_to_end" / "test_empty_comment_e2e.py"
        empty_file.write_text("# only comments\n\n", encoding="utf-8")

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        # Zero-byte files have no assertions and no violations
        v_zero = audit_test_file(zero_file, self.sandbox)
        self.assertEqual(v_zero, [])

        v_empty = audit_test_file(empty_file, self.sandbox)
        self.assertEqual(v_empty, [])

    def test_malformed_syntax_error_handling(self) -> None:
        """Malformed input: Syntax errors emit ERR_PARSE_ERROR gracefully without uncaught exceptions."""
        bad_file = self.sandbox / "tests" / "end_to_end" / "test_syntax_error_e2e.py"
        bad_file.write_text("def broken_syntax(:\n", encoding="utf-8")

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        violations = audit_test_file(bad_file, self.sandbox)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][1], "ERR_PARSE_ERROR")

    def test_invariant_bypass_attempt_disguised_receipt_reader(self) -> None:
        """Invariant bypass: Dynamic string concatenation reading reports is caught."""
        disguised_file = self.sandbox / "tests" / "end_to_end" / "test_disguised_receipt_e2e.py"
        disguised_file.write_text(
            "import json, unittest\n"
            "from pathlib import Path\n"
            "class DisguisedTest(unittest.TestCase):\n"
            "    def test_bypass(self):\n"
            "        p = Path('reports/' + 'receipt_data.json')\n"
            "        data = json.loads(p.read_text())\n",
            encoding="utf-8",
        )

        from tools.lint_e2e_runtime_authenticity import audit_test_file

        violations = audit_test_file(disguised_file, self.sandbox)
        self.assertTrue(
            any(code == "ERR_E2E_RECEIPT_ONLY" for _, code, _ in violations),
            f"Expected ERR_E2E_RECEIPT_ONLY for disguised receipt reader, got {violations}",
        )

    def test_concurrency_parallel_auditor_scans(self) -> None:
        """Concurrency: 8 parallel threads executing TestingGapAuditor scans complete without collisions."""
        (self.sandbox / "tests" / "end_to_end" / "test_parallel_e2e.py").write_text(
            "import unittest\n"
            "from dual_pipeline.core_mod import compute_val\n"
            "class ParTest(unittest.TestCase):\n"
            "    def test_val(self):\n"
            "        self.assertEqual(compute_val(10), 11)\n",
            encoding="utf-8",
        )

        from tools.audit_testing_gaps import TestingGapAuditor

        def _run_scan():
            auditor = TestingGapAuditor(repo_root=self.sandbox)
            auditor.scan()
            return auditor.compute_overall_summary()["true_runtime_e2e_count"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_run_scan) for _ in range(8)]
            results = [f.result() for f in futures]

        self.assertEqual(results, [1] * 8, f"Expected all parallel scans to return 1, got {results}")

    def test_rollback_and_recovery_corrupt_cache_recovery(self) -> None:
        """Rollback & Recovery: Corrupt disk cache is self-healed and re-parsed without failing."""
        cache_dir = self.sandbox / ".local"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / "test_file_info_cache.json"
        cache_file.write_text("{this is corrupted invalid json content", encoding="utf-8")

        (self.sandbox / "tests" / "end_to_end" / "test_recovery_e2e.py").write_text(
            "import unittest\n"
            "from dual_pipeline.core_mod import compute_val\n"
            "class RecTest(unittest.TestCase):\n"
            "    def test_rec(self):\n"
            "        self.assertEqual(compute_val(99), 100)\n",
            encoding="utf-8",
        )

        from tools.audit_testing_gaps import TestingGapAuditor

        auditor = TestingGapAuditor(repo_root=self.sandbox)
        # Should not crash on corrupt cache; loads clean
        auditor.scan()
        summary = auditor.compute_overall_summary()
        self.assertEqual(summary["true_runtime_e2e_count"], 1)


if __name__ == "__main__":
    unittest.main()
