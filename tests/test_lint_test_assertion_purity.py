"""
tests/test_lint_test_assertion_purity.py

Unit tests for tools/lint_test_assertion_purity.py verifying that synthetic healer tokens
in test assertions are caught.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_test_assertion_purity import check_test_file


class TestLintTestAssertionPurity(unittest.TestCase):
    def test_catches_synthetic_tokens_in_test(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
import unittest

class MyTestCase(unittest.TestCase):
    def test_assertion(self):
        self.assertIn("ev-910-03", text)
        self.assertEqual(val, "stmt-opus-3616")
""")
            tf_path = Path(tf.name)

        try:
            violations = check_test_file(tf_path)
            self.assertTrue(len(violations) >= 2)
            self.assertTrue(any("ev-910-03" in v for v in violations))
            self.assertTrue(any("stmt-opus-3616" in v for v in violations))
        finally:
            tf_path.unlink(missing_ok=True)

    def test_passes_structural_invariants_in_test(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
import unittest

class MyTestCase(unittest.TestCase):
    def test_assertion(self):
        self.assertTrue(len(signals) > 0)
        self.assertIn("owner", item)
        self.assertRegex(item["date"], r"^\\d{2}/\\d{2}/\\d{4}$")
""")
            tf_path = Path(tf.name)

        try:
            violations = check_test_file(tf_path)
            self.assertEqual(len(violations), 0)
        finally:
            tf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
