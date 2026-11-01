"""
tests/test_section3_anchor_gate_adversarial.py

Adversarial and Edge-Case Test Suite for Section 3 Thematic Decoupling & Persona Anchor Gate (Wave 2).
Covers Mandatory Categories per e2e-and-edge-case-coverage.md:
  1. Boundary payload: Empty text, missing Section 3, single-bullet Section 3.
  2. Malformed input: Missing meta, None meta, malformed roster dict/list.
  3. Exact production regression: Pinning the 5 Bryce Poeter anchors and Nathan Sutton canonical anchor.
  4. Invariant bypass attempts: Subtle whitespace, punctuation, and casing variations of bare persona collapses.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.prompts.contracts.output2_contract import (
    _find_bare_persona_anchors,
    validate_c21_depth_floors_and_anchors,
    validate_section3_thematic_decoupling,
)
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


class Section3AnchorGateAdversarialTests(unittest.TestCase):
    """Adversarial suite testing Section 3 anchor validation against regressions and bypasses."""

    def setUp(self) -> None:
        self.roster = {"Bryce Poeter", "Amit Ayer", "Nathan Sutton"}
        self.meta: Dict[str, Any] = {
            "attendees": {
                "PartnerAdvisory": ["Bryce Poeter (Tech Lead)", "Amit Ayer (Principal Architect & Technology Strategist)"],
                "Client": ["Nathan Sutton (Director)"],
            }
        }

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # -------------------------------------------------------------------------
    def test_boundary_empty_section3(self) -> None:
        """Boundary: Empty string, whitespace-only, or text without Section 3."""
        self.assertEqual(_find_bare_persona_anchors("", self.roster), [])
        self.assertEqual(_find_bare_persona_anchors("   \n\n  ", self.roster), [])

        ok, failures = validate_section3_thematic_decoupling("", self.meta)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

        text_no_sec3 = "# Executive Summary\nSome summary text\n# Next Steps\nDo stuff."
        ok2, failures2 = validate_section3_thematic_decoupling(text_no_sec3, self.meta)
        self.assertTrue(ok2)
        self.assertEqual(failures2, [])

    def test_boundary_single_bullet_thematic_anchor(self) -> None:
        """Boundary: Single bullet with purely thematic non-persona anchor."""
        sec3 = "### Key Discussion Points & Findings\n* **Single-Pane Application Architecture & API Layer:** Discussed API endpoints."
        ok, failures = validate_section3_thematic_decoupling(sec3, self.meta)
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    # -------------------------------------------------------------------------
    # Category 2: Malformed Inputs
    # -------------------------------------------------------------------------
    def test_malformed_meta_and_roster(self) -> None:
        """Malformed: meta is None, empty dict, or contains non-standard structures."""
        sec3 = "### Key Discussion Points & Findings\n* **Nathan Sutton defines developer velocity friction:** Nathan explained the issue."
        # None meta
        ok1, f1 = validate_section3_thematic_decoupling(sec3, None)
        self.assertTrue(ok1)
        self.assertEqual(f1, [])

        # Empty dict meta
        ok2, f2 = validate_section3_thematic_decoupling(sec3, {})
        self.assertTrue(ok2)
        self.assertEqual(f2, [])

        # Malformed attendees formats
        malformed_meta = {"attendees": ["NotADictOrStandardString", 12345, None, {"invalid_key": True}]}
        ok3, f3 = validate_section3_thematic_decoupling(sec3, malformed_meta)
        self.assertTrue(ok3)
        self.assertEqual(f3, [])

    # -------------------------------------------------------------------------
    # Category 3: Exact Production Regression
    # -------------------------------------------------------------------------
    def test_exact_production_regression_bryce_five_anchors(self) -> None:
        """Exact Production Regression: The 5 Bryce Poeter anchors from 10-12-2026 run must pass."""
        bryce_sec3 = """
