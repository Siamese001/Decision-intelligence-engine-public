"""
tests/test_output2_redundancy_decoupling_e2e.py

Class B E2E Receipt Test Suite for Wave 1:
Output 2a (Main Body) and Output 2b (Appendix Speaker Summary) Decoupling & Healer Remediation.

Verifies:
1. End-to-End Gate 2.14 Decoupling: High (>50%) 4-gram overlap and verbatim quote overlap between
   Section 3 and Section 7 pass Gate 2 with zero redundancy errors.
2. Appendix Healer Scalability (8+ Speakers): Synthesizes distinct, persona-anchored bullets
   for 8+ attendees without triggering SECTION_7_INTRA_APPENDIX_REDUNDANCY.
3. Full Healer Pipeline Integrity: Verifies C-27 possessive syntax, action ledger status,
   preservation of off-roster owners, and duration locator clamping.
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
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap


class Output2RedundancyDecouplingE2ETests(unittest.TestCase):
    """End-to-end verification of Output 2a/2b redundancy decoupling and healer fixes."""

    def setUp(self) -> None:
        self.meta: Dict[str, Any] = {
            "title": "PartnerAdvisory GCP Strategic Partnership Alignment",
            "meeting_name": "PartnerAdvisory GCP Strategic Partnership Alignment",
            "date": "10/13/2026",
            "duration": 28,
            "attendees": {
                "Google": [
                    "Alex Brannan (Partner Director)",
                    "Dianne Eldridge (Account Executive)",
                    "Vani Eswarappa (Google Partner Engineer)",
                    "Robson N. Silva (Strategic Partner Manager)",
                    "Chad Brockman (Cloud Architect)",
                    "Rusty Gattmann (Google Alliances)",
                ],
                "PartnerAdvisory": [
                    "Amit Ayer (Principal Architect & Technology Strategist)",
                    "Drew Oakley (Client Partner, PartnerAdvisory)",
                    "Bryan Armalavage (Senior Director)",
                    "Anya Gayvoronskaya (Consultant)",
                ],
            },
        }

    def test_e2e_section3_and_appendix_heavy_overlap_passes_gate2(self) -> None:
        """E2E: Output 2 with >50% 4-gram overlap and shared quotes between 2a and 2b passes Gate 2.14."""
        recap_text = """# Subject: PartnerAdvisory GCP Strategic Alignment | 10/13/2026

**Date:** 10/13/2026
**Time:** 03:00 PM - 03:28 PM ET
**Duration:** ~28 minutes
**Attendees:**
- **Google:** Alex Brannan (Partner Director), Rusty Gattmann (Google Alliances)
- **PartnerAdvisory:** Amit Ayer (Principal Architect & Technology Strategist), Drew Oakley (Client Partner)

## 1. Executive Summary
The session confirmed PartnerAdvisory's award on the Apex Global automation RFP and aligned engineering support.

## 2. Strategic Context & Business Drivers
Apex Global Enterprises automation program awarded to PartnerAdvisory, moving to broader implementation in Q1.

## 3. Key Discussion Points & Findings
* **Amit Ayer confirmed the automation RFP award and staffing requirements:** Amit stated: "We have to staff it now, and we have to put a lot of software engineers." Amit emphasized that resource allocations remain contingent on final customer approval of full-time and part-time allocations. [00:03:30–00:04:26]
* **Rusty Gattmann commended the RFP win and pledged Google Cloud practice support:** Rusty noted: "PartnerAdvisory is the only one on this list that we feel like has the GCP skill sets." Rusty confirmed that Google partner engineering will closely support PartnerAdvisory's architecture work. [00:04:26–00:05:19]
* **Drew Oakley aligned delivery cadence with Apex Global executive expectations:** Dan coordinated with Apex Global leadership to structure Phase 1 on GCP and Phase 2 on AWS. [00:06:08–00:07:00]

## 4. Architectural Trade-Off Matrix
| Consideration | Approach | Evaluation |
|---|---|---|
| Cloud Platform Sequencing | GCP Phase 1, AWS Phase 2 | Minimizes multi-cloud concurrency risk |

## 5. Action & Governance Ledger
| # | Type | Action Item | Accountable Owner | Target Date | Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Submit automation staffing allocations to Apex Global leadership | Amit Ayer | 10-20-2026 | [00:06:08] | Proposed / Open |
| 2 | Action | Align partner engineering architectural reviews with Vani Eswarappa | Drew Oakley | 10-22-2026 | [00:08:48] | Proposed / Open |

## 6. Next Steps & Milestones
- Advance staffing reviews with Apex Global leadership.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer — PartnerAdvisory, Principal Architect & Technology Strategist
- **Automation RFP Delivery & Staffing**: Amit stated: "We have to staff it now, and we have to put a lot of software engineers." Amit emphasized that resource allocations remain contingent on final customer approval of full-time and part-time allocations. [00:03:30–00:04:26]
- **Partner Engineering Collaboration**: Reviewed architectural touchpoints and aligned with Google Cloud consulting to ensure seamless engineering delivery. [00:06:08–00:07:00]

### Rusty Gattmann — Google, Google Alliances
- **Vendor Selection & GCP Practice Advocacy**: Rusty noted: "PartnerAdvisory is the only one on this list that we feel like has the GCP skill sets." Rusty confirmed that Google partner engineering will closely support PartnerAdvisory's architecture work. [00:04:26–00:05:19]
- **Partner Ecosystem Governance**: Emphasized that partners working on GCP initiatives must deploy certified GCP resources. [00:07:00–00:07:53]

