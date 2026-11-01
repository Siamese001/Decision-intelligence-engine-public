"""Tests for visible transcript context and unresolved speaker formatting."""

from dual_pipeline.speaker_attribution_core.review_context import build_speaker_review_context
from dual_pipeline.speaker_attribution_core.review_context_render import (
    format_speaker_review_context,
    parse_speaker_review_turns,
)
from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
import pytest


def test_context_renderer_shows_four_neighbors_and_bold_unresolved_target():
    turns = [
        {"turn_id": str(i), "speaker": "Speaker 1" if i == 3 else "Known Person",
         "timestamp": f"00:00:0{i}", "text": f"verbatim {i}"}
        for i in range(7)
    ]
    rendered = format_speaker_review_context(build_speaker_review_context(turns, "3", "meeting-1"))
    assert "BEFORE 2 [00:00:01] Known Person: verbatim 1" in rendered
    assert "BEFORE 1 [00:00:02] Known Person: verbatim 2" in rendered
    assert "TARGET [00:00:03] **UNRESOLVED — Speaker 1**: verbatim 3" in rendered
    assert "AFTER 1 [00:00:04] Known Person: verbatim 4" in rendered
    assert "AFTER 2 [00:00:05] Known Person: verbatim 5" in rendered


def test_context_renderer_labels_unresolved_neighbor_and_boundary():
    turns = [
        {"turn_id": str(i), "speaker": "Unknown" if i == 1 else "Speaker 2",
         "timestamp": "Unavailable", "text": f"line {i}"}
        for i in range(3)
    ]
    rendered = format_speaker_review_context(build_speaker_review_context(turns, "1", "meeting-2"))
    assert "Transcript start: 1 earlier turn(s) unavailable" in rendered
    assert "Transcript end: 1 later turn(s) unavailable" in rendered
    assert "**UNRESOLVED — Unknown**" in rendered


def test_source_parser_keeps_multiline_turn_content():
    transcript = "Speaker 1: First line\ncontinuation exactly  \nSpeaker 2: next turn\n"
    turns = parse_speaker_review_turns(transcript, "meeting-multiline")
    assert turns[0]["text"] == "First line\ncontinuation exactly  "


def test_unresolved_room_microphone_neighbor_is_visually_marked():
    turns = [
        {"turn_id": str(i), "speaker": "Shared Room Mic" if i == 1 else "Avery",
         "timestamp": str(i), "text": f"speech {i}"}
        for i in range(5)
    ]
    rendered = format_speaker_review_context(build_speaker_review_context(turns, "2", "meeting"))
    assert "BEFORE 1 [1] **UNRESOLVED — Shared Room Mic**: speech 1" in rendered


def test_antigravity_bridge_requires_and_preserves_visible_speaker_context(tmp_path):
    bridge = AntigravityUIBridge(
        arbiter=HitlArbiterAgent(ledger_path=tmp_path / "ledger.json"),
        interactive_callback=lambda _card: {},
    )
    visible = "BEFORE 2 old\nBEFORE 1 nearer\nTARGET **UNRESOLVED — Speaker 1** current\nAFTER 1 next\nAFTER 2 later"
    decision = bridge.arbitrate_and_present(
        pipeline_id="test", decision_id="speaker-context", category="speaker_attribution",
        question="Who is speaking?", options=[
            {"key": "A", "label": "A", "confidence": 0.55},
            {"key": "B", "label": "B", "confidence": 0.45},
        ], context={"visible_context": visible},
    )
    assert visible in decision["atomic_card"]["question"]
    with pytest.raises(ValueError, match="context is missing"):
        bridge.arbitrate_and_present(
            pipeline_id="test", decision_id="speaker-context-missing", category="speaker_attribution",
            question="Who is speaking?", options=[{"key": "A", "confidence": 0.55}, {"key": "B", "confidence": 0.45}],
        )


