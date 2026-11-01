"""tests/test_wave1_forensic_alignment_e2e.py

End-to-End verification of Wave 1 Forensic Transcript alignment:
1. Header metadata (Platform Reference, Diarization Limitation Notice) receives 0 editorial deductions.
2. Corporate affiliation fallbacks ([PartnerAdvisory Speaker], [Client Speaker]) do not trigger generic speaker ratio penalties.
3. Monotonic timestamps and 32s gap notations are validated end-to-end.
4. Corporate affiliation check dynamically respects metadata roster.
"""

from __future__ import annotations

import pytest
from dual_pipeline.llm_judge_core.forensic_checks import (
    check_dimension1_grounding,
    check_generic_speaker_ratio,
    check_corporate_affiliation_leaks,
    compute_attribution_score,
    is_generic_speaker,
    is_corporate_affiliation_fallback,
)
from dual_pipeline.transcript_qa_core.gate1_helpers import (
    _is_generic_speaker,
    _is_corporate_affiliation_fallback,
    compute_released_text_coverage,
)


@pytest.fixture
def compliant_stage01_text() -> str:
    return """# Standup 9/30 Mitch PartnerAdvisory -- HITL-Resolved Transcript | 09/30/2026
**Date:** 09/30/2026
**Time:** 09:30 AM ET
**Project:** NEE Decision Intelligence
**Platform Reference:** Direct 1:1 Peer Coaching & Strategic Alignment Session
**Participants:** Mitch Garstka (Lead Software Engineer, PartnerAdvisory); Amit Ayer (Lead Client Partner, PartnerAdvisory)
> **Diarization Limitation Notice:** Source audio was captured without multi-track speaker separation.

---

## Forensic Transcript

[00:00:00] **Mitch Garstka:** Good morning, Amit. Let's walk through the architecture updates for the ingestion pipeline today.
[00:00:15] **Amit Ayer:** Morning Mitch. Sounds good, let's make sure we review the judge alignment and gate thresholds.
[00:00:32] **[PartnerAdvisory Speaker]:** Exactly. We have to ensure zero false-positive deductions on contract-mandated headers.
[00:00:45] **Mitch Garstka:** The 32-second gap threshold is now synchronized across prompt, QA gate, and judge.
[00:01:05] **[PartnerAdvisory Speaker]:** Verified. We can run the deterministic pre-checks and pass with full marks.

[Natural pause: 00:01:20 – 00:01:55 (no speech)]

[00:01:56] **Amit Ayer:** Excellent. Let's proceed to the next milestone.

---

## Ambiguity Lock Register

| ID | Term / Anchor | Turn Range | Evidence & Cues | Resolution Rationale | Confidence |
|---|---|---|---|---|---|
| 1 | Mitch Garstka | [00:00:00 – 00:01:56] | Lead Software Engineer | Resolved via vocal profile | HIGH |
| 2 | Amit Ayer | [00:00:00 – 00:01:56] | Lead Client Partner | Resolved via conversational context | HIGH |
| 3 | PartnerAdvisory Speaker | [00:00:32 – 00:01:05] | Corporate peer contribution | Grounded to PartnerAdvisory team affiliation | HIGH |
"""


def test_header_metadata_zero_editorial_deduction(compliant_stage01_text: str):
    """Verify Platform Reference and Diarization Limitation Notice do not trigger ERR_UNGROUNDED_EDITORIAL_COMMENTARY."""
    passed, defect_codes, rationales, total_deduct = check_dimension1_grounding(
        compliant_stage01_text, metadata={"clean_transcript": compliant_stage01_text}
    )
    assert "ERR_UNGROUNDED_EDITORIAL_COMMENTARY" not in defect_codes
    assert total_deduct == 0
    assert passed is True


def test_corporate_affiliation_fallback_classification():
    """Verify corporate fallbacks are recognized as organization-grounded and not generic."""
    assert is_corporate_affiliation_fallback("PartnerAdvisory Speaker") is True
    assert is_corporate_affiliation_fallback("[Client Speaker]") is True
    assert is_corporate_affiliation_fallback("**Partner Speaker:**") is True
    assert is_corporate_affiliation_fallback("Speaker") is False

    assert is_generic_speaker("PartnerAdvisory Speaker") is False
    assert is_generic_speaker("[Client Speaker]") is False
    assert is_generic_speaker("Speaker") is True
    assert is_generic_speaker("Speaker 1") is True
    assert is_generic_speaker("Unknown") is True

    # Gate 1 helper consistency
    assert _is_corporate_affiliation_fallback("PartnerAdvisory Speaker") is True
    assert _is_generic_speaker("PartnerAdvisory Speaker") is False
    assert _is_generic_speaker("Speaker 2") is True


def test_corporate_fallbacks_do_not_trigger_generic_ratio_cap():
    """Verify that turns using [PartnerAdvisory Speaker] fallback (>15%) do not trigger ERR_GENERIC_SPEAKER_RATIO."""
    transcript_with_fallbacks = """## Forensic Transcript
[00:00:00] **Mitch Garstka:** Turn 1.
[00:00:10] **Amit Ayer:** Turn 2.
[00:00:20] **[PartnerAdvisory Speaker]:** Turn 3 fallback.
[00:00:30] **[PartnerAdvisory Speaker]:** Turn 4 fallback.
[00:00:40] **Mitch Garstka:** Turn 5.
"""
    is_excessive, ratio, generic_cnt, total_cnt = check_generic_speaker_ratio(transcript_with_fallbacks)
    assert is_excessive is False
    assert generic_cnt == 0
    assert ratio == 0.0


def test_dynamic_roster_affiliation_checking():
    """Verify check_corporate_affiliation_leaks uses metadata roster dynamically."""
    f1_text = """**Participants:** Alice Walker (Software Engineer, Client); Bob Ross (Designer, Unknown)"""
    # Without metadata, default PartnerAdvisory names only
    assert len(check_corporate_affiliation_leaks(f1_text)) == 0

    # With metadata specifying Alice Walker is PartnerAdvisory
    meta = {"roster": [{"name": "Alice Walker", "organization": "PartnerAdvisory"}]}
    leaks = check_corporate_affiliation_leaks(f1_text, metadata=meta)
    assert len(leaks) == 1
    assert "Alice Walker" in leaks[0]


def test_end_to_end_attribution_score_on_compliant_artifact(compliant_stage01_text: str):
    """Verify full Dimension 2 attribution evaluation on compliant Stage 01 output."""
    meta = {
        "roster": [
            {"name": "Mitch Garstka", "organization": "PartnerAdvisory"},
            {"name": "Amit Ayer", "organization": "PartnerAdvisory"},
        ]
    }
    score, defects, rationales = compute_attribution_score(compliant_stage01_text, metadata=meta)
    assert "ERR_GENERIC_SPEAKER_RATIO" not in defects
    assert "ERR_SPEAKER_COLLAPSE" not in defects
    assert score >= 20
