"""Unit tests for Phase 4: Request fidelity, roster resolution, and utterance selection."""
from __future__ import annotations

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    DistinguishableEvidence,
    EvidenceSourceType,
    RosterResolutionResult,
    RosterResolver,
    UtteranceSelector,
    filter_assertions_for_utterance_topic,
    parse_copilot_request,
)


def test_parse_copilot_request_regression_format():
    """Verifies natural language extraction from canonical regression request."""
    req_text = "React to Mitch's comment about load and agents."
    parsed = parse_copilot_request(raw_request=req_text)

    assert parsed.inferred_intent == "respond"
    assert parsed.target_speaker == "Mitch"
    assert parsed.topic_query == "load and agents"
    assert "load" in parsed.topic_keywords
    assert "agents" in parsed.topic_keywords


def test_parse_copilot_request_various_intents_and_overrides():
    """Verifies intent inference and explicit CLI flag precedence."""
    # Questions
    p1 = parse_copilot_request("What are next questions about SCADA latency?")
    assert p1.inferred_intent == "next-questions"
    assert p1.topic_query == "SCADA latency"
    assert "scada" in p1.topic_keywords
    assert "latency" in p1.topic_keywords

    # Overrides take precedence
    p2 = parse_copilot_request(
        raw_request="React to Mitch on load",
        intent_override="next-actions",
        target_override="Mitchell",
        topic_override="concurrency limits",
    )
    assert p2.inferred_intent == "next-actions"
    assert p2.target_speaker == "Mitchell"
    assert p2.topic_query == "concurrency limits"
    assert "concurrency" in p2.topic_keywords


def test_roster_resolution_exact_and_alias():
    """Verifies exact full-name and English nickname alias resolution."""
    roster = [
        {"canonical_name": "Alex Rivera", "display_name": "Alex Rivera", "role": "Engineer"},
        {"canonical_name": "Jennifer Pan", "display_name": "Jennifer Pan", "role": "PM"},
        {"canonical_name": "Amit Ayer", "display_name": "Amit Ayer", "role": "Architect"},
    ]

    # Exact full name
    r1 = RosterResolver.resolve("Alex Rivera", roster)
    assert r1.status == "RESOLVED"
    assert r1.canonical_name == "Alex Rivera"

    # Alias: Mitch -> Alex Rivera
    r2 = RosterResolver.resolve("Mitch", roster)
    assert r2.status == "RESOLVED"
    assert r2.canonical_name == "Alex Rivera"

    # Alias: Jen -> Jennifer Pan
    r3 = RosterResolver.resolve("Jen", roster)
    assert r3.status == "RESOLVED"
    assert r3.canonical_name == "Jennifer Pan"


def test_roster_resolution_ambiguity_fails_closed():
    """Multiple candidates matching the same alias or prefix must fail closed as AMBIGUOUS."""
    ambiguous_roster = [
        {"canonical_name": "Alex Rivera", "display_name": "Alex Rivera"},
        {"canonical_name": "Mitchell Davis", "display_name": "Mitchell Davis"},
    ]

    res = RosterResolver.resolve("Mitch", ambiguous_roster)
    assert res.status == "AMBIGUOUS"
    assert res.error_code == "AMBIGUOUS_SPEAKER_IDENTITY"
    assert "Multiple roster candidates match" in (res.details or "")


def test_roster_resolution_unknown_speaker():
    """Target speaker absent from non-empty roster must fail closed as NOT_FOUND."""
    roster = [
        {"canonical_name": "Jennifer Pan", "display_name": "Jennifer Pan"},
    ]
    res = RosterResolver.resolve("Unknown Person", roster)
    assert res.status == "NOT_FOUND"
    assert res.error_code == "SPEAKER_NOT_IN_ROSTER"


def test_utterance_selection_picks_topic_relevant_turn():
    """UtteranceSelector selects turn with highest topic keyword overlap."""
    turns = [
        {"seq": 1, "speaker": "Alex Rivera", "text": "Good morning team, let us start the meeting.", "turn_sec": 5.0},
        {"seq": 2, "speaker": "Jen Davis", "text": "Let us discuss DocIQ.", "turn_sec": 10.0},
        {"seq": 3, "speaker": "Alex Rivera", "text": "I am concerned about high system load when 16 agents run concurrently.", "turn_sec": 20.0},
        {"seq": 4, "speaker": "Amit Ayer", "text": "We should check the write serializer.", "turn_sec": 25.0},
    ]

    res = UtteranceSelector.select(
        turns=turns,
        target_speaker="Alex Rivera",
        topic_keywords=["load", "agents"],
        window_sec=15.0,
    )

    assert res.status == "SELECTED"
    assert res.selected_utterance is not None
    assert res.selected_utterance.seq == 3
    assert res.selected_utterance.matched_keywords == ["load", "agents"]
    # Window turns should capture turns 2, 3, 4 (within 20s +/- 15s)
    window_seqs = [t["seq"] for t in res.window_turns]
    assert 3 in window_seqs
    assert 2 in window_seqs
    assert 4 in window_seqs


def test_utterance_selection_fails_closed_when_topic_absent():
    """If speaker spoke but never addressed topic keywords, fail closed with ZERO_RELEVANT_UTTERANCES."""
    turns = [
        {"seq": 1, "speaker": "Alex Rivera", "text": "The weather in Seattle is great.", "turn_sec": 5.0},
        {"seq": 2, "speaker": "Alex Rivera", "text": "Let us grab some coffee.", "turn_sec": 10.0},
    ]
    res = UtteranceSelector.select(
        turns=turns,
        target_speaker="Alex Rivera",
        topic_keywords=["load", "agents"],
    )
    assert res.status == "NO_RELEVANT_UTTERANCE"
    assert res.error_code == "ZERO_RELEVANT_UTTERANCES"


def test_filter_assertions_for_utterance_topic_excludes_unrelated():
    """Assertions not matching topic keywords are strictly filtered out."""
    candidates = [
        {"assertion_id": "ast-001", "subject_node_id": "person:mitch", "predicate": "ROLE", "object_literal": "Delivery Lead"},
        {"assertion_id": "ast-002", "subject_node_id": "anchor_sqlite", "predicate": "CONSTRAINS", "object_literal": "SQLite serializer limits concurrent agent load"},
    ]

    filtered = filter_assertions_for_utterance_topic(candidates, topic_keywords=["load", "agents"])
    assert len(filtered) == 1
    assert filtered[0]["assertion_id"] == "ast-002"


def test_distinguishable_evidence_breakdown_serialization():
    """Verifies evidence items preserve distinct source categories and provenance."""
    e1 = DistinguishableEvidence(
        source_type=EvidenceSourceType.ACOUSTIC_TRANSCRIPT,
        content={"text": "Acoustic turn text"},
        speaker="Mitch",
        timestamp="00:01:15",
    )
    e2 = DistinguishableEvidence(
        source_type=EvidenceSourceType.GRAPH_ASSERTION,
        content={"claim": "Graph assertion"},
        assertion_id="ast-999",
        provenance="source-release-1",
    )

    d1 = e1.to_dict()
    d2 = e2.to_dict()

    assert d1["source_type"] == "ACOUSTIC_TRANSCRIPT"
    assert d1["speaker"] == "Mitch"
    assert d2["source_type"] == "GRAPH_ASSERTION"
    assert d2["assertion_id"] == "ast-999"
