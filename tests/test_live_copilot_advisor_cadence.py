"""
tests/test_live_copilot_advisor_cadence.py

Unit tests for LiveCopilotAdvisor priority preemption, gpt-6-luna model resolution, and cadence triggers.
"""
from __future__ import annotations

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.inflection_detector import (
    InflectionEvent,
    InflectionTriggerType,
)


def test_copilot_model_resolution_default():
    advisor = LiveCopilotAdvisor(mock_mode=True)
    assert advisor.model_id == "gpt-6-luna"
    assert advisor.reasoning_effort in ("low", "medium", "high")


def test_copilot_format_output_with_inflection():
    advisor = LiveCopilotAdvisor(mock_mode=True)
    event = InflectionEvent(
        trigger_type=InflectionTriggerType.STOPPING_POINT_QUESTION,
        cue_text="Which decisions will you own, and which remain with me?",
        timestamp_sec=32.0,
        is_priority=True,
        confidence=0.98,
        reason="Interrogative question hand-off",
    )

    parsed = {
        "has_guidance": True,
        "category": "ALIGNMENT",
        "snap_cue": "Lock the staffing handoff boundary",
        "suggested_script": "I agree. I will own the demand and technical profile, while you drive the sourcing engine.",
        "risk_trap": "Unowned staffing creates delivery delays",
        "confidence": 0.95,
    }

    formatted = advisor._format_copilot_output(parsed, graph_context=None, inflection_event=event)
    assert formatted is not None
    assert formatted["type"] == "COPILOT_FEEDBACK"
    assert formatted["inflection_trigger"] == "STOPPING_POINT_QUESTION"
    assert formatted["trigger_reason"] == "Interrogative question hand-off"
    assert formatted["is_priority"] is True
    assert "suggested_script" in formatted


def test_copilot_priority_preemption():
    advisor = LiveCopilotAdvisor(mock_mode=True, cooldown_seconds=60.0)

    # Set last evaluated time to right now
    advisor._last_evaluated_time = 1000.0

    # Buffer a couple turns
    advisor.record_turn("Speaker", "First dialogue sentence with enough length to meet character threshold", "[00:08]")
    advisor.record_turn("Speaker", "Second sentence continuing the active conversation across the meeting", "[00:16]")

    # Non-priority call at t=1005s (cooldown is 60s) -> should be rejected by cooldown
    res_non_priority = advisor.evaluate_live_turns(current_time=1005.0, inflection_event=None)
    assert res_non_priority is None

    # Priority call at t=1005s with STOPPING_POINT_QUESTION -> must preempt cooldown and evaluate!
    priority_event = InflectionEvent(
        trigger_type=InflectionTriggerType.STOPPING_POINT_QUESTION,
        cue_text="What do you think about that split?",
        timestamp_sec=16.0,
        is_priority=True,
        confidence=0.99,
        reason="Detected interrogative question hand-off",
    )
    res_priority = advisor.evaluate_live_turns(current_time=1005.0, inflection_event=priority_event)
    assert res_priority is not None
    assert res_priority["has_guidance"] is True
    assert res_priority["inflection_trigger"] == "STOPPING_POINT_QUESTION"
    assert res_priority["is_priority"] is True
