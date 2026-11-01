"""
tests/test_output2_redundancy_adversarial.py

Class B Adversarial and Edge-Case Test Suite for Wave 1:
Output 2a/2b Redundancy Decoupling & Healer Edge Cases.

Mandatory Categories per e2e-and-edge-case-coverage.md:
  1. Boundary payload: 100% 4-gram overlap, 16+ speakers scaling, empty and single-turn sections.
  2. Malformed input: Table rows missing trailing pipes, malformed duration locators, corrupted metadata.
  3. Exact production regression: Pinning PartnerAdvisory/GCP 09-29 and 10-13 shared quote scenarios.
  4. Invariant bypass attempts: Bare persona anchors rejected; intra-appendix cross-speaker copy-paste caught.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.prompts.contracts.output2_contract import (
    validate_c21_depth_floors_and_anchors,
    validate_section3_thematic_decoupling,
)
from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_action_ledger_status
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap


class Output2RedundancyAdversarialTests(unittest.TestCase):
    """Adversarial and boundary test suite for Output 2a/2b decoupling and healing invariants."""

    def setUp(self) -> None:
        self.meta: Dict[str, Any] = {
            "title": "Adversarial Decoupling Verification",
            "date": "10/13/2026",
            "duration": 30,
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Principal Architect)", "Drew Oakley (Client Partner)"],
                "Google": ["Alex Brannan (Partner Director)", "Rusty Gattmann (Google Alliances)"],
            },
        }

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # -------------------------------------------------------------------------
    def test_boundary_100_percent_overlap_passes_decoupling(self) -> None:
        """Boundary: Exact verbatim copy of Section 3 in Appendix must NOT trigger 2a/2b overlap error."""
        shared_text = (
            "Amit Ayer and Rusty Gattmann reviewed the cloud architecture and committed to full partner alignment. "
            "Drew Oakley confirmed that staffing reviews will be presented directly to client leadership."
        )
        f2 = f"""# Subject: Full Overlap Test | 10/13/2026
**Date:** 10/13/2026
**Duration:** ~30 minutes
**Attendees:**
- **PartnerAdvisory:** Amit Ayer (Principal Architect), Drew Oakley (Client Partner)
- **Google:** Alex Brannan (Partner Director), Rusty Gattmann (Google Alliances)

## 3. Key Discussion Points & Findings
* **Amit Ayer and Rusty Gattmann reviewed cross-cloud automation scope:** {shared_text}

## 5. Action & Governance Ledger
| # | Type | Action Item | Owner | Target Date | Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Submit staffing | Amit Ayer | 10-20-2026 | [00:10:00] | Proposed / Open |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer — PartnerAdvisory, Principal Architect
- **Architecture Review**: {shared_text}
- **Delivery Governance**: Provided ongoing technical oversight across workstreams.

### Drew Oakley — PartnerAdvisory, Client Partner
- **Program Leadership**: {shared_text}
- **Client Strategy**: Aligned team capabilities with executive expectations.
"""
        ok, failures = validate_section3_thematic_decoupling(f2, self.meta)
        self.assertTrue(ok, f"Decoupling failed on 100% overlap: {failures}")
        overlap_fails = [f for f in failures if "OVERLAP_EXCESSIVE" in f or "DUPLICATE_QUOTES" in f]
        self.assertEqual(overlap_fails, [])

    def test_boundary_16_speaker_pool_wrapping_no_intra_appendix_collisions(self) -> None:
        """Boundary: 16 silent attendees wrapping modulo pool produce ZERO intra-appendix collisions."""
        names = [
            f"Attendee {i}" for i in range(1, 17)
        ]
        meta_16 = {
            "title": "Large Meeting",
            "duration": 45,
            "attendees": {"Enterprise": [f"{n} (Consultant)" for n in names]},
        }
        stub = f"""# Subject: 16 Attendee Call | 10/13/2026
