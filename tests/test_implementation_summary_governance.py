#!/usr/bin/env python3
"""
Unit tests for wave implementation summary governance and validator.

Verifies:
1. Compliant implementation summaries with Wave Implementation Status Table pass.
2. Freeform narrative-only summaries are rejected with actionable diagnostics.
3. Tables missing mandatory columns (Wave Number, Description, Status) are rejected.
4. Tables with 0 rows, invalid wave numbers, or unrecognized status values fail closed.
5. Strict --require-all-complete enforcement correctly filters open/blocked waves.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from tools.validate_implementation_summary import validate_summary_content

ROOT = Path(__file__).resolve().parents[1]


class ImplementationSummaryGovernanceTests(unittest.TestCase):
    def test_compliant_summary_table_passes(self) -> None:
        """Verify that a properly structured Wave Implementation Status Table passes."""
        summary = """# Implementation Walkthrough

We have completed the scheduled engineering work.

### Wave Implementation & Completion Status Table

| Wave Number | Description / Scope | Completion Status | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- |
| **Wave 1** | Foundation & Tooling hardening | ✅ COMPLETE | `tools/pr_governance.py doctor` (PASS) |
| **Wave 2** | Route suite rebind and policy sync | ✅ COMPLETE | `eval-route-run` (100% pass) |
| **Wave 3** | Assertion alignment and test wrappers | ✅ COMPLETE | `pytest tests/test_pipeline.py` (PASS) |

## Summary of Results
All tests passed.
"""
        is_valid, errors, records = validate_summary_content(summary, "test_walkthrough.md")
        self.assertTrue(is_valid, f"Expected valid summary, got errors: {errors}")
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["wave_number"], 1)
        self.assertEqual(records[0]["status"], "COMPLETE")
        self.assertEqual(records[1]["wave_number"], 2)
        self.assertEqual(records[2]["wave_number"], 3)

    def test_narrative_only_summary_is_rejected(self) -> None:
        """Verify that freeform narrative-only summaries without a table fail closed."""
        narrative_only = """# Implementation Summary

We worked on Wave 1 and Wave 2 today. Everything went well and all tests passed.
- Wave 1 was completed.
- Wave 2 was completed.

Thanks!
"""
        is_valid, errors, records = validate_summary_content(narrative_only, "narrative.md")
        self.assertFalse(is_valid)
        self.assertTrue(
            any("Missing mandatory Wave Implementation & Completion Status Table" in err for err in errors),
            f"Expected missing table error, got: {errors}",
        )

    def test_table_missing_status_column_is_rejected(self) -> None:
        """Verify that tables lacking the Status column fail header detection."""
        bad_table = """# Walkthrough

| Wave Number | Description / Scope | Notes |
| :--- | :--- | :--- |
| Wave 1 | Setup files | Done |
"""
        is_valid, errors, _ = validate_summary_content(bad_table, "bad_cols.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Missing mandatory Wave Implementation & Completion Status Table" in err for err in errors))

    def test_empty_table_is_rejected(self) -> None:
        """Verify that a table header with 0 data rows fails closed."""
        empty_table = """# Walkthrough

| Wave Number | Description / Scope | Completion Status | Runtime Receipt |
| :--- | :--- | :--- | :--- |
"""
        is_valid, errors, _ = validate_summary_content(empty_table, "empty_table.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("contains 0 wave rows" in err for err in errors))

    def test_invalid_status_tokens_are_rejected(self) -> None:
        """Verify that ambiguous or unstandardized status values fail closed."""
        ambiguous_status = """# Walkthrough

| Wave Number | Description / Scope | Completion Status | Runtime Receipt |
| :--- | :--- | :--- | :--- |
| Wave 1 | Core implementation | Maybe | N/A |
"""
        is_valid, errors, _ = validate_summary_content(ambiguous_status, "ambiguous.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("unrecognized status" in err for err in errors))

    def test_open_and_blocked_statuses_parsed(self) -> None:
        """Verify that OPEN and BLOCKED statuses are recognized."""
        mixed_summary = """# Walkthrough

