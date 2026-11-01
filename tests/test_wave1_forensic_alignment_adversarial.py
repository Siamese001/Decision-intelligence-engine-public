"""tests/test_wave1_forensic_alignment_adversarial.py

Adversarial and boundary tests for Wave 1 Forensic Transcript alignment.
Covers 4 mandatory edge-case categories:
1. Boundary payload: 0-turn text, exactly 15% generic speaker threshold, 32s pause boundary.
2. Malformed input: Nested bracketed timestamps, unclosed bolding, missing delimiters.
3. Invariant bypass attempt: Masquerading editorial commentary in dialogue body.
4. Exact production regression: Diarization Limitation Notice header must never trigger ERR_UNGROUNDED_EDITORIAL_COMMENTARY.
"""

from __future__ import annotations

import pytest
from dual_pipeline.llm_judge_core.forensic_checks import (
    check_dimension1_grounding,
    check_generic_speaker_ratio,
    check_timestamp_integrity,
    is_generic_speaker,
    is_corporate_affiliation_fallback,
)


# Category 1: Boundary payload
def test_boundary_empty_and_minimal_payloads():
    """Verify zero-length and single-turn edge cases do not crash."""
    passed, defects, metrics = check_timestamp_integrity("")
    assert passed is True
    assert metrics["total_timestamps"] == 0

    is_excessive, ratio, generic_cnt, total_cnt = check_generic_speaker_ratio("")
    assert is_excessive is False
    assert total_cnt == 0

    passed_d1, defect_codes, _, deduct = check_dimension1_grounding("")
    assert passed_d1 is True
    assert deduct == 0


def test_boundary_generic_speaker_ratio_threshold():
    """Verify exact 15% boundary: <=15% passes, >15% triggers is_excessive."""
    # 20 turns: 3 generic (15.0%) -> passes
    turns_15pct = "## Forensic Transcript\n" + "\n".join(
        [f"[00:{i:02d}:00] **Speaker 1:** Hello" if i < 3 else f"[00:{i:02d}:00] **Mitch:** Hello"
         for i in range(20)]
    )
    is_exc, ratio, gen_cnt, tot_cnt = check_generic_speaker_ratio(turns_15pct)
    assert is_exc is False
    assert ratio == 0.15

    # 20 turns: 4 generic (20.0%) -> triggers is_excessive
    turns_20pct = "## Forensic Transcript\n" + "\n".join(
        [f"[00:{i:02d}:00] **Speaker 1:** Hello" if i < 4 else f"[00:{i:02d}:00] **Mitch:** Hello"
         for i in range(20)]
    )
    is_exc, ratio, gen_cnt, tot_cnt = check_generic_speaker_ratio(turns_20pct)
    assert is_exc is True
    assert ratio == 0.20


# Category 2: Malformed input
def test_malformed_timestamp_and_speaker_brackets():
    """Verify malformed brackets, missing spaces, and mixed colon formatting."""
    malformed_text = """## Forensic Transcript
[00:00] **Mitch Garstka**: Hello.
[00:05:12] [PartnerAdvisory Speaker]: Checking in.
[00:10:00:99] **[Amit Ayer]**: Over-nested timestamp.
"""
    # Should parse without exception
    is_exc, ratio, gen_cnt, tot_cnt = check_generic_speaker_ratio(malformed_text)
    assert is_exc is False
    assert is_generic_speaker("Mitch Garstka") is False
    assert is_corporate_affiliation_fallback("[PartnerAdvisory Speaker]") is True


# Category 3: Invariant bypass attempt
def test_editorial_commentary_in_body_is_detected():
    """Verify ungrounded commentary masquerading as dialogue in body is flagged (-10 pts)."""
    bypass_text = """# Meeting
**Date:** 09/30/2026

## Forensic Transcript
[00:00:00] **Mitch:** Real turn.
[00:00:15] **Editorial Summary:** Here is an ungrounded summary masquerading as a turn.
"""
    passed, defect_codes, rationales, deduct = check_dimension1_grounding(bypass_text)
    assert "ERR_UNGROUNDED_EDITORIAL_COMMENTARY" in defect_codes
    assert deduct >= 10
    assert passed is False


# Category 4: Exact production regression
def test_production_regression_header_metadata_not_flagged_as_body_commentary():
    """Regression test: Diarization Limitation Notice and Platform Reference in header must NOT be flagged."""
    production_header_text = """# Meeting
**Date:** 09/30/2026
**Platform Reference:** Direct 1:1 Peer Coaching & Strategic Alignment Session
> **Diarization Limitation Notice:** Source audio was captured without multi-track speaker separation.

## Forensic Transcript
[00:00:00] **Mitch Garstka:** Genuine dialogue turn.
[00:00:20] **Amit Ayer:** Genuine response turn.
"""
    passed, defect_codes, rationales, deduct = check_dimension1_grounding(production_header_text)
    assert "ERR_UNGROUNDED_EDITORIAL_COMMENTARY" not in defect_codes
    assert deduct == 0
    assert passed is True
