#!/usr/bin/env python3
"""tests/test_lint_mock_boundaries.py

Unit tests for tools/lint_mock_boundaries.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_mock_boundaries import MockBoundaryVisitor, is_exempt


class TestLintMockBoundaries(unittest.TestCase):
    """Verifies behavior of mock boundary AST visitor and linter."""

    def test_clean_production_code_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("""
def execute_task(model="gpt-5.6-luna", mock: bool = False):
    client, _ = get_openai_client(allow_mock=mock)
    return client
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            import ast
            visitor.visit(ast.parse(temp_path.read_text()))
            self.assertEqual(visitor.violations, [])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_script_hardcoding_allow_mock_true_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("""
client, _ = CredentialManager.get_openai_client(allow_mock=True)
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=True)
            import ast
            visitor.visit(ast.parse(temp_path.read_text()))
            self.assertEqual(len(visitor.violations), 1)
            self.assertIn("Operational script hardcodes `allow_mock=True`", visitor.violations[0][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_function_defaulting_allow_mock_true_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("""
def get_client(allow_mock: bool = True):
    pass
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            import ast
            visitor.visit(ast.parse(temp_path.read_text()))
            self.assertEqual(len(visitor.violations), 1)
            self.assertIn("defaults `allow_mock=True`", visitor.violations[0][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_function_defaulting_mock_or_mock_mode_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("""
def parse_stream(mock: bool = True):
    pass

def init_sandbox(*, mock_mode: bool = True):
    pass

async def async_fetch(use_mock: bool = True):
    pass
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            import ast
            visitor.visit(ast.parse(temp_path.read_text()))
            self.assertEqual(len(visitor.violations), 3)
            self.assertIn("defaults `mock=True`", visitor.violations[0][1])
            self.assertIn("defaults `mock_mode=True`", visitor.violations[1][1])
            self.assertIn("defaults `use_mock=True`", visitor.violations[2][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_call_hardcoding_mock_mode_or_use_mock_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("""
res = run_eval(mock_mode=True)
res2 = fetch(use_mock=True)
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            import ast
            visitor.visit(ast.parse(temp_path.read_text()))
            self.assertEqual(len(visitor.violations), 2)
            self.assertIn("Production module hardcodes `mock_mode=True`", visitor.violations[0][1])
            self.assertIn("Production module hardcodes `use_mock=True`", visitor.violations[1][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_call_hardcoding_mock_aliases_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("""
res1 = run_task(offline_mode=True)
res2 = run_task(fake_mode=1)
res3 = run_task(dummy_mode="true")
res4 = run_task(mock_provider=True)
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            import ast
            visitor.visit(ast.parse(temp_path.read_text()))
            self.assertEqual(len(visitor.violations), 4)
            self.assertIn("hardcodes `offline_mode=True`", visitor.violations[0][1])
            self.assertIn("hardcodes `fake_mode=True`", visitor.violations[1][1])
            self.assertIn("hardcodes `dummy_mode=True`", visitor.violations[2][1])
            self.assertIn("hardcodes `mock_provider=True`", visitor.violations[3][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_audit_file_parse_error_fails_closed(self) -> None:
        from tools.lint_mock_boundaries import audit_file
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w+", delete=False) as f:
            f.write("def invalid_syntax(:\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations = audit_file(temp_path)
            self.assertEqual(len(violations), 1)
            self.assertIn("Parse error", violations[0])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_empty_scope_fails_closed(self) -> None:
        from tools.lint_mock_boundaries import main
        # Passing an explicit non-existent file leads to scanned == 0 and fails closed
        rc = main(["/nonexistent/path/never_found.py"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
