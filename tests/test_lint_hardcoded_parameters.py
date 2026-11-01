#!/usr/bin/env python3
"""Unit test suite for tools.lint_hardcoded_parameters."""
import tempfile
import unittest
from pathlib import Path

from tools.lint_hardcoded_parameters import scan_file


class TestLintHardcodedParameters(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detects_prohibited_model_string(self):
        bad_file = self.root / "bad_agent.py"
        bad_file.write_text(
            'def get_card():\n'
            '    return {"model": "gpt-5.6-luna"}\n',
            encoding="utf-8",
        )
        violations = scan_file(bad_file, self.root)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "HARDCODED_MODEL_STRING")
        self.assertIn("gpt-5.6-luna", violations[0].detail)

    def test_ignores_docstring_mentions(self):
        clean_file = self.root / "doc_agent.py"
        clean_file.write_text(
            '"""This agent was historically tested with gpt-5.6-luna."""\n'
            'def get_card():\n'
            '    return {"model_tier": "DIALECTICAL_JUDGE"}\n',
            encoding="utf-8",
        )
        violations = scan_file(clean_file, self.root)
        self.assertEqual(len(violations), 0)

    def test_respects_noqa_comment(self):
        noqa_file = self.root / "noqa_agent.py"
        noqa_file.write_text(
            'def get_card():\n'
            '    return {"model": "gpt-5.6-luna"}  # noqa: model-string-allowed\n',
            encoding="utf-8",
        )
        violations = scan_file(noqa_file, self.root)
        self.assertEqual(len(violations), 0)


    def test_scripts_directory_is_scanned(self):
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir()
        bad_script = scripts_dir / "bad_runner.py"
        bad_script.write_text(
            'def run():\n'
            '    return "gpt-5.6-luna"\n',
            encoding="utf-8",
        )
        violations = scan_file(bad_script, self.root)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "HARDCODED_MODEL_STRING")
        self.assertIn("gpt-5.6-luna", violations[0].detail)


if __name__ == "__main__":
    unittest.main()
