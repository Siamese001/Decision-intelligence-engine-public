"""
Wave 1 tests for Live Transcript Copilot RCA:
- CLI ingress (--query, --prompt aliases on copilot subparser)
- Prompt parser meta-phrase stripping ("based on live transcript", "review last 2 mins in detail", "review whole transcript")
- Intent inference ("next best questions", "questiion", "next best 2 comments to Daniel")
- Temporal window slicing (window_seconds -> ConversationContextModel.focus_seconds)
- Requested count extraction ("next best 2 comments", "next two questions", "strategic speaker point")
- Post-retrieval PARSER_NO_TOPIC false-positive elimination when live context exists
"""
from __future__ import annotations

import argparse
from pathlib import Path
import tempfile

import pytest

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _copilot_failure,
    _parse_and_validate_copilot_args,
)
from dual_pipeline.live_meeting_recorder_core.copilot_offline import diagnose_unresolved_evidence


def test_cli_parser_supports_query_and_prompt_aliases() -> None:
    parser = build_parser()
    args_q = parser.parse_args(["copilot", "--query", "next best questions for Daniel", "--active"])
    assert args_q.request == "next best questions for Daniel"
    assert args_q.active is True

    args_p = parser.parse_args(["copilot", "--prompt", "review whole transcript", "--active"])
    assert args_p.request == "review whole transcript"


@pytest.mark.parametrize(
    "raw_prompt,expected_intent,expected_target,expected_keywords,expected_window,expected_count",
    [
        ("--copilot next best questions for Daniel", "next-questions", "Daniel", [], None, None),
        ("--query next best questiion based on live transcript", "next-questions", None, [], None, 1),
        ("--query next best questions to Daniel", "next-questions", "Daniel", [], None, None),
        ("next best questions on live transcripts", "next-questions", None, [], None, None),
        ("next best question on transcript strategic", "next-questions", None, [], None, 1),
        ("use liver transcript for next two questions", "next-questions", None, [], None, 2),
        (
            "--query live transcript next best strategic speaker point to drive discussion forward",
            "next-questions",
            None,
            [],
            None,
            1,
        ),
        (
            "need comment on velocity from Daniel from live transcript - review last 2 mins in detail",
            "respond",
            "Daniel",
            ["velocity"],
            120.0,
            None,
        ),
        (
            "review whole transcript and synthesize for next best 2 comments to Daniel",
            "respond",
            "Daniel",
            [],
            86400.0,
            2,
        ),
        (
            "review whole transcript and synthesize for next best comments to Daniel",
            "respond",
            "Daniel",
            [],
            86400.0,
            None,
        ),
        (
            "based on live transcript - synthesize for next best comments to Daniel",
            "respond",
            "Daniel",
            [],
            None,
            None,
        ),
    ],
)
def test_daniel_brankin_session_prompts_parse_cleanly(
    raw_prompt: str,
    expected_intent: str,
    expected_target: str | None,
    expected_keywords: list[str],
    expected_window: float | None,
    expected_count: int | None,
) -> None:
    parsed = parse_copilot_request(raw_prompt)
    assert parsed.inferred_intent == expected_intent
    assert parsed.target_speaker == expected_target
    assert parsed.topic_keywords == expected_keywords
    assert parsed.window_seconds == expected_window
    assert parsed.requested_count == expected_count
    assert parsed.has_explicit_intent is True


def test_parse_and_validate_copilot_args_allows_live_transcript_prompts_and_blocks_gibberish() -> None:
    valid_args = argparse.Namespace(
        request="--query next best questiion based on live transcript",
        intent=None,
        target=None,
        topic=None,
        json=True,
        runtime_root=None,
    )
    req_tuple, fail_res = _parse_and_validate_copilot_args(valid_args)
    assert fail_res is None
    assert req_tuple is not None
    intent, target, topic_query, topic_keywords = req_tuple
    assert intent == "next-questions"
    assert target is None
    assert topic_keywords == []
    assert getattr(valid_args, "_parsed_requested_count", None) == 1

    bad_args = argparse.Namespace(
        request="???",
        intent=None,
        target=None,
        topic=None,
        json=True,
        runtime_root=None,
    )
    bad_tuple, bad_fail = _parse_and_validate_copilot_args(bad_args)
    assert bad_tuple is None
    assert bad_fail is not None
    assert bad_fail["error"] == "PARSER_NO_TOPIC"


def test_diagnose_unresolved_evidence_does_not_emit_parser_no_topic_when_live_context_present() -> None:
    # Without live context (unit default), empty query/target returns PARSER_NO_TOPIC
    code_no_live, _ = diagnose_unresolved_evidence(
        None, topic_keywords=[], topic_query=None, target=None, has_live_context=False
    )
    assert code_no_live == "PARSER_NO_TOPIC"

    # With live context (e.g. --intent next-questions --active), returns ZERO_MATCHING_ASSERTIONS instead of PARSER_NO_TOPIC
    code_live, _ = diagnose_unresolved_evidence(
        None, topic_keywords=[], topic_query=None, target=None, has_live_context=True
    )
    assert code_live == "ZERO_MATCHING_ASSERTIONS"


def test_temporal_window_slicing_in_context_model() -> None:
    cm_2min = ConversationContextModel(project_scope="project_jarvis", attendees=["Daniel Brankin"], focus_seconds=120.0)
    cm_2min.add_turn("Host", "Early turn at 10s", "00:00:10", turn_sec=10.0)
    cm_2min.add_turn("Host", "Turn at 200s", "00:03:20", turn_sec=200.0)
    cm_2min.add_turn("Host", "Turn at 300s", "00:05:00", turn_sec=300.0)
    focus_2min = cm_2min.focus_turns()
    assert len(focus_2min) == 2
    assert [t["turn_sec"] for t in focus_2min] == [200.0, 300.0]

    cm_whole = ConversationContextModel(project_scope="project_jarvis", attendees=["Daniel Brankin"], focus_seconds=86400.0)
    cm_whole.add_turn("Host", "Early turn at 10s", "00:00:10", turn_sec=10.0)
    cm_whole.add_turn("Host", "Turn at 200s", "00:03:20", turn_sec=200.0)
    cm_whole.add_turn("Host", "Turn at 300s", "00:05:00", turn_sec=300.0)
    assert len(cm_whole.focus_turns()) == 3


def test_failure_ledger_retains_session_id_and_resolved_roots() -> None:
    import json

    with tempfile.TemporaryDirectory(prefix="copilot_w1_ledger_") as tmp:
        out_dir = Path(tmp)
        res = _copilot_failure(
            "ZERO_MATCHING_ASSERTIONS",
            "No relevant graph assertions match the current discussion.",
            machine=True,
            output_dir=out_dir,
            runtime_root=out_dir,
            session_id="fastboot_1793034180_47404",
        )
        assert res["status"] == "FAIL"
        records = [json.loads(line) for line in (out_dir / "copilot_attempts.jsonl").read_text().splitlines() if line.strip()]
        assert len(records) == 1
        assert records[0]["session_id"] == "fastboot_1793034180_47404"
        assert records[0]["resolved_roots"]["output_dir"] == str(out_dir)
