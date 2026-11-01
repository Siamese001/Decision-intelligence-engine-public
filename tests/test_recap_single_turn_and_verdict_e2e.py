"""
tests/test_recap_single_turn_and_verdict_e2e.py

Class B E2E Receipt Test for Wave 1:
Validates single-turn attendee calibration, C-21 depth floor exemption,
and Output 2 judge scorecard verdict schema (PASS >= 90, HITL_REQUIRED 85-89, FAIL < 85).
"""
import pytest
from dual_pipeline.prompts.contracts.output2_contract import validate_c21_depth_floors_and_anchors
from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY


@pytest.mark.e2e
def test_single_turn_attendee_c21_depth_floor_pass():
    """Reproduces PartnerAdvisory/GCP 10/13/2026 recap structure where Bryan Armalavage spoke only once."""
    recap_markdown = """# Subject: Weekly PartnerAdvisory & GCP Alignment -- Executive Strategic & Technical Brief | 10/13/2026

**Date:** 10/13/2026
**Duration:** ~25 minutes
**Attendees:**
Google:
- Alex Brannan (Partner Alliances Manager)
- Rusty Gattmann (Strategic Account Executive)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)
- Bryan Armalavage (Client Service Lead)

# Meeting Objective
This weekly partner alignment session convened PartnerAdvisory and Google Cloud leadership to review joint enterprise initiatives across Apex Global Enterprises following PartnerAdvisory's conditional automation RFP selection. Attendees reviewed staffing allocations and landing-zone bottlenecks.

# Key Discussion Points & Findings
- **Amit Ayer described the automation RFP award as conditional on resourcing commitments:** Amit confirmed that Apex Global communicated intent to award the flagship modernization initiative, emphasizing delivery starts in Q1. [00:03:30]
- **Rusty Gattmann aligned on PartnerAdvisory certified practice delivery:** Rusty confirmed Google's policy requiring certified practice personnel rather than paper subcontracting. [00:06:08]

# Action & Governance Ledger

| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |
|---|---|---|---|---|---|---|
| 1 | Action | Discuss staffing allocations with client leadership. | Amit Ayer | 10/20/2026 | [00:05:19] | Proposed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

#### Amit Ayer — PartnerAdvisory, Lead Client Partner
- **Automation RFP Award Validation, Contingency Management & Resourcing Strategy**: Amit confirmed that Apex Global intends to award PartnerAdvisory the flagship enterprise automation RFP, estimating the award status at 85 to 90 percent contingent while maintaining strict procedural caution until official contracts are formalized. He emphasized the operational challenges associated with rapidly mobilizing a specialized software engineering cohort, noting that Drew Oakley and the account leadership team must present verifiable proof of full-time versus part-time resource allocations directly to Apex Global directors this week. [00:03:30]
- **Partner Delivery Governance, Professional Services Boundaries & Dedicated GCP Practice Mobilization**: In addressing the operational mechanics of joint delivery, Amit explicitly clarified that PartnerAdvisory will not subcontract Google professional services or list Google Cloud Consulting on paper, honoring Google's institutional policy. Instead, he committed to drawing dedicated, certified cloud architects and senior software engineers directly from PartnerAdvisory's national Google Cloud practice. [00:06:08]

#### Rusty Gattmann — Google, Strategic Account Executive
- **Automation RFP Executive Advocacy, Vendor Qualification & Mandatory GCP Practice Standards**: Rusty opened the working session by warmly congratulating PartnerAdvisory on winning the automation RFP, revealing that Google actively advocated for PartnerAdvisory during executive reviews with Grace Curran, Nathan Sutton, and Apex Global leadership. He shared that Google had evaluated all competing partners in the mix and explicitly communicated to Apex Global decision-makers that PartnerAdvisory was the only vendor possessing the requisite Google Cloud skill sets to execute the modernization roadmap successfully. [00:03:30]
- **Partner Engineering Enablement, Architectural Coaching & Dedicated Resource Integration**: Rusty championed the formal introduction and activation of Vani Eswarappa as the dedicated Google Partner Engineer aligned to PartnerAdvisory. He urged Amit to aggressively leverage Vani for architecture coaching, technical guidance, and workbook design, ensuring that account technical leads Chad Brockman and Robson Silva connect with her to transfer institutional account context. [00:07:53]

#### Alex Brannan — Google, Partner Alliances Manager
- **Partner Engineering Introduction & Technical Alignment**: Alex introduced Vani Eswarappa as the dedicated Google Partner Engineer assigned to support PartnerAdvisory across joint accounts, setting the expectation for collaborative technical engagement and workbook coordination. [00:07:53]
- **Session Startup & Attendee Check-In**: Alex greeted participants during session opening, checking in with PartnerAdvisory and Google colleagues as attendees joined the virtual bridge. [00:00:25]

#### Bryan Armalavage — PartnerAdvisory, Client Service Lead
- **Meeting Attendance & Opening Greeting**: Bryan attended the weekly partner alignment call representing PartnerAdvisory client service leadership, greeting Google and PartnerAdvisory attendees during meeting introductions. [00:00:25]
- **Client Leadership Observation & Presence**: Bryan maintained observer presence alongside PartnerAdvisory and Google account teams throughout discussions on automation staffing, technical coaching, and cloud environment provisioning. [00:00:25]
"""
    meta = {
        "attendees": [
            "Amit Ayer (Lead Client Partner)",
            "Rusty Gattmann (Strategic Account Executive)",
            "Alex Brannan (Partner Alliances Manager)",
            "Bryan Armalavage (Client Service Lead)",
        ]
    }
    passed, failures = validate_c21_depth_floors_and_anchors(recap_markdown, meta)
    assert passed, f"Expected C-21 depth check to pass, but got failures: {failures}"


