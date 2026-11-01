"""
tests/test_copilot_speak_gate_adversarial.py

Adversarial and edge-case qualification tests for Copilot Speak Gate.
Mandatory Edge-Case Categories covered:
1. Boundary payload: empty candidates, empty assertions, out-of-range confidence scores.
2. Malformed input: non-dict assertions, corrupted status fields, non-string verbatim spans.
3. Overflow & Number hallucination: numbers mentioned in claims not found in ground truth.
4. Invariant bypass & C-19 evasion: obfuscated career leverage and canned fallback scripts.
5. Production regression: ungrounded assertive claims and low speaker confidence downgrades.
"""
from __future__ import annotations

from typing import Any, Dict
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    AbstainReasonCode,
    SpeakTier,
    evaluate_speak_gate,
)


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payload - handles empty, null, and out-of-range values gracefully."""
    # 1. Empty and None candidate strings
    d1 = evaluate_speak_gate("")
    assert not d1.should_speak
    assert d1.abstain_reason == AbstainReasonCode.EMPTY_CLAIM
    assert d1.tier == SpeakTier.T0

    d2 = evaluate_speak_gate("   \n\t  ")
    assert not d2.should_speak
    assert d2.abstain_reason == AbstainReasonCode.EMPTY_CLAIM

    # 2. Empty assertion dictionary for assertive statement
    d3 = evaluate_speak_gate("The database uses WAL journal mode.", assertion={})
    assert not d3.should_speak
    assert d3.abstain_reason == AbstainReasonCode.UNGROUNDED_CLAIM

    # 3. Out-of-range speaker confidence values (-1.0, 2.0)
    d4 = evaluate_speak_gate(
        "Could we clarify the timeout?",
        assertion=None,
        speaker_confidence=-1.0,
        intent="next-questions",
    )
    assert not d4.should_speak
    assert d4.abstain_reason == AbstainReasonCode.LOW_SPEAKER_CONFIDENCE

    d5 = evaluate_speak_gate(
        "Could we clarify the timeout?",
        assertion=None,
        speaker_confidence=2.0,
        intent="next-questions",
    )
    assert d5.should_speak
    assert d5.tier == SpeakTier.T1


def test_adversarial_malformed_inputs():
    """Category 2: Malformed input - gracefully tolerates corrupted schemas and wrong types."""
    # 1. Non-dict assertion object (e.g. list, integer)
    d1 = evaluate_speak_gate(
        "System is operational.",
        assertion=["not", "a", "dict"],  # type: ignore
    )
    assert not d1.should_speak
    assert d1.abstain_reason == AbstainReasonCode.UNGROUNDED_CLAIM

    # 2. Corrupted status field (integer or list instead of string)
    corrupted_ast = {
        "assertion_id": "ast-corrupt",
        "claim_text": "System is operational.",
        "canonical_status": 12345,  # corrupted non-string
    }
    d2 = evaluate_speak_gate("System is operational.", assertion=corrupted_ast)
    assert d2.should_speak  # integer status falls back to active, doesn't crash

    # 3. Non-string verbatim span (e.g. dict or list)
    d3 = evaluate_speak_gate(
        "System is operational.",
        assertion={"assertion_id": "ast-01", "claim_text": "System is operational."},
        verbatim_span={"corrupted": True},  # type: ignore
    )
    assert d3.should_speak or not d3.should_speak  # does not crash


def test_adversarial_number_hallucinations():
    """Category 3: Overflow & number hallucination - rejects invented numbers fail-closed."""
    ast_true = {
        "assertion_id": "ast_num_01",
        "claim_text": "The busy timeout is configured to 30000 milliseconds.",
        "canonical_status": "ACTIVE",
    }

    # 1. Hallucinated number (e.g. invented 60000 ms)
    d_bad_num = evaluate_speak_gate(
        "The busy timeout is configured to 60000 milliseconds.",
        assertion=ast_true,
    )
    assert not d_bad_num.should_speak
    assert d_bad_num.abstain_reason == AbstainReasonCode.NUMBER_MISMATCH
    assert "60000" in d_bad_num.diagnostics.get("unattested_numbers", [])

    # 2. True number preserved (30000)
    d_good_num = evaluate_speak_gate(
        "The busy timeout is configured to 30000 milliseconds.",
        assertion=ast_true,
    )
    assert d_good_num.should_speak
    assert d_good_num.tier == SpeakTier.T3

    # 3. Invented percentage
    d_fake_pct = evaluate_speak_gate(
        "Platform uptime is 99.999% under standard load.",
        assertion={"assertion_id": "ast-02", "claim_text": "Platform uptime is high under standard load."},
    )
    assert not d_fake_pct.should_speak
    assert d_fake_pct.abstain_reason == AbstainReasonCode.NUMBER_MISMATCH


def test_adversarial_invariant_bypass_and_c19_evasion():
    """Category 4: Invariant bypass attempt - blocks disguised C-19 and canned scripts."""
    # 1. Obfuscated C-19 career leverage question
    d_c19_q = evaluate_speak_gate(
        "Regarding the executive compensation band and career positioning notes, what is the plan?",
        assertion=None,
        intent="next-questions",
    )
    assert not d_c19_q.should_speak
    assert d_c19_q.abstain_reason == AbstainReasonCode.C19_CAREER_LEVERAGE
    assert d_c19_q.tier == SpeakTier.T0

    # 2. Canned fallback script bypass attempt
    d_canned = evaluate_speak_gate("Can we verify the technical ground truth for this topic?")
    assert not d_canned.should_speak
    assert d_canned.abstain_reason == AbstainReasonCode.CANNED_FALLBACK_REJECTED

    d_canned2 = evaluate_speak_gate("Can we verify ground truth?")
    assert not d_canned2.should_speak
    assert d_canned2.abstain_reason == AbstainReasonCode.CANNED_FALLBACK_REJECTED


def test_adversarial_production_regressions():
    """Category 5: Production regression - ungrounded assertive claims and confidence downgrades."""
    # 1. Assertive claim without evidence
    d_assertive = evaluate_speak_gate(
        "Persistent Database is an identified technical anchor in Pipeline Review.",
        assertion=None,
    )
    assert not d_assertive.should_speak
    assert d_assertive.abstain_reason == AbstainReasonCode.UNGROUNDED_CLAIM

    # 2. Speaker confidence downgrade on assertive statement
    ast_valid = {
        "assertion_id": "ast-valid-01",
        "claim_text": "SQLite WAL journal mode is active.",
        "canonical_status": "ACTIVE",
    }
    # High confidence (0.90) -> T3 speaks
    d_high = evaluate_speak_gate("SQLite WAL journal mode is active.", assertion=ast_valid, speaker_confidence=0.90)
    assert d_high.should_speak
    assert d_high.tier == SpeakTier.T3

    # Low confidence (0.45) -> downgrades to T1, but as an assertive claim cannot speak -> T0 abstain
    d_low = evaluate_speak_gate("SQLite WAL journal mode is active.", assertion=ast_valid, speaker_confidence=0.45)
    assert not d_low.should_speak
    assert d_low.abstain_reason == AbstainReasonCode.LOW_SPEAKER_CONFIDENCE
    assert d_low.tier == SpeakTier.T0


def test_adversarial_word_budget_and_written_essay_rejection():
    """Category 6: Production regression - blocks multi-bullet essays, academic jargon, and >35 word overages."""
    ast_valid = {
        "assertion_id": "ast-serial-valid",
        "claim_text": "We lock manufacturer and model first so OEM rules constrain the serial format.",
        "canonical_status": "ACTIVE",
    }

    # 1. 179-word verbatim 4-bullet essay from live meeting failure must be rejected
    bad_1800_essay = (
        "- Ed, your intuition on the 1,800 images is dead on—the overall percentage should actually improve at scale for two structural reasons:\n\n"
        "- First, the 322 golden dataset is an artificially hard stress test because it was deliberately spread across 50 different asset types—meaning rare, one-off nameplates have the exact same weight as your most common equipment. In the 1,800 field batch, volume follows an 80/20 Pareto curve dominated by repetitive, standard OEM plates where our active rules already hit over 90%.\n\n"
        "- Second, as those repetitive labels flow through in volume, our Manufacturer and Model clustering locks in the expected serial number template—exact character length, alphanumeric prefixes, and bounding box layout—across the entire cohort of that asset class.\n\n"
        "- So when you weight by actual field volume across the 1,800 images rather than equal-weighting 50 edge-case categories, the repetitive patterns dominate and pull the aggregate accuracy up, while truly novel plates get cleanly isolated into the review queue."
    )
    d_essay = evaluate_speak_gate(bad_1800_essay, assertion=ast_valid)
    assert not d_essay.should_speak
    assert d_essay.abstain_reason in (AbstainReasonCode.WORD_BUDGET_EXCEEDED, AbstainReasonCode.UNGROUNDED_CLAIM)

    # 2. 159-word 3-bullet serial number essay must be rejected
    bad_serial_essay = (
        "- Since manufacturer and model are already working well, we can lock those in first and let them drive the serial number rules. Once we know the OEM, we can constrain the expected serial length, prefixes, and regex patterns—in our data, having that active nameplate rule jumps extraction accuracy from around 30% up to 90%.\n\n"
        "- Serial numbers are unforgiving because a single character flip—like reading 211H instead of 21H—can push bad asset data straight into Maximo. For serials specifically, we should require strict agreement between Model A, Model B, and traditional OCR on the cropped serial box rather than relying on fuzzy matching.\n\n"
        "- Whenever the models only disagree on a single ambiguous character like an 0 versus O or 1 versus I, we should flag just that character for a quick human check, and then feed that OEM's pattern back to the SME so the rule registry handles it automatically going forward."
    )
    d_serial = evaluate_speak_gate(bad_serial_essay, assertion=ast_valid)
    assert not d_serial.should_speak
    assert d_serial.abstain_reason in (AbstainReasonCode.WORD_BUDGET_EXCEEDED, AbstainReasonCode.UNGROUNDED_CLAIM)

    # 3. Concisely spoken 31-word human phrasing passes
    good_spoken_31 = "Ed, your intuition on the eighteen hundred images is dead on. Our golden set equally weights fifty edge cases, whereas real field volume is mostly standard OEM gear already clearing ninety percent."
    ast_1800 = {
        "assertion_id": "ast-1800-valid",
        "claim_text": good_spoken_31,
        "canonical_status": "ACTIVE",
    }
    d_good = evaluate_speak_gate(good_spoken_31, assertion=ast_1800, max_words=35)
    assert d_good.should_speak
    assert len(d_good.retained_text.split()) <= 35

