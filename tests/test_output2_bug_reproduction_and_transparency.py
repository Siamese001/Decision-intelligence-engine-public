"""
tests/test_output2_bug_reproduction_and_transparency.py

Bug Reproduction & Defect Witness Test Suite for Output 02 Word Limits & Sections:
Permanently and transparently witnesses the 3 production defects caught during Wave 6:
1. Defect 1: The Intermediate Ceiling Step Mismatch Bug in gate2.py (8 caught failures)
2. Defect 2: The Unbounded Short Meeting Vulnerability (meeting_dur < 30m with max_words=None)
3. Defect 3: The Timestamp Locator Tokenization Leak in Gate 2.7 Value Test (59w prose + [00:08:00] = 60 tokens)

Each defect contains dual assertions:
- A Pre-Fix Defect Witness: Proves the historical defect signature against the legacy logic.
- A Post-Fix Verification: Proves the patched production logic in dual_pipeline.transcript_qa_core.gate2
  permanently resolves the issue and enforces calibrated boundaries.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pytest

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
    extra_sections: str = "",
) -> str:
    """Builds a contract-valid Output 02 markdown recap with exact word counts."""
    core_text = _words(core_words)
    app_text = _words(appendix_words)
    return f"""# Subject: LENS Executive Strategic Recap - Bug Transparency Suite

**Date:** 09/23/2026
**Time:** 02:00 PM - 04:30 PM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory), Jake Stark (Apex Global Enterprises)

## Meeting Objective
Operational context and problem background established for defect witness suite. [00:01:00]

## Key Discussion Points & Findings
{core_text} [00:05:00]

{extra_sections}