@pytest.mark.e2e
def test_output2_judge_scorecard_verdict_schema():
    """Validates that Output 2 scorecards accept PASS (>=90) and HITL_REQUIRED (85-89), rejecting PASS_ADVISORY."""
    # 1. Score 85 with HITL_REQUIRED is valid
    card_85_hitl = {
        "status": "PASS",
        "verdict": "HITL_REQUIRED",
        "composite_score": 85.0,
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 30,
            "conditional_extensions": 20,
            "appendix_stakeholder_perspectives": 25,
        },
    }
    is_valid, errs = validate_micro_judge_scorecard(card_85_hitl, output_num=2)
    assert is_valid, f"Expected 85.0 HITL_REQUIRED scorecard to be valid, got errors: {errs}"

    # 2. Score 85 with PASS_ADVISORY is rejected (Output 2 does not allow PASS_ADVISORY)
    card_85_adv = {
        "status": "PASS",
        "verdict": "PASS_ADVISORY",
        "composite_score": 85.0,
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 30,
            "conditional_extensions": 20,
            "appendix_stakeholder_perspectives": 25,
        },
    }
    is_valid, errs = validate_micro_judge_scorecard(card_85_adv, output_num=2)
    assert not is_valid
    assert any("does not match policy expected 'FAIL'" in e for e in errs)

    # 3. Score 90 with PASS is valid
    card_90_pass = {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": 90.0,
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 30,
            "conditional_extensions": 20,
            "appendix_stakeholder_perspectives": 30,
        },
    }
    is_valid, errs = validate_micro_judge_scorecard(card_90_pass, output_num=2)
    assert is_valid, f"Expected 90.0 PASS scorecard to be valid, got errors: {errs}"


@pytest.mark.e2e
def test_prompt_registry_manifest_integrity():
    """Verify that updated judge prompt passes prompt registry manifest verification."""
    registry = CANONICAL_PROMPT_REGISTRY
    prompt_text = registry.get_prompt("JUDGE_RECAP", reload=True)
    assert "Single-Turn & Greeting-Only Attendee Exemption" in prompt_text
    assert '"verdict": "PASS" | "HITL_REQUIRED" | "FAIL"' in prompt_text
    assert registry.verify_manifest()
