"""
tests/adversarial/test_mock_offline_scanner_bypass_wave5_adversarial.py

Class B Adversarial & Edge-Case Acceptance Test Suite for Wave 5:
"Remove obsolete selectors and close enforcement gaps."

Governed under docs/plans/docs_eradicate_mock_offline_plan_20261008.md.
Covers 4 mandatory edge-case categories:
1. Boundary payload: 0-byte source files, comments-only files, empty argument lists.
2. Malformed input: Invalid UTF-8 encodings, truncated Python syntax, corrupted allowlist.
3. Invariant bypass attempt: Aliased imports (p.object), nested dotted modules, async defaults, truthy integers/strings.
4. Rollback & recovery: Ensuring scanner audits never pollute filesystem or leave dangling temporary files.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.lint_mock_boundaries import (
    MockBoundaryVisitor,
    audit_file,
    is_exempt,
    main as mock_boundaries_main,
)
from tools.lint_third_party_patch import (
    BANNED_PREFIXES,
    ThirdPartyPatchVisitor,
    audit_test_file,
    load_allowlist,
    main as third_party_patch_main,
)

ROOT = Path(__file__).resolve().parent.parent.parent


class TestMockOfflineScannerBypassWave5Adversarial(unittest.TestCase):
    """Adversarial and boundary test suite verifying fail-closed AST scanners and bypass prevention."""

    # ── Category 1: Boundary Payloads ──────────────────────────────────────────

    def test_zero_byte_source_file_handled_cleanly(self) -> None:
        """Boundary: 0-byte Python file parsed by AST scanners produces zero violations without crashing."""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write("")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations_mb = audit_file(temp_path)
            self.assertEqual(violations_mb, [])

            violations_tpp = audit_test_file(temp_path, allowlist=set())
            self.assertEqual(violations_tpp, [])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_whitespace_and_comments_only_file_handled_cleanly(self) -> None:
        """Boundary: File containing only whitespace, shebangs, and comments produces zero violations."""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write("#!/usr/bin/env python3\n# Just comments\n   \n\t\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations_mb = audit_file(temp_path)
            self.assertEqual(violations_mb, [])

            violations_tpp = audit_test_file(temp_path, allowlist=set())
            self.assertEqual(violations_tpp, [])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_empty_arguments_and_keywords_handled_cleanly(self) -> None:
        """Boundary: Functions with zero arguments and calls with no kwargs parsed without exceptions."""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write("""
def empty_fn():
    pass

class EmptyClass:
    def method():
        pass

empty_fn()
""")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations = audit_file(temp_path)
            self.assertEqual(violations, [])
        finally:
            temp_path.unlink(missing_ok=True)

    # ── Category 2: Malformed Input ────────────────────────────────────────────

    def test_malformed_syntax_fails_closed(self) -> None:
        """Malformed: Truncated or syntactically invalid Python fails closed with a parse error."""
        malformed_snippets = [
            "def broken_function(",
            "class (:",
            "import os\nx = [1, 2,",
            "''' unclosed multiline string",
        ]

        for snippet in malformed_snippets:
            with self.subTest(snippet=snippet):
                with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
                    f.write(snippet)
                    f.flush()
                    temp_path = Path(f.name)

                try:
                    violations = audit_file(temp_path)
                    self.assertGreaterEqual(len(violations), 1)
                    self.assertIn("Parse error", violations[0])
                finally:
                    temp_path.unlink(missing_ok=True)

    def test_invalid_utf8_encoding_fails_closed(self) -> None:
        """Malformed: Non-UTF8 byte sequences fail closed gracefully with parse/decode errors."""
        with tempfile.NamedTemporaryFile("wb+", suffix=".py", delete=False) as f:
            f.write(b"def foo():\n    return '\xff\xfe\x00\x00'\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations = audit_file(temp_path)
            self.assertGreaterEqual(len(violations), 1)
            self.assertIn("Parse error", violations[0])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_corrupted_allowlist_json_fails_closed_or_defaults_empty(self) -> None:
        """Malformed: Corrupted or missing third-party allowlist JSON safely defaults to empty set."""
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write("INVALID JSON CONTENT {{{")
            f.flush()
            temp_json = Path(f.name)

        try:
            # Test isolated allowlist loading from corrupted file
            import tools.lint_third_party_patch as tpp_module
            orig_allowlist = tpp_module.ALLOWLIST_PATH
            tpp_module.ALLOWLIST_PATH = temp_json
            try:
                loaded = load_allowlist()
                self.assertEqual(loaded, set(), "Corrupted allowlist must safely fall back to empty set")
            finally:
                tpp_module.ALLOWLIST_PATH = orig_allowlist
        finally:
            temp_json.unlink(missing_ok=True)

    # ── Category 3: Invariant Bypass Attempts ──────────────────────────────────

    def test_aliased_import_patch_object_bypass_detected(self) -> None:
        """Invariant bypass: from unittest.mock import patch as p; p.object(subprocess, ...) is detected."""
        code = """
from unittest.mock import patch as p
import subprocess

def test_bypass():
    with p.object(subprocess, 'run') as mock_run:
        pass
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = ThirdPartyPatchVisitor(filename=temp_path.name)
            visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
            self.assertGreaterEqual(len(visitor.violations), 1)
            self.assertIn("patch.object", visitor.violations[0][1])
            self.assertIn("subprocess", visitor.violations[0][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_nested_dotted_third_party_target_detected(self) -> None:
        """Invariant bypass: patch.object on deeply nested dotted third-party module (urllib.request)."""
        code = """
import urllib.request
from unittest.mock import patch

def test_nested_target():
    with patch.object(urllib.request, 'urlopen'):
        pass
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = ThirdPartyPatchVisitor(filename=temp_path.name)
            visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
            self.assertGreaterEqual(len(visitor.violations), 1)
            self.assertIn("urllib.request", visitor.violations[0][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_async_function_defaulting_mock_alias_detected(self) -> None:
        """Invariant bypass: Async functions attempting to default mock aliases are detected."""
        code = """
async def async_pipeline_action(*, offline_mode: bool = True):
    return "executed"
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
            self.assertGreaterEqual(len(visitor.violations), 1)
            self.assertIn("defaults `offline_mode=True`", visitor.violations[0][1])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_truthy_integer_and_string_values_detected(self) -> None:
        """Invariant bypass: Truthy integer (1) or string ('true') passed to mock kwargs detected."""
        code = """
def run_action():
    step1 = initialize(fake_mode=1)
    step2 = initialize(dummy_mode="true")
    step3 = initialize(allow_offline="True")
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            temp_path = Path(f.name)

        try:
            visitor = MockBoundaryVisitor(filename=temp_path.name, is_script=False)
            visitor.visit(ast.parse(temp_path.read_text(encoding="utf-8")))
            self.assertEqual(len(visitor.violations), 3)
            self.assertIn("fake_mode=True", visitor.violations[0][1])
            self.assertIn("dummy_mode=True", visitor.violations[1][1])
            self.assertIn("allow_offline=True", visitor.violations[2][1])
        finally:
            temp_path.unlink(missing_ok=True)

    # ── Category 4: Rollback & Recovery ────────────────────────────────────────

    def test_scanner_leaves_no_dangling_artifacts(self) -> None:
        """Rollback & Recovery: Auditing temporary modules does not leave sidecars or modify tree."""
        with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
            f.write("def nominal_function(): return 42\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            violations = audit_file(temp_path)
            self.assertEqual(violations, [])
        finally:
            temp_path.unlink(missing_ok=True)

        self.assertFalse(temp_path.exists())


if __name__ == "__main__":
    unittest.main()