### Drew Oakley — PartnerAdvisory, Client Partner
- **Multi-Cloud Phase Sequencing**: Dan coordinated with Apex Global leadership to structure Phase 1 on GCP and Phase 2 on AWS. [00:06:08–00:07:00]
- **Client Alignment & Practice Governance**: Supported staffing reviews and client presentations. [00:07:00–00:08:00]

### Alex Brannan — Google, Partner Director
- **Partner Engineering Enablement**: Facilitated partner engineer alignment for PartnerAdvisory across technical guidance and architectural reviews. [00:08:00–00:09:00]
- **Governance Oversight**: Monitored weekly cadence outcomes. [00:09:00–00:10:00]
"""
        # Validate through contracts
        c21_ok, c21_failures = validate_c21_depth_floors_and_anchors(recap_text, meta=self.meta)
        self.assertTrue(c21_ok, f"validate_c21_depth_floors_and_anchors failed: {c21_failures}")
        self.assertEqual(c21_failures, [])

        sec3_ok, sec3_failures = validate_section3_thematic_decoupling(recap_text, meta=self.meta)
        self.assertTrue(sec3_ok, f"validate_section3_thematic_decoupling failed: {sec3_failures}")
        self.assertEqual(sec3_failures, [])

        # Validate through Gate 2
        gate2_res = run_qa_gate_2(recap_text, meta=self.meta)
        g214 = gate2_res.get("gates", {}).get("Gate_2_14_Section3_Thematic_Decoupling", {})
        self.assertTrue(g214.get("passed"), f"Gate 2.14 failed: {g214.get('failures')}")
        self.assertEqual(g214.get("failures", []), [])

    def test_e2e_appendix_healer_multi_speaker_zero_intra_redundancy(self) -> None:
        """E2E: Appendix healer synthesizes 10 attendees with zero intra-appendix duplication."""
        stub_recap = """# Subject: Multi-Speaker Test | 10/13/2026
**Date:** 10/13/2026
**Duration:** ~28 minutes
**Attendees:**
- **Google:** Alex Brannan (Partner Director), Dianne Eldridge (Account Executive), Vani Eswarappa (Partner Engineer), Robson N. Silva (Partner Manager), Chad Brockman (Cloud Architect), Rusty Gattmann (Google Alliances)
- **PartnerAdvisory:** Amit Ayer (Principal Architect), Drew Oakley (Client Partner), Bryan Armalavage (Senior Director), Anya Gayvoronskaya (Consultant)

## 1. Executive Summary
Brief summary.

## 3. Key Discussion Points & Findings
* **Amit Ayer led the technical briefing:** Discussed roadmap.

## 5. Action & Governance Ledger
| # | Type | Action Item | Owner | Target Date | Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Review plan | Amit Ayer | 10-20-2026 | [00:05:00] | Proposed / Open |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
"""
        changes: list[str] = []
        healed_appendix = heal_output_2_appendix(stub_recap, metadata=self.meta, changes=changes)

        # Audit with gold standard
        audit = audit_appendix_gold_standard(healed_appendix, duration_minutes=28)
        self.assertTrue(audit.passed, f"Gold standard audit failed: {audit.failures}")

        # Assert no intra-appendix redundancy failure
        dupe_failures = [f for f in audit.failures if "INTRA_APPENDIX_REDUNDANCY" in f]
        self.assertEqual(dupe_failures, [])

        # Assert all 10 attendees are present in appendix
        for spk in [
            "Alex Brannan", "Dianne Eldridge", "Vani Eswarappa", "Robson N. Silva",
            "Chad Brockman", "Rusty Gattmann", "Amit Ayer", "Drew Oakley",
            "Bryan Armalavage", "Anya Gayvoronskaya",
        ]:
            self.assertIn(spk, healed_appendix)

        # Assert em-dash formatting
        self.assertIn("### Amit Ayer — PartnerAdvisory", healed_appendix)
        self.assertIn("### Rusty Gattmann — Google", healed_appendix)

    def test_e2e_full_healer_pipeline_preserves_possessives_and_owners(self) -> None:
        """E2E: Full recap healer preserves possessives and handles off-roster owners and locators."""
        raw_text = """# Executive Brief: Architecture Review
**Date:** 10/13/2026
**Duration:** ~28 minutes
**Attendees:**
- **PartnerAdvisory:** Dan (Client Partner), Amit Ayer (Principal Architect)

## 3. Key Discussion Points & Findings
* **Dan's architecture guidance and review:** Dan outlined the strategy during the session. [00:45:00]

## 5. Action & Governance Ledger
| # | Type | Action Item | Accountable Owner | Target Date | Locator | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Review data model | Jen Pan | Post-meeting | [01:10:00–01:15:00] | In Progress |
"""
        meta = {
            "title": "Architecture Review",
            "duration": 28,
            "attendees": {"PartnerAdvisory": ["Drew Oakley (Client Partner)", "Amit Ayer (Principal Architect)"]},
        }
        healed = heal_output_2_recap(raw_text, metadata=meta)

        # 1. C-27 possessive check: Dan's must become Drew Oakley's, NOT Drew Oakley (Client Partner, PartnerAdvisory)'s
        self.assertIn("Drew Oakley's", healed)
        self.assertNotIn("Drew Oakley (Client Partner, PartnerAdvisory)'s", healed)

        # 2. Off-roster owner check: Jen Pan must NOT be clobbered to Unassigned
        self.assertIn("Jen Pan", healed)
        self.assertNotIn("| Unassigned |", healed)

        # 3. Action status check: Unscheduled 'In Progress' must become 'Proposed / Open'
        self.assertIn("Proposed / Open", healed)

        # 4. Locator clamp check: 01:10:00 (out of 28m duration) clamped to within 28 minutes
        self.assertNotIn("01:10:00", healed)
        self.assertNotIn("00:45:00", healed)


if __name__ == "__main__":
    unittest.main()