**Date:** 10/13/2026
**Duration:** ~45 minutes
**Attendees:**
- **Enterprise:** {", ".join(f"{n} (Consultant)" for n in names)}

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
"""
        healed = heal_output_2_appendix(stub, metadata=meta_16)
        audit = audit_appendix_gold_standard(healed, duration_minutes=45)

        # Must have zero intra-appendix redundancy failures
        dupe_fails = [f for f in audit.failures if "INTRA_APPENDIX_REDUNDANCY" in f]
        self.assertEqual(dupe_fails, [], f"Intra-appendix redundancy collisions detected: {dupe_fails}")

    # -------------------------------------------------------------------------
    # Category 2: Malformed Inputs
    # -------------------------------------------------------------------------
    def test_malformed_action_row_without_trailing_pipe(self) -> None:
        """Malformed Input: Table row lacking trailing pipe must normalize status without clobbering locator."""
        malformed_table = (
            "| # | Type | Action Item | Owner | Target Date | Locator | Status\n"
            "|---|---|---|---|---|---|---\n"
            "| 1 | Action | Finalize staffing plan | Amit Ayer | 10-20-2026 | [00:15:30] | In Progress\n"
        )
        healed = heal_action_ledger_status(malformed_table)

        # Status normalized
        self.assertIn("Proposed / Open", healed)
        # Locator preserved intact
        self.assertIn("[00:15:30]", healed)
        # Trailing pipe restored
        self.assertTrue(any(line.endswith("|") for line in healed.splitlines() if line.startswith("| 1")))

    def test_malformed_and_out_of_bounds_locators(self) -> None:
        """Malformed Input: Locators like [01:25:30] and ranges in a 20-min call get clamped properly."""
        text = (
            "Discussion points:\n"
            "* **Strategy review:** Discussed at [01:25:30] and [00:45:00–00:48:00].\n"
            "* **Normal locator:** Discussed at [00:10:00].\n"
        )
        meta = {"duration": 20}
        healed = heal_output_2_recap(text, metadata=meta)

        self.assertNotIn("01:25:30", healed)
        self.assertNotIn("00:45:00", healed)
        self.assertIn("00:10:00", healed)

    # -------------------------------------------------------------------------
    # Category 3: Exact Production Regression
    # -------------------------------------------------------------------------
    def test_exact_production_regression_partner_gcp_duplicate_quotes(self) -> None:
        """Exact Production Regression: Dianne Eldridge's verbatim quote in both Section 3 and 7 passes Gate 2.14."""
        quote = "If they don't even have basic access for it, what are we enabling? I'm very confused by this."
        recap = f"""# Subject: PartnerAdvisory GCP Weekly Sync | 09/29/2026
**Date:** 09/29/2026
**Duration:** ~43 minutes
**Attendees:**
- **Google:** Dianne Eldridge (Account Executive), Alex Brannan (Partner Director)
- **PartnerAdvisory:** Amit Ayer (Principal Architect)

## 3. Key Discussion Points & Findings
* **Dianne Eldridge challenged enablement access dependencies:** Dianne expressed operational concern: "{quote}" [00:22:40–00:28:04]

## 5. Action & Governance Ledger
| # | Type | Action Item | Owner | Target Date | Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify licenses | Amit Ayer | 10-05-2026 | [00:26:08] | Proposed / Open |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Dianne Eldridge — Google, Account Executive
- **Enablement Readiness**: Dianne expressed the meeting's strongest pushback: "{quote}"
- **User Adoption Barriers**: Detailed access requirements across business units.

### Alex Brannan — Google, Partner Director
- **License Allocation Oversight**: Separated provisioned licenses from active usage.
- **Delivery Cadence**: Aligned partner review timeline.
"""
        meta = {
            "date": "09/29/2026",
            "duration": 43,
            "attendees": {
                "Google": ["Dianne Eldridge (Account Executive)", "Alex Brannan (Partner Director)"],
                "PartnerAdvisory": ["Amit Ayer (Principal Architect)"],
            },
        }
        res = run_qa_gate_2(recap, meta=meta)
        g214 = res.get("gates", {}).get("Gate_2_14_Section3_Thematic_Decoupling", {})
        self.assertTrue(g214.get("passed"), f"Gate 2.14 failed: {g214.get('failures')}")

    # -------------------------------------------------------------------------
    # Category 4: Invariant Bypass Attempts
    # -------------------------------------------------------------------------
    def test_invariant_bypass_bare_persona_anchor_still_rejected(self) -> None:
        """Invariant Bypass: Bare persona anchors (**Amit Ayer:**) are still strictly rejected by Gate 2.14."""
        violating = """# Subject: Violation Test | 10/13/2026
**Date:** 10/13/2026
**Duration:** ~28 minutes
**Attendees:**
- **PartnerAdvisory:** Amit Ayer (Principal Architect)

## 3. Key Discussion Points & Findings
* **Amit Ayer:** Outlined the architecture without actor-plus-action substance.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Amit Ayer — PartnerAdvisory, Principal Architect
- **Perspective**: Substantive narrative.
"""
        meta = {"attendees": {"PartnerAdvisory": ["Amit Ayer (Principal Architect)"]}}
        ok, failures = validate_section3_thematic_decoupling(violating, meta)
        self.assertFalse(ok)
        self.assertTrue(any("SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f for f in failures))

    def test_invariant_bypass_intra_appendix_copy_paste_still_caught(self) -> None:
        """Invariant Bypass: Direct copy-pasting of body text between different speakers in Appendix is caught."""
        identical_sentence = "The engineering group monitored sprint deliverables and validated operational parameters without advancing isolated mandates."
        violating_appendix = f"""# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alex Brannan — Google, Partner Director
- **Delivery Oversight**: {identical_sentence}
- **Governance Oversight**: Tracked review milestones and confirmed cross-team operational visibility.
- **Architecture Review**: Reviewed system interface requirements and verification gates.

### Rusty Gattmann — Google, Google Alliances
- **Vendor Alignment**: {identical_sentence}
- **Ecosystem Review**: Monitored verification checkpoints and ensured testing rigor across phases.
- **Interface Controls**: Supervised interface contracts and compliance checkpoints during the review.
"""
        audit = audit_appendix_gold_standard(violating_appendix, duration_minutes=30)
        self.assertFalse(audit.passed)
        self.assertTrue(any("CROSS_SPEAKER_DUPLICATION" in f or "INTRA_APPENDIX_REDUNDANCY" in f for f in audit.failures))

        meta = {"attendees": {"Google": ["Alex Brannan (Partner Director)", "Rusty Gattmann (Google Alliances)"]}}
        c21_ok, c21_fails = validate_c21_depth_floors_and_anchors(violating_appendix, meta=meta)
        self.assertFalse(c21_ok)
        self.assertTrue(any("INTRA_APPENDIX_REDUNDANCY" in f for f in c21_fails))


if __name__ == "__main__":
    unittest.main()
