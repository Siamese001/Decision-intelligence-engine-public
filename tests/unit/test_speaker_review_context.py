"""Tests for exact, source-bound speaker-review context windows."""

import pytest

from dual_pipeline.speaker_attribution_core.review_context import (
    SpeakerReviewContextError,
    build_speaker_review_context,
)


def _turns(count=7):
    return [
        {
            "turn_id": f"turn-{index}",
            "speaker": f"Speaker {index % 2}",
            "timestamp": f"00:00:{index:02d}",
            "text": f"original turn {index}",
            "position": index,
        }
        for index in range(count)
    ]


def test_middle_target_preserves_exact_order_and_text():
    context = build_speaker_review_context(_turns(7), "turn-3", "meeting-a")
    assert [turn.turn_id for turn in context.turns] == [
        "turn-1",
        "turn-2",
        "turn-3",
        "turn-4",
        "turn-5",
    ]
    assert [turn.text for turn in context.turns] == [f"original turn {i}" for i in range(1, 6)]
    assert context.turns[2].is_target
    assert context.before_unavailable == 0
    assert context.after_unavailable == 0
    assert context.source_id == "meeting-a"
    assert len(context.source_digest) == 64


@pytest.mark.parametrize(
    ("count", "target", "before", "after"),
    [
        (1, 0, 2, 2),
        (2, 0, 2, 1),
        (2, 1, 1, 2),
        (3, 0, 2, 0),
        (3, 1, 1, 1),
        (3, 2, 0, 2),
        (4, 0, 2, 0),
        (4, 3, 0, 2),
        (5, 0, 2, 0),
        (5, 4, 0, 2),
    ],
)
def test_true_boundaries_report_unavailable_turns(count, target, before, after):
    context = build_speaker_review_context(_turns(count), f"turn-{target}", "meeting-a")
    assert context.before_unavailable == before
    assert context.after_unavailable == after
    assert any(turn.is_target and turn.turn_id == f"turn-{target}" for turn in context.turns)


def test_multiline_and_long_turn_is_preserved_verbatim():
    source = _turns(7)
    multiline_text = "First line of long speech.\nSecond line with indentation.\n    Third line."
    source[3]["text"] = multiline_text
    context = build_speaker_review_context(source, "turn-3", "meeting-a")
    assert context.turns[2].text == multiline_text


def test_equal_timestamps_preserve_source_sequence():
    source = _turns(5)
    for t in source:
        t["timestamp"] = "00:01:00"
    context = build_speaker_review_context(source, "turn-2", "meeting-a")
    assert [turn.turn_id for turn in context.turns] == [f"turn-{i}" for i in range(5)]
    assert all(turn.timestamp == "00:01:00" for turn in context.turns)


def test_missing_timestamp_is_labeled_unavailable():
    source = _turns(3)
    source[0]["timestamp"] = None
    source[1]["timestamp"] = ""
    del source[2]["timestamp"]
    context = build_speaker_review_context(source, "turn-1", "meeting-a")
    assert [turn.timestamp for turn in context.turns] == [
        "Unavailable",
        "Unavailable",
        "Unavailable",
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_target",
        "duplicate_id",
        "cross_source",
        "stale_digest",
        "wrong_source",
        "interior_gap",
        "empty_turns",
        "none_text",
        "empty_turn_id",
        "invalid_position",
    ],
)
def test_invalid_context_fails_closed(mutation):
    source = _turns(7)
    kwargs = {}
    target = "turn-3"
    source_id = "meeting-a"

    if mutation == "missing_target":
        target = "absent-turn"
    elif mutation == "duplicate_id":
        source[-1]["turn_id"] = "turn-3"
    elif mutation == "cross_source":
        source[2]["source_id"] = "meeting-b"
    elif mutation == "stale_digest":
        kwargs["expected_source_digest"] = "0" * 64
    elif mutation == "wrong_source":
        kwargs["expected_source_id"] = "meeting-b"
    elif mutation == "interior_gap":
        source[4]["position"] = 8
    elif mutation == "empty_turns":
        source = []
    elif mutation == "none_text":
        source[2]["text"] = None
    elif mutation == "empty_turn_id":
        source[2]["turn_id"] = "   "
    elif mutation == "invalid_position":
        source[2]["position"] = "not_an_int"

    with pytest.raises(SpeakerReviewContextError):
        build_speaker_review_context(source, target, source_id, **kwargs)


def test_non_two_window_count_raises_value_error():
    with pytest.raises(ValueError, match="exactly two preceding and two following"):
        build_speaker_review_context(_turns(5), "turn-2", "meeting-a", before_count=1)
    with pytest.raises(ValueError, match="exactly two preceding and two following"):
        build_speaker_review_context(_turns(5), "turn-2", "meeting-a", after_count=3)


def test_to_dict_structure():
    context = build_speaker_review_context(_turns(5), "turn-2", "meeting-a")
    data = context.to_dict()
    assert data["source_id"] == "meeting-a"
    assert len(data["source_digest"]) == 64
    assert data["target_turn_id"] == "turn-2"
    assert data["before_unavailable"] == 0
    assert data["after_unavailable"] == 0
    assert len(data["turns"]) == 5
    for turn in data["turns"]:
        assert "turn_id" in turn
        assert "position" in turn
        assert "is_target" in turn
        assert "source_id" in turn
        assert "speaker" in turn
        assert "text" in turn
        assert "timestamp" in turn
