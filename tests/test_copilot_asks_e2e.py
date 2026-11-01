"""
tests/test_copilot_asks_e2e.py

End-to-End qualification tests for Conversation Understanding under Wave 3.
Verifies that:
1. Asks are detected reliably with or without '?' (inversion, Wh-questions, imperative prompts).
2. Addressees are identified and canonicalized to roster participants.
3. Answered asks are closed in AskLedger by subsequent replies.
4. Roster matching forbids partial substring match ("Al" never matches "Alan").
5. Rolling digest tracks active topic, open asks, and key entities.
"""
from __future__ import annotations

import pytest
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_turn_signals import (
    AskKind,
    AskLedger,
    detect_addressee,
    detect_ask_signals,
    generate_rolling_digest,
    roster_fuzzy_match,
)


def test_ask_detection_without_question_marks():
    """Verify asks are detected and classified even without punctuation."""
    test_cases = [
        ("Can we verify the database schema migration status", True, "clarification"),
        ("Tell me about the pipeline ingestion latency bottleneck", True, "clarification"),
        ("Explain how the vector sidecar cache is updated", True, "clarification"),
        ("Should we decide on option A or option B for the release", True, "decision"),
        ("Can we confirm whether the tests passed", True, "confirmation"),
        ("Who will take the lead on the security remediation", True, "action"),
        ("What is the current status of the staging cluster", True, "open"),
        ("The deployment completed smoothly yesterday", False, None),
        ("Yeah exactly makes sense", False, None),
    ]

    for utterance, expected_is_ask, expected_kind in test_cases:
        is_ask, kind, is_bc = detect_ask_signals(utterance)
        assert is_ask == expected_is_ask, f"Failed on '{utterance}': expected is_ask={expected_is_ask}, got {is_ask}"
        if expected_is_ask:
            assert kind == expected_kind, f"Failed on '{utterance}': expected kind={expected_kind}, got {kind}"


def test_addressee_detection_and_vocative_alignment():
    """Verify addressees are resolved accurately from vocative patterns against roster."""
    roster = [
        {"canonical_name": "Alan Smith", "display_name": "Alan Smith"},
        {"canonical_name": "Jen Davis", "display_name": "Jen Davis"},
        {"canonical_name": "David Miller", "display_name": "David Miller"},
    ]

    # Opening vocative
    addr1 = detect_addressee("Alan, could we check the latency numbers?", roster)
    assert addr1 == "Alan Smith"

    # Informal opening vocative
    addr2 = detect_addressee("Hey Jen - can you verify the migration deliverables?", roster)
    assert addr2 == "Jen Davis"

    # Closing vocative
    addr3 = detect_addressee("What is the rollout timeline, Dave?", roster)
    assert addr3 == "David Miller"

    # Unaddressed utterance
    addr4 = detect_addressee("Moving on to the next item on the agenda.", roster)
    assert addr4 is None


def test_ask_ledger_lifecycle_open_and_close():
    """Verify AskLedger registers questions and properly closes them when answered."""
    model = ConversationContextModel(
        project_scope="project_alpha",
        attendees=["Jen Davis", "David Miller", "Mandy Jones"],
    )

    # Turn 1: Jen asks Dave a question (Registered as OPEN)
    model.add_turn(
        speaker="Jen Davis",
        text="Dave, could you clarify whether the database backup completed?",
        timestamp="10:00:00",
        turn_sec=10.0,
    )
    open_asks = model.ask_ledger.open_asks()
    assert len(open_asks) == 1
    ask = open_asks[0]
    assert ask.speaker == "Jen Davis"
    assert ask.addressee == "David Miller"
    assert ask.status == "OPEN"

    # Turn 2: Mandy provides backchannel feedback (Ask must remain OPEN)
    model.add_turn(
        speaker="Mandy Jones",
        text="Yeah, got it.",
        timestamp="10:00:05",
        turn_sec=15.0,
    )
    assert len(model.ask_ledger.open_asks()) == 1, "Backchannel prematurely resolved open ask!"

    # Turn 3: Dave responds with substantive answer (Ask MUST close)
    model.add_turn(
        speaker="David Miller",
        text="Yes, I confirmed the database backup completed successfully at 2 AM.",
        timestamp="10:00:10",
        turn_sec=20.0,
    )

    # Invariant: Answered ask must NOT stay open
    assert len(model.ask_ledger.open_asks()) == 0, "Fail if: an answered ask stays open!"
    all_asks = model.ask_ledger.all_asks()
    assert len(all_asks) == 1
    resolved_ask = all_asks[0]
    assert resolved_ask.status == "ANSWERED"
    assert resolved_ask.answered_by == "David Miller"
    assert resolved_ask.answer_turn_seq == 3


def test_roster_exact_and_fuzzy_matching_purity():
    """Strictly verify 'Al' does not match 'Alan' and substring leakage is barred."""
    roster = [
        {"canonical_name": "Alan Smith", "display_name": "Alan Smith"},
        {"canonical_name": "Robert Taylor", "display_name": "Robert Taylor"},
        {"canonical_name": "Michael Brown", "display_name": "Michael Brown"},
    ]

    # Explicit Invariant: "Al" must NOT match "Alan"
    match_al, status_al = roster_fuzzy_match("Al", roster)
    assert status_al != "RESOLVED", "CRITICAL INVARIANT VIOLATION: 'Al' matched 'Alan'!"
    assert match_al is None

    # Exact first name match: "Alan" matches "Alan Smith"
    match_alan, status_alan = roster_fuzzy_match("Alan", roster)
    assert status_alan == "RESOLVED"
    assert match_alan["canonical_name"] == "Alan Smith"

    # Nickname alias match: "Bob" matches "Robert Taylor"
    match_bob, status_bob = roster_fuzzy_match("Bob", roster)
    assert status_bob == "RESOLVED"
    assert match_bob["canonical_name"] == "Robert Taylor"

    # Single-edit typo tolerance on length >= 4: "Micheal" matches "Michael Brown"
    match_micheal, status_micheal = roster_fuzzy_match("Micheal", roster)
    assert status_micheal == "RESOLVED"
    assert match_micheal["canonical_name"] == "Michael Brown"

    # Ambiguous query when two members share first name
    roster_ambiguous = [
        {"canonical_name": "Alan Smith", "display_name": "Alan Smith"},
        {"canonical_name": "Alan Turing", "display_name": "Alan Turing"},
    ]
    match_amb, status_amb = roster_fuzzy_match("Alan", roster_ambiguous)
    assert status_amb == "AMBIGUOUS"
    assert match_amb is None


def test_rolling_topic_and_digest_generation():
    """Verify rolling digest synthesizes active topics, open asks, and key entities."""
    ledger = AskLedger()
    ask = ledger.register_ask(
        turn_seq=1,
        speaker="Jen Davis",
        text="What are the scaling limits?",
        addressee=None,
        ask_kind="open",
        created_sec=10.0,
    )

    digest = generate_rolling_digest(
        active_topic="Vector Database Scaling",
        open_asks=ledger.open_asks(),
        salient_entities=["VectorDB", "SCADA", "Postgres"],
        last_act="question_to_operator",
    )

    assert "Active Topic: Vector Database Scaling" in digest
    assert "Open Asks (1): Jen Davis: What are the scaling limits?" in digest
    assert "VectorDB" in digest
    assert "Current Act: question_to_operator" in digest
