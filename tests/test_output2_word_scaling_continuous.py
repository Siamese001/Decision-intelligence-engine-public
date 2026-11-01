"""
tests/test_output2_word_scaling_continuous.py

Validates continuous duration scaling for Output 02 word limits:
1. Zero step cliffs between adjacent durations (59m vs 60m, 89m vs 90m).
2. Smooth linear floor scaling (8 words/minute, clamped to [100, 600]).
3. Thorough recaps exceeding advisory target ceilings pass Gate 2.5 without failure.
4. Superficial recaps below the floor fail closed.
5. AdversarialRecapAuditor shares the exact same duration floor.
"""
from __future__ import annotations

import pytest

from dual_pipeline.transcript_qa_core.gate2 import _check_word_count_bounds, run_qa_gate_2
from dual_pipeline.transcript_qa_core.signal_density import (
    RigorTier,
    compute_duration_word_bounds,
    compute_technical_signal_density,
)
from dual_pipeline.llm_judge_core.adversarial_recap_auditor import _audit_density_and_sections
from dual_pipeline.prompts import OUTPUT2_CONTRACT, MeetingArchetype


def test_no_step_cliffs_across_durations():
    """Verify that adjacent minutes have continuous smooth scaling without step cliffs."""
    durations = [10, 15, 25, 30, 45, 58, 59, 60, 61, 88, 89, 90, 91, 120, 150, 180]
    prev_min = 0
    for d in durations:
        min_w, max_w, max_e, max_t = compute_duration_word_bounds(d, RigorTier.TIER_2_STRATEGIC_OPERATIONAL)
        assert min_w >= prev_min, f"Floor decreased at duration {d}m"
        assert max_w > min_w, f"Ceiling not strictly greater than floor at {d}m"
        assert max_w - min_w >= 1200, f"Insufficient breathing room at {d}m"
        # Check no cliff > 15 words between adjacent minutes
        if d in (59, 60, 89, 90):
            prev_bound = compute_duration_word_bounds(d - 1, RigorTier.TIER_2_STRATEGIC_OPERATIONAL)[0]
            assert min_w - prev_bound <= 8, f"Unexpected step cliff at {d}m: {min_w - prev_bound} words"
        prev_min = min_w


def test_thorough_recap_exceeding_advisory_ceiling_passes_gate_2_5():
    """Proves that a 2,500-word recap for a 25m meeting passes Gate 2.5 (advisory only)."""
    passed, fails = _check_word_count_bounds(word_count=2500, meeting_dur=25)
    assert passed is True
    assert len(fails) == 0


def test_89m_session_with_3500_words_passes_gate_2_5():
    """Proves that an 89-minute session with 3,500 words passes Gate 2.5 cleanly."""
    passed, fails = _check_word_count_bounds(word_count=3500, meeting_dur=89)
    assert passed is True
    assert len(fails) == 0


def test_superficial_recap_below_floor_fails_gate_2_5():
    """Proves that a superficial recap below the duration-scaled floor still fails closed."""
    # 60m meeting has floor = 480 words
    passed, fails = _check_word_count_bounds(word_count=200, meeting_dur=60)
    assert passed is False
    assert any("too light" in f and "480 words floor" in f for f in fails)


def test_tier3_lightweight_status_scaling():
    """Tier 3 lightweight status scales at 4 words/minute, clamped to [100, 300]."""
    min_w_15, max_w_15, _, _ = compute_duration_word_bounds(15, RigorTier.TIER_3_LIGHTWEIGHT_STATUS)
    assert min_w_15 == 100  # 15 * 4 = 60 -> clamped to 100

    min_w_45, max_w_45, _, _ = compute_duration_word_bounds(45, RigorTier.TIER_3_LIGHTWEIGHT_STATUS)
    assert min_w_45 == 180  # 45 * 4 = 180

    min_w_90, max_w_90, _, _ = compute_duration_word_bounds(90, RigorTier.TIER_3_LIGHTWEIGHT_STATUS)
    assert min_w_90 == 300  # 90 * 4 = 360 -> clamped to 300


def test_adversarial_recap_auditor_aligned_with_signal_density():
    """Verify that AdversarialRecapAuditor shares the signal density floor without false indictment."""
    profile = compute_technical_signal_density(
        "Standard architecture transcript discussion with OCR and Maximo systems.",
        metadata={"duration_minutes": 60},
    )
    # 60m session has min_narrative_words = 480
    assert profile.min_narrative_words == 480

    # 490 words should NOT be indicted as light
    defects, findings = _audit_density_and_sections(
        dur_min=60,
        word_count=490,
        rigor_tier=profile.rigor_tier.value,
        arch=MeetingArchetype.GENERAL,
        has_synthesis=False,
        wedge_words=0,
        output2_contract=OUTPUT2_CONTRACT,
        min_floor=profile.min_narrative_words,
    )
    assert not any("Recap word count is light" in d for d in defects)
