"""
tests/test_module_budget_policy.py

Unit test suite for shared module budget evaluator (Wave 2).
Validates Acceptance Criteria 1 and 2:
- Boundary checks at 399/400/401, 649/650/651, 799/800/801 lines.
- Empty and invalid Python handling (fail-closed ERROR).
- Declarations that acquire branch logic.
- Small but complex modules (structural CC block).
- Path-name neutrality (no filename shortcut grants higher ceiling).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.file_budget_core.policy import (
    CandidatePolicy,
    PolicyVerdict,
    evaluate_module_file,
    evaluate_module_source,
    is_ast_pure_declarative,
    load_candidate_policy,
)


class TestModuleBudgetEvaluator(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_candidate_policy()

    def _generate_code(self, num_lines: int, with_branch: bool = False, complex_func: bool = False) -> str:
        lines = []
        if complex_func:
            lines.extend([
                "def deeply_nested(a, b, c, d, e):\n",
                "    if a:\n",
                "        if b:\n",
                "            if c:\n",
                "                for x in d:\n",
                "                    while x > 0:\n",
                "                        if x % 2 == 0:\n",
                "                            return x\n",
                "    return None\n",
            ])
        elif with_branch:
            lines.extend([
                "def check(x):\n",
                "    if x > 0:\n",
                "        return True\n",
                "    return False\n",
            ])
        else:
            lines.append("DATA = {}\n")

        rem = num_lines - len(lines)
        if rem > 0:
            lines.extend([f"VAR_{i} = {i}\n" for i in range(rem)])
        return "".join(lines[:num_lines])

    def test_empty_python_source(self) -> None:
        verdict = evaluate_module_source("", "dual_pipeline/empty.py", policy=self.policy)
        self.assertEqual(verdict.verdict, "PASS")
        self.assertFalse(verdict.is_blocking)
        self.assertEqual(verdict.lines, 0)

    def test_invalid_python_syntax_fails_closed(self) -> None:
        bad_code = "def syntax_error(\n    print('unclosed'"
        verdict = evaluate_module_source(bad_code, "dual_pipeline/bad.py", policy=self.policy)
        self.assertEqual(verdict.verdict, "ERROR")
        self.assertTrue(verdict.is_blocking)
        self.assertEqual(verdict.severity, "CRITICAL")
        self.assertIn("Parsing error", verdict.reason)

    def test_clean_boundary_399_and_400(self) -> None:
        code_399 = self._generate_code(399)
        v_399 = evaluate_module_source(code_399, "dual_pipeline/test_399.py", policy=self.policy)
        self.assertEqual(v_399.verdict, "PASS")
        self.assertFalse(v_399.is_blocking)
        self.assertFalse(v_399.warning)

        code_400 = self._generate_code(400)
        v_400 = evaluate_module_source(code_400, "dual_pipeline/test_400.py", policy=self.policy)
        self.assertEqual(v_400.verdict, "PASS")
        self.assertFalse(v_400.is_blocking)
        self.assertFalse(v_400.warning)

    def test_warning_boundary_401(self) -> None:
        code_401 = self._generate_code(401, with_branch=True)
        v_401 = evaluate_module_source(code_401, "dual_pipeline/test_401.py", policy=self.policy)
        self.assertEqual(v_401.verdict, "WARNING")
        self.assertFalse(v_401.is_blocking)
        self.assertTrue(v_401.warning)
        self.assertEqual(v_401.severity, "LOW")

    def test_standard_warning_ceiling_649_and_650(self) -> None:
        code_649 = self._generate_code(649, with_branch=True)
        v_649 = evaluate_module_source(code_649, "dual_pipeline/test_649.py", policy=self.policy)
        self.assertEqual(v_649.verdict, "WARNING")
        self.assertFalse(v_649.is_blocking)

        code_650 = self._generate_code(650, with_branch=True)
        v_650 = evaluate_module_source(code_650, "dual_pipeline/test_650.py", policy=self.policy)
        self.assertEqual(v_650.verdict, "WARNING")
        self.assertFalse(v_650.is_blocking)

    def test_declarative_vs_ordinary_boundary_651(self) -> None:
        # 651 lines pure declarative -> WARNING
        code_decl = self._generate_code(651, with_branch=False)
        v_decl = evaluate_module_source(code_decl, "dual_pipeline/table_651.py", policy=self.policy)
        self.assertEqual(v_decl.verdict, "WARNING")
        self.assertFalse(v_decl.is_blocking)
        self.assertEqual(v_decl.severity, "MEDIUM")

        # 651 lines with branch logic -> BLOCK
        code_ord = self._generate_code(651, with_branch=True)
        v_ord = evaluate_module_source(code_ord, "dual_pipeline/ord_651.py", policy=self.policy)
        self.assertEqual(v_ord.verdict, "BLOCK")
        self.assertTrue(v_ord.is_blocking)
        self.assertIn("exceeds 650-line ordinary ceiling", v_ord.reason)

    def test_declarative_ceiling_799_and_800(self) -> None:
        code_799 = self._generate_code(799, with_branch=False)
        v_799 = evaluate_module_source(code_799, "dual_pipeline/decl_799.py", policy=self.policy)
        self.assertEqual(v_799.verdict, "WARNING")
        self.assertFalse(v_799.is_blocking)

        code_800 = self._generate_code(800, with_branch=False)
        v_800 = evaluate_module_source(code_800, "dual_pipeline/decl_800.py", policy=self.policy)
        self.assertEqual(v_800.verdict, "WARNING")
        self.assertFalse(v_800.is_blocking)

    def test_above_declarative_ceiling_801(self) -> None:
        code_801 = self._generate_code(801, with_branch=False)
        v_801 = evaluate_module_source(code_801, "dual_pipeline/decl_801.py", policy=self.policy)
        self.assertEqual(v_801.verdict, "BLOCK")
        self.assertTrue(v_801.is_blocking)
        self.assertIn("exceeds maximum unexempted warning ceiling", v_801.reason)

    def test_small_module_with_high_cognitive_complexity_blocked(self) -> None:
        """Structural check: a small file (50 lines) with CC > 15 must be BLOCKED."""
        code_complex = self._generate_code(50, complex_func=True)
        verdict = evaluate_module_source(code_complex, "dual_pipeline/tiny_complex.py", policy=self.policy)
        self.assertEqual(verdict.verdict, "BLOCK")
        self.assertTrue(verdict.is_blocking)
        self.assertEqual(verdict.severity, "CRITICAL")
        self.assertIn("Cognitive complexity violation", verdict.reason)

    def test_no_pathname_shortcut_for_declarative_ceiling(self) -> None:
        """Naming a file 'models.py' does NOT grant 800 ceiling if it contains branch logic."""
        code_with_branch = self._generate_code(700, with_branch=True)
        verdict = evaluate_module_source(code_with_branch, "dual_pipeline/models.py", policy=self.policy)
        self.assertEqual(verdict.verdict, "BLOCK")
        self.assertTrue(verdict.is_blocking)

    def test_declarations_that_acquire_branch_logic_transition_to_block(self) -> None:
        """A 700-line declarative file that acquires branch logic transitions from WARNING to BLOCK."""
        pure_decl = self._generate_code(700, with_branch=False)
        v1 = evaluate_module_source(pure_decl, "dual_pipeline/lookup.py", policy=self.policy)
        self.assertEqual(v1.verdict, "WARNING")
        self.assertFalse(v1.is_blocking)

        branch_decl = pure_decl + "\ndef lookup(k):\n    if k in DATA: return 1\n"
        v2 = evaluate_module_source(branch_decl, "dual_pipeline/lookup.py", policy=self.policy)
        self.assertEqual(v2.verdict, "BLOCK")
        self.assertTrue(v2.is_blocking)

    def test_file_evaluation_parity_with_source_evaluation(self) -> None:
        """Disk file evaluation yields identical verdict to source evaluation."""
        code = self._generate_code(450, with_branch=True)
        with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as tf:
            tf.write(code)
            tmp_path = Path(tf.name)

        try:
            v_source = evaluate_module_source(code, "test.py", policy=self.policy)
            v_file = evaluate_module_file(tmp_path, repo_root=tmp_path.parent, policy=self.policy, use_exclusions=False, use_baseline=False)
            self.assertEqual(v_source.verdict, v_file.verdict)
            self.assertEqual(v_source.lines, v_file.lines)
            self.assertEqual(v_source.is_blocking, v_file.is_blocking)
            self.assertEqual(v_source.max_cc, v_file.max_cc)
        finally:
            tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
