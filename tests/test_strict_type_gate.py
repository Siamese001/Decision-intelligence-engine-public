"""Unit tests for the strict static type annotation gate."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.run_strict_type_gate import (
    DEFAULT_GOVERNED_PACKAGES,
    FileTypeAuditResult,
    audit_file_types,
    build_type_gate_receipt,
    collect_governed_files,
    is_path_in_packages,
)


class StrictTypeGateTests(unittest.TestCase):
    """Test suite verifying AST type annotation scanning and violation detection."""

    def test_audit_file_types_clean(self) -> None:
        """File with complete annotations should have 0 violations."""
        code = '''
def add(a: int, b: int) -> int:
    return a + b

class Greeter:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self, prefix: str = "Hello") -> str:
        return f"{prefix}, {self.name}"
'''
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            f_path = Path(f.name)

        try:
            res = audit_file_types(f_path)
            self.assertTrue(res.is_compliant)
            self.assertEqual(len(res.violations), 0)
            self.assertEqual(res.function_count, 3)
        finally:
            f_path.unlink(missing_ok=True)

    def test_audit_file_types_missing_parameter(self) -> None:
        """Function with missing parameter type annotation should be detected."""
        code = '''
def multiply(x, y: int) -> int:
    return x * y
'''
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            f_path = Path(f.name)

        try:
            res = audit_file_types(f_path)
            self.assertFalse(res.is_compliant)
            self.assertEqual(len(res.violations), 1)
            self.assertEqual(res.violations[0].parameter_name, "x")
            self.assertIn("missing a type annotation", res.violations[0].message)
        finally:
            f_path.unlink(missing_ok=True)

    def test_audit_file_types_missing_return(self) -> None:
        """Function with missing return type annotation should be detected."""
        code = '''
def compute(x: int):
    return x * 2
'''
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            f_path = Path(f.name)

        try:
            res = audit_file_types(f_path)
            self.assertFalse(res.is_compliant)
            self.assertEqual(len(res.violations), 1)
            self.assertIsNone(res.violations[0].parameter_name)
            self.assertIn("missing a return type annotation", res.violations[0].message)
        finally:
            f_path.unlink(missing_ok=True)

    def test_audit_file_types_kwonly_missing(self) -> None:
        """Keyword-only parameter missing annotation should be flagged."""
        code = '''
def execute(*, dry_run=False) -> bool:
    return not dry_run
'''
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            f_path = Path(f.name)

        try:
            res = audit_file_types(f_path)
            self.assertFalse(res.is_compliant)
            self.assertEqual(len(res.violations), 1)
            self.assertEqual(res.violations[0].parameter_name, "dry_run")
        finally:
            f_path.unlink(missing_ok=True)

    def test_audit_file_types_syntax_error(self) -> None:
        """File with invalid syntax should return syntax error violation."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("def broken(x: {\n")
            f_path = Path(f.name)

        try:
            res = audit_file_types(f_path)
            self.assertFalse(res.is_compliant)
            self.assertEqual(len(res.violations), 1)
            self.assertIn("Syntax/Parse error", res.violations[0].message)
        finally:
            f_path.unlink(missing_ok=True)

    def test_is_path_in_packages(self) -> None:
        """is_path_in_packages should correctly identify matching files."""
        root = Path("/repo")
        self.assertTrue(is_path_in_packages(root / "storage" / "db.py", ["storage"], root))
        self.assertTrue(
            is_path_in_packages(
                root / "dual_pipeline" / "telemetry_core" / "log.py",
                ["dual_pipeline/telemetry_core"],
                root,
            )
        )
        self.assertFalse(is_path_in_packages(root / "other" / "file.py", ["storage"], root))

    def test_build_type_gate_receipt(self) -> None:
        """build_type_gate_receipt should emit a deterministic receipt."""
        res = [
            FileTypeAuditResult(
                filepath="storage/test.py",
                function_count=2,
                violations=[],
            )
        ]
        receipt = build_type_gate_receipt(res, ["storage"])
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["wave_id"], "Wave-5")
        self.assertEqual(receipt["metrics"]["violations_count"], 0)
        self.assertIn("canonical_digest", receipt)

    def test_governed_modules_pass_gate(self) -> None:
        """All default governed modules in repository must pass strict type gate."""
        root = Path(__file__).resolve().parents[1]
        files = collect_governed_files(DEFAULT_GOVERNED_PACKAGES, root)
        self.assertGreater(len(files), 10)
        for f in files:
            res = audit_file_types(f)
            self.assertTrue(
                res.is_compliant,
                f"File {f} failed strict type gate: {res.violations}",
            )


if __name__ == "__main__":
    unittest.main()
