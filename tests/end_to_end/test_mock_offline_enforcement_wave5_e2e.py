"""
tests/end_to_end/test_mock_offline_enforcement_wave5_e2e.py

Class B E2E Acceptance Test Suite for Wave 5:
"Remove obsolete selectors and close enforcement gaps."

Governed under docs/plans/docs_eradicate_mock_offline_plan_20261008.md.
Enforces:
1. Scanner detects mock aliases (offline_mode, fake_mode, dummy_mode, mock_provider, etc.).
2. Scanner detects patch.object, setattr, and monkeypatch on third-party libraries.
3. Fail-closed on unexpected empty scope and AST parse errors.
4. Absence of whole-file allowlists in production mock boundaries.
5. Integration with readiness and commit verification tooling.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.lint_mock_boundaries import (
    BANNED_MOCK_KWARGS,
    EXEMPT_FILES,
    MockBoundaryVisitor,
    audit_file,
)
from tools.lint_third_party_patch import (
    BANNED_PREFIXES,
    ThirdPartyPatchVisitor,
    audit_test_file,
)

ROOT = Path(__file__).resolve().parent.parent.parent


class TestMockOfflineEnforcementWave5E2E(unittest.TestCase):
    """E2E suite verifying full enforcement of mock boundaries and patch bans."""

    def test_full_repository_mock_boundary_linter_passes(self) -> None:
        """Verify that the repository-wide mock boundary linter runs and passes with zero violations."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_mock_boundaries.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertIn("Mock boundary check passed", proc.stdout)

    def test_full_repository_third_party_patch_linter_passes(self) -> None:
        """Verify that the third-party patch linter runs across all test files and passes."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_third_party_patch.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertIn("Third-Party Patch Linter PASS", proc.stdout)

    def test_banned_mock_aliases_detected_in_production_scope(self) -> None:
        """Verify that newly expanded mock aliases are detected as violations in production code."""
        aliases_to_test = [
            ("allow_mock", "True"),
            ("mock", "True"),
            ("mock_mode", "True"),
            ("use_mock", "True"),
            ("offline_mode", "True"),
            ("fake_mode", "1"),
            ("dummy_mode", '"true"'),
            ("mock_provider", "True"),
            ("use_fake", "True"),
            ("allow_fake", "True"),
            ("allow_offline", "True"),
        ]

        for kwarg, val_repr in aliases_to_test:
            with self.subTest(kwarg=kwarg, val=val_repr):
                with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
                    f.write(f"""
def production_action():
    return execute_client({kwarg}={val_repr})
""")
                    f.flush()
                    temp_path = Path(f.name)

                try:
                    visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
                    visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
                    self.assertGreaterEqual(
                        len(visitor.violations), 1,
                        f"Expected violation for keyword {kwarg}={val_repr}"
                    )
                    self.assertIn(f"hardcodes `{kwarg}=True`", visitor.violations[0][1])
                finally:
                    temp_path.unlink(missing_ok=True)

    def test_patch_object_on_third_party_detected_in_test_scope(self) -> None:
        """Verify that patch.object on banned third-party libraries is flagged in test code."""
        code_samples = [
            "from unittest import mock; mock.patch.object(subprocess, 'run')",
            "from unittest.mock import patch; patch.object(subprocess, 'Popen')",
            "from unittest.mock import patch; patch.object(requests, 'get')",
            "from unittest.mock import patch; patch.object(urllib.request, 'urlopen')",
            "from unittest.mock import patch; patch.object(httpx, 'Client')",
        ]

        for code in code_samples:
            with self.subTest(code=code):
                with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
                    f.write(f"def test_example():\n    {code}\n")
                    f.flush()
                    temp_path = Path(f.name)

                try:
                    visitor = ThirdPartyPatchVisitor(filename=temp_path.name)
                    visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
                    self.assertGreaterEqual(len(visitor.violations), 1)
                    self.assertIn("patch.object", visitor.violations[0][1])
                finally:
                    temp_path.unlink(missing_ok=True)

    def test_setattr_and_monkeypatch_on_third_party_detected(self) -> None:
        """Verify that direct setattr and monkeypatch.setattr on third-party modules are flagged."""
        code_samples = [
            "setattr(subprocess, 'run', lambda *a, **k: None)",
            "monkeypatch.setattr(subprocess, 'run', lambda *a, **k: None)",
            "monkeypatch.setattr(requests, 'post', lambda *a, **k: None)",
            "monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: None)",
        ]

        for code in code_samples:
            with self.subTest(code=code):
                with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
                    f.write(f"def test_foo(monkeypatch):\n    {code}\n")
                    f.flush()
                    temp_path = Path(f.name)

                try:
                    visitor = ThirdPartyPatchVisitor(filename=temp_path.name)
                    visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
                    self.assertGreaterEqual(len(visitor.violations), 1)
                finally:
                    temp_path.unlink(missing_ok=True)

    def test_fail_closed_on_empty_scope(self) -> None:
        """Verify that linters fail closed with non-zero exit code if target files resolve to 0."""
        # 1. lint_mock_boundaries
        proc_mb = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_mock_boundaries.py"), "/tmp/nonexistent_mock_file_12345.py"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_mb.returncode, 1)
        self.assertIn("Unexpected empty scope", proc_mb.stderr)

        # 2. lint_third_party_patch
        proc_tpp = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_third_party_patch.py"), "/tmp/nonexistent_patch_file_12345.py"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_tpp.returncode, 1)
        self.assertIn("Unexpected empty scope", proc_tpp.stderr)

    def test_fail_closed_on_syntax_and_parse_errors(self) -> None:
        """Verify that malformed Python syntax fails closed with a parse error violation."""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write("def broken_syntax(x, y:\n    return x + y\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations_mb = audit_file(temp_path)
            self.assertGreaterEqual(len(violations_mb), 1)
            self.assertIn("Parse error", violations_mb[0])

            violations_tpp = audit_test_file(temp_path, allowlist=set())
            self.assertGreaterEqual(len(violations_tpp), 1)
            self.assertIn("Parse error", violations_tpp[0])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_absence_of_whole_file_allowlists_in_mock_boundaries(self) -> None:
        """Verify that EXEMPT_FILES in tools/lint_mock_boundaries.py is empty (no whole-file exemptions)."""
        self.assertEqual(
            len(EXEMPT_FILES), 0,
            f"EXEMPT_FILES must be empty but contains: {EXEMPT_FILES}"
        )

    def test_linter_enrolled_in_readiness_and_commit_verification(self) -> None:
        """Verify that lint_mock_boundaries.py is wired into verify_commit and readiness runners."""
        categories_py = (ROOT / "tools" / "verify_commit_core" / "categories.py").read_text(encoding="utf-8")
        self.assertIn("lint_mock_boundaries.py", categories_py)

        readiness_py = (ROOT / "tools" / "readiness_core.py").read_text(encoding="utf-8")
        self.assertIn("lint_mock_boundaries.py", readiness_py)
        self.assertIn("tests.test_lint_mock_boundaries", readiness_py)


if __name__ == "__main__":
    unittest.main()
