#!/usr/bin/env python3
"""tests/test_action_ledger_grounding_adversarial.py

Adversarial and edge-case testing suite for Stage 02 Action & Governance Ledger Grounding
and Gate X2 anti-mail-in enforcement under e2e-and-edge-case-coverage.md.

Covers:
1. Boundary payload: Empty tables, header-only ledgers, clean zero-commitment sentinels.
2. Malformed input: Malformed pipe boundaries, invalid relative dates, truncated row cells.
3. Invariant bypass attempt: Disguised Unassigned tokens, collective teams, and X2 rejection.
4. Relative timing math: Spoken weekdays, EOD, and next-week calculations against meeting base date.
5. Healer pruning & sentinel conversion: Deterministic elimination of unowned filler rows.
"""

from __future__ import annotations

import unittest
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_qa_core.table_healer import (
    heal_output_2_table,
    resolve_relative_timing,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    Pass2BStructureError,
    validate_pass_2b_structure,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


class TestActionLedgerGroundingAdversarial(unittest.TestCase):
    """Adversarial and edge-case verification for Action & Governance Ledger grounding."""

    def setUp(self) -> None:
        self.xgates = XExitGatesEvaluator()
        self.base_date = "10/17/2026"  # Saturday

    # =========================================================================
    # Category 1: Boundary Payloads
    # =========================================================================

    def test_boundary_empty_and_header_only_tables_rejected_by_dag(self) -> None:
        """Verify empty tables or header-only markdown tables fail structure validation."""
        # Completely empty
        with self.assertRaises(Pass2BStructureError):
            validate_pass_2b_structure("")

        # Header only without separator
        with self.assertRaises(Pass2BStructureError):
            validate_pass_2b_structure("| # | Type | Item | Accountable Owner | Target Timing | Evidence | Status |")

        # Header with separator but zero data rows
        header_only = (
            "| # | Type | Item | Accountable Owner | Target Timing | Evidence | Status |\n"
            "|---|---|---|---|---|---|---|"
        )
        with self.assertRaises(Pass2BStructureError):
            validate_pass_2b_structure(header_only)

    def test_boundary_zero_commitment_sentinels_accepted(self) -> None:
        """Verify explicit standard sentinels pass validation without requiring tables."""
        sentinel_v57 = "No formal actions were established in this session; open initiatives remain unresolved questions."
        self.assertTrue(validate_pass_2b_structure(sentinel_v57))

        sentinel_v56 = "No forward commitments or action items were assigned during this discussion."
        self.assertTrue(validate_pass_2b_structure(sentinel_v56))

        sentinel_case_insensitive = "no formal actions were established in this session"
        self.assertTrue(validate_pass_2b_structure(sentinel_case_insensitive))

    # =========================================================================
    # Category 2: Malformed Input
    # =========================================================================

    def test_malformed_relative_date_resolution_fails_safely(self) -> None:
        """Verify unparseable or nonsense base dates and relative timings fail safely."""
        # Invalid base date format
        res, did_resolve = resolve_relative_timing("by Friday", "not-a-date")
        self.assertFalse(did_resolve)
        self.assertEqual(res, "by Friday")

        # Nonsense calendar base date
        res, did_resolve = resolve_relative_timing("by Friday", "99/99/9999")
        self.assertFalse(did_resolve)

        # Empty timing string
        res, did_resolve = resolve_relative_timing("", "10/17/2026")
        self.assertFalse(did_resolve)

    def test_malformed_table_row_handling_in_healers(self) -> None:
        """Verify table healer survives malformed markdown rows without crashing."""
        malformed_recap = (
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item | Accountable Owner | Target Timing | Evidence | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Incomplete row |\n"
            "| 2 | Action | Valid item | Amit Ayer | 10/24/2026 | [00:10:00] | Open |\n"
        )
        changes = []
        healed = heal_output_2_table(malformed_recap, changes)
        self.assertIn("Amit Ayer", healed)

    # =========================================================================
    # Category 3: Invariant Bypass Attempts
    # =========================================================================

    def test_invariant_bypass_attempt_unassigned_with_tbd_timing_rejected_by_dag(self) -> None:
        """Verify Pass 2B structure validation strictly rejects disguised unassigned TBD filler."""
        disguised_table = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Configure firewall | Unassigned (Pending Owner Assignment) | TBD - Unspecified | [00:14:00] | Open |"
        )
        with self.assertRaises(Pass2BStructureError) as ctx:
            validate_pass_2b_structure(disguised_table)
        self.assertIn("lazy unowned/undated filler row", str(ctx.exception))

    def test_invariant_bypass_attempt_rejected_by_gate_x2(self) -> None:
        """Verify Gate X2 rejects unassigned owners paired with TBD or Not stated timing."""
        # 1. Bare Unassigned with TBD
        recap_bare = (
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Configure firewall | Unassigned | TBD | [00:14:00] | Open |\n"
        )
        with self.assertRaises(ValueError) as ctx:
            self.xgates._validate_action_items(recap_bare)
        self.assertIn("X2 FAIL", str(ctx.exception))
        self.assertIn("pairs unassigned owner", str(ctx.exception))

        # 2. Approved token with 'Not stated' timing
        recap_token_not_stated = (
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Configure firewall | Unassigned (Pending Owner Assignment) | Not stated | [00:14:00] | Open |\n"
        )
        with self.assertRaises(ValueError) as ctx:
            self.xgates._validate_action_items(recap_token_not_stated)
        self.assertIn("X2 FAIL", str(ctx.exception))
        self.assertIn("pairs unassigned owner", str(ctx.exception))

        # 3. All rows lack named DRI even if timing is concrete
        recap_all_unassigned = (
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Configure firewall | Unassigned (Pending Owner Assignment) | 10/24/2026 | [00:14:00] | Open |\n"
        )
        with self.assertRaises(ValueError) as ctx:
            self.xgates._validate_action_items(recap_all_unassigned)
        self.assertIn("X2 FAIL", str(ctx.exception))
        self.assertIn("contains no named accountable owners", str(ctx.exception))

    def test_valid_grounded_action_row_passes_gate_x2(self) -> None:
        """Verify grounded action items with named DRI and concrete date pass Gate X2."""
        valid_recap = (
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Deploy staging environment | Amit Ayer | 10/24/2026 | [00:14:00] | In Progress |\n"
            "| 2 | Gate | Architecture approval | Unassigned (Pending Owner Assignment) | 11/01/2026 | [00:20:00] | Proposed |\n"
        )
        # Should not raise
        self.xgates._validate_action_items(valid_recap)

    # =========================================================================
    # Category 4: Relative Timing Math
    # =========================================================================

    def test_relative_timing_resolution_against_base_date(self) -> None:
        """Verify relative spoken timing expressions resolve to exact MM/DD/YYYY dates."""
        # base_date is 10/17/2026 (Saturday)

        # Same day / EOD
        res, did = resolve_relative_timing("EOD", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/17/2026")

        res, did = resolve_relative_timing("by close of business", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/17/2026")

        # Tomorrow -> 10/18/2026 (Sunday)
        res, did = resolve_relative_timing("tomorrow", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/18/2026")

        # Next week -> 10/24/2026 (Saturday)
        res, did = resolve_relative_timing("next week", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/24/2026")

        # Spoken weekdays:
        # From Saturday 10/17/2026:
        # Monday is 2 days ahead -> 10/19/2026
        res, did = resolve_relative_timing("by Monday", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/19/2026")

        # Wednesday is 4 days ahead -> 10/21/2026
        res, did = resolve_relative_timing("for Wednesday's review", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/21/2026")

        # Friday is 6 days ahead -> 10/23/2026
        res, did = resolve_relative_timing("this Friday", self.base_date)
        self.assertTrue(did)
        self.assertEqual(res, "10/23/2026")

    # =========================================================================
    # Category 5: Healer Pruning & Degenerate Sentinel Conversion
    # =========================================================================

    def test_table_healer_prunes_unowned_tbd_filler_and_preserves_valid(self) -> None:
        """Verify table healer prunes unowned TBD rows while preserving named DRI rows."""
        recap = (
            "**Date:** 10/17/2026\n\n"
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Deploy staging environment | Amit Ayer | by Friday | [00:14:00] | In Progress |\n"
            "| 2 | Action | Follow up on general thoughts | Unassigned | TBD | [00:20:00] | Open |\n"
        )
        changes = []
        healed = heal_output_2_table(recap, changes)

        # Row 1 preserved and timing healed to 10/23/2026
        self.assertIn("Amit Ayer", healed)
        self.assertIn("10/23/2026", healed)

        # Row 2 pruned
        self.assertNotIn("Follow up on general thoughts", healed)
        self.assertTrue(any("Pruned unowned/undated filler row" in c for c in changes))

    def test_table_healer_converts_all_filler_table_to_sentinel(self) -> None:
        """Verify table healer replaces all-filler table with standard confirmation sentinel."""
        recap = (
            "**Date:** 10/17/2026\n\n"
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | General alignment | Unassigned | TBD | [00:14:00] | Open |\n"
            "| 2 | Action | Follow up thoughts | Unassigned (Pending Owner Assignment) | Not stated | [00:20:00] | Open |\n"
        )
        changes = []
        healed = heal_output_2_table(recap, changes)

        # Clean confirmation sentinel emitted
        self.assertIn("No formal actions were established in this session; open initiatives remain unresolved questions.", healed)
        # Table markup removed
        self.assertNotIn("| General alignment |", healed)
        self.assertNotIn("| # | Type |", healed)

    def test_recap_healer_does_not_strip_approved_fallback_token(self) -> None:
        """Verify recap healer preserves 'Unassigned (Pending Owner Assignment)' without stripping."""
        recap = (
            "## Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Deploy code | Amit Ayer | 10/24/2026 | [00:10:00] | In Progress |\n"
            "| 2 | Gate | Review Charter | Unassigned (Pending Owner Assignment) | 11/01/2026 | [00:20:00] | Proposed |\n"
        )
        changes = []
        audit_log = []
        healed = heal_output_2_recap(recap, changes, audit_log)
        self.assertIn("Unassigned (Pending Owner Assignment)", healed)


if __name__ == "__main__":
    unittest.main()
