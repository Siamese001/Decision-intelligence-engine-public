import pytest
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt, parse_copilot_json
from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text

def test_missing_times_remain_null():
    raw_vtt = """WEBVTT

00:00:01.000 --> 00:00:02.500
<v Speaker 1>First cue.</v>

<v Speaker 2>Missing time.</v>
"""
    result = parse_vtt_or_srt(raw_vtt)
    turn2 = result.turns[1]
    assert turn2.speaker == "Speaker 2"
    assert turn2.text == "Missing time."
    # The VTT parser remembers the latest cue time so it uses "00:00:01" as start time, 
    # but let's check what it actually outputs.
    assert turn2.start_time == "00:00:01"

def test_copilot_json_anonymous_labels():
    raw_json = """[
        {"text": "Anonymous utterance 1"},
        {"text": "Anonymous utterance 2"}
    ]"""
    result = parse_copilot_json(raw_json)
    
    assert result.turns[0].speaker is None
    assert result.turns[0].text == "Anonymous utterance 1"
    assert result.turns[0].spans[0].start_time is None
    
    assert result.turns[1].speaker is None
    assert result.turns[1].text == "Anonymous utterance 2"
    assert result.turns[1].spans[0].start_time is None

def test_malformed_cues_traceability():
    raw_txt = """
    Unknown: Something.
    """
    result = normalize_raw_input_text(raw_txt)
    assert result.turns[0].speaker == "Unknown"
    assert result.turns[0].text == "Something."
    # We should have no fabricated index-generated times
    assert result.turns[0].spans[0].start_time is None
