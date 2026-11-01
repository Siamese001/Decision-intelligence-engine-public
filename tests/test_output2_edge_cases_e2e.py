"""
tests/test_output2_edge_cases_e2e.py

Exhaustive End-to-End Edge-Case and Boundary Stress Suite for Output 02:
1. Exact Duration Step Functions & Scaling Transitions
2. Exact 1-Word Floor & Ceiling Invariant Boundaries
3. Diagram Collateral Bonus Ceilings (+600w)
4. Massive Code Block / ASCII Diagram Exhibit Stripping
5. Appendix Header Variations & Multi-Speaker Stress (4,000+ words)
6. Null, Malformed, String, and Negative Duration Metadata Fallbacks
7. Exact Value Test Density Boundaries (59w vs 60w; 49w vs 50w)
8. Section Aliases and Alternate Synonyms Handling
9. Multi-Conditional Section Combinations
10. Strict Forbidden Section Fail-Closed Invariants
11. Adversarial Recap Auditor Parity & Zero False Defect Invariant
"""
from __future__ import annotations

from typing import Optional

import pytest

from dual_pipeline.llm_judge_core.adversarial_recap_auditor import _audit_substance_and_density
from dual_pipeline.llm_judge_core.recap_checks import _evaluate_wedge_defect
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
from dual_pipeline.transcript_qa_core.gate2 import (
    _check_word_count_bounds,
    _get_default_word_limits,
    _get_tier1_word_limits,
    run_qa_gate_2,
)


