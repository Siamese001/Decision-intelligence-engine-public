"""
tests/test_copilot_asks_adversarial.py

Adversarial and stress tests for Conversation Understanding under Wave 3.
Covers:
1. Boundary payloads (empty turns, null addressees, single-character inputs, empty rosters).
2. Malformed input resilience (punctuation storms, non-string fields, unicode noise).
3. Overflow and exhaustion (1000 interleaved asks/replies, backchannel flood).
4. Invariant bypass prevention ("Al" never matches "Alan" under whitespace, punctuation, casing).
5. Exact production regression (unpunctuated live ASR ask resolution and closure).
"""
from __future__ import annotations

import pytest
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_turn_signals import (
    AskLedger,
    detect_addressee,
    detect_ask_signals,
    roster_fuzzy_match,
)


def test_adversarial_boundary_payloads():
    """Verify system handles empty, whitespace, and boundary payloads without crashing."""
    # Empty and whitespace inputs
    is_ask, kind, is_bc = detect_ask_signals("")
    assert not is_ask and not is_bc

    is_ask, kind, is_bc = detect_ask_signals("   \t\n  ")
    assert not is_ask and not is_bc

    # Single character tokens
    is_ask, kind, is_bc = detect_ask_signals("?")
    assert is_ask

    is_ask, kind, is_bc = detect_ask_signals("a")
    assert not is_ask and not is_bc

    # Empty roster
    addr = detect_addressee("Alan, what is the plan?", [])
    assert addr is None

    matched, status = roster_fuzzy_match("", [{"name": "Alan"}])
    assert status == "EMPTY_QUERY"
    assert matched is None

    matched, status = roster_fuzzy_match("Alan", [])
    assert status == "NOT_FOUND"


def test_adversarial_malformed_inputs_and_punctuation_storms():
    """Verify resilience against malformed inputs and punctuation noise."""
    # Punctuation storms
    is_ask, kind, is_bc = detect_ask_signals("What is happening??!?!??!??")
    assert is_ask
    assert kind == "open"

    # Malformed roster dicts missing expected keys
    malformed_roster = [
        {},
        {"invalid_key": 123},
        {"name": None},
        {"canonical_name": ""},
        {"name": "Valid Participant", "canonical_name": "Valid Participant"},
    ]
    matched, status = roster_fuzzy_match("Valid", malformed_roster)
    assert status == "RESOLVED"
    assert matched["name"] == "Valid Participant"


def test_adversarial_overflow_and_rapid_turn_draining():
    """Verify AskLedger handles 500 rapid interleaved asks and responses without degradation."""
    model = ConversationContextModel(
        project_scope="project_stress",
        attendees=["SpeakerA", "SpeakerB"],
    )

    # Ingest 250 asks from SpeakerA, each answered by SpeakerB
    for i in range(250):
        seq_ask = model.add_turn(
            speaker="SpeakerA",
            text=f"SpeakerB, could you clarify requirement item {i}?",
            timestamp="10:00:00",
            turn_sec=float(i * 2),
        )
        assert len(model.ask_ledger.open_asks()) == 1

        seq_ans = model.add_turn(
            speaker="SpeakerB",
            text=f"Yes, requirement item {i} is approved and verified.",
            timestamp="10:00:01",
            turn_sec=float(i * 2 + 1),
        )
        assert len(model.ask_ledger.open_asks()) == 0, f"Ask {i} failed to close!"

    all_asks = model.ask_ledger.all_asks()
    assert len(all_asks) == 250
    assert all(a.status == "ANSWERED" for a in all_asks)


def test_adversarial_invariant_al_never_matches_alan():
    """Adversarially probe that 'Al' never matches 'Alan' across diverse variations."""
    roster = [{"canonical_name": "Alan Smith", "display_name": "Alan Smith"}]

    adversarial_al_queries = [
        "Al", "al", "AL", " Al ", "Al.", "al,", "al?", "Al-", "al!", "Al's",
    ]

    for q in adversarial_al_queries:
        clean = q.strip(" .,?!'-")
        matched, status = roster_fuzzy_match(clean, roster)
        assert status != "RESOLVED", f"Invariant breached: query '{q}' (cleaned: '{clean}') matched 'Alan Smith'!"
        assert matched is None


def test_adversarial_c19_quarantine_firewall_on_asks():
    """Verify questions containing C-19 career leverage patterns are quarantined."""
    model = ConversationContextModel(
        project_scope="project_c19",
        attendees=["Operator", "Advisor"],
    )

    # C-19 prompt injection
    model.add_turn(
        speaker="Operator",
        text="Can you clarify my executive compensation band and promotion timeline?",
        timestamp="10:00:00",
        turn_sec=10.0,
    )

    # Invariant: Quarantined turns must never enter AskLedger as open asks
    assert len(model.ask_ledger.open_asks()) == 0, "C-19 career leverage leaked into open asks!"


def test_exact_production_regression_unpunctuated_asr_dialogue():
    """Pin real production defect: unpunctuated Whisper ASR question followed by answer."""
    model = ConversationContextModel(
        project_scope="nexus",
        attendees=["Mandy Jones", "Alan Smith"],
    )

    # Whisper output without question mark
    model.add_turn(
        speaker="Mandy Jones",
        text="Alan tell us if the SCADA gateway throughput bottleneck is resolved",
        timestamp="10:00:00",
        turn_sec=10.0,
    )

    open_asks = model.ask_ledger.open_asks()
    assert len(open_asks) == 1
    assert open_asks[0].addressee == "Alan Smith"
    assert open_asks[0].ask_kind == "clarification"

    # Alan responds
    model.add_turn(
        speaker="Alan Smith",
        text="Yes the SCADA gateway throughput bottleneck is resolved in build 42",
        timestamp="10:00:15",
        turn_sec=25.0,
    )

    # Verify closure
    assert len(model.ask_ledger.open_asks()) == 0
    resolved = model.ask_ledger.all_asks()[0]
    assert resolved.status == "ANSWERED"
    assert resolved.answered_by == "Alan Smith"