| Wave Number | Description / Scope | Completion Status | Runtime Receipt |
| :--- | :--- | :--- | :--- |
| Wave 1 | Core database models | ✅ COMPLETE | Receipt A |
| Wave 2 | API Endpoint Integration | ⏳ OPEN | In progress |
| Wave 3 | Downstream Reports | 🚫 BLOCKED | Blocked on auth |
"""
        is_valid, errors, records = validate_summary_content(mixed_summary, "mixed.md")
        self.assertTrue(is_valid, f"Expected valid summary syntax, got errors: {errors}")
        self.assertEqual(records[0]["status"], "COMPLETE")
        self.assertEqual(records[1]["status"], "OPEN")
        self.assertEqual(records[2]["status"], "BLOCKED")

    def test_cli_execution_and_require_all_complete(self) -> None:
        """Verify CLI execution handles --require-all-complete properly."""
        py = sys.executable
        tool_path = ROOT / "tools" / "validate_implementation_summary.py"

        # Complete summary via CLI text
        complete_text = """| Wave Number | Description / Scope | Completion Status | Receipt |
| :--- | :--- | :--- | :--- |
| Wave 1 | Implementation complete | ✅ COMPLETE | receipt1 |
"""
        res = subprocess.run(
            [py, "-B", str(tool_path), "--text", complete_text, "--require-all-complete"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Expected CLI success, stderr: {res.stderr}")

        # Incomplete summary with --require-all-complete
        incomplete_text = """| Wave Number | Description / Scope | Completion Status | Receipt |
| :--- | :--- | :--- | :--- |
| Wave 1 | Implementation in flight | ⏳ OPEN | receipt1 |
"""
        res_incomplete = subprocess.run(
            [py, "-B", str(tool_path), "--text", incomplete_text, "--require-all-complete"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_incomplete.returncode, 1)
        self.assertIn("not marked COMPLETE", res_incomplete.stdout)

    def test_consolidated_5_column_table_passes(self) -> None:
        """Verify that 5-column table with Detailed Deliverable & Empirical Evidence passes cleanly."""
        summary = """# Execution Complete
### Wave Implementation & Completion Status Table

| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| **Wave 1** | Model weight pinning | ✅ COMPLETE | Pinned pyannote 3.3.2 in pyproject.toml | `pytest test_pin.py` (PASS) |
| **Wave 2** | Real audio E2E test | ✅ COMPLETE | 15s audio extracted, 7 turns across 2 speakers verified on MPS | `pytest test_e2e.py` (PASS) |
"""
        is_valid, errors, records = validate_summary_content(summary, "consolidated.md")
        self.assertTrue(is_valid, f"Expected valid 5-column summary, got errors: {errors}")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["deliverable"], "Pinned pyannote 3.3.2 in pyproject.toml")
        self.assertEqual(records[0]["receipt"], "`pytest test_pin.py` (PASS)")
        self.assertEqual(records[1]["deliverable"], "15s audio extracted, 7 turns across 2 speakers verified on MPS")

    def test_consolidated_5_column_table_rejects_empty_deliverable_on_complete(self) -> None:
        """Verify that a 5-column table with empty deliverable on COMPLETE fails closed."""
        summary = """# Execution Complete
### Wave Implementation & Completion Status Table

| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| **Wave 1** | Model weight pinning | ✅ COMPLETE | | `pytest test_pin.py` (PASS) |
"""
        is_valid, errors, _ = validate_summary_content(summary, "empty_deliverable.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("lacks required Detailed Deliverable" in err for err in errors))

    def test_post_execution_single_table_rejects_contradictory_trailing_plan_link(self) -> None:
        """Verify that a summary with all waves COMPLETE rejects trailing implementation_plan.md awaiting execution."""
        summary = """# Execution Complete
### Wave Implementation & Completion Status Table

| Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| **Wave 1** | Model weight pinning | ✅ COMPLETE | Pinned pyannote 3.3.2 in pyproject.toml | `pytest test_pin.py` (PASS) |

The full plan is in the implementation_plan.md. No code has been modified — awaiting your authorization to execute.
"""
        is_valid, errors, _ = validate_summary_content(summary, "contradictory_plan.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("contradictory trailing plan artifact reference" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
