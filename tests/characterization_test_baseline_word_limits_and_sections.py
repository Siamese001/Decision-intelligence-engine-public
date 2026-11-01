"""
tests/characterization_test_baseline_word_limits_and_sections.py

Baseline characterization test capturing existing failure signatures before Wave 3 & 4 refactoring:
1. Long meetings (>45m, e.g. 154m) trigger fail-closed ceiling violations when core prose exceeds 1,800 words.
2. Executive recaps with only the Two Mandatory Sections fail closed under GOVERNANCE_REVIEW because
   Business Meaning and Value Case and Recommendations & Decisions Required are artificially marked MANDATORY.
"""
from __future__ import annotations

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


def _make_long_recap_prose(core_words_target: int) -> str:
    """Helper generating synthetic core prose of a given word count."""
    repeats = core_words_target // 20
    chunk = (
        "The delivery team evaluated the computer vision OCR architecture and ratified "
        "acceptance thresholds under SOW Contract 4600027256 with Apex Global Enterprises leaders. "
    )
    return chunk * repeats


def test_resolved_word_ceiling_on_long_meeting():
    """Proves that a 154-minute meeting with 2,100+ core words passes under duration-scaled limits."""
    core_prose = _make_long_recap_prose(2100)
    actual_words = len(core_prose.split())
    assert actual_words >= 2000

    f2_text = f"""# Subject: LENS Executive Recap - Long Working Session

**Date:** 09/23/2026
**Time:** 02:13 PM - 04:47 PM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory)

## Meeting Objective
Executive objective established for testing long session word bounds. [00:01:00]

## Key Discussion Points & Findings
{core_prose} [00:05:00]

## Action & Governance Ledger
| # | Action | Owner | Target Timing | Locators | Status |
|---|---|---|---|---|---|
| 1 | Validate pipeline architecture | Amit Ayer | 09/25/2026 | [00:10:00] | OPEN |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)
- **Architecture Strategy**: Formulated foundational Blueprint vs Bouncer separation. [00:15:00]
"""
    res = run_qa_gate_2(f2_text, metadata={"duration_minutes": 154, "archetype": "GOVERNANCE_REVIEW"})

    # Gate 2.5 now passes cleanly with 4,000w marathon ceiling
    g25 = res.get("gates", {}).get("Gate_2_5_Word_Count_Bounds", {})
    ceiling_errors = [f for f in res.get("failures", []) if "exceeded ceiling" in f]
    print(f"DEBUG: actual_words={actual_words}, g25={g25}, ceiling_errors={ceiling_errors}")
    assert g25.get("passed", False), f"Expected Gate 2.5 to pass under scaled limits: {g25}"
    assert len(ceiling_errors) == 0


def test_resolved_missing_sections_on_governance_review():
    """Proves that a brief with only the Two Mandatory Sections passes under GOVERNANCE_REVIEW."""
    f2_text = """# Subject: LENS Strategic Discovery Brief

**Date:** 09/23/2026
**Time:** 02:13 PM - 04:47 PM ET
**Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory)

## Meeting Objective
Operational context and problem background established for testing two mandatory sections. [00:01:00]

## Key Discussion Points & Findings
The delivery team evaluated the computer vision OCR architecture and ratified acceptance thresholds. [00:05:00]

## Action & Governance Ledger
| # | Action | Owner | Target Timing | Locators | Status |
|---|---|---|---|---|---|
| 1 | Validate pipeline architecture | Amit Ayer | 09/25/2026 | [00:10:00] | OPEN |

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)
- **Architecture Strategy**: Formulated foundational Blueprint vs Bouncer separation. [00:15:00]
"""
    res = run_qa_gate_2(f2_text, metadata={"duration_minutes": 154, "archetype": "GOVERNANCE_REVIEW"})

    g21 = res.get("gates", {}).get("Gate_2_1_Parsed_Structural_Completeness", {})
    missing_errors = [f for f in res.get("failures", []) if "missing required section" in f]
    print(f"DEBUG: g21={g21}, missing_errors={missing_errors}")
    assert g21.get("passed", False), f"Expected Gate 2.1 to pass with two mandatory sections: {g21}"
    assert len(missing_errors) == 0
