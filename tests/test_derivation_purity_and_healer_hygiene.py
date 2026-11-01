#!/usr/bin/env python3
"""
tests/test_derivation_purity_and_healer_hygiene.py

Verifies Wave 2 Heuristic Healer Debt Elimination & Derivation Purity Enforcement
under Master Invariant #8 (Production Derivation Integrity) and Master Invariant #12
(Plain-English Executive Recap & Anti-Word-Salad Standard).
"""

from __future__ import annotations

import unittest

from dual_pipeline.transcript_qa_core.appendix_healer import (
    _synthesize_speaker_section,
)
from dual_pipeline.transcript_qa_core.table_healer import (
    _extract_attendees_roster,
    _heal_table_line,
    heal_output_2_table,
)


class TestDerivationPurityAndHealerHygiene(unittest.TestCase):
    """Ensures deterministic healers do not inject synthetic text or hardcoded names/dates."""

    def test_extract_attendees_roster_returns_empty_when_absent(self):
        """Zero ungrounded names injected when text has no attendees."""
        names = _extract_attendees_roster("Text without attendee list")
        self.assertEqual(names, [])
        self.assertNotIn("Amit Ayer", names)
        self.assertNotIn("Jake Stark", names)
        self.assertNotIn("Miles Erickson", names)

    def test_table_line_healer_does_not_inject_fixed_date(self):
        """Action table healer derives from provided date or today, never a hardcoded historical date."""
        changes = []
        raw_line = "| 1 | Review architecture specifications | Unassigned | Engineering | Not agreed | [00:01:00] | Proposed |"
        healed, mod = _heal_table_line(raw_line, changes, row_idx=1, base_date="11/15/2026")
        self.assertTrue(mod)
        self.assertIn("11/15/2026", healed)
        self.assertNotIn("09/25/2026", healed)
        self.assertNotIn("09/22/2026", healed)

    def test_table_healer_scaffolding_preserves_derivation_purity(self):
        """Scaffolded table does not fabricate attendee identities."""
        changes = []
        sample_recap = (
            "## Executive Summary\n"
            "Project kickoff discussion.\n\n"
            "## Next Steps & Action Items\n"
            "Warning: Low action density\n"
        )
        healed = heal_output_2_table(sample_recap, changes)
        self.assertNotIn("Jake Stark", healed)
        self.assertNotIn("Miles Erickson", healed)
        self.assertTrue("| # | Type |" in healed or "| # | Action Item |" in healed)

    def test_appendix_healer_handles_attendee_with_zero_dialogue_factually(self):
        """Attendees with zero spoken turns receive factual note, not corporate buzzwords."""
        section = _synthesize_speaker_section(
            name="Silent Observer",
            role="Observer",
            org="Client Org",
            dialogue=[],
        )
        self.assertIn("### Silent Observer - Client Org, Observer", section)
        self.assertIn("Listed as attendee in meeting metadata; no spoken dialogue recorded in transcript.", section)
        self.assertNotIn("contributed to core architectural and operational priorities", section)

    def test_appendix_healer_dialogue_bullets_are_source_bound(self):
        """Attendees with dialogue receive grounded citations rather than invented boilerplate."""
        section = _synthesize_speaker_section(
            name="Alice Walker",
            role="Lead Engineer",
            org="Apex Global",
            dialogue=[
                "We need to calibrate the SCADA alarms to filter out momentary line trips.",
                "The telemetry feeds from Pano cameras are updating every 30 seconds.",
                "I will verify the API endpoints with the enterprise security team.",
            ],
        )
        self.assertIn("### Alice Walker - Apex Global, Lead Engineer", section)
        self.assertIn("calibrate the SCADA alarms", section)
        self.assertIn("telemetry feeds from Pano cameras", section)
        self.assertNotIn("Emphasized aligning operational needs with technical and delivery standards", section)
        self.assertNotIn("Advocated for inspecting architectural structure", section)


if __name__ == "__main__":
    unittest.main()
