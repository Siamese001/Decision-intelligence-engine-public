"""
tests/test_lint_no_custom_ingest_scripts.py

Unit test suite for tools/lint_no_custom_ingest_scripts.py.
Verifies that:
1. Custom ingestion scripts in scripts/ are detected and blocked.
2. Invariant 10 bans scripts/build_and_ingest_*.py.
3. Clean modules and CLI commands pass without violations.
4. The linter integrates with --staged and --check flags.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_no_custom_ingest_scripts import (
    STRICTLY_BANNED_FILES,
    inspect_ast_for_pipeline_instantiation,
    lint_file,
    run_linter,
)


class LintNoCustomIngestScriptsTests(unittest.TestCase):
    def test_strictly_banned_files_detected(self) -> None:
        """Verify that strictly banned files trigger immediate blocking violations."""
        for banned in STRICTLY_BANNED_FILES:
            errs = lint_file(banned, Path("/fake/root"), is_staged=False)
            self.assertTrue(len(errs) > 0, f"Expected violation for banned file {banned}")
            self.assertIn("Strictly prohibited", errs[0])

    def test_new_build_and_ingest_script_blocked(self) -> None:
        """Verify that newly authored build_and_ingest_* scripts are blocked."""
        errs = lint_file("scripts/build_and_ingest_new_custom_meeting.py", Path("/fake/root"), is_staged=False)
        self.assertTrue(len(errs) > 0)
        self.assertIn("Unauthorized new ingestion script", errs[0])

    def test_staged_prohibits_all_custom_ingestion_scripts(self) -> None:
        """In staged mode, any build_and_ingest script modification fails."""
        errs = lint_file("scripts/build_and_ingest_any_meeting.py", Path("/fake/root"), is_staged=True)
        self.assertTrue(len(errs) > 0)
        self.assertIn("Prohibited ingestion script in staged changes", errs[0])

    def test_ast_detects_raw_transcript_pipeline_instantiation(self) -> None:
        """Verify that AST inspection catches direct RawTranscriptPipeline instantiation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            f = Path(tmp_dir) / "custom_runner.py"
            f.write_text(
                "from dual_pipeline.transcript_pipeline import RawTranscriptPipeline\n"
                "p = RawTranscriptPipeline()\n"
                "p.run('file.txt', {})\n",
                encoding="utf-8",
            )
            violations = inspect_ast_for_pipeline_instantiation(f)
            self.assertTrue(len(violations) > 0)
            self.assertIn("RawTranscriptPipeline", violations[0])

    def test_clean_utility_script_passes(self) -> None:
        """Verify that valid utility scripts (without pipeline instantiations) pass."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            f = Path(tmp_dir) / "valid_tool.py"
            f.write_text(
                "import sys\n"
                "print('Hello world')\n",
                encoding="utf-8",
            )
            violations = inspect_ast_for_pipeline_instantiation(f)
            self.assertEqual(len(violations), 0)

    def test_run_linter_with_explicit_clean_files(self) -> None:
        """Verify run_linter returns exit code 0 when given clean files."""
        exit_code, violations = run_linter(
            repo_root=Path("/fake/root"),
            staged_only=False,
            files=["dual_pipeline/cli/handlers.py", "tools/lint_production_purity.py"],
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
