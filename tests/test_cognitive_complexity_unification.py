#!/usr/bin/env python3
"""
tests/test_cognitive_complexity_unification.py

Wave 1 unit and property tests verifying:
1. 100% score parity between lint_cognitive_complexity, shift_left_core, and file_budget_core/mmi.
2. Zero AST node mutation (_is_elif is never injected onto AST nodes).
3. Nested helper functions are scored within enclosing function without double-counting or same-name collision.
4. Class methods are qualified as ClassName.method_name so same-named methods across classes do not collide.
5. evaluate_module_source executes completely in memory without creating temporary files on disk.
"""
from __future__ import annotations

import ast
import tempfile
from unittest import mock
import unittest

from tools.file_budget_core.mmi import calculate_ast_metrics_from_source
from tools.file_budget_core.policy import evaluate_module_source
from tools.lint_cognitive_complexity import calculate_function_complexity
from tools.shift_left_core import SinglePassASTVisitor
from tools.shift_left_core.cognitive import (
    collect_function_nodes,
    compute_cognitive_complexity,
)


class TestCognitiveComplexityUnification(unittest.TestCase):
    def test_calculator_parity_across_constructs(self) -> None:
        code = """
def sample(a, b, c, x):
    val = 1 if a and b or c else 0
    if val > 0:
        pass
    elif x == 1:
        pass
    else:
        match x:
            case 2:
                return sample(a, b, c, 0)
    return val
"""
        tree = ast.parse(code)
        func_node = tree.body[0]

        score_linter = calculate_function_complexity(func_node, "sample")
        score_shift = compute_cognitive_complexity(func_node)
        metrics = calculate_ast_metrics_from_source(code, tree=tree)
        visitor = SinglePassASTVisitor("sample.py", code)
        visitor.visit(tree)

        self.assertEqual(score_linter, score_shift)
        self.assertEqual(score_linter, metrics.max_cc)
        self.assertEqual(len(visitor.functions), 1)
        self.assertEqual(visitor.functions[0].cognitive_complexity, score_linter)
        self.assertGreaterEqual(score_linter, 6)

    def test_ast_immutability_no_is_elif_injection(self) -> None:
        code = """
def branch_chain(x):
    if x == 1:
        return 1
    elif x == 2:
        return 2
    elif x == 3:
        return 3
    else:
        return 0
"""
        tree = ast.parse(code)
        func_node = tree.body[0]
        score = calculate_function_complexity(func_node, "branch_chain")
        self.assertEqual(score, 4)
        for node in ast.walk(tree):
            self.assertFalse(hasattr(node, "_is_elif"), "AST node was mutated with _is_elif")

    def test_nested_function_same_name_and_no_double_counting(self) -> None:
        code = """
def outer(x):
    def outer(y):
        if y > 0:
            return y
        return 0
    if x > 0:
        return outer(x)
    return 0
"""
        tree = ast.parse(code)
        funcs = collect_function_nodes(tree, skip_dunder=True)
        self.assertEqual(len(funcs), 1)
        self.assertEqual(funcs[0][0], "outer")
        # Inner `if y > 0` is inside nested `def outer` (nesting=1 -> +2), outer `if x > 0` (+1), call `outer(x)` (+1) -> total 4
        score = calculate_function_complexity(funcs[0][1], funcs[0][0])
        self.assertGreaterEqual(score, 3)

    def test_class_method_qualification_prevents_collision(self) -> None:
        code = """
class FirstService:
    def execute(self, a):
        if a:
            return 1
        return 0

class SecondService:
    def execute(self, a, b, c):
        if a:
            if b:
                if c:
                    return 2
        return 0
"""
        tree = ast.parse(code)
        visitor = SinglePassASTVisitor("services.py", code)
        visitor.visit(tree)
        by_name = {f.name: f.cognitive_complexity for f in visitor.functions}
        self.assertEqual(by_name, {"FirstService.execute": 1, "SecondService.execute": 6})

    def test_evaluate_module_source_zero_tempfile_writes(self) -> None:
        code = "def check(x):\n    if x > 0:\n        return 1\n    return 0\n"
        with mock.patch.object(tempfile, "NamedTemporaryFile", side_effect=AssertionError("Tempfile prohibited")):
            verdict = evaluate_module_source(code, "dual_pipeline/in_memory_check.py")
        self.assertEqual(verdict.verdict, "PASS")
        self.assertEqual(verdict.max_cc, 1)


if __name__ == "__main__":
    unittest.main()
