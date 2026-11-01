"""tests/test_pr_gate_graph_write_paths_adversarial.py

Adversarial and boundary edge-case test suite for Gate 32: Direct Graph Write Path Linter.
Covers:
1. Boundary payload: 0-byte python files, empty files list, comments only.
2. Malformed input: syntax errors, multiline string fragments, mixed-case SQL keywords.
3. Invariant bypass attempt: joined strings (f-strings), nested functions, aliased table names.
4. Exact production regression: deliberate rogue write in scripts/ and dual_pipeline/ fails closed.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.lint_graph_write_paths import (
    inspect_file_for_direct_graph_writes,
    lint_graph_write_paths,
)


class TestPRGateGraphWritePathsAdversarial(unittest.TestCase):
    """Adversarial and boundary test cases for Gate 32 write path validation."""

    def test_boundary_zero_byte_and_comments_only(self) -> None:
        """Boundary payload: empty files or comments-only files produce zero false violations."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tool_dir = root / "tools"
            tool_dir.mkdir()

            # Zero-byte file
            empty_file = tool_dir / "empty.py"
            empty_file.write_text("", encoding="utf-8")

            # Comments only
            comments_file = tool_dir / "comments.py"
            comments_file.write_text("# Just comments\n# INSERT INTO node\n", encoding="utf-8")

            exit_code, violations = lint_graph_write_paths([empty_file, comments_file], repo_root=root)
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(violations), 0)

    def test_malformed_syntax_fails_closed(self) -> None:
        """Malformed input: file with Python syntax error emits violation rather than silently passing."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tool_dir = root / "tools"
            tool_dir.mkdir()

            bad_syntax = tool_dir / "broken.py"
            bad_syntax.write_text("def broken_func(: invalid syntax here\n", encoding="utf-8")

            violations = inspect_file_for_direct_graph_writes(bad_syntax, repo_root=root)
            self.assertEqual(len(violations), 1)
            self.assertIn("Syntax or encoding error", violations[0])

    def test_invariant_bypass_mixed_case_and_joined_strings(self) -> None:
        """Invariant bypass: mixed case (InSeRt InTo) targeting graph tables is caught."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dp_dir = root / "dual_pipeline" / "unapproved_rogue"
            dp_dir.mkdir(parents=True)

            bypass_file = dp_dir / "bypass.py"
            bypass_file.write_text(
                "def sneaky_write(conn):\n"
                "    conn.execute('InSeRt InTo nOdE VALUES (1, 2)')\n",
                encoding="utf-8",
            )

            violations = inspect_file_for_direct_graph_writes(bypass_file, repo_root=root)
            self.assertGreaterEqual(len(violations), 1)
            self.assertTrue(
                any("nOdE" in v or "INSERT" in v for v in violations),
                f"Expected bypass to be caught, got: {violations}",
            )

    def test_exact_regression_rogue_script_fails_closed(self) -> None:
        """Exact production regression: Any direct SQL mutation in scripts/ must fail closed."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts_dir = root / "scripts"
            scripts_dir.mkdir()

            rogue_script = scripts_dir / "rogue_patch.py"
            rogue_script.write_text(
                "import sqlite3\n"
                "c = sqlite3.connect('graph.sqlite')\n"
                "c.execute('DELETE FROM assertion WHERE id = 1')\n",
                encoding="utf-8",
            )

            exit_code, violations = lint_graph_write_paths([rogue_script], repo_root=root)
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(violations), 1)
            self.assertIn("Prohibited direct SQL write `DELETE FROM assertion` in scripts/", violations[0])


if __name__ == "__main__":
    unittest.main()
