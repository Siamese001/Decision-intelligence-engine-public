"""
tests/test_copilot_live_transcript_rca_wave2.py

Wave 2 unit and integration tests for Speaker Identity Fallback, Nickname Alias Unification,
and Live Hint Queue Reconciliation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dual_pipeline.cli.copilot_session import _resolve_session_and_context_model
from dual_pipeline.cli.speaker_hint_handlers import _resolve_speaker_name
from dual_pipeline.live_meeting_recorder_core.attendees import resolve_roster_name
from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import ChatAttributionCollector
from dual_pipeline.live_meeting_recorder_core.copilot_context import UtteranceSelector
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_offline import is_tolerated_speaker_error
from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import (
    normalize_search_text,
    segment_contains_quote,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hints import SpeakerHintManager


def test_nickname_alias_unification_across_roster_resolvers() -> None:
    roster = ["Daniel Brankin", "Amit Ayer"]
    assert resolve_roster_name("Dan", roster) == "Daniel Brankin"
    assert resolve_roster_name("Daniel", roster) == "Daniel Brankin"
    assert _resolve_speaker_name("Dan", session_attendees=roster) == "Daniel Brankin"

    collector = ChatAttributionCollector(attendees=roster)
    items = collector.parse_message('Dan "Sap integration on for tomorrow"')
    assert len(items) == 1
    item = items[0]
    assert item.speaker_name == "Daniel Brankin"
    assert item.quoted_text == "Sap integration on for tomorrow"


def test_uiux_compound_normalization_matches_ui_slash_ux() -> None:
    clean_q = normalize_search_text("excited for this UIUX thing")
    assert clean_q == "excited for this ui ux thing"
    seg = {"text": "Yeah, I am really excited for this UI/UX thing with Tracy."}
    assert segment_contains_quote(seg, clean_q) is True


def test_speaker_hint_engine_voice_host_scoped_relabel(tmp_path: Path) -> None:
    seg_file = tmp_path / "segments.jsonl"
    md_file = tmp_path / "transcript.md"
    segs = [
        {"chunk_idx": 0, "speaker": "Host", "voice_id": "Voice Host", "text": "Good morning Daniel."},
        {"chunk_idx": 1, "speaker": "Host", "voice_id": "Voice Host", "text": "We are excited for this UI/UX thing."},
        {"chunk_idx": 2, "speaker": "Host", "voice_id": "Voice Host", "text": "Let me share my screen."},
    ]
    seg_file.write_text("\n".join(json.dumps(s) for s in segs) + "\n", encoding="utf-8")
    md_file.write_text(
        "[00:00:05] Host: Good morning Daniel.\n"
        "[00:00:10] Host: We are excited for this UI/UX thing.\n"
        "[00:00:15] Host: Let me share my screen.\n",
        encoding="utf-8",
    )

    engine = SpeakerHintManager(
        attendees=["Daniel Brankin", "Amit Ayer"],
        output_dir=tmp_path,
        cache_dir=tmp_path,
        ledger_path=tmp_path / "ledger.json",
    )
    res = engine.apply_hint(
        quote="excited for this UIUX thing",
        speaker_name="Dan",
        segments_jsonl_path=seg_file,
        transcript_md_path=md_file,
    )
    assert res.status == "PASS"
    assert res.speaker_name == "Daniel Brankin"
    assert res.updated_segments_count == 1

    updated = [json.loads(line) for line in seg_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert updated[0]["speaker"] == "Host"
    assert updated[1]["speaker"] == "Daniel Brankin"
    assert updated[2]["speaker"] == "Host"

    md_lines = md_file.read_text(encoding="utf-8").splitlines()
    assert "Host: Good morning Daniel." in md_lines[0]
    assert "Daniel Brankin: We are excited for this UI/UX thing." in md_lines[1]
    assert "Host: Let me share my screen." in md_lines[2]


def test_live_buffer_and_copilot_session_reconcile_hint_queue_and_staging(tmp_path: Path) -> None:
    tc_dir = tmp_path / "output" / "transcript_cache" / "project_jarvis" / "10-26-2026"
    staging_dir = tc_dir / ".staging_Daniel_Brankin_Amit_Ayer_130302"
    staging_dir.mkdir(parents=True)

    # Segments file in parent transcript_cache dir (as written during live recording)
    parent_seg = tc_dir / "Daniel_Brankin_Amit_Ayer_segments.jsonl"
    parent_seg.write_text(
        json.dumps({"chunk_idx": 0, "speaker": "Host", "text": "We are excited for this UI/UX thing.", "start_sec": 12.0}) + "\n",
        encoding="utf-8",
    )
    # Queued hints in staging dir: one matching existing segment, one pending ASR segment
    queue_file = staging_dir / "speaker_hints_queue.json"
    queue_file.write_text(
        json.dumps([
            {
                "hint_id": "q1",
                "speaker_name": "Dan",
                "quote": "excited for this UIUX thing",
                "status": "QUEUED",
                "session_elapsed_seconds": 12.0,
            },
            {
                "hint_id": "q2",
                "speaker_name": "Dan",
                "quote": "Sap integration on for tomorrow",
                "status": "QUEUED",
                "session_elapsed_seconds": 25.0,
            },
        ]),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        meeting_dir=str(staging_dir),
        runtime_root=str(tmp_path),
        _parsed_window_seconds=120.0,
    )
    out_dir, model, _, _ = _resolve_session_and_context_model(args)
    assert out_dir == staging_dir.resolve()
    turns = model.focus_turns()
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Daniel Brankin"
    assert "UI/UX" in turns[0]["text"]
    assert turns[1]["speaker"] == "Daniel Brankin"
    assert turns[1]["text"] == "Sap integration on for tomorrow"


def test_utterance_selector_treats_host_as_acoustic_placeholder() -> None:
    turns = [
        {"seq": 1, "speaker": "Host", "text": "Monthly SAP scoring workflow is our top priority.", "timestamp": "00:01:00", "turn_sec": 60.0},
    ]
    res = UtteranceSelector.select(turns, target_speaker="Daniel Brankin", topic_keywords=["velocity"], window_sec=120.0)
    assert res.status == "SELECTED"
    assert res.selected_utterance is not None
    assert "Monthly SAP scoring" in res.selected_utterance.text


def test_tolerated_speaker_error_allows_challenge_with_live_focus_turns() -> None:
    model = ConversationContextModel(project_scope="project_jarvis", attendees=["Daniel Brankin", "Amit Ayer"])
    model.add_turn("Host", "We need to automate monthly SAP scoring without adding headcount.", "00:01:10", turn_sec=70.0)
    assert is_tolerated_speaker_error(
        ("SPEAKER_HAS_NO_TURNS", "Speaker 'Daniel Brankin' has no spoken turns"),
        intent="challenge",
        topic_keywords=[],
        topic_query=None,
        context_model=model,
    ) is True
    # Respond with no topic query/keywords remains strict
    empty_model = ConversationContextModel()
    assert is_tolerated_speaker_error(
        ("ZERO_RELEVANT_UTTERANCES", "No turns"),
        intent="respond",
        topic_keywords=[],
        topic_query=None,
        context_model=empty_model,
    ) is False