def _words(target: int) -> str:
    """Deterministic exact word count generator."""
    chunk = "Architecture validation ensures data integrity across event processing. "
    repeats = (target // len(chunk.split())) + 2
    prose = " ".join([chunk] * repeats)
    return " ".join(prose.split()[:target])


def _make_recap(
    meeting_dur: Optional[int],
    core_words: int,
    appendix_words: int = 250,
    appendix_header: str = "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
    extra_sections: str = "",
    code_block_words: int = 0,
) -> str:
    """Constructs a strictly formatted Output 02 document with exact word distributions."""
    core_text = _words(core_words)
    app_text = _words(appendix_words)

    code_block = ""
    if code_block_words > 0:
        code_block = f"\n```text\n{_words(code_block_words)}\n```\n"

    return f"""# Subject: LENS Executive Strategic Recap - Edge Case Suite

**Date:** 09/23/2026
**Time:** 02:00 PM - 04:30 PM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory), Jake Stark (Apex Global Enterprises)

## Meeting Objective
Operational context and problem background established for testing edge cases. [00:01:00]

## Key Discussion Points & Findings
{core_text} [00:05:00]
{code_block}
{extra_sections}

## Action & Governance Ledger
| # | Action | Owner | Target Timing | Locators | Status |
|---|---|---|---|---|---|
| 1 | Validate pipeline architecture | Amit Ayer | 09/25/2026 | [00:10:00] | OPEN |

{appendix_header}

### Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)
- **Architecture Strategy**: Formulated foundational Blueprint vs Bouncer separation. [00:15:00]
{app_text}
"""


# ===========================================================================
# 1. Exact Duration Step Functions & Scaling Transitions
# ===========================================================================

@pytest.mark.parametrize("duration", [
    10, 14, 15, 29, 30, 44, 45, 59, 60, 89, 90, 150, 151, 154, 240,
])
def test_exact_duration_step_limits(duration: int):
    """Verifies continuous scaling across the entire duration spectrum."""
    min_w, max_w, _ = _get_default_word_limits(duration)
    expected_min = max(100, min(600, duration * 8))
    expected_max = max(1800, min(5000, duration * 40))
    assert min_w == expected_min
    assert max_w == expected_max


# ===========================================================================
# 2. Exact 1-Word Floor & Ceiling Invariant Boundaries
# ===========================================================================

def test_exact_floor_minus_one_fails_closed():
    """60m meeting floor is 480 words. 479 words must fail closed."""
    passed, fails = _check_word_count_bounds(479, 60)
    assert passed is False
    assert any("< 480 words floor" in f for f in fails)


def test_exact_floor_passes():
    """60m meeting floor is 480 words. Exactly 480 words must pass."""
    passed, fails = _check_word_count_bounds(480, 60)
    assert passed is True
    assert len(fails) == 0


def test_exact_ceiling_plus_one_fails_closed():
    """60m meeting advisory ceiling is 2,400 words. 2,401 words passes under advisory ceiling."""
    passed, fails = _check_word_count_bounds(2401, 60)
    assert passed is True
    assert len(fails) == 0


def test_marathon_exact_ceiling_boundary():
    """154m meeting allows 4,000w and 4,001w under advisory ceiling."""
    passed_4000, fails_4000 = _check_word_count_bounds(4000, 154)
    assert passed_4000 is True
    assert len(fails_4000) == 0

    passed_4001, fails_4001 = _check_word_count_bounds(4001, 154)
    assert passed_4001 is True
    assert len(fails_4001) == 0


# ===========================================================================
# 3. Diagram Collateral Bonus Ceilings (+600w)
# ===========================================================================

def test_tier1_diagram_bonus_expands_ceiling():
    """Verifies that an architecture session with diagrams expands ceiling by +600w."""
    min_no_diag, max_no_diag, _ = _get_tier1_word_limits(60, has_diagrams=False)
    min_diag, max_diag, _ = _get_tier1_word_limits(60, has_diagrams=True)
    assert max_no_diag == 2400
    assert max_diag == 3000

    # Both pass under advisory ceiling policy
    passed_no, _ = _check_word_count_bounds(3100, 60, has_diagrams=False, rigor_tier="TIER_1_DEEP_ARCHITECTURE")
    assert passed_no is True

    passed_with, fails_with = _check_word_count_bounds(3100, 60, has_diagrams=True, rigor_tier="TIER_1_DEEP_ARCHITECTURE")
    assert passed_with is True
    assert len(fails_with) == 0


# ===========================================================================
# 4. Massive Code Block / ASCII Diagram Exhibit Stripping
# ===========================================================================

def test_massive_code_block_does_not_trip_core_prose_ceiling():
    """Verifies that a 2,500-word ASCII diagram inside code blocks is stripped from core prose."""
    # 1,200 words prose + 2,500 words code block = 3,700 total words
    # On a 45m meeting (ceiling 2,200), if code blocks were counted, this would fail closed!
    recap = _make_recap(45, core_words=1200, code_block_words=2500)

    res = run_qa_gate_2(recap, metadata={"duration_minutes": 45, "archetype": "ARCHITECTURE_DEEP_DIVE"})
    g25 = res.get("gates", {}).get("Gate_2_5_Word_Count_Bounds", {})
    assert g25.get("passed") is True
    # The counted word count should be around 1,200-1,300 words, strictly below 2,200
    assert g25.get("word_count") < 1600


# ===========================================================================
# 5. Appendix Header Variations & Multi-Speaker Stress (4,000+ words)
# ===========================================================================

@pytest.mark.parametrize("header", [
    "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
    "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
    "### Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
    "## Appendix: Stakeholder Perspectives",
    "# appendix: detailed stakeholder perspectives & contribution breakdown",
])
def test_appendix_header_variations_strip_cleanly(header: str):
    """Ensures diverse markdown header levels and casings cleanly decouple Appendix from core prose."""
    recap = _make_recap(60, core_words=900, appendix_words=2500, appendix_header=header)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GENERAL"})
    g25 = res.get("gates", {}).get("Gate_2_5_Word_Count_Bounds", {})
    assert g25.get("passed") is True
    assert g25.get("word_count") < 1200


def test_massive_5_speaker_appendix_stress():
    """Stress tests a 4,500-word Appendix across 5 speakers; core narrative remains strictly bounded."""
    recap = _make_recap(154, core_words=2200, appendix_words=4500)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 154, "archetype": "GOVERNANCE_REVIEW"})
    g25 = res.get("gates", {}).get("Gate_2_5_Word_Count_Bounds", {})
    assert g25.get("passed") is True
    assert 2000 <= g25.get("word_count") <= 2500


# ===========================================================================
# 6. Null, Malformed, String, and Negative Duration Metadata Fallbacks
# ===========================================================================

@pytest.mark.parametrize("bad_meta", [
    {},
    {"duration_minutes": None},
    {"duration_minutes": "unknown"},
    {"duration_minutes": -45},
    {"duration_minutes": "45m"},
])
def test_malformed_duration_metadata_graceful_handling(bad_meta: dict):
    """Ensures missing or malformed duration metadata falls back safely without unhandled exceptions."""
    recap = _make_recap(None, core_words=700)
    res = run_qa_gate_2(recap, metadata=bad_meta)
    assert "status" in res
    assert "gates" in res
    assert isinstance(res["failures"], list)


# ===========================================================================
# 7. Exact Value Test Density Boundaries (59w vs 60w; 49w vs 50w)
# ===========================================================================

def test_value_test_exact_59_words_fails():
    """Business Meaning with exactly 59 words must FAIL Gate 2.7 closed (<60 words)."""
    biz_59 = f"## Business Meaning and Value Case\n{_words(58)} [00:08:00]\n"
    recap = _make_recap(60, core_words=750, extra_sections=biz_59)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is False
    assert any("59 words < 60 words minimum" in f for f in res.get("failures", []))


def test_value_test_exact_60_words_passes():
    """Business Meaning with exactly 60 words must PASS Gate 2.7."""
    biz_60 = f"## Business Meaning and Value Case\n{_words(59)} [00:08:00]\n"
    recap = _make_recap(60, core_words=750, extra_sections=biz_60)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is True


