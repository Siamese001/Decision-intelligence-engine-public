"""Adversarial stress harness for consolidated wave completion table governance.

Covers >=3 adversarial traits:
1. Boundary Payload: Whitespace-only cells, zero-width Unicode characters, massive multi-kilobyte cells.
2. Invariant Bypass: False claim of COMPLETE with empty/whitespace deliverable; contradictory trailing implementation_plan.md reference.
3. Malformed Input: Mismatched column counts, corrupted pipe formatting, non-numeric wave headers.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.validate_implementation_summary import validate_summary_content

ROOT = Path(__file__).resolve().parents[2]


class TestWaveCompletionTableAdversarial(unittest.TestCase):
    """Adversarial test harness for wave completion table parser and validator."""

    def test_adversarial_boundary_whitespace_and_zero_width_chars(self) -> None:
        """Trait 1 (Boundary Payload): Cells containing only Unicode whitespace or zero-width spaces fail validation."""
        zero_width_content = (
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| **Wave 1** | \u200b\u200b\u200b | ✅ COMPLETE | Delivered component | `receipt` |\n"
        )
        is_valid, errors, _ = validate_summary_content(zero_width_content, "zero_width.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("description is too short or empty" in err for err in errors))

    def test_adversarial_boundary_massive_cell_payload(self) -> None:
        """Trait 1 (Boundary Payload): 10,000-character table cell parses without hanging or memory exhaustion."""
        giant_evidence = "Evidence detail: " + ("A" * 10000)
        giant_content = (
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            f"| **Wave 1** | Large payload testing | ✅ COMPLETE | {giant_evidence} | `pytest` |\n"
        )
        is_valid, errors, records = validate_summary_content(giant_content, "giant.md")
        self.assertTrue(is_valid, f"Expected giant payload to parse, got: {errors}")
        self.assertEqual(len(records), 1)
        self.assertIn("Evidence detail", records[0]["deliverable"])

    def test_adversarial_invariant_bypass_empty_evidence_on_complete(self) -> None:
        """Trait 2 (Invariant Bypass): Claiming COMPLETE with only backticks or whitespace in deliverable column fails closed."""
        bypass_content = (
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| **Wave 1** | Hardening pipeline | ✅ COMPLETE | `  ` | `pytest test.py` |\n"
        )
        is_valid, errors, _ = validate_summary_content(bypass_content, "bypass_evidence.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("lacks required Detailed Deliverable & Empirical Evidence" in err for err in errors))

    def test_adversarial_invariant_bypass_contradictory_plan_artifact_link(self) -> None:
        """Trait 2 (Invariant Bypass): Re-surfacing implementation_plan.md with 'awaiting authorization' after complete execution fails."""
        contradictory_content = (
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| **Wave 1** | All tasks done | ✅ COMPLETE | Full test suite verified | `pytest` (100% pass) |\n\n"
            "The full plan is in implementation_plan.md — no code has been modified, awaiting your authorization to execute.\n"
        )
        is_valid, errors, _ = validate_summary_content(contradictory_content, "contradictory_artifact.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("contradictory trailing plan artifact reference" in err for err in errors))

    def test_adversarial_malformed_mismatched_columns(self) -> None:
        """Trait 3 (Malformed Input): Rows with fewer than 3 columns or invalid wave names fail closed."""
        malformed_content = (
            "| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| InvalidWaveName | Description text | ✅ COMPLETE |\n"
            "| RowWithOnlyOneCol |\n"
        )
        is_valid, errors, _ = validate_summary_content(malformed_content, "malformed.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("fewer than 3 columns" in err for err in errors))
        self.assertTrue(any("invalid wave identifier" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
