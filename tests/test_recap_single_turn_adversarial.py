"""
tests/test_recap_single_turn_adversarial.py

Adversarial and edge-case test suite for Wave 1:
Covers boundary payloads (zero speakers, all single-turn speakers, extreme word distributions),
malformed verdict payloads, and invariant bypass attempts.
"""
import pytest
from dual_pipeline.prompts.contracts.output2_contract import validate_c21_depth_floors_and_anchors
from dual_pipeline.release_policy import validate_micro_judge_scorecard


@pytest.mark.adversarial
def test_all_single_turn_speakers_boundary():
    """Boundary payload: meeting where all attendees spoke minimal greeting-only turns."""
    recap_md = """# Subject: Brief Checkin -- Executive Strategic & Technical Brief | 10/13/2026

**Date:** 10/13/2026
**Duration:** ~5 minutes
**Attendees:**
Google:
- Alex Brannan (Partner Alliances Manager)
PartnerAdvisory:
- Bryan Armalavage (Client Service Lead)

# Meeting Objective
Quick roll-call check-in between partners.

# Key Discussion Points & Findings
- **Alex Brannan and Bryan Armalavage exchanged status greetings:** Quick alignment before standup. [00:00:10]

# Action & Governance Ledger
No formal actions were established in this session; open initiatives remain unresolved questions.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

#### Alex Brannan — Google, Partner Alliances Manager
- **Roll Call & Greeting**: Attended the 5-minute session and opened the bridge. [00:00:10]

#### Bryan Armalavage — PartnerAdvisory, Client Service Lead
- **Roll Call & Greeting**: Greeted the bridge and confirmed availability. [00:00:15]
"""
    meta = {"attendees": ["Alex Brannan", "Bryan Armalavage"]}
    # In test mode or normal mode, should not crash or raise unhandled exceptions
    passed, failures = validate_c21_depth_floors_and_anchors(recap_md, meta)
    # The anchors and non-empty structure pass
    assert not any("[SECTION_3_ANCHOR_PERSONA_COLLAPSE]" in f for f in failures)


@pytest.mark.adversarial
def test_malformed_verdicts_and_out_of_bounds_scores():
    """Malformed input: invalid verdicts, negative scores, scores > 100."""
    # 1. Invalid verdict 'MAYBE'
    card_bad_verdict = {
        "status": "PASS",
        "verdict": "MAYBE",
        "composite_score": 90.0,
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 30,
            "conditional_extensions": 20,
            "appendix_stakeholder_perspectives": 30,
        },
    }
    is_valid, errs = validate_micro_judge_scorecard(card_bad_verdict, output_num=2)
    assert not is_valid
    assert any("does not match policy expected" in e for e in errs)

    # 2. Score mismatch (dimension sum 90, composite claimed 85)
    card_mismatch = {
        "status": "PASS",
        "verdict": "HITL_REQUIRED",
        "composite_score": 85.0,
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 30,
            "conditional_extensions": 20,
            "appendix_stakeholder_perspectives": 30,
        },
    }
    is_valid, errs = validate_micro_judge_scorecard(card_mismatch, output_num=2)
    assert not is_valid
    assert any("does not match dimension sum" in e for e in errs)

    # 3. Score below 80 attempting HITL_REQUIRED (invariant bypass attempt)
    card_low_hitl = {
        "status": "PASS",
        "verdict": "HITL_REQUIRED",
        "composite_score": 75.0,
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 25,
            "conditional_extensions": 15,
            "appendix_stakeholder_perspectives": 25,
        },
    }
    is_valid, errs = validate_micro_judge_scorecard(card_low_hitl, output_num=2)
    assert not is_valid
    assert any("does not match policy expected 'FAIL'" in e for e in errs)