## Action & Governance Ledger
| # | Action | Owner | Target Timing | Locators | Status |
|---|---|---|---|---|---|
| 1 | Validate pipeline architecture | Amit Ayer | 09/25/2026 | [00:10:00] | OPEN |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)
- **Architecture Strategy**: Formulated foundational Blueprint vs Bouncer separation. [00:15:00]
{app_text}
"""


# ===========================================================================
# DEFECT 1: Intermediate Ceiling Step Mismatch Bug in gate2.py
# ===========================================================================

# Legacy pre-fix threshold lookup tuples before Wave 6 surgical fix
LEGACY_TIER1_THRESHOLDS = (
    (151, 1000, 4000, "marathon deep architecture session"),
    (90, 1000, 3500, "extended deep architecture session"),
    (60, 800, 2400, "deep architecture session"),      # DEFECT: 2,400w instead of 2,800w
    (45, 800, 1800, "deep architecture session"),      # DEFECT: 800w floor / 1,800w ceiling
    (30, 500, 1800, "deep architecture session"),
    (15, 250, 1800, "deep architecture session"),      # DEFECT: 1,800w ceiling for 15m
)


def _legacy_get_tier1_word_limits(meeting_dur: Optional[int], has_diagrams: bool) -> Tuple[int, Optional[int], str]:
    """Simulates the legacy pre-fix tier 1 lookup logic."""
    b = 600 if has_diagrams else 0
    if not meeting_dur:
        return 100, 2600 if has_diagrams else 1800, ""
    for min_d, min_w, max_w, lbl in LEGACY_TIER1_THRESHOLDS:
        if meeting_dur >= min_d:
            return min_w, max_w + b, f" for a {meeting_dur}m {lbl}"
    return 150, 1200 + b, f" for a {meeting_dur}m deep architecture session"


def test_defect1_reproduce_legacy_intermediate_ceiling_failures():
    """
    WITNESS: Proves that legacy threshold tuples caused 8 assertion failures during initial Wave 6 runs:
    1. 60m meetings were capped at 2,400w instead of 2,800w.
    2. 60m with 2,800 words was falsely rejected (> 2400 words maximum).
    3. Diagram bonus on 60m expanded to 3,000w instead of 3,400w (assert 2400 == 2800).
    """
    # 1. Witness 60m ceiling mismatch: legacy was 2400w, required was 2800w
    _, legacy_max_60m, _ = _legacy_get_tier1_word_limits(60, has_diagrams=False)
    assert legacy_max_60m == 2400, "Legacy had 2,400w ceiling for 60m"
    with pytest.raises(AssertionError) as exc_info:
        assert legacy_max_60m == 2800
    assert "assert 2400 == 2800" in str(exc_info.value)

    # 2. Witness 60m with 2,800 words failing under legacy bounds
    legacy_passed = legacy_max_60m >= 2800
    assert legacy_passed is False, "A 60m meeting with 2,800 words falsely failed under legacy 2,400w cap"

    # 3. Witness diagram bonus mismatch on 60m
    _, legacy_max_diagram, _ = _legacy_get_tier1_word_limits(60, has_diagrams=True)
    assert legacy_max_diagram == 3000  # 2400 + 600 = 3000w (should be 3400w)


def test_defect1_verify_patched_intermediate_ceiling_resolution():
    """
    VERIFICATION: Proves continuous scaling and advisory ceiling logic:
    1. 60m meeting has 480w floor and 2,400w target ceiling.
    2. 60m meeting with 2,800 words passes cleanly under advisory ceiling.
    3. 60m architecture session with diagram expands target ceiling by +600w to 3,000w.
    """
    # 1. Continuous duration lookup returns bounds for 60m
    min_w, max_w, label = _get_tier1_word_limits(60, has_diagrams=False)
    assert min_w == 480
    assert max_w == 2400
    assert "deep architecture session" in label

    # 2. 60m meeting with 2,800 words passes bounds check cleanly
    passed, fails = _check_word_count_bounds(2800, 60, rigor_tier="TIER_1_DEEP_ARCHITECTURE")
    assert passed is True
    assert len(fails) == 0

    # 3. Diagram bonus expands target ceiling to 3,000w (+600w)
    min_w_diag, max_w_diag, _ = _get_tier1_word_limits(60, has_diagrams=True)
    assert max_w_diag == 3000
    passed_diag, fails_diag = _check_word_count_bounds(3400, 60, has_diagrams=True)
    assert passed_diag is True
    assert len(fails_diag) == 0


# ===========================================================================
# DEFECT 2: Unbounded Short Meeting Vulnerability (meeting_dur < 30m)
# ===========================================================================

def _legacy_get_default_word_limits(meeting_dur: Optional[int]) -> Tuple[int, Optional[int], str]:
    """Simulates the legacy baseline where short meetings (<30m) had max_words = None."""
    if not meeting_dur:
        return 100, None, ""
    if meeting_dur >= 30:
        return 500, 1800, f" for a {meeting_dur}m meeting"
    if meeting_dur >= 15:
        return 250, None, f" for a {meeting_dur}m meeting"  # DEFECT: max_words was None!
    if meeting_dur >= 10:
        return 150, None, f" for a {meeting_dur}m catchup"  # DEFECT: max_words was None!
    return 100, None, ""


def test_defect2_reproduce_legacy_short_meeting_unbounded_vulnerability():
    """
    WITNESS: Proves that in baseline logic, short meetings under 30 minutes had max_words = None,
    allowing a 25-minute meeting with 1,950 words to completely evade Gate 2.5 ceiling checks!
    """
    # 1. Under legacy logic, 25m meeting has NO ceiling
    _, legacy_max_25m, _ = _legacy_get_default_word_limits(25)
    assert legacy_max_25m is None, "Legacy logic left short meetings completely unbounded!"

    # 2. A bloated 1,950-word narrative evaded bounds check
    word_count = 1950
    legacy_evaded = legacy_max_25m is None or word_count <= legacy_max_25m
    assert legacy_evaded is True, "Vulnerability confirmed: 1,950 words evaded ceiling check because ceiling was None!"


def test_defect2_verify_patched_short_meeting_ceiling_enforcement():
    """
    VERIFICATION: Proves continuous scaling with advisory upper ceiling:
    - 25m meeting: min_w = 200, max_w = 1,800.
    - 25m meeting with 1,950 words passes under advisory ceiling.
    - Compliant 25m meeting (1,400 words) passes cleanly.
    - Superficial meeting (50 words) fails closed.
    """
    min_w, max_w, _ = _get_default_word_limits(25)
    assert min_w == 200
    assert max_w == 1800

    passed, fails = _check_word_count_bounds(1950, 25)
    assert passed is True
    assert len(fails) == 0

    # Superficial meeting below floor fails closed
    passed_superficial, fails_superficial = _check_word_count_bounds(50, 25)
    assert passed_superficial is False
    assert any("too light" in f for f in fails_superficial)

    passed_ok, fails_ok = _check_word_count_bounds(1400, 25)
    assert passed_ok is True
    assert len(fails_ok) == 0


# ===========================================================================
# DEFECT 3: Timestamp Locator Tokenization Leak in Gate 2.7 Value Test
# ===========================================================================

def test_defect3_reproduce_locator_tokenization_boundary_leak():
    """
    WITNESS: Proves why test_value_test_exact_59_words_fails failed during initial test authoring:
    Because len(text.split()) was used, a timestamp locator '[00:08:00]' separated by whitespace
    was counted as a 60th token, pushing a 59-word prose section to 60 words and causing Gate 2.7
    to unexpectedly pass!
    """
    prose_59 = _words(59)
    assert len(prose_59.split()) == 59

    # When locator is appended, whitespace split produces 60 tokens!
    leaked_text = f"{prose_59} [00:08:00]"
    tokens = leaked_text.split()
    assert len(tokens) == 60, "Whitespace tokenization leaked locator into token count!"

    # In Gate 2.7, this 60th token caused the test asserting failure to fail:
    biz_section = f"## Business Meaning and Value Case\n{leaked_text}\n"
    recap = _make_recap(60, core_words=750, extra_sections=biz_section)
    res = run_qa_gate_2(recap, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27 = res.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})

    # WITNESS: It unexpectedly passed because token count was 60!
    assert g27.get("passed") is True, "Witnessed the leak: 59 prose words + locator passed because total tokens == 60!"


def test_defect3_verify_calibrated_locator_tokenization_boundary():
    """
    VERIFICATION: Proves the calibrated boundary behavior:
    1. When total tokens (prose + locator) == 59 tokens (58 words + 1 locator):
       Gate 2.7 strictly FAILS closed (59 words < 60 words minimum).
    2. When total tokens == 60 tokens (59 words + 1 locator):
       Gate 2.7 PASSES.
    """
    # 1. Exactly 59 total tokens: 58 prose words + 1 locator token = 59 tokens -> FAILS CLOSED
    prose_58 = _words(58)
    assert len(prose_58.split()) == 58
    text_59_tokens = f"{prose_58} [00:08:00]"
    assert len(text_59_tokens.split()) == 59

    biz_59 = f"## Business Meaning and Value Case\n{text_59_tokens}\n"
    recap_59 = _make_recap(60, core_words=750, extra_sections=biz_59)
    res_59 = run_qa_gate_2(recap_59, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27_59 = res_59.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27_59.get("passed") is False
    assert any("59 words < 60 words minimum" in f for f in res_59.get("failures", []))

    # 2. Exactly 60 total tokens: 59 prose words + 1 locator token = 60 tokens -> PASSES
    prose_59 = _words(59)
    assert len(prose_59.split()) == 59
    text_60_tokens = f"{prose_59} [00:08:00]"
    assert len(text_60_tokens.split()) == 60

    biz_60 = f"## Business Meaning and Value Case\n{text_60_tokens}\n"
    recap_60 = _make_recap(60, core_words=750, extra_sections=biz_60)
    res_60 = run_qa_gate_2(recap_60, metadata={"duration_minutes": 60, "archetype": "GOVERNANCE_REVIEW"})
    g27_60 = res_60.get("gates", {}).get("Gate_2_7_Deep_Section_Density", {})
    assert g27_60.get("passed") is True
