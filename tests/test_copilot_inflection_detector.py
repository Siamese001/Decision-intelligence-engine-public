"""
tests/test_copilot_inflection_detector.py

Unit tests for InflectionDetector: tone inflection, questions, pauses, and cadence.
"""
from __future__ import annotations

from dual_pipeline.live_meeting_recorder_core.inflection_detector import (
    InflectionDetector,
    InflectionTriggerType,
)


def test_interrogative_inflection_trigger():
    detector = InflectionDetector(cadence_seconds=15.0)

    # Direct question mark
    event = detector.evaluate_chunk(
        chunk_idx=1,
        sec=8.0,
        text="Which decisions will you own, and which remain with me?",
        rms=0.08,
        current_time=100.0,
    )
    assert event is not None
    assert event.trigger_type == InflectionTriggerType.STOPPING_POINT_QUESTION
    assert event.is_priority is True
    assert event.confidence >= 0.95

    # Conversational pass-back: "is that fair?"
    event2 = detector.evaluate_chunk(
        chunk_idx=2,
        sec=16.0,
        text="We could structure that as an initial two-week spike, is that fair?",
        rms=0.06,
        current_time=108.0,
    )
    assert event2 is not None
    assert event2.trigger_type == InflectionTriggerType.STOPPING_POINT_QUESTION
    assert event2.is_priority is True


def test_acoustic_pause_trigger():
    detector = InflectionDetector(cadence_seconds=15.0, silence_rms_threshold=0.015)

    # Chunk 1: Active speech
    ev1 = detector.evaluate_chunk(
        chunk_idx=1,
        sec=8.0,
        text="I think we need to look at the delivery model across all of our Apex Global workstreams",
        rms=0.09,
        current_time=100.0,
    )
    assert ev1 is None

    # Chunk 2: Speaker pauses (energy drops to silence)
    ev2 = detector.evaluate_chunk(
        chunk_idx=2,
        sec=16.0,
        text="",
        rms=0.003,
        current_time=108.0,
    )
    assert ev2 is not None
    assert ev2.trigger_type == InflectionTriggerType.STOPPING_POINT_PAUSE
    assert ev2.is_priority is True


def test_sentence_completion_trigger():
    detector = InflectionDetector(cadence_seconds=15.0)

    event = detector.evaluate_chunk(
        chunk_idx=1,
        sec=8.0,
        text="I feel like that is something I can take off your plate.",
        rms=0.05,
        current_time=100.0,
    )
    assert event is not None
    assert event.trigger_type == InflectionTriggerType.STOPPING_POINT_COMPLETION
    assert event.is_priority is False


def test_periodic_cadence_trigger():
    detector = InflectionDetector(cadence_seconds=15.0)

    # Active continuous speech at t=0
    detector.evaluate_chunk(
        chunk_idx=1,
        sec=8.0,
        text="We need to review the entire staffing and talent architecture across the teams",
        rms=0.07,
        current_time=100.0,
    )

    # 16 seconds later, continuous speech continues without punctuation or questions
    ev_cadence = detector.evaluate_chunk(
        chunk_idx=2,
        sec=16.0,
        text="and ensure that we have proper coverage for all the upcoming enterprise requirements",
        rms=0.08,
        current_time=116.0,
    )
    assert ev_cadence is not None
    assert ev_cadence.trigger_type == InflectionTriggerType.CADENCE_INTERVAL
    assert ev_cadence.is_priority is False

    # After reset, cadence doesn't immediately fire
    detector.reset_cadence(current_time=116.0)
    ev_post_reset = detector.evaluate_chunk(
        chunk_idx=3,
        sec=24.0,
        text="which will give us stability moving forward",
        rms=0.06,
        current_time=120.0,
    )
    assert ev_post_reset is None
