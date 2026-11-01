#!/usr/bin/env python3
"""Adversarial and Edge-Case tests for Operator Decision Vocabulary and Linter."""

import tempfile
import unittest
from pathlib import Path

from config.operator_decision_vocabulary import (
    TOOL_APPROVAL_REQUIRED,
    disambiguate_term,
    is_canonical_term,
    validate_decision_record_boundary,
)
from tools.lint_operator_decision_terms import (
    is_exempt_path,
    lint_files,
    scan_file_for_replaced_terms,
)


class TestOperatorDecisionVocabularyAdversarial(unittest.TestCase):
    """Adversarial tests defending vocabulary boundaries and linter integrity."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_adv_01_term_spoofing_detection(self) -> None:
        """Adversarial Vector 1: Attempt to conceal deprecated terms inside comments/docstrings."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("# In this module, requires_hitl is evaluated\n")
            f.write("def evaluate():\n    return False\n")
            temp_path = Path(f.name)

        try:
            findings = scan_file_for_replaced_terms(temp_path, self.repo_root)
            self.assertEqual(len(findings), 1)
            line_no, matched, replacement, line_snippet = findings[0]
            self.assertEqual(line_no, 1)
            self.assertEqual(matched, "requires_hitl")
            self.assertEqual(replacement, "needs_operator_decision")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_adv_02_new_file_alias_reuse_blocked(self) -> None:
        """Adversarial Vector 2: Reusing legacy aliases in a new file fails the linter."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=self.repo_root, delete=False) as f:
            f.write("RUN_STATUS = 'HITL_REQUIRED'\n")
            temp_path = Path(f.name)

        try:
            # Lint strictly targeting the new file
            exit_code = lint_files(
                repo_root=self.repo_root,
                target_files=[temp_path],
                allow_grandfathered=True,
            )
            self.assertEqual(exit_code, 1, "New file with legacy alias must fail lint")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_adv_03_external_sdk_isolation(self) -> None:
        """Adversarial Vector 3: External SDK names must not pollute canonical vocabulary."""
        external_sdk_names = [
            "policy.ask_user",
            "agent_sdk.Decision.ASK_USER",
            "google.cloud.hitl_approval",
        ]
        for sdk_name in external_sdk_names:
            # Canonical check must reject external SDK names
            self.assertFalse(is_canonical_term(sdk_name))
            # Disambiguation handles Decision.ASK_USER to TOOL_APPROVAL_REQUIRED
            if "Decision.ASK_USER" in sdk_name:
                self.assertEqual(disambiguate_term("Decision.ASK_USER"), TOOL_APPROVAL_REQUIRED)

    def test_adv_04_exemption_path_traversal_resistance(self) -> None:
        """Adversarial Vector 4: Relative path trickery cannot bypass linter exemption checks."""
        # Non-exempt relative path attempting to pretend to be reports/
        bogus_path = "services/reports/my_script.py"
        self.assertFalse(is_exempt_path(bogus_path, allow_grandfathered=True, baseline_files=set()))

        bogus_path_2 = "tests/test_something_else.py"
        self.assertFalse(
            is_exempt_path(bogus_path_2, allow_grandfathered=True, baseline_files=set())
        )

    def test_adv_05_boundary_validation_evasion_blocked(self) -> None:
        """Adversarial Vector 5: Attempting to pass truthy non-booleans to boundary validator."""
        # Falsy truthy evasion
        with self.assertRaises(ValueError):
            validate_decision_record_boundary(is_tool_approval=True, has_option_selection=True)


if __name__ == "__main__":
    unittest.main()
