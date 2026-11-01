#!/usr/bin/env python3
"""
tests/test_cognitive_complexity_linter.py

Class A unit tests for AST-based Cognitive Complexity Linter and Ratchet Budget Governance.
Enforces that cognitive complexity correctly evaluates breaks in linear flow and nesting depth.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.lint_cognitive_complexity import (
    calculate_function_complexity,
    scan_file,
)
from tools.lint_file_budgets import (
    BASELINE_PATH,
    load_ratchet_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class CognitiveComplexityLinterTests(unittest.TestCase):
    """Verifies Sonar-compliant Cognitive Complexity scoring and CLI behavior."""

    def test_linear_code_zero_complexity(self):
        code = """
def linear_func(x, y):
    a = x + y
    b = a * 2
    print(b)
    return b
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        score = calculate_function_complexity(func_node, "linear_func")
        self.assertEqual(score, 0)

    def test_single_if_scores_one(self):
        code = """
def check_val(x):
    if x > 10:
        return True
    return False
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        score = calculate_function_complexity(func_node, "check_val")
        self.assertEqual(score, 1)

    def test_nested_if_scores_three(self):
        # Outer if: +1
        # Inner if: +1 (base) + 1 (nesting level 1) = +2
        # Total = 3
        code = """
def nested_if(x, y):
    if x > 0:
        if y > 0:
            return True
    return False
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        score = calculate_function_complexity(func_node, "nested_if")
        self.assertEqual(score, 3)

    def test_nested_loop_and_except_penalties(self):
        # for: +1 (depth 0) -> nesting 1
        # while: +1 + 1 (depth 1) = +2 -> nesting 2
        # if: +1 + 2 (depth 2) = +3 -> nesting 3
        # try/except: +1 + 3 (depth 3) = +4
        code = """
def deep_nesting(items):
    for item in items:
        while item.has_next():
            if item.is_valid():
                try:
                    item.process()
                except Exception:
                    pass
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        score = calculate_function_complexity(func_node, "deep_nesting")
        self.assertGreaterEqual(score, 10)

    def test_boolean_operator_sequence(self):
        # if: +1
        # a and b and c: +1 for sequence of 'and'
        # Total = 2
        code = """
def bool_test(a, b, c):
    if a and b and c:
        return True
    return False
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        score = calculate_function_complexity(func_node, "bool_test")
        self.assertEqual(score, 2)

    def test_scan_file_detects_violations(self):
        high_complexity_code = """
def very_complex_func(a, b, c, d, e):
    if a:
        if b:
            if c:
                if d:
                    if e:
                        return 1
                    else:
                        return 2
                return 3
            return 4
        return 5
    return 6
"""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
            tf.write(high_complexity_code)
            tf_path = Path(tf.name)

        try:
            violations = scan_file(tf_path, tf_path.parent, max_complexity=10)
            self.assertEqual(len(violations), 1)
            self.assertGreater(violations[0].score, 10)
            self.assertEqual(violations[0].function_name, "very_complex_func")
        finally:
            tf_path.unlink(missing_ok=True)

    def test_ratchet_baseline_file_exists_and_valid(self):
        """Validates that reports/file_budget_burndown_baseline.json exists and tracks oversized files."""
        self.assertTrue(BASELINE_PATH.is_file(), f"Baseline file {BASELINE_PATH} must exist.")
        data = load_ratchet_baseline(BASELINE_PATH)
        self.assertIn("modules", data)
        self.assertGreater(len(data["modules"]), 50)
        self.assertIn("standard_max_lines", data)
        self.assertEqual(data["standard_max_lines"], 400)

    def test_ratchet_linter_passes_on_baseline(self):
        """Runs tools/lint_file_budgets.py --ratchet and verifies it passes."""
        res = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "lint_file_budgets.py"),
                "--ratchet",
                str(REPO_ROOT / "tools" / "lint_cognitive_complexity.py"),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Ratchet linter failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("PASSED", res.stdout)

    def test_tri_calculator_parity_on_boolop_ternary_and_recursion(self):
        """Verify cognitive.py, lint_cognitive_complexity.py, and mmi.py produce identical scores."""
        from tools.file_budget_core.mmi import calculate_ast_metrics
        from tools.shift_left_core.cognitive import compute_cognitive_complexity

        code = """
def tricky_fn(a, b, c):
    x = 1 if (a and b) or c else 0
    if x > 0:
        return tricky_fn(a - 1, b, c)
    return x
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        s1 = compute_cognitive_complexity(func_node)
        s2 = calculate_function_complexity(func_node, "tricky_fn")
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
            tf.write(code)
            tf_path = Path(tf.name)
        try:
            s3 = calculate_ast_metrics(tf_path).max_cc
        finally:
            tf_path.unlink(missing_ok=True)

        self.assertEqual(s1, s2)
        self.assertEqual(s2, s3)
        self.assertGreaterEqual(s1, 4)


if __name__ == "__main__":
    unittest.main()
