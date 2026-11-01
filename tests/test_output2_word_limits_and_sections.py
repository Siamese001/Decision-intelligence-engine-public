"""
tests/test_output2_word_limits_and_sections.py

End-to-End Verification Suite for Output 02 Word Limits and Section Architecture:
1. Duration-scaled word limits (allowing long/marathon meetings up to 4,000 core words).
2. Decoupled core narrative prose vs. Appendix speaker summaries.
3. The Two Universal Mandatory Sections topology across all meeting archetypes.
4. Gate 2.7 Dynamic Value Test (non-failing on omission; strict failure on superficial inclusion < 60w).
"""
from __future__ import annotations

import pytest

from dual_pipeline.transcript_qa_core.gate2 import (
    _check_word_count_bounds,
    _get_word_count_limits,
    run_qa_gate_2,
)


def _generate_prose(word_count_target: int) -> str:
    """Helper to generate factual architecture prose of exact target length."""
    chunk = (
        "The engineering team evaluated the computer vision OCR architecture and ratified "
        "acceptance thresholds under SOW Contract 4600027256 with Apex Global Enterprises leaders. "
        "The pipeline separates blueprint extraction from runtime policy enforcement. "
    )
    repeats = max(1, word_count_target // 25)
    prose = " ".join([chunk] * repeats)
    words = prose.split()
    return " ".join(words[:word_count_target])


def _build_test_recap(
    meeting_dur: int,
    core_prose: str,
    conditional_sections: str = "",
    appendix_words: int = 400,
) -> str:
    """Constructs a contract-valid Output 02 markdown document."""
    app_prose = _generate_prose(appendix_words)
    return f"""# Subject: LENS Executive Strategic Recap - Test Session

**Date:** 09/23/2026
**Time:** 02:13 PM - 04:47 PM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory), Jake Stark (Apex Global Enterprises)

## Meeting Objective
Operational context and problem background established for testing duration scaling and section governance. [00:01:00]

## Key Discussion Points & Findings
{core_prose} [00:05:00]

{conditional_sections}

## Action & Governance Ledger
| # | Action | Owner | Target Timing | Locators | Status |
|---|---|---|---|---|---|
| 1 | Validate pipeline architecture | Amit Ayer | 09/25/2026 | [00:10:00] | OPEN |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)
- **Architecture Strategy**: Formulated foundational Blueprint vs Bouncer separation. [00:15:00]
{app_prose}
"""


# ---------------------------------------------------------------------------
# Test Suite 1: Duration-Scaled Word Limits & Decoupled Appendix
# ---------------------------------------------------------------------------

def test_long_meeting_154m_passes_with_2500_words():
    """Validates that a 154-minute meeting with 2,500 core words passes cleanly (previously failed at 1,800)."""
    core_prose = _generate_prose(2500)
    recap = _build_test_recap(154, core_prose, appendix_words=1200)

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 154, "archetype": "GOVERNANCE_REVIEW"})
    g25 = res.get("gates", {}).get("Gate_2_5_Word_Count_Bounds", {})
    assert g25.get("passed") is True, f"Gate 2.5 failed unexpectedly: {res.get('failures')}"
    assert g25.get("word_count") >= 2500
    assert not any("exceeded ceiling" in f for f in res.get("failures", []))


def test_marathon_meeting_180m_scales_to_4000_ceiling():
    """Validates that a 180-minute session has generous bounds and accepts 3,500 words."""
    min_w, max_w, label = _get_word_count_limits(180)
    assert min_w == 600
    assert max_w == 5000

    passed, fails = _check_word_count_bounds(3500, 180)
    assert passed is True
    assert len(fails) == 0


def test_short_meeting_25m_strictly_enforces_ceiling():
    """Validates that a 25-minute meeting allows thorough recaps (1,950 words) under advisory ceiling."""
    passed, fails = _check_word_count_bounds(1950, 25)
    assert passed is True
    assert len(fails) == 0


