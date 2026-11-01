import pytest
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt, parse_copilot_json
from dual_pipeline.transcript_pipeline_core.source_spans import NormalizedTranscript

def test_vtt_preserves_cue_and_time_spans():
    raw_vtt = """WEBVTT

cue-123
00:00:01.000 --> 00:00:02.500
<v Speaker 1>First cue.</v>

cue-456
00:00:02.600 --> 00:00:03.000
<v Speaker 1>Second cue.</v>

cue-789
00:00:04.000 --> 00:00:05.000
<v Speaker 2>Third cue.</v>
"""
    result = parse_vtt_or_srt(raw_vtt)
    assert isinstance(result, NormalizedTranscript)
    
    assert len(result.turns) == 2
    
    turn1 = result.turns[0]
    assert turn1.speaker == "Speaker 1"
    assert turn1.start_time == "00:00:01"
    assert turn1.end_time == "00:00:03"
    assert turn1.text == "First cue. Second cue."
    assert len(turn1.spans) == 2
    assert turn1.spans[0].cue_id == "cue-123"
    assert turn1.spans[1].cue_id == "cue-456"
    
    turn2 = result.turns[1]
    assert turn2.speaker == "Speaker 2"
    assert turn2.start_time == "00:00:04"
    assert turn2.end_time == "00:00:05"
    assert turn2.text == "Third cue."
    assert turn2.spans[0].cue_id == "cue-789"

def test_copilot_json_preserves_record_id():
    raw_json = """[
        {"id": "msg-1", "speaker": "Alice", "time": 10.5, "text": "Hello"},
        {"id": "msg-2", "speaker": "Alice", "time": 15.0, "text": "World"},
        {"id": "msg-3", "speaker": "Bob", "time": 20.0, "text": "Hi Alice"}
    ]"""
    result = parse_copilot_json(raw_json)
    assert isinstance(result, NormalizedTranscript)
    
    assert len(result.turns) == 2
    assert result.turns[0].speaker == "Alice"
    assert result.turns[0].text == "Hello World"
    assert len(result.turns[0].spans) == 2
    assert result.turns[0].spans[0].record_id == "msg-1"
    assert result.turns[0].spans[1].record_id == "msg-2"
    assert result.turns[0].spans[0].start_time == "00:00:10"
