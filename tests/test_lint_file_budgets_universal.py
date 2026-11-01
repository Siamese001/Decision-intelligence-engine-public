#!/usr/bin/env python3
"""
Unit and Adversarial Tests for universal scripts/ and tools/ governance in tools/lint_file_budgets.py.

Verifies:
- Explicit, justified non-production exclusions are honored.
- Unclassified or production exclusions are rejected (Case F).
- Oversized modules without valid non-production exclusion violate budget.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_file_budgets import (
    _collect_oversized_modules,
    is_excluded,
    validate_exclusion_entry,
)


class TestUniversalFileBudgets(unittest.TestCase):
    def test_legacy_grandfathered_script_with_justified_classification_is_excluded(self):
        self.assertTrue(is_excluded("scripts/prepare_fpl_manifests.py"))
        self.assertTrue(is_excluded("tools/run_decision_intelligence_vs_knowledge_graph_e2e_30.py"))

    def test_new_script_is_not_excluded(self):
        self.assertFalse(is_excluded("scripts/custom_shadow_pipeline.py"))
        self.assertFalse(is_excluded("scripts/my_new_pipeline_runner.py"))


    def test_new_tool_is_not_excluded(self):
        self.assertFalse(is_excluded("tools/my_new_analysis_tool.py"))

    def test_oversized_new_script_violates_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script_file = root / "scripts" / "oversized_new_script.py"
            script_file.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# Line of code\n"] * 450
            script_file.write_text("".join(lines), encoding="utf-8")

            oversized = _collect_oversized_modules(root, max_lines=400)
            self.assertEqual(len(oversized), 1)
            self.assertIn("scripts/oversized_new_script.py", oversized)
            self.assertEqual(oversized["scripts/oversized_new_script.py"], 450)

    def test_case_f_production_script_exclusion_rejected(self):
        """Case F: An exclusion claiming 'production' classification must be rejected."""
        prod_entry = {
            "path": "scripts/active_production_pipeline.py",
            "classification": "production",
            "justification": "This is a heavy production script that runs in main pipeline.",
        }
        valid, reason = validate_exclusion_entry(prod_entry)
        self.assertFalse(valid, "Production script cannot escape architecture budget via exclusion")
        self.assertIn("cannot escape budget governance", reason)

    def test_case_f_unclassified_exclusion_rejected(self):
        """Case F: An unclassified exclusion entry missing classification or justification is rejected."""
        unclassified_entry = {
            "path": "scripts/some_script.py",
        }
        valid, reason = validate_exclusion_entry(unclassified_entry)
        self.assertFalse(valid, "Unclassified exclusion must be rejected")
        self.assertIn("lacks mandatory 'classification'", reason)

        # Raw string entry
        valid_raw, _ = validate_exclusion_entry("scripts/raw_script.py")
        self.assertFalse(valid_raw, "Raw string entry without structured classification must be rejected")

    def test_case_f_justified_non_production_classification_accepted(self):
        """Valid non-production classification with sufficient justification is accepted."""
        valid_entry = {
            "path": "scripts/historical_migration.py",
            "classification": "one_off_migration",
            "justification": "One-time historical database migration script; not part of production pipeline.",
            "expires_at": "2027-01-01T00:00:00Z",
        }
        valid, reason = validate_exclusion_entry(valid_entry)
        self.assertTrue(valid, f"Expected valid exclusion, got: {reason}")
        self.assertEqual(reason, "Valid")


if __name__ == "__main__":
    unittest.main()
