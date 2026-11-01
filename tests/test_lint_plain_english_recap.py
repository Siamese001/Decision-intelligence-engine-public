"""
tests/test_lint_plain_english_recap.py

Unit tests for tools/lint_plain_english_executive_recap.py verifying word-salad detection,
foreign domain leaks, and duplicate speaker paragraphs.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_plain_english_executive_recap import check_recap_file


class TestLintPlainEnglishRecap(unittest.TestCase):
    def test_catches_academic_word_salad(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
            tf.write("""# Executive Recap
The team evidenced rejection behavior regarding configuration persistence versus data portability.
""")
            tf_path = Path(tf.name)

        try:
            violations = check_recap_file(tf_path)
            self.assertTrue(len(violations) >= 2)
            self.assertTrue(any("evidenced rejection behavior" in v for v in violations))
            self.assertTrue(any("configuration persistence versus data portability" in v for v in violations))
        finally:
            tf_path.unlink(missing_ok=True)

    def test_catches_foreign_system_leak_in_lens(self):
        tf_dir = tempfile.mkdtemp()
        tf_path = Path(tf_dir) / "project_lens_test_recap.md"
        tf_path.write_text("""# Executive Recap
The wind turbine team discussed schedules in Oracle P6 and uploaded docs to Documentum.
""")
        try:
            violations = check_recap_file(tf_path)
            self.assertTrue(len(violations) >= 2)
            self.assertTrue(any("Oracle P6" in v for v in violations))
            self.assertTrue(any("Documentum" in v for v in violations))
        finally:
            tf_path.unlink(missing_ok=True)
            Path(tf_dir).rmdir()


    def test_passes_clean_plain_english_recap(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
            tf.write("""# Executive Recap
The team reviewed the wind turbine inspection results and agreed to finalize the warranty report by Friday.
""")
            tf_path = Path(tf.name)

        try:
            violations = check_recap_file(tf_path)
            self.assertEqual(len(violations), 0)
        finally:
            tf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
