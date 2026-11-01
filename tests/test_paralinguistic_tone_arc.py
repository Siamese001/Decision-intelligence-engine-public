"""
tests/test_paralinguistic_tone_arc.py

Tests for paralinguistic acoustic & lexical tone analysis (Wave 5):
- Tone category classification across lexical markers
- Paralinguistic speaking rate and pause gap heuristics
- Meeting tone arc 3-phase trajectory and engagement score
- Output 2 '## Meeting Tone Arc' formatting and deterministic injection before Appendix
- Output 3 'tone_signals' structured projection
- Markdown transcript turn parser
"""
from dual_pipeline.tone_analysis_core import (
    MeetingToneArc,
    ParalinguisticToneAnalyzer,
    ToneCategory,
)


def test_tone_category_classification_lexical():
    analyzer = ParalinguisticToneAnalyzer()
    turns = [
        {"speaker": "Alice", "text": "We will finalize the architecture. The decision is locked in.", "start": 0.0, "end": 4.0},
        {"speaker": "Bob", "text": "Great idea, I fully support this and appreciate the team effort.", "start": 5.0, "end": 9.0},
        {"speaker": "Charlie", "text": "Maybe, I guess potentially that could work, but I am not sure.", "start": 10.0, "end": 14.0},
        {"speaker": "David", "text": "There is strong pushback. This timeline is completely problematic and unacceptable.", "start": 15.0, "end": 19.0},
        {"speaker": "Eve", "text": "Looking back in hindsight, let's step back and consider lessons learned.", "start": 20.0, "end": 24.0},
    ]

    arc = analyzer.analyze_turns(turns)
    assert len(arc.turn_telemetry) == 5
    assert arc.turn_telemetry[0].primary_tone == ToneCategory.DECISIVE
    assert arc.turn_telemetry[1].primary_tone == ToneCategory.COLLABORATIVE
    assert arc.turn_telemetry[2].primary_tone == ToneCategory.HESITANT
    assert arc.turn_telemetry[3].primary_tone == ToneCategory.TENSE
    assert arc.turn_telemetry[4].primary_tone == ToneCategory.REFLECTIVE

    assert arc.decisiveness_count == 1
    assert arc.tension_count == 1
    assert arc.reflection_count == 1


def test_paralinguistic_features():
    analyzer = ParalinguisticToneAnalyzer()
    turns = [
        # Disengaged: monosyllabic
        {"speaker": "Alice", "text": "Sure.", "start": 0.0, "end": 2.0},
        # Reflective due to 8-second pause gap before speaking
        {"speaker": "Bob", "text": "We need to weigh all our long term options carefully.", "start": 10.0, "end": 15.0},
        # Tense due to accelerated cadence: 25 words in 3.0 seconds (> 8.0 wps)
        {"speaker": "Charlie", "text": "We have to fix this now immediately otherwise the entire server crashes and everything goes down without any recovery path available at all!", "start": 16.0, "end": 19.0},
    ]

    arc = analyzer.analyze_turns(turns)
    assert arc.turn_telemetry[0].primary_tone == ToneCategory.DISENGAGED
    assert arc.turn_telemetry[1].primary_tone == ToneCategory.REFLECTIVE
    assert arc.turn_telemetry[2].primary_tone == ToneCategory.TENSE


def test_meeting_tone_arc_aggregation():
    analyzer = ParalinguisticToneAnalyzer()
    turns = [
        {"speaker": "Alice", "text": "Great idea, fully support partnering.", "start": 0.0, "end": 5.0},
        {"speaker": "Bob", "text": "I disagree, there is massive friction and pushback.", "start": 10.0, "end": 15.0},
        {"speaker": "Alice", "text": "The decision is approved, we commit to this action item.", "start": 20.0, "end": 25.0},
    ]
    arc = analyzer.analyze_turns(turns)
    assert isinstance(arc, MeetingToneArc)
    assert len(arc.phase_breakdown) == 3
    assert arc.trajectory != ""
    assert 0.0 <= arc.engagement_score <= 1.0


def test_output_2_tone_arc_formatting_and_injection():
    analyzer = ParalinguisticToneAnalyzer()
    turns = [
        {"speaker": "Alice", "text": "The decision is approved and confirmed.", "start": 0.0, "end": 5.0},
        {"speaker": "Bob", "text": "Great, thanks for sharing. We agree.", "start": 6.0, "end": 10.0},
    ]
    arc = analyzer.analyze_turns(turns)
    rendered = analyzer.format_output_2_tone_arc(arc)
    assert "## Meeting Tone Arc & Strategic Dynamics" in rendered
    assert "Dominant Dynamic" in rendered
    assert "Tone Dynamics Summary" in rendered

    # Test injection before appendix
    recap_md = (
        "# Executive Recap\n\n"
        "## Summary\nDiscussion points.\n\n"
        "# Appendix: Detailed Stakeholder Perspectives\nSpeaker details here."
    )
    injected = analyzer.inject_tone_arc_into_recap(recap_md, arc)
    assert "## Meeting Tone Arc & Strategic Dynamics" in injected
    assert injected.index("## Meeting Tone Arc") < injected.index("# Appendix")

    # Idempotent: should not duplicate if already present
    re_injected = analyzer.inject_tone_arc_into_recap(injected, arc)
    assert re_injected == injected


def test_output_3_tone_signals():
    analyzer = ParalinguisticToneAnalyzer()
    turns = [
        {"speaker": "Alice", "text": "The decision is approved and locked in.", "start": 0.0, "end": 5.0},
        {"speaker": "Bob", "text": "Great, we agree.", "start": 6.0, "end": 10.0},
        {"speaker": "Charlie", "text": "This is a serious blocker and friction point.", "start": 11.0, "end": 15.0},
    ]
    arc = analyzer.analyze_turns(turns)
    signals = analyzer.format_output_3_tone_signals(arc)
    assert isinstance(signals, list)
    assert len(signals) >= 2  # Decisive and Tense turns
    for sig in signals:
        assert "turn_index" in sig
        assert "tone_category" in sig
        assert "confidence" in sig
        assert "lexical_cues" in sig


def test_parse_turns_from_text():
    analyzer = ParalinguisticToneAnalyzer()
    transcript = """# Transcript

**Alice (PartnerAdvisory):** We need to decide on the architecture.

**Bob (Client):** In hindsight, let's step back and consider lessons learned.

[00:02:15] Charlie: I have serious concerns and pushback on that approach.
"""
    turns = analyzer.parse_turns_from_text(transcript)
    assert len(turns) == 3
    assert turns[0]["speaker"] == "Alice"
    assert "decide on the architecture" in turns[0]["text"]
    assert turns[1]["speaker"] == "Bob"
    assert turns[2]["speaker"] == "Charlie"
    assert turns[2]["start"] == 135.0  # 2m 15s