def test_validate_visible_speaker_context_requires_separate_rows_per_marker():
    from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context

    # Valid multi-row context
    valid_context = (
        "Meeting/source: LENS Working Session OCR (10/16/2026)\n\n"
        "BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff\n\n"
        "BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration?\n\n"
        "TARGET [00:07:01] **UNRESOLVED — Unknown**: Oh, both.\n\n"
        "AFTER 1 [00:07:03] **UNRESOLVED — Unknown**: I mean\n\n"
        "AFTER 2 [00:07:15] Amit Ayer: What is the response time?\n\n"
        "Who is speaking in the TARGET turn?"
    )
    validated = validate_visible_speaker_context(valid_context)
    assert validated == valid_context.strip()

    # Exact collapsed single-line failure from user incident
    collapsed = (
        "Meeting/source: LENS Working Session OCR (10/16/2026) "
        "BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff "
        "BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration? "
        "TARGET [00:07:01] UNRESOLVED: Oh, both. "
        "AFTER 1 [00:07:03] UNRESOLVED: I mean "
        "AFTER 2 [00:07:15] Amit Ayer: What is the response time? "
        "Who is speaking in the TARGET turn?"
    )
    with pytest.raises(ValueError, match="multiple turn markers .* collapsed onto the same line"):
        validate_visible_speaker_context(collapsed)

    # Bullet markers on separate rows
    bulleted_context = (
        "Meeting/source: LENS Working Session OCR (10/16/2026)\n"
        "- BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff\n"
        "- BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration?\n"
        "- TARGET [00:07:01] **UNRESOLVED — Unknown**: Oh, both.\n"
        "- AFTER 1 [00:07:03] **UNRESOLVED — Unknown**: I mean\n"
        "- AFTER 2 [00:07:15] Amit Ayer: What is the response time?\n"
        "Who is speaking in the TARGET turn?"
    )
    assert validate_visible_speaker_context(bulleted_context) == bulleted_context.strip()


def test_antigravity_bridge_rejects_collapsed_speaker_context(tmp_path):
    bridge = AntigravityUIBridge(
        arbiter=HitlArbiterAgent(ledger_path=tmp_path / "ledger.json"),
        interactive_callback=lambda _card: {},
    )
    collapsed = (
        "Meeting/source: LENS Working Session OCR (10/16/2026) "
        "BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff "
        "BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration? "
        "TARGET [00:07:01] UNRESOLVED: Oh, both. "
        "AFTER 1 [00:07:03] UNRESOLVED: I mean "
        "AFTER 2 [00:07:15] Amit Ayer: What is the response time? "
        "Who is speaking in the TARGET turn?"
    )
    with pytest.raises(ValueError, match="multiple turn markers .* collapsed onto the same line"):
        bridge.arbitrate_and_present(
            pipeline_id="test", decision_id="speaker-collapsed", category="speaker_attribution",
            question="Who is speaking?", options=[{"key": "A", "confidence": 0.55}, {"key": "B", "confidence": 0.45}],
            context={"visible_context": collapsed},
        )


def test_antigravity_bridge_renders_multi_row_bulleted_speaker_card(tmp_path):
    bridge = AntigravityUIBridge(
        arbiter=HitlArbiterAgent(ledger_path=tmp_path / "ledger.json"),
        interactive_callback=lambda _card: {},
    )
    bulleted = (
        "Meeting/source: LENS Working Session OCR (10/16/2026)\n\n"
        "- BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff\n\n"
        "- BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration?\n\n"
        "- TARGET [00:07:01] **UNRESOLVED — Unknown**: Oh, both.\n\n"
        "- AFTER 1 [00:07:03] **UNRESOLVED — Unknown**: I mean\n\n"
        "- AFTER 2 [00:07:15] Amit Ayer: What is the response time?\n\n"
        "Who is speaking in the TARGET turn?"
    )
    decision = bridge.arbitrate_and_present(
        pipeline_id="test", decision_id="speaker-bulleted", category="speaker_attribution",
        question="Select the speaker:", options=[
            {"key": "A", "label": "Alex", "confidence": 0.55},
            {"key": "B", "label": "Amit", "confidence": 0.45},
        ], context={"visible_context": bulleted},
    )
    assert decision["status"] in ("HITL_REQUIRED", "AWAITING_APPROVAL")
    assert bulleted in decision["atomic_card"]["question"]
    from dual_pipeline.speaker_attribution_core.review_context_render import validate_visible_speaker_context
    assert validate_visible_speaker_context(decision["atomic_card"]["question"])


