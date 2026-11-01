"""Wave 1 tests: Prompt Window Regex, Meta-Topic Stopwords & Canonical Speaker Identity Equivalence."""
from __future__ import annotations

from dual_pipeline.cli.copilot_speech import _extract_topic_anchor, _is_different_speaker
from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    RosterResolver,
    _extract_topic_from_prompt,
    _filter_speaker_turns,
    parse_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import (
    are_same_speaker_identity,
    parse_prompt_window_and_count,
    strip_copilot_prompt_meta,
)


def test_entire_live_transcript_window_parsing() -> None:
    """'entire live transcript' and 'whole call transcript' must return full-session window (>= 5000s)."""
    win_sec, req_cnt, has_explicit = parse_prompt_window_and_count(
        "respond to ed's overall comments entire live transcript on how we can work better together"
    )
    assert win_sec is not None and win_sec >= 5000.0
    assert has_explicit is True

    win_call, _, _ = parse_prompt_window_and_count("review the whole call transcript")
    assert win_call == 86400.0

    parsed = parse_copilot_request(
        "respond to ed's overall comments entire live transcript on how we can work better together"
    )
    assert parsed.window_seconds == 86400.0
    assert parsed.target_speaker in ("Ed", "ed")
    assert "overall" not in parsed.topic_keywords
    assert "how" not in parsed.topic_keywords
    assert "work" not in parsed.topic_keywords
    assert "better" not in parsed.topic_keywords
    assert "together" not in parsed.topic_keywords


def test_meta_topic_stopword_stripping() -> None:
    """Conversational meta-words ('overall', 'how', 'work', 'better', 'together') must not extract as topic keywords."""
    topic = _extract_topic_from_prompt("respond to ed's overall comments live transcript", target="Ed")
    assert not topic

    topic2 = _extract_topic_from_prompt(
        "respond to ed's overall comments entire live transcript on how we can work better together",
        target="Ed",
    )
    assert not topic2

    stripped = strip_copilot_prompt_meta("entire live transcript on how we can work better together")
    assert "entire live transcript" not in stripped.lower()

    assert _extract_topic_anchor({"matched_keywords": ["how", "overall"]}) == ""
    assert _extract_topic_anchor({"matched_keywords": ["how", "nameplate"]}) == "on your nameplate point, "


def test_canonical_speaker_identity_equivalence() -> None:
    """'Ed', 'Ed More', and 'Eduardo Juan More' must resolve as the same canonical speaker identity."""
    assert are_same_speaker_identity("Ed More", "Eduardo Juan More") is True
    assert are_same_speaker_identity("Eduardo Juan More", "Ed More") is True
    assert are_same_speaker_identity("Ed", "Eduardo Juan More") is True
    assert are_same_speaker_identity("Eduardo", "Ed More") is True
    assert are_same_speaker_identity("Ed More", "Daniel Schaap") is False
    assert are_same_speaker_identity("Ed More", "Ed Brankin") is False

    assert _is_different_speaker("Ed More", "Eduardo Juan More") is False
    assert _is_different_speaker("Eduardo Juan More", "Ed More") is False
    assert _is_different_speaker("Ed", "Eduardo Juan More") is False
    assert _is_different_speaker("Ed More", "Skyler") is True

    roster = [
        {"name": "Ed More", "canonical_name": "Ed More", "display_name": "Ed More"},
        {"name": "Eduardo Juan More", "canonical_name": "Eduardo Juan More", "display_name": "Eduardo Juan More"},
        {"name": "Skyler", "canonical_name": "Skyler", "display_name": "Skyler"},
    ]
    res1 = RosterResolver.resolve("Ed", roster)
    assert res1.status == "RESOLVED"
    assert res1.error_code is None

    res2 = RosterResolver.resolve("Eduardo Juan More", [{"name": "Ed More", "canonical_name": "Ed More"}])
    assert res2.status == "RESOLVED"
    assert res2.canonical_name == "Ed More"

    turns = [
        {"seq": 1, "speaker": "Eduardo Juan More", "text": "The GE project rule had 100% hardcoded."},
        {"seq": 2, "speaker": "Skyler", "text": "Let us check with Daniel Schaap."},
    ]
    matched_turns = _filter_speaker_turns(turns, "Ed More")
    assert len(matched_turns) == 1
    assert matched_turns[0][1]["speaker"] == "Eduardo Juan More"