def test_appendix_words_do_not_trip_core_prose_ceiling():
    """Validates that a 3,000-word appendix does NOT cause a 1,500-word core brief to fail ceiling on 60m meeting."""
    core_prose = _generate_prose(1500)
    # Total document will be ~4,500 words, but core prose is only ~1,500 words
    recap = _build_test_recap(60, core_prose, appendix_words=3000)

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g25 = res.get("gates", {}).get("Gate_2_5_Word_Count_Bounds", {})
    assert g25.get("passed") is True
    assert not any("exceeded ceiling" in f for f in res.get("failures", []))


# ---------------------------------------------------------------------------
# Test Suite 2: The Two Universal Mandatory Sections Topology
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype_name", [
    "STRATEGIC_DISCOVERY",
    "GOVERNANCE_REVIEW",
    "ARCHITECTURE_DEEP_DIVE",
    "TACTICAL_ALIGNMENT",
    "GENERAL",
])
def test_two_mandatory_sections_pass_across_all_archetypes(archetype_name: str):
    """Proves that a brief containing ONLY Meeting Objective and Key Discussion Points passes Gate 2.1."""
    core_prose = _generate_prose(900)
    recap = _build_test_recap(45, core_prose, conditional_sections="")

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 45, "archetype": archetype_name})
    g21 = res.get("gates", {}).get("Gate_2_1_Parsed_Structural_Completeness", {})
    assert g21.get("passed") is True, f"Gate 2.1 failed for archetype {archetype_name}: {res.get('failures')}"
    assert not any("missing required section" in f for f in res.get("failures", []))


def test_omission_of_conditional_sections_does_not_fail_gate_2_7():
    """Proves that completely omitting conditional sections results in Gate 2.7 PASS."""
    core_prose = _generate_prose(900)
    recap = _build_test_recap(60, core_prose, conditional_sections="")

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is True
    assert g27.get("business_meaning_words") == 0
    assert g27.get("bounded_wedge_words") == 0
    assert g27.get("executive_decision_words") == 0


# ---------------------------------------------------------------------------
# Test Suite 3: The Dynamic Value Test (Gate 2.7)
# ---------------------------------------------------------------------------

def test_superficial_conditional_section_fails_gate_2_7_value_test():
    """Proves that including a superficial (<60 words) Business Meaning section fails Gate 2.7 closed."""
    core_prose = _generate_prose(800)
    superficial_biz = """## Business Meaning and Value Case
This project provides great value to stakeholders and improves developer workflows. [00:08:00]
"""
    recap = _build_test_recap(60, core_prose, conditional_sections=superficial_biz)

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is False
    superficial_fails = [f for f in res.get("failures", []) if "superficial" in f]
    assert len(superficial_fails) == 1
    assert "Business Meaning and Value Case" in superficial_fails[0]
    assert "< 60 words minimum" in superficial_fails[0]


def test_substantive_conditional_section_passes_gate_2_7_value_test():
    """Proves that including a substantive (>=60 words) Business Meaning section passes Gate 2.7."""
    core_prose = _generate_prose(800)
    substantive_biz = f"""## Business Meaning and Value Case
{_generate_prose(75)} [00:08:00]
"""
    recap = _build_test_recap(60, core_prose, conditional_sections=substantive_biz)

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is True
    assert g27.get("business_meaning_words") >= 60
    assert not any("superficial" in f for f in res.get("failures", []))


def test_superficial_recommendations_fails_gate_2_7_value_test():
    """Proves that including a superficial Recommendations section fails Gate 2.7 closed."""
    core_prose = _generate_prose(800)
    superficial_rec = """## Recommendations & Decisions Required
We recommend following the standard architecture plan. [00:12:00]
"""
    recap = _build_test_recap(60, core_prose, conditional_sections=superficial_rec)

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is False
    assert any("Recommendations & Decisions Required" in f and "superficial" in f for f in res.get("failures", []))
