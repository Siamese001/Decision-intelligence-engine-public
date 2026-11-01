"""
tests/test_copilot_utterance_resolution_e2e.py

End-to-End verification of topic-driven speaker and utterance resolution
when operator triggers copilot without an explicit target speaker.
"""
from __future__ import annotations

from typing import Any, Dict, List
import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_context import SelectedUtterance
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _build_evidence_breakdown,
    _resolve_speaker_and_utterance,
    _select_topic_utterance,
)


def test_copilot_resolve_utterance_by_topic_when_target_is_none():
    """Verifies that Voice A's turn on vector database is selected when target is None."""
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[
            {"name": "Amit Ayer", "role": "Lead Architect"},
            {"name": "Voice A", "role": "Speaker"},
        ],
    )
    context_model.add_turn(
        speaker="Voice B",
        text="Let's review the architectural dependencies.",
        timestamp="07:10",
        turn_sec=430.0,
    )
    context_model.add_turn(
        speaker="Voice A",
        text="And you can always set up a Vector database.",
        timestamp="07:40",
        turn_sec=460.0,
    )
    context_model.add_turn(
        speaker="Voice B",
        text="Agreed on that point.",
        timestamp="08:00",
        turn_sec=480.0,
    )

    resolved_target, utterance, window, err = _resolve_speaker_and_utterance(
        target=None,
        topic_keywords=["vector", "database"],
        context_model=context_model,
        in_mem=None,
    )

    assert err is None
    assert resolved_target is None
    assert utterance is not None
    assert isinstance(utterance, SelectedUtterance)
    assert utterance.speaker == "Voice A"
    assert utterance.timestamp == "07:40"
    assert "Vector database" in utterance.text
    assert "vector" in utterance.matched_keywords
    assert "database" in utterance.matched_keywords
    assert len(window) >= 1


def test_copilot_evidence_breakdown_contains_selected_acoustic_utterance():
    """Verifies that evidence breakdown correctly packages selected utterance."""
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[],
    )
    utterance = SelectedUtterance(
        seq=2,
        speaker="Voice A",
        text="And you can always set up a Vector database.",
        timestamp="07:40",
        turn_sec=460.0,
        relevance_score=2.0,
        matched_keywords=["vector", "database"],
    )

    assertions = [
        {
            "assertion_id": "ast-vec-1",
            "predicate": "OPEN_QUESTION",
            "claim_text": "Vector database sizing constraints",
        }
    ]

    breakdown = _build_evidence_breakdown(utterance, context_model, assertions)
    assert "acoustic_transcript_evidence" in breakdown
    acoustic = breakdown["acoustic_transcript_evidence"]
    assert len(acoustic) == 1
    assert acoustic[0]["speaker"] == "Voice A"
    assert acoustic[0]["timestamp"] == "07:40"
    assert acoustic[0]["matched_keywords"] == ["vector", "database"]


def test_copilot_select_topic_utterance_fallback_to_latest_turn():
    """Verifies fallback to latest focus turn when topic keywords do not match."""
    all_turns = [
        {"seq": 1, "speaker": "Daniel", "text": "Starting sprint review.", "timestamp": "01:00", "turn_sec": 60.0},
        {"seq": 2, "speaker": "Amit", "text": "Reviewing data architecture.", "timestamp": "01:30", "turn_sec": 90.0},
    ]

    utterance, window = _select_topic_utterance(all_turns, topic_keywords=["unrelated_topic"])
    assert utterance is not None
    assert utterance.speaker == "Amit"
    assert utterance.text == "Reviewing data architecture."
    assert utterance.timestamp == "01:30"


def test_copilot_request_topic_fusion_and_speaker_summary_for_ninan():
    """Verifies --topic + --request keyword fusion, dynamic speaker extraction for Speaker_Beta, and multi-turn speaker summary."""
    from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request

    parsed = parse_copilot_request(
        raw_request="challenge productivity increase from Speaker_Beta",
        intent_override="respond",
        target_override=None,
        topic_override="metrics",
    )
    assert parsed.target_speaker == "Speaker_Beta"
    assert parsed.inferred_intent == "respond"
    assert parsed.topic_keywords == ["metrics", "productivity", "increase"]

    context_model = ConversationContextModel(
        project_scope="project:project_beacon",
        attendees=[{"name": "Speaker_Beta", "role": "Stakeholder"}, {"name": "Amit Ayer", "role": "Lead"}],
    )
    context_model.add_turn(
        speaker="Speaker_Beta",
        text="We rolled out the coding assistant across three squads this quarter.",
        timestamp="10:05",
        turn_sec=605.0,
    )
    context_model.add_turn(
        speaker="Speaker_Beta",
        text="Our developer velocity and commit metrics increased by 35% this sprint.",
        timestamp="10:20",
        turn_sec=620.0,
    )
    context_model.add_turn(
        speaker="Amit Ayer",
        text="Let us check whether cycle time or defect escape rates changed.",
        timestamp="10:30",
        turn_sec=630.0,
    )

    resolved_target, utterance, window, err = _resolve_speaker_and_utterance(
        target=parsed.target_speaker,
        topic_keywords=parsed.topic_keywords,
        context_model=context_model,
        in_mem=None,
    )
    assert err is None
    assert resolved_target == "Speaker_Beta"
    assert utterance is not None
    assert utterance.get("speaker") == "Speaker_Beta"
    assert "velocity" in utterance.text
    assert "35%" in utterance.speaker_summary
    assert "Amit Ayer:" in utterance.window_summary
    assert len(window) == 3


def test_copilot_based_on_feedback_directive_and_live_synthesis_e2e():
    """Verifies 'based on <Speaker> feedback' activates live transcript directive and strips dangling tails."""
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
        _clean_live_turn_claim,
        has_live_transcript_directive,
    )

    assert has_live_transcript_directive("based on Traci feedback on what new role would be - what role should backfill Traci's role")
    cleaned = _clean_live_turn_claim("So, we are proposing two product strategists to support discovery and")
    assert not cleaned.endswith(" and")
    assert "two product strategists" in cleaned


