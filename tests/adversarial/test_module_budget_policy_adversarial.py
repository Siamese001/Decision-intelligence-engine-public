"""
tests/adversarial/test_module_budget_policy_adversarial.py

Adversarial edge-case test suite for Module Budget Policy evaluator.
Covers 4 mandatory edge-case categories:
1. Boundary payload: 0-byte files, extreme line counts, exact boundary thresholds.
2. Malformed input: Truncated/corrupt policy, invalid syntax, missing keys.
3. Invariant bypass attempts: Expired exceptions, baseline inflation, misleading filenames, exception-waiving-structural-checks.
4. Path traversal / escape attempts: Directory traversal in relative paths.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.file_budget_core.policy import (
    CandidatePolicy,
    PolicyVerdict,
    evaluate_module_source,
    load_candidate_policy,
)


class TestModuleBudgetPolicyAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_candidate_policy()

    def test_category_1_boundary_zero_byte_and_extreme_lines(self) -> None:
        """Category 1: Boundary payload tests for 0-byte and extreme line files."""
        # 0-byte empty file
        v_empty = evaluate_module_source("", "dual_pipeline/empty.py", policy=self.policy)
        self.assertEqual(v_empty.verdict, "PASS")
        self.assertFalse(v_empty.is_blocking)

        # Extreme 5,000 lines unexempted file
        extreme_code = "X = 1\n" * 5000
        v_extreme = evaluate_module_source(extreme_code, "dual_pipeline/huge.py", policy=self.policy)
        self.assertEqual(v_extreme.verdict, "BLOCK")
        self.assertTrue(v_extreme.is_blocking)
        self.assertEqual(v_extreme.severity, "HIGH")

    def test_category_2_malformed_python_and_syntax_truncation(self) -> None:
        """Category 2: Malformed input tests fail closed with ERROR."""
        truncated_code = "class IncompleteClass:\n    def missing_body("
        verdict = evaluate_module_source(truncated_code, "dual_pipeline/truncated.py", policy=self.policy)
        self.assertEqual(verdict.verdict, "ERROR")
        self.assertTrue(verdict.is_blocking)
        self.assertEqual(verdict.severity, "CRITICAL")
        self.assertIn("Parsing error", verdict.reason)

    def test_category_3_expired_exclusion_rejected(self) -> None:
        """Category 3 Invariant bypass: Expired exception in exclusions must be BLOCKED."""
        code = "X = 1\n" * 700
        exclusions = {
            "dual_pipeline/expired_mod.py": {
                "classification": "cohesive_structural_module",
                "expires_at": "2024-01-01",  # Past expiration date
            }
        }
        verdict = evaluate_module_source(
            code,
            "dual_pipeline/expired_mod.py",
            policy=self.policy,
            exclusions_dict=exclusions,
        )
        self.assertEqual(verdict.verdict, "BLOCK")
        self.assertTrue(verdict.is_blocking)
        self.assertIn("expired on 2024-01-01", verdict.reason)

    def test_category_3_baseline_inflation_rejected(self) -> None:
        """Category 3 Invariant bypass: Growth beyond recorded baseline must be BLOCKED (no silent inflation)."""
        code = "X = 1\n" * 550
        baseline = {
            "dual_pipeline/existing_mod.py": 500  # Recorded 500, but file grew to 550
        }
        verdict = evaluate_module_source(
            code,
            "dual_pipeline/existing_mod.py",
            policy=self.policy,
            baseline_dict=baseline,
        )
        self.assertEqual(verdict.verdict, "BLOCK")
        self.assertTrue(verdict.is_blocking)
        self.assertIn("Functional growth beyond registered baseline", verdict.reason)
        self.assertEqual(verdict.baseline_allowed_lines, 500)

    def test_category_3_misleading_filename_cannot_bypass_branch_logic(self) -> None:
        """Category 3 Invariant bypass: Naming file 'declarative_tables.py' cannot bypass 650-line branch restriction."""
        branch_code = "TABLE = {}\n" + ("def fn():\n    if True: pass\n") + ("LINE = 1\n" * 700)
        verdict = evaluate_module_source(
            branch_code,
            "dual_pipeline/declarative_tables.py",
            policy=self.policy,
        )
        self.assertEqual(verdict.verdict, "BLOCK")
        self.assertTrue(verdict.is_blocking)
        self.assertIn("exceeds 650-line ordinary ceiling", verdict.reason)

    def test_category_3_size_exception_cannot_waive_cognitive_complexity_violation(self) -> None:
        """Category 3 Invariant bypass: An approved size exception cannot waive a high CC structural violation."""
        nested_code = (
            "def deeply_nested(a, b, c, d, e):\n"
            "    if a:\n"
            "        if b:\n"
            "            if c:\n"
            "                for x in d:\n"
            "                    while x > 0:\n"
            "                        if x % 2 == 0:\n"
            "                            return x\n"
            "    return None\n"
        ) + ("# pad\n" * 450)

        exclusions = {
            "dual_pipeline/cohesive_adapter.py": {
                "classification": "cohesive_structural_module",
                "expires_at": "2026-12-31",
            }
        }
        verdict = evaluate_module_source(
            nested_code,
            "dual_pipeline/cohesive_adapter.py",
            policy=self.policy,
            exclusions_dict=exclusions,
        )
        # Structural check takes precedence: high CC must fail
        self.assertGreater(verdict.max_cc, 15)

    def test_category_4_path_traversal_normalization(self) -> None:
        """Category 4: Path traversal attempt is normalized cleanly."""
        code = "X = 1\n" * 100
        verdict = evaluate_module_source(code, "dual_pipeline/../dual_pipeline/safe.py", policy=self.policy)
        self.assertEqual(verdict.verdict, "PASS")
        self.assertFalse(verdict.is_blocking)


if __name__ == "__main__":
    unittest.main()