### Key Discussion Points & Findings
- **Bryce Poeter described interest in a single-pane application after prior utility demonstrations:** Outlined the positive reception from client leadership and prior delivery experience.
- **Bryce Poeter outlined a user-focused workshop as the first discovery step:** Detailed the two-workshop sequence required before project kickoff.
- **Bryce Poeter described the technical questions for a follow-on workshop:** Identified system of record inventory and API discovery requirements.
- **Bryce Poeter raised system consolidation as a longer-term consideration:** Discussed post-acquisition platform differences and data flow challenges.
- **Bryce Poeter committed to check whether the earlier presentation was recorded:** Offered to search collateral repository for recorded demonstrations.
"""
        bare = _find_bare_persona_anchors(bryce_sec3, self.roster)
        self.assertEqual(
            bare,
            [],
            f"Production anchors incorrectly flagged as bare persona collapses: {bare}",
        )

        ok, failures = validate_section3_thematic_decoupling(bryce_sec3, self.meta)
        collapse_failures = [f for f in failures if "SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f]
        self.assertEqual(collapse_failures, [])

    def test_exact_production_regression_canonical_nathan_sutton_example(self) -> None:
        """Exact Production Regression: Gate's own example in error message must pass."""
        text = """
### Key Discussion Points & Findings
* **Nathan Sutton defines developer velocity friction:** Nathan detailed how pipeline delays and slow build iterations impair team throughput.
"""
        bare = _find_bare_persona_anchors(text, self.roster)
        self.assertEqual(bare, [])

        ok, failures = validate_section3_thematic_decoupling(text, self.meta)
        collapse_failures = [f for f in failures if "SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f]
        self.assertEqual(collapse_failures, [])

    # -------------------------------------------------------------------------
    # Category 4: Invariant Bypass Attempts (Bare Persona Collapses)
    # -------------------------------------------------------------------------
    def test_invariant_bypass_bare_persona_collapses_rejected(self) -> None:
        """Invariant Bypass: Rejects bare persona anchors across varied punctuation and capitalization."""
        violating_sec3 = """
### Key Discussion Points & Findings
- **Bryce Poeter:** Discussed utility demonstrations.
- **Bryce Poeter** Explained the workshop.
- **Bryce Poeter Update:** Provided project update.
- **Bryce Poeter's Notes:** Notes on systems.
- **Nathan Sutton - Update:** Update on delivery.
- **Nathan Sutton Discussion:** Overview of velocity.
"""
        bare = _find_bare_persona_anchors(violating_sec3, self.roster)
        self.assertEqual(len(bare), 6)

        ok, failures = validate_section3_thematic_decoupling(violating_sec3, self.meta)
        self.assertFalse(ok)
        self.assertTrue(any("SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f for f in failures))

    def test_gate2_deduplication_integrity(self) -> None:
        """Verify check_executive_recap_gate contains Gate_2_14 once and Gate_2_13 is not duplicated."""
        sample_recap = """
# Executive Strategic Brief: Test Meeting
- **Date:** 10-12-2026
- **Participants:**
  - **PartnerAdvisory:** Bryce Poeter (Tech Lead)

## 1. Executive Summary
This is a substantive executive summary describing utility architecture.

## 2. Strategic Context & Business Drivers
Strategic rationale and commercial context for the engagement.

## 3. Key Discussion Points & Findings
* **Bryce Poeter described interest in a single-pane application after prior utility demonstrations:** Detailed presentation context.

## 4. Key Decisions & Rationales
Decisions ratified during the discussion.

## 5. Unified Action & Governance Ledger
| # | Type | Action | Owner | Target Date | Lineage Locator | Status |
|---|------|--------|-------|-------------|-----------------|--------|
| 1 | Action | Coordinate workshop | Bryce Poeter | 10-20-2026 | [00:10:00] | Proposed |

## 6. Next Steps & Milestones
Next steps outlined.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
# Bryce Poeter — PartnerAdvisory, Tech Lead
- **Discovery Strategy**: Detailed workshop progression. [00:06:16–00:07:04]
"""
        res = run_qa_gate_2(sample_recap, meta=self.meta)
        gates = res.get("gates", {})

        # Gate 2.14 must be present
        self.assertIn("Gate_2_14_Section3_Thematic_Decoupling", gates)
        # Gate 2.13 must be the Operator Thought Framing Firewall, NOT duplicated Section3 Decoupling
        self.assertIn("Gate_2_13_Operator_Thought_Framing_Firewall", gates)
        self.assertNotIn("Gate_2_13_Section3_Thematic_Decoupling", gates)

    def test_gate1_10_skip_roster_header_and_table_rows(self) -> None:
        """Verify Gate 1.10 _is_skip_line skips roster headers, metadata, and action table rows."""
        from dual_pipeline.transcript_qa_core.gate1_entity_context import _is_skip_line

        # Roster group lines
        self.assertTrue(_is_skip_line("- **PartnerAdvisory:** Bryce Poeter (Tech Lead), Amit Ayer (Principal Architect)"))
        self.assertTrue(_is_skip_line("  - **Dominion:** Colleen Jones (VP Utility Ops)"))
        self.assertTrue(_is_skip_line("- **Attendees:**"))
        self.assertTrue(_is_skip_line("- **Participants:**"))
        self.assertTrue(_is_skip_line("- **Date:** 10-12-2026"))

        # Table rows
        self.assertTrue(_is_skip_line("| 1 | Action | Coordinate with Andrew on demo | Bryce Poeter | [00:10:00] | Proposed |"))
        self.assertTrue(_is_skip_line("| Item | Finding | Category |"))

        # Headings
        self.assertTrue(_is_skip_line("# Executive Strategic Brief"))
        self.assertTrue(_is_skip_line("## 3. Key Discussion Points & Findings"))

        # Spoken narrative lines must NOT be skipped
        self.assertFalse(_is_skip_line("Colleen mentioned that the server migration was postponed."))
        self.assertFalse(_is_skip_line("Andrew asked about the timeline for the discovery workshop."))


if __name__ == "__main__":
    unittest.main()
