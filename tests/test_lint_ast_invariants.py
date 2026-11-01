#!/usr/bin/env python3
"""
Unit tests for tools/lint_ast_invariants.py.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_ast_invariants import scan_file


class TestASTInvariantsLinter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_file(self, rel_path: str, content: str) -> Path:
        file_path = self.root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def test_detects_raw_sqlite_import(self):
        code = (
            "import sqlite3\n"
            "def query():\n"
            "    conn = sqlite3.connect('test.db')\n"
        )
        f = self._create_file("scripts/bad_script.py", code)
        violations = scan_file(f, self.root)
        rules = [v.rule for v in violations]
        self.assertIn("NO_RAW_SQLITE", rules)

    def test_allows_sqlite_in_storage_database(self):
        code = (
            "import sqlite3\n"
            "def query():\n"
            "    conn = sqlite3.connect('test.db')\n"
        )
        f = self._create_file("storage/database.py", code)
        violations = scan_file(f, self.root)
        self.assertEqual(len(violations), 0)

    def test_detects_machine_absolute_path(self):
        code = (
            "DOCS_DIR = '/Users/someone/Documents/APEX_TRANS'\n"
            "def load():\n"
            "    return DOCS_DIR\n"
        )
        f = self._create_file("scripts/path_leak.py", code)
        violations = scan_file(f, self.root)
        rules = [v.rule for v in violations]
        self.assertIn("NO_MACHINE_ABSOLUTE_PATHS", rules)

    def test_detects_oversized_string_literal(self):
        big_str = "x" * 2500
        code = (
            f"TRANSCRIPT_PAYLOAD = '''{big_str}'''\n"
            "def run():\n"
            "    return len(TRANSCRIPT_PAYLOAD)\n"
        )
        f = self._create_file("scripts/huge_payload.py", code)
        violations = scan_file(f, self.root)
        rules = [v.rule for v in violations]
        self.assertIn("DATA_CODE_DECOUPLING", rules)

    def test_detects_oversized_dict_literal(self):
        entries = ", ".join(f"'key_{i}': {i}" for i in range(60))
        code = (
            f"GOLDEN_PROJECTION = {{{entries}}}\n"
            "def get_dict():\n"
            "    return GOLDEN_PROJECTION\n"
        )
        f = self._create_file("scripts/huge_dict.py", code)
        violations = scan_file(f, self.root)
        rules = [v.rule for v in violations]
        self.assertIn("DATA_CODE_DECOUPLING", rules)

    def test_clean_file_passes(self):
        code = (
            "'''Clean script docstring.'''\n"
            "from pathlib import Path\n"
            "from dual_pipeline.manifest import IngestionMeetingManifest\n"
            "def run():\n"
            "    return 42\n"
        )
        f = self._create_file("scripts/clean_script.py", code)
        violations = scan_file(f, self.root)
        self.assertEqual(len(violations), 0)

    def test_detects_sqlite_and_machine_path_inside_dict_and_all_assign(self):
        code = (
            "import sqlite3\n"
            "CFG = {'db': sqlite3.connect('/Users/someone/secret.db')}\n"
            "__all__ = [sqlite3.connect('nested_in_all.db')]\n"
        )
        f = self._create_file("dual_pipeline/bad_dict.py", code)
        violations = scan_file(f, self.root)
        rules = [v.rule for v in violations]
        self.assertGreaterEqual(rules.count("NO_RAW_SQLITE"), 3)
        self.assertIn("NO_MACHINE_ABSOLUTE_PATHS", rules)

    def test_detects_fstring_machine_path_and_oversized_tuple_set(self):
        entries = ", ".join(str(i) for i in range(55))
        code = (
            "USER = 'alice'\n"
            "PATH = f'/Users/{USER}/private/data.db'\n"
            f"BIG_TUPLE = ({entries},)\n"
            f"BIG_SET = {{{entries}}}\n"
        )
        f = self._create_file("dual_pipeline/fstring_and_tuple.py", code)
        violations = scan_file(f, self.root)
        rules = [v.rule for v in violations]
        self.assertIn("NO_MACHINE_ABSOLUTE_PATHS", rules)
        self.assertGreaterEqual(rules.count("DATA_CODE_DECOUPLING"), 2)

    def test_layer_import_from_submodule_caught_in_shift_left(self):
        from tools.shift_left_core.ast_framework import analyze_shift_left_file

        self._create_file("storage/bad_l0.py", "from dual_pipeline import cli\n")
        passed, violations = analyze_shift_left_file(
            self.root, "storage/bad_l0.py", diff_scoped=False, baseline_modules={}
        )
        self.assertFalse(passed)
        self.assertTrue(any("[LAYER VIOLATION]" in v and "cli" in v for v in violations))


if __name__ == "__main__":
    unittest.main()
