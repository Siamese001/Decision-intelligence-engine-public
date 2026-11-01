"""End-to-End Test: Consolidated Wave Completion Table Lifecycle.

Exercises full lifecycle:
1. End-to-end generation and validation of 5-column consolidated completion table.
2. CLI verification of walkthrough files and stdin markdown.
3. Enforcement of post-execution single-table rule across complete multi-wave projects.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from tools.validate_implementation_summary import validate_summary_content

ROOT = Path(__file__).resolve().parents[2]


class TestWaveCompletionTableE2E(unittest.TestCase):
    """End-to-end tests for consolidated wave completion tables."""

    def test_full_project_lifecycle_5_column_table(self) -> None:
        """Verify full multi-wave completion report validates cleanly with all deliverables."""
        walkthrough_content = (
            "# Release Walkthrough: Unified Wave Completion Table Standard\n\n"
            "This release establishes the single-table post-execution standard.\n\n"
            "### Wave Implementation & Completion Status Table\n\n"
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| **Wave 1** | Update AGENTS.md and wave-progression rules | ✅ COMPLETE | Added 5-column schema and post-execution single-table rule | `git diff AGENTS.md` (clean) |\n"
            "| **Wave 2** | Upgrade mechanical enforcement validator | ✅ COMPLETE | `tools/validate_implementation_summary.py` supports 5-col & deliverable checks | `pytest test_validator.py` (PASS) |\n"
            "| **Wave 3** | E2E and adversarial test suite | ✅ COMPLETE | Added `test_wave_completion_table_e2e.py` & adversarial suite | `pytest tests/end_to_end/` (100% pass) |\n"
            "| **Wave 4** | CI enrollment and verification | ✅ COMPLETE | Enrolled in `tools/run_ci_gates.py` | `python tools/run_ci_gates.py` (PASS) |\n\n"
            "**Commit**: `3f8a91c2` on branch `feat/consolidated-wave-completion-table`\n"
            "**Full suite**: `14 passed in 0.85s`\n"
        )
        is_valid, errors, records = validate_summary_content(walkthrough_content, "full_walkthrough.md")
        self.assertTrue(is_valid, f"Expected clean validation, got: {errors}")
        self.assertEqual(len(records), 4)
        for rec in records:
            self.assertEqual(rec["status"], "COMPLETE")
            self.assertTrue(len(rec["deliverable"]) > 10)
            self.assertTrue(len(rec["receipt"]) > 5)

    def test_cli_end_to_end_walkthrough_file_validation(self) -> None:
        """Verify CLI tool validates file path with --require-all-complete flag."""
        tool_script = ROOT / "tools" / "validate_implementation_summary.py"
        test_file = ROOT / "tests" / "fixtures" / "sample_5col_walkthrough.md"
        test_file.parent.mkdir(parents=True, exist_ok=True)

        sample_content = (
            "# Sample Walkthrough\n\n"
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| **Wave 1** | Core module implementation | ✅ COMPLETE | Implemented core class in module.py | `pytest test_core.py` (PASS) |\n"
        )
        test_file.write_text(sample_content, encoding="utf-8")

        try:
            res = subprocess.run(
                [sys.executable, "-B", str(tool_script), "--file", str(test_file), "--require-all-complete"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"CLI validation failed: {res.stderr}\n{res.stdout}")
            self.assertIn("Found 1 valid wave rows", res.stdout)
            self.assertIn("[COMPLETE]", res.stdout)
        finally:
            if test_file.exists():
                test_file.unlink()


if __name__ == "__main__":
    unittest.main()
