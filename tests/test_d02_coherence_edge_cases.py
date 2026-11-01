"""Adversarial and edge-case test suite for Deliverable 02 Deterministic Cross-Pass Coherence Gate.

Covers:
1. Seeded Contradiction Fixture: Hallucinated / ungrounded owner in 2B triggers CrossPassDivergenceError.
2. Grounded Owners: Full and token-level names matching attendees or transcript pass cleanly.
3. Sentinel Non-Owner Filtering: 'Unassigned', 'Team', 'None', 'N/A', 'TBD', '-' do not trigger false positives.
4. Sentinel Zero-Commitments Statement: 'No forward commitments...' table-free sentinel passes cleanly.
5. Robustness to Malformed Rows: Uneven pipes, extra spaces, and markdown bolding are handled defensively.
6. Integration with execute_d02_3pass_dag(enforce_coherence=True): Catches divergence at DAG boundary.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from dual_pipeline.transcript_stages_core.recap_coherence_gate import (
    CrossPassDivergenceError,
    extract_ledger_owners,
    validate_cross_pass_coherence,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import execute_d02_3pass_dag


class D02CoherenceEdgeCasesTests(unittest.TestCase):
    """Adversarial and edge-case suite for Cross-Pass Coherence Gate."""

    def setUp(self) -> None:
        self.sample_narrative = (
            "# Executive Recap: Sprint Alignment\n\n"
            "**Date:** 10/03/2026\n"
            "**Attendees:** Alex Brennan (PartnerAdvisory, Principal Architect), Amit Ayer (PartnerAdvisory, Director)\n\n"
            "## Meeting Objective & Executive Summary\n"
            "The team finalized the Wave 5 delivery plan. "
            + ("Substantive discussion notes on architecture and gate compliance. " * 40)
        )
        self.sample_transcript = (
            "## Forensic Transcript\n\n"
            "[00:00:10] Alex Brennan: Let's confirm owner assignments for the CI gates.\n"
            "[00:01:00] Amit Ayer: I will oversee the verification run.\n"
            "[00:02:15] Jen: I will help coordinate with the downstream consumers.\n"
        )

    def test_seeded_contradiction_catches_hallucinated_owner(self) -> None:
        """Adversarial seeded contradiction: ungrounded owner 'Phantom Hacker' in 2B raises CrossPassDivergenceError."""
        contradictory_ledger = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Complete Wave 5 Audit | Amit Ayer | 10/04/2026 | None |\n"
            "| 2 | Infiltrate Production DB | Phantom Hacker | 10/05/2026 | Backdoor active |"
        )

        with self.assertRaises(CrossPassDivergenceError) as ctx:
            validate_cross_pass_coherence(
                narrative=self.sample_narrative,
                ledger_text=contradictory_ledger,
                forensic_transcript=self.sample_transcript,
            )

        self.assertIn("Phantom Hacker", str(ctx.exception))
        self.assertIn("does not exist in Strategic Narrative (Pass 2A) or Forensic Transcript", str(ctx.exception))

    def test_grounded_owners_pass_cleanly(self) -> None:
        """Valid owners (Alex Brennan, Amit Ayer, Jen) present in narrative or transcript pass cleanly."""
        valid_ledger = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Prepare Test Suite | Alex Brennan | 10/04/2026 | None |\n"
            "| 2 | Gate Signoff | Amit | 10/05/2026 | Green tests |\n"
            "| 3 | Downstream Handshake | Jen | 10/06/2026 | Verification |"
        )

        is_valid = validate_cross_pass_coherence(
            narrative=self.sample_narrative,
            ledger_text=valid_ledger,
            forensic_transcript=self.sample_transcript,
        )
        self.assertTrue(is_valid)

    def test_sentinel_non_owners_do_not_trigger_false_positives(self) -> None:
        """Sentinel non-owners like 'Unassigned', 'Team', 'None', 'N/A', 'TBD', '-' are ignored."""
        sentinel_ledger = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Backlog Triage | Unassigned | 10/04/2026 | None |\n"
            "| 2 | Architecture Retro | Team | 10/05/2026 | None |\n"
            "| 3 | Hardware Purchase | N/A | 10/06/2026 | None |\n"
            "| 4 | External SOW | TBD | 10/07/2026 | None |\n"
            "| 5 | Legacy Deprecation | - | 10/08/2026 | None |"
        )

        owners = extract_ledger_owners(sentinel_ledger)
        self.assertEqual(owners, [])

        is_valid = validate_cross_pass_coherence(
            narrative=self.sample_narrative,
            ledger_text=sentinel_ledger,
            forensic_transcript=self.sample_transcript,
        )
        self.assertTrue(is_valid)

    def test_zero_commitments_sentinel_passes_cleanly(self) -> None:
        """Table-free sentinel 'No forward commitments or action items were assigned...' passes cleanly."""
        zero_commitments_ledger = "No forward commitments or action items were assigned during this sync."

        owners = extract_ledger_owners(zero_commitments_ledger)
        self.assertEqual(owners, [])

        is_valid = validate_cross_pass_coherence(
            narrative=self.sample_narrative,
            ledger_text=zero_commitments_ledger,
            forensic_transcript=self.sample_transcript,
        )
        self.assertTrue(is_valid)

    def test_malformed_and_bolded_table_rows_handled_defensively(self) -> None:
        """Markdown bolding, irregular pipes, and role annotations are parsed defensively."""
        noisy_ledger = (
            "| Item # | Action | Owner | Target Date | Dependencies |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| 1 | Deliverable 02 Hardening | **Amit Ayer** (PartnerAdvisory) | 10/04/2026 | None |\n"
            "| 2 | Code Review | [Alex Brennan] | 10/05/2026 | None |\n"
            "| irregular row with no trailing pipe\n"
            "||||\n"
        )

        owners = extract_ledger_owners(noisy_ledger)
        self.assertIn("Amit Ayer", owners)
        self.assertIn("Alex Brennan", owners)

        is_valid = validate_cross_pass_coherence(
            narrative=self.sample_narrative,
            ledger_text=noisy_ledger,
            forensic_transcript=self.sample_transcript,
        )
        self.assertTrue(is_valid)

    def test_execute_d02_3pass_dag_enforces_coherence_at_boundary(self) -> None:
        """execute_d02_3pass_dag with enforce_coherence=True halts on seeded contradiction."""
        contradictory_ledger = (
            "| Item # | Action | Owner | Target Date | Next Step |\n"
            "| 1 | Rogue Operation | Dr. Evil | 10/04/2026 | Exploit |"
        )
        perspectives = "# Alex Brennan - PartnerAdvisory\n- **Theme**: Alignment.\n" + (" notes" * 80)

        with patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content") as mock_call:
            mock_call.side_effect = [
                self.sample_narrative,
                contradictory_ledger,
                perspectives,
            ]

            with self.assertRaises(CrossPassDivergenceError) as ctx:
                execute_d02_3pass_dag(
                    model="gemini-3.8-flash",
                    system_prompt="sys",
                    user_content=f"<forensic_transcript>{self.sample_transcript}</forensic_transcript>",
                    min_words_2a=100,
                    min_words_2c=80,
                    enforce_coherence=True,
                    enable_caching=False,
                )

            self.assertIn("Dr. Evil", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