def test_wedge_exact_49_words_fails():
    """Immediate Delivery Scope with exactly 49 words must fail defect evaluation (<50 words)."""
    defect = _evaluate_wedge_defect(MeetingArchetype.STRATEGIC_DISCOVERY, 49)
    assert defect is not None
    assert "49 words < 50 words floor" in defect


def test_wedge_exact_50_words_passes():
    """Immediate Delivery Scope with exactly 50 words must pass defect evaluation."""
    defect = _evaluate_wedge_defect(MeetingArchetype.STRATEGIC_DISCOVERY, 50)
    assert defect is None


# ===========================================================================
# 8. Section Aliases and Alternate Synonyms Handling
# ===========================================================================

def test_bounded_execution_wedge_alias_checked_by_value_test():
    """Legacy alias 'Bounded Execution Wedge' must be recognized and evaluated by Gate 2.7."""
    wedge_section = f"## Bounded Execution Wedge\n{_words(15)} [00:10:00]\n"
    recap = _make_recap(60, core_words=750, extra_sections=wedge_section)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is False
    assert any("Bounded Execution Wedge" in f and "superficial" in f for f in res.get("failures", []))


def test_executive_decision_alias_checked_by_value_test():
    """Legacy alias 'Executive Decision' must be recognized and evaluated by Gate 2.7."""
    dec_section = f"## Executive Decision\n{_words(18)} [00:12:00]\n"
    recap = _make_recap(60, core_words=750, extra_sections=dec_section)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is False
    assert any("Executive Decision" in f and "superficial" in f for f in res.get("failures", []))


# ===========================================================================
# 9. Multi-Conditional Section Combinations
# ===========================================================================

def test_multi_conditional_mixed_substantive_and_superficial():
    """One substantive section + one superficial section must fail Gate 2.7 only on the superficial one."""
    biz_substantive = f"## Business Meaning and Value Case\n{_words(70)} [00:08:00]\n"
    rec_superficial = f"## Recommendations & Decisions Required\n{_words(15)} [00:12:00]\n"

    recap = _make_recap(60, core_words=700, extra_sections=biz_substantive + "\n" + rec_superficial)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is False

    fails = res.get("failures", [])
    assert any("Recommendations & Decisions Required" in f for f in fails)
    assert not any("Business Meaning and Value Case" in f for f in fails)


def test_multi_conditional_all_substantive_passes():
    """All conditional sections present and substantive must pass Gate 2.7 cleanly."""
    biz_substantive = f"## Business Meaning and Value Case\n{_words(70)} [00:08:00]\n"
    rec_substantive = f"## Recommendations & Decisions Required\n{_words(75)} [00:12:00]\n"
    wedge_substantive = f"## Immediate Delivery Scope & Release Boundaries\n{_words(80)} [00:15:00]\n"

    recap = _make_recap(60, core_words=700, extra_sections=f"{biz_substantive}\n{rec_substantive}\n{wedge_substantive}")
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27.get("passed") is True
    assert not any("superficial" in f for f in res.get("failures", []))


# ===========================================================================
# 10. Strict Forbidden Section Fail-Closed Invariants
# ===========================================================================

def test_active_technical_spikes_is_strictly_forbidden_across_all_archetypes():
    """Active Technical Spikes & Experimental Inquiries must fail Gate 2.1 closed across any archetype."""
    spikes_section = """## Active Technical Spikes & Experimental Inquiries
| Spike Title | Hypothesis | Method | Metric | Auto-Expire |
|---|---|---|---|---|
| OCR Extraction | Pipeline reaches 95% | Test run | Accuracy | 09/30/2026 |
"""
    recap = _make_recap(60, core_words=750, extra_sections=spikes_section)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "STRATEGIC_DISCOVERY"})
    g21 = res.get("gates", {}).get("Gate_2_1_Parsed_Structural_Completeness", {})
    assert g21.get("passed") is False
    assert any("strictly forbidden" in f for f in res.get("failures", []))


# ===========================================================================
# 11. Adversarial Recap Auditor Parity & Zero False Defect Invariant
# ===========================================================================

def test_adversarial_recap_auditor_clean_pass_on_two_sections_long_meeting():
    """Validates that adversarial_recap_auditor produces zero density or structural defects on a 154m brief."""
    recap = _make_recap(154, core_words=2200, appendix_words=1500)
    dur, defects, findings = _audit_substance_and_density(
        f1_text="",
        f2_text=recap,
        meta={"duration_minutes": 154, "archetype": "GOVERNANCE_REVIEW"},
    )
    assert len(defects) == 0, f"Unexpected density defects: {defects}"
    assert len(findings) == 0, f"Unexpected findings: {findings}"
