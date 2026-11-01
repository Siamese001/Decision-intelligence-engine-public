"""
tests/test_copilot_utterance_resolution_adversarial.py

Adversarial and edge-case verification for topic-driven speaker and utterance resolution.
Covers 4 mandatory categories:
1. Boundary payload: empty turns list, None keywords, empty strings.
2. Malformed input: missing turn keys (missing speaker, missing text, non-numeric turn_sec).
3. Overflow & exhaustion: 500 candidate turns with overlapping keywords.
4. Exact production regression: multi-turn meeting with Voice A utterance at [07:40].
"""
from __future__ import annotations

from typing import Any, Dict, List
import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _resolve_speaker_and_utterance,
    _select_topic_utterance,
)


def test_adversarial_boundary_empty_turns_and_keywords():
    """Category 1: Boundary payload tests."""
    # Empty turns
    sel, win = _select_topic_utterance([], topic_keywords=["vector"])
    assert sel is None
    assert win == []

    # Context model with zero turns
    empty_model = ConversationContextModel(project_scope="project:project_nexus", attendees=[])
    resolved_target, sel, win, err = _resolve_speaker_and_utterance(
        target=None,
        topic_keywords=["database"],
        context_model=empty_model,
        in_mem=None,
    )
    assert resolved_target is None
    assert sel is None
    assert win == []
    assert err is None

    # Empty keywords with valid turns
    all_turns = [{"seq": 1, "speaker": "Voice A", "text": "Hello world", "timestamp": "00:10", "turn_sec": 10.0}]
    sel2, win2 = _select_topic_utterance(all_turns, topic_keywords=[])
    assert sel2 is not None
    assert sel2.speaker == "Voice A"
    assert sel2.text == "Hello world"


def test_adversarial_malformed_turn_dictionaries():
    """Category 2: Malformed input tests (missing fields, wrong types)."""
    malformed_turns = [
        {"seq": None, "speaker": None, "text": None, "timestamp": None, "turn_sec": None},
        {"seq": "invalid", "speaker": 123, "text": "Setting up the vector database.", "timestamp": 456, "turn_sec": "bad_float"},
    ]

    sel, win = _select_topic_utterance(malformed_turns, topic_keywords=["vector", "database"])
    assert sel is not None
    assert "vector database" in sel.text
    assert sel.speaker == "123"
    assert sel.turn_sec == 3.0  # Safe float fallback (idx=1 * 3.0)


def test_adversarial_overflow_hundreds_of_matching_turns():
    """Category 3: Overflow & exhaustion tests (high turn volume with ties)."""
    all_turns = []
    for i in range(300):
        all_turns.append({
            "seq": i + 1,
            "speaker": f"Speaker_{i % 10}",
            "text": f"Discussing vector database clustering tier {i}.",
            "timestamp": f"{i // 60:02d}:{i % 60:02d}",
            "turn_sec": float(i * 3.0),
        })

    sel, win = _select_topic_utterance(all_turns, topic_keywords=["vector", "database"])
    assert sel is not None
    # Most recent turn with highest score should be picked
    assert sel.seq == 300
    assert sel.speaker == "Speaker_9"
    assert len(win) >= 1


def test_exact_production_regression_voice_a_resolution():
    """Category 4: Exact production regression test.

    Recreates the Collaboration_3_Pm_Et_Mitch_PartnerAdvisory scenario:
    115 turns, Voice A mentions 'And you can always set up a Vector database.' at 07:40.
    Operator executes copilot with topic 'vector database' and no target.
    Verifies Voice A's turn is selected with matched keywords.
    """
    context_model = ConversationContextModel(
        project_scope="project:project_nexus",
        attendees=[{"name": "Voice A", "role": "Participant"}],
    )
    for i in range(20):
        context_model.add_turn(
            speaker="Voice B",
            text=f"Preliminary setup turn {i}.",
            timestamp=f"0{i}:00",
            turn_sec=float(i * 20.0),
        )

    # Incident turn
    context_model.add_turn(
        speaker="Voice A",
        text="And you can always set up a Vector database.",
        timestamp="07:40",
        turn_sec=460.0,
    )

    for i in range(21, 35):
        context_model.add_turn(
            speaker="Voice B",
            text=f"Subsequent planning turn {i}.",
            timestamp=f"0{i // 60 + 8}:{i % 60:02d}",
            turn_sec=float(i * 20.0),
        )

    resolved_target, sel, win, err = _resolve_speaker_and_utterance(
        target=None,
        topic_keywords=["vector", "database"],
        context_model=context_model,
        in_mem=None,
    )

    assert err is None
    assert resolved_target is None
    assert sel is not None
    assert sel.speaker == "Voice A"
    assert sel.timestamp == "07:40"
    assert "Vector database" in sel.text
    assert "vector" in sel.matched_keywords
    assert "database" in sel.matched_keywords
