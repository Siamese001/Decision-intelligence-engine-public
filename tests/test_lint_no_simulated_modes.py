"""Tests for tools/lint_no_simulated_modes.py linter."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT
from tools.lint_no_simulated_modes import (
    find_forbidden_cli_flags_in_file,
    lint_no_simulated_modes,
    verify_parser_rejection_map,
)


class TestLintNoSimulatedModes(unittest.TestCase):
    """Verifies that the no-simulated-modes linter accurately detects violations and passes on clean tree."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_lint_sim_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_linter_passes_on_current_repository_tree(self) -> None:
        """Verify the current worktree passes zero-simulated-modes lint check with 0 violations."""
        count, findings = lint_no_simulated_modes(ROOT)
        self.assertEqual(
            count,
            0,
            f"Expected zero violations in codebase, found {count}:\n" + "\n".join(findings),
        )

    def test_linter_detects_forbidden_flags_in_synthetic_file(self) -> None:
        """Verify find_forbidden_cli_flags_in_file flags each prohibited simulated CLI option."""
        bad_code = """
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--package-only", action="store_true")
parser.add_argument("--plan-only", action="store_true")
parser.add_argument("--dry-run-graph", action="store_true")
parser.add_argument("--no-mutate-graph", action="store_true")
parser.add_argument("--dry-run-qa", action="store_true")
parser.add_argument("--real-flag", action="store_true")
"""
        bad_file = self.temp_dir / "bad_cli.py"
        bad_file.write_text(bad_code, encoding="utf-8")

        violations = find_forbidden_cli_flags_in_file(bad_file)
        detected_flags = {flag for _, flag, _ in violations}

        expected_flags = {
            "--dry-run",
            "--package-only",
            "--plan-only",
            "--dry-run-graph",
            "--no-mutate-graph",
            "--dry-run-qa",
        }
        self.assertEqual(detected_flags, expected_flags)
        self.assertNotIn("--real-flag", detected_flags)

    def test_parser_rejection_map_verification(self) -> None:
        """Verify parser.py correctly includes all required rejection mappings."""
        parser_path = ROOT / "dual_pipeline" / "cli" / "parser.py"
        errors = verify_parser_rejection_map(parser_path)
        self.assertEqual(errors, [], f"Parser missing required rejections: {errors}")


if __name__ == "__main__":
    unittest.main()
