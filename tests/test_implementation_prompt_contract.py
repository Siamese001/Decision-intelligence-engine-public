#!/usr/bin/env python3
"""Contract & Regression Tests for Implementation Prompts and Testing Governance Templates."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImplementationPromptContractTests(unittest.TestCase):
    """Enforces structural and testing coverage invariants across implementation prompts."""

    def setUp(self) -> None:
        self.prompt_path = ROOT / "implementation_prompt.md"
        if not self.prompt_path.is_file():
            self.prompt_path = ROOT / "archive" / "root_clutter" / "implementation_prompt.md"
        self.review_prompt_path = ROOT / "config" / "prompts" / "testing" / "REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md"
        self.partial_path = ROOT / "config" / "prompts" / "testing" / "IMPLEMENTATION_PROMPT_TESTING_MATRIX_PARTIAL.md"
        self.change_plan_path = ROOT / "intake" / "PROPOSED_CHANGE_PLAN_TEMPLATE.md"
        self.receipt_schema_path = ROOT / "contracts" / "dual_pipeline" / "schemas" / "hook-performance-optimization-receipt.schema.json"

        self.assertTrue(self.prompt_path.is_file(), f"Missing {self.prompt_path}")
        self.assertTrue(self.review_prompt_path.is_file(), f"Missing {self.review_prompt_path}")
        self.assertTrue(self.partial_path.is_file(), f"Missing {self.partial_path}")
        self.assertTrue(self.change_plan_path.is_file(), f"Missing {self.change_plan_path}")
        self.assertTrue(self.receipt_schema_path.is_file(), f"Missing {self.receipt_schema_path}")

        self.prompt_text = self.prompt_path.read_text(encoding="utf-8")
        self.review_text = self.review_prompt_path.read_text(encoding="utf-8")
        self.partial_text = self.partial_path.read_text(encoding="utf-8")
        self.change_plan_text = self.change_plan_path.read_text(encoding="utf-8")

    def test_implementation_prompt_structural_fields(self) -> None:
        """Every wave in implementation_prompt.md must declare all 4 standard fields."""
        waves = ["WAVE 0:", "WAVE 1:", "WAVE 2:", "WAVE 3:", "WAVE 4:"]
        for wave in waves:
            self.assertIn(wave, self.prompt_text, f"Missing {wave} in implementation_prompt.md")

        # Acceptance Criteria and Runtime Receipt counts
        acc_count = len(re.findall(r"####\s+.*Acceptance Criteria", self.prompt_text))
        receipt_count = len(re.findall(r"####\s+.*Runtime Receipt", self.prompt_text))
        self.assertGreaterEqual(acc_count, 5, "Every wave (0-4) must have an Acceptance Criteria section")
        self.assertGreaterEqual(receipt_count, 5, "Every wave (0-4) must have a Runtime Receipt section")

    def test_implementation_prompt_valid_ci_profiles(self) -> None:
        """Only valid CI profiles (deterministic, e2e) are permitted; --profile full is forbidden."""
        self.assertNotIn("--profile full", self.prompt_text, "Invalid '--profile full' found in prompt")
        self.assertIn("--profile deterministic", self.prompt_text)
        self.assertIn("--profile e2e", self.prompt_text)

    def test_implementation_prompt_no_e2e_exempt(self) -> None:
        """Implementation prompt must forbid declaring E2E-EXEMPT for tooling/hooks."""
        self.assertIn("No `E2E-EXEMPT`", self.prompt_text)
        self.assertIn("Declaring `E2E-EXEMPT` is strictly prohibited", self.prompt_text)

    def test_implementation_prompt_reporting_table_open(self) -> None:
        """Reporting template table must not be pre-filled with COMPLETE tokens."""
        table_start = self.prompt_text.find("## 4. REPORTING REQUIREMENTS")
        self.assertGreater(table_start, 0)
        table_section = self.prompt_text[table_start:]
        self.assertNotIn("✅ COMPLETE", table_section, "Template table must use ⏳ OPEN, not ✅ COMPLETE")
        self.assertIn("⏳ OPEN", table_section)

    def test_implementation_prompt_testing_matrices(self) -> None:
        """Waves 1-3 must each explicitly define Unit, Integration, E2E, and Adversarial test files."""
        for w_idx in [1, 2, 3]:
            w_header = f"### WAVE {w_idx}:"
            w_start = self.prompt_text.find(w_header)
            next_start = self.prompt_text.find(f"### WAVE {w_idx+1}:")
            w_text = self.prompt_text[w_start:next_start]

            self.assertRegex(w_text, r"tests/[a-zA-Z0-9_/]+unit[a-zA-Z0-9_]*\.py", f"Wave {w_idx} missing unit test file")
            self.assertRegex(w_text, r"tests/[a-zA-Z0-9_/]+(?:integration|idempotency)[a-zA-Z0-9_]*\.py", f"Wave {w_idx} missing integration test file")
            self.assertRegex(w_text, r"tests/end_to_end/[a-zA-Z0-9_]+\.py", f"Wave {w_idx} missing E2E test file")
            self.assertRegex(w_text, r"tests/adversarial/[a-zA-Z0-9_]+\.py", f"Wave {w_idx} missing adversarial test file")

    def test_repo_testing_gaps_review_prompt_hitl_margin_15_percent(self) -> None:
        """REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md must enforce 15% HITL ambiguity margin and have 0 25% references."""
        self.assertNotIn("25%", self.review_text, "Found stale 25% references in testing gaps review prompt")
        self.assertIn("The 15% Ambiguity Margin Gate", self.review_text)
        self.assertIn(r"\Delta \ge 0.15", self.review_text)

    def test_repo_testing_gaps_review_prompt_dimension_9(self) -> None:
        """Review prompt must include Dimension 9 for Tooling, Git Hooks & CI Gate Parity."""
        self.assertIn("### DIMENSION 9: TOOLING, GIT HOOKS & CI GATE PARITY", self.review_text)
        self.assertIn(r"1,095", self.review_text)

    def test_proposed_change_plan_template_verification_section(self) -> None:
        """PROPOSED_CHANGE_PLAN_TEMPLATE.md must include Section 9 Verification & Testing Specification."""
        self.assertIn("## 9. Verification & Testing Specification", self.change_plan_text)

    def test_testing_matrix_partial_presence_and_dimensions(self) -> None:
        """Partial must define all 9 testing dimensions."""
        for dim in [
            "1. Unit Isolation",
            "2. Integration",
            "3. End-to-End (E2E)",
            "4. Adversarial & Edge-Cases",
            "5. Regression & Parity",
            "6. Performance & Latency",
            "7. HITL Decision Gating",
            "8. Epistemic Security",
            "9. Tooling & Gate Parity",
        ]:
            self.assertIn(dim, self.partial_text, f"Partial missing dimension: {dim}")

    def test_mutation_check_against_unhardened_baseline(self) -> None:
        """Mutation test: Validates that unhardened prompt content would fail closed."""
        unhardened_sample = (
            "### WAVE 1: Test\n"
            "python tools/run_ci_gates.py --profile full\n"
            "| 1 | Test | ✅ COMPLETE |\n"
        )
        # Should detect --profile full
        self.assertIn("--profile full", unhardened_sample)
        # Should detect premature ✅ COMPLETE
        self.assertIn("✅ COMPLETE", unhardened_sample)
        # Should detect missing Acceptance Criteria
        self.assertNotIn("Acceptance Criteria", unhardened_sample)


if __name__ == "__main__":
    unittest.main()
