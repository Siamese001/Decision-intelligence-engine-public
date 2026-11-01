#!/usr/bin/env python3
"""
tests/test_lint_production_purity.py

Unit and integration tests for tools/lint_production_purity.py.
Validates:
1. Detection of hardcoded default write destinations to output/ in code.
2. Detection of prohibited synthetic fixture markers in output/ files.
3. Detection of forbidden synthetic meeting directories like output/project_nexus/09-12-2026.
4. Clean pass when all code writes to tempdir and output contains authentic meetings.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.lint_production_purity import (
    lint_code_for_production_writes,
    lint_output_directory_purity,
)


class TestLintProductionPurity(unittest.TestCase):
    """Verifies that lint_production_purity detects violations and passes clean repositories."""

    def setUp(self) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="test_purity_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_detects_hardcoded_output_dir_default_in_evals(self) -> None:
        """Flags python files in evals/ defaulting output_dir to output/."""
        evals_dir = self.temp_root / "evals"
        evals_dir.mkdir(parents=True)
        bad_file = evals_dir / "bad_evaluator.py"
        bad_file.write_text(
            'self.output_dir = output_dir or (ROOT / "output" / "project_nexus" / "09-12-2026")\n',
            encoding="utf-8",
        )

        violations = lint_code_for_production_writes(self.temp_root)
        self.assertTrue(len(violations) >= 1)
        self.assertTrue(any("bad_evaluator.py" in v for v in violations))

    def test_detects_forbidden_09122026_directory(self) -> None:
        """Flags presence of prohibited output/project_nexus/09-12-2026 directory."""
        nexus_dir = self.temp_root / "output" / "project_nexus" / "09-12-2026"
        nexus_dir.mkdir(parents=True)
        (nexus_dir / "01_test.md").write_text("sample", encoding="utf-8")

        violations = lint_output_directory_purity(self.temp_root)
        self.assertTrue(any("project_nexus/09-12-2026" in v for v in violations))

    def test_detects_synthetic_persona_markers_in_output(self) -> None:
        """Flags synthetic persona markers like Alex Rivera in output artifacts."""
        meeting_dir = self.temp_root / "output" / "project_nexus" / "08-28-2026"
        meeting_dir.mkdir(parents=True)
        bad_artifact = meeting_dir / "02_recap.md"
        bad_artifact.write_text("Attendees: Alex Rivera and Jordan Taylor", encoding="utf-8")

        violations = lint_output_directory_purity(self.temp_root)
        self.assertTrue(any("Alex Rivera" in v for v in violations))
        self.assertTrue(any("Jordan Taylor" in v for v in violations))

    def test_detects_prohibited_outputs_duplicate_directory(self) -> None:
        """Flags presence of prohibited legacy duplicate 'outputs' directory or symlink."""
        outputs_dir = self.temp_root / "outputs"
        outputs_dir.mkdir(parents=True)

        violations = lint_output_directory_purity(self.temp_root)
        self.assertTrue(any("outputs" in v and "Only canonical 'output/' is permitted" in v for v in violations))

    def test_clean_repository_passes_with_zero_violations(self) -> None:
        """Passes cleanly when evals use tempdir and output has no synthetic markers."""
        evals_dir = self.temp_root / "evals"
        evals_dir.mkdir(parents=True)
        good_file = evals_dir / "good_evaluator.py"
        good_file.write_text(
            'self.output_dir = output_dir or Path(tempfile.mkdtemp())\n',
            encoding="utf-8",
        )

        meeting_dir = self.temp_root / "output" / "project_nexus" / "08-28-2026"
        meeting_dir.mkdir(parents=True)
        good_artifact = meeting_dir / "02_recap.md"
        good_artifact.write_text("Attendees: Kannan and Amit Ayer", encoding="utf-8")

        code_v = lint_code_for_production_writes(self.temp_root)
        out_v = lint_output_directory_purity(self.temp_root)
        self.assertEqual(code_v, [])
        self.assertEqual(out_v, [])


if __name__ == "__main__":
    unittest.main()
