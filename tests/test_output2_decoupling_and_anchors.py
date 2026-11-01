"""
tests/test_output2_decoupling_and_anchors.py

Unit tests for Wave 1: Output 02 Contract & Prompt Decoupling.
Verifies thematic Section 3 anchors, prohibition of persona-first anchors,
and 4-gram / quote overlap checks with Section 7 (Appendix: Rule C-21).
"""
import unittest
from typing import Any, Dict

from dual_pipeline.prompts.contracts.output2_contract import (
    OUTPUT2_CONTRACT,
    validate_c21_depth_floors_and_anchors,
)
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


class TestOutput2DecouplingAndAnchors(unittest.TestCase):
    """Test suite for Output 02 thematic anchors and Appendix decoupling."""

    def setUp(self) -> None:
        self.meta: Dict[str, Any] = {
            "attendees": {
                "Google": ["Alex Brannan (Partner Lead)", "Dianne Eldridge (CE Director)"],
                "PartnerAdvisory": ["Amit Ayer (Lead Client Partner)", "Drew Oakley (Client Service Lead)"],
            },
            "meeting_type": "GENERAL",
        }

    def test_thematic_anchors_pass(self) -> None:
        """Section 3 with pure thematic mechanism anchors must pass."""
        text = """# Subject: Partner Sync -- Executive Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
Google:
- Alex Brannan (Partner Lead)
- Dianne Eldridge (CE Director)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)
- Drew Oakley (Client Service Lead)

# Meeting Objective
The team aligned on partnership initiatives and resolving enterprise cloud licensing blockers.

# Key Discussion Points & Findings

- **Apex Global HR Architecture Consolidation & Platform Boundaries:** The team established that enterprise HR systems require a single front door through HR for You, integrating ServiceNow and SuccessFactors. PartnerAdvisory will lead the landscape scoping. [00:05:00]
- **Nuclear Gemini Enterprise Isolation Blockers:** Attendees identified that nuclear workloads require a dedicated, isolated Google Cloud project with security controls before users can be onboarded. [00:15:00]
- **License Entitlement vs. Active Consumption Discrepancy:** The group reconciled provisioned licenses with active enablement seats to prevent voucher forfeiture and secure sustained platform adoption. [00:25:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify enablement access | Amit Ayer | 10/05/2026 | 00:25:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dianne Eldridge — Google, CE Director
- **Access Prerequisite Stance**: Dianne pushed back strongly on running enablement workshops without active user access.
- **Seat Expansion Objectives**: Dianne noted that Apex Global withdrew a seat expansion order due to unassigned licenses.

# Amit Ayer — PartnerAdvisory, Lead Client Partner
- **Partnership Architecture**: Amit proposed modular pods to avoid sprawl and focus joint engineering capacity.
- **Security Dependency Recognition**: Amit agreed that InfoSec controls in the nuclear project represent the critical path.
"""
        passed, failures = validate_c21_depth_floors_and_anchors(text, meta=self.meta)
        self.assertTrue(passed, f"Expected pass but got failures: {failures}")
        self.assertEqual(len(failures), 0)

    def test_persona_anchors_fail(self) -> None:
        """Section 3 anchors starting with attendee names must fail with SECTION_3_ANCHOR_PERSONA_COLLAPSE."""
        text = """# Subject: Partner Sync -- Executive Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
Google:
- Alex Brannan (Partner Lead)
- Dianne Eldridge (CE Director)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)
- Drew Oakley (Client Service Lead)

# Meeting Objective
The team aligned on partnership initiatives and resolving enterprise cloud licensing blockers.

# Key Discussion Points & Findings

- **Amit Ayer raised the HR work overlap, and Robson N. Silva clarified the SOW:** Amit proposed avoiding duplicate responses to Apex Global HR requests. [00:05:00]
- **Drew Oakley mapped the fragmented HR architecture across ServiceNow and SuccessFactors:** Dan walked through the connector landscape. [00:10:00]
- **Dianne Eldridge challenged the value of enablement sessions without access:** Dianne expressed frustration with training users who lack licenses. [00:20:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify enablement access | Amit Ayer | 10/05/2026 | 00:25:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dianne Eldridge — Google, CE Director
- **Enablement**: Challenged lack of access.
"""
        passed, failures = validate_c21_depth_floors_and_anchors(text, meta=self.meta)
        self.assertFalse(passed)
        self.assertTrue(any("SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f for f in failures))

    def test_verbatim_overlap_excessive_fails(self) -> None:
        """4-gram overlap exceeding 35% between Section 3 and Appendix must fail."""
        repeated_sentence = (
            "The enterprise HR architecture consolidation requires establishing a unified front door "
            "through HR for You, integrating ServiceNow and SuccessFactors with dedicated security controls "
            "for nuclear tenant isolation and protected personnel data."
        )
        text = f"""# Subject: Partner Sync -- Executive Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
Google:
- Alex Brannan (Partner Lead)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Review enterprise architecture and platform controls.

# Key Discussion Points & Findings

- **Apex Global Architecture Review:** {repeated_sentence} {repeated_sentence} [00:10:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify enablement access | Amit Ayer | 10/05/2026 | 00:25:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Amit Ayer — PartnerAdvisory, Lead Client Partner
- **Architecture Stance**: {repeated_sentence} {repeated_sentence}
- **Governance Focus**: Amit emphasized strict adherence to enterprise security rules.
"""
        passed, failures = validate_c21_depth_floors_and_anchors(text, meta=self.meta)
        self.assertFalse(passed)
        self.assertTrue(any("SECTION_3_APPENDIX_OVERLAP_EXCESSIVE" in f for f in failures))

    def test_duplicate_quotes_fail(self) -> None:
        """Verbatim attendee quotes duplicated across Section 3 and Appendix must fail."""
        quote1 = "“if they don't even have basic access for it, what are we enabling?”"
        quote2 = "“you cannot enable anybody if they do not have active licenses ready”"
        text = f"""# Subject: Partner Sync -- Executive Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
Google:
- Dianne Eldridge (CE Director)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Enablement readiness discussion.

# Key Discussion Points & Findings

- **Enablement Pre-Flight Constraints:** Leadership stated {quote1} and emphasized that {quote2}. [00:15:00]
- **Security Isolation Controls:** Dedicated project setup must precede user provisioning. [00:20:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify enablement access | Amit Ayer | 10/05/2026 | 00:25:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dianne Eldridge — Google, CE Director
- **Access Challenge**: Dianne challenged the team directly: {quote1}.
- **Adoption Reality**: She reiterated that {quote2} to reinforce license prerequisite standards.
"""
        passed, failures = validate_c21_depth_floors_and_anchors(text, meta=self.meta)
        self.assertFalse(passed)
        self.assertTrue(any("SECTION_3_DUPLICATE_QUOTES" in f for f in failures))

    def test_contract_anti_patterns_present(self) -> None:
        """Verify OUTPUT2_CONTRACT specifies anti-patterns for persona anchors and overlap."""
        sec3_spec = OUTPUT2_CONTRACT.sections.get("Key Discussion Points & Findings")
        self.assertIsNotNone(sec3_spec)
        anti_pats = sec3_spec.anti_patterns
        self.assertTrue(any("persona" in ap.lower() for ap in anti_pats))
        self.assertTrue(any("overlap" in ap.lower() for ap in anti_pats))

    def test_gate2_integration_fails_on_persona_anchors(self) -> None:
        """Integration: run_qa_gate_2 must fail Gate 2.13 when Section 3 contains persona anchors."""
        text = """# Subject: Brief | 09/29/2026

**Date:** 09/29/2026
**Time:** 03:00 PM - 03:45 PM ET
**Duration:** ~45 minutes
**Attendees:**
Google:
- Dianne Eldridge (CE Director)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)

# Meeting Objective
Session on architecture and enablement.

# Key Discussion Points & Findings

- **Amit Ayer raised the HR work overlap, and Robson Silva clarified the SOW:** Amit noted the risk. [00:05:00]
- **Dianne Eldridge challenged the value of enablement sessions without access:** Dianne expressed concerns. [00:10:00]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Verify access | Amit Ayer | 10/05/2026 | 00:05:00 | Confirmed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

# Dianne Eldridge — Google, CE Director
- **Access Challenge**: Dianne pushed back.
- **Licensing Stance**: Clear prerequisites required.

# Amit Ayer — PartnerAdvisory, Lead Client Partner
- **Architecture**: Core systems setup.
- **Coordination**: Align with Google team.
"""
        res = run_qa_gate_2(text, meta=self.meta)
        self.assertIn("Gate_2_13_Section3_Thematic_Decoupling", res["gates"])
        self.assertFalse(res["gates"]["Gate_2_13_Section3_Thematic_Decoupling"]["passed"])


if __name__ == "__main__":
    unittest.main()
