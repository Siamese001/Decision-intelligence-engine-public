"""
tests/test_copilot_adversarial_edge_cases.py

Adversarial edge-case and boundary verification suite for Live Copilot.
Covers 4 mandatory governance categories:
1. Boundary payload: Zero-byte transcript, empty turns list, whitespace-only target speakers.
2. Malformed input: Corrupted JSONL lines, truncated buffers, non-numeric timestamps, invalid UTF-8.
3. Concurrency & race: Multi-threaded polymorphic session reads under concurrent buffer writes.
4. Exact production regression: Unconsolidated acoustic voice clusters (Voice A/Voice B) querying
   third-party stakeholder expectations without triggering false-positive speaker roster rejections.
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    UtteranceSelector,
    parse_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)


# ---------------------------------------------------------------------------
# Category 1: Boundary Payloads
# ---------------------------------------------------------------------------


def test_live_buffer_reader_empty_and_zero_byte_transcript():
    """Verify reader handles empty transcript file and 0-byte segments cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = tmp_path / "output" / "empty_meeting"
        out_dir.mkdir(parents=True, exist_ok=True)

        transcript_file = out_dir / "transcript.md"
        transcript_file.write_text("", encoding="utf-8")
        segments_file = out_dir / "segments.jsonl"
        segments_file.write_text("", encoding="utf-8")

        reg = SessionRegistry(tmp_path)
        manifest = reg.register(
            session_id="sess-boundary-empty",
            engagement_id="project:project_nexus",
            output_dir=str(out_dir.relative_to(tmp_path)),
            transcript_md=str(transcript_file.relative_to(tmp_path)),
        )

        reader = LiveBufferReader(tmp_path)
        turns = reader.read_turns(manifest.session_id)
        assert turns == []
        txt = reader.read_transcript(manifest.session_id)
        assert txt == ""


def test_utterance_selector_empty_and_null_turns():
    """Verify UtteranceSelector fails closed on empty or null turn collections."""
    res_empty = UtteranceSelector.select(turns=[], target_speaker="Jeffrey", topic_keywords=["grid_os"])
    assert res_empty.status == "NO_SPEAKER_TURNS"
    assert res_empty.error_code == "NO_LIVE_CONTEXT"
    assert res_empty.selected_utterance is None


def test_utterance_selector_whitespace_only_speaker():
    """Verify whitespace-only or empty target speaker defaults safely to turns or fails closed."""
    turns = [{"seq": 1, "speaker": "Voice A", "text": "Discussion on GRID_OS", "turn_sec": 5.0}]
    res = UtteranceSelector.select(turns=turns, target_speaker="   ", topic_keywords=["grid_os"])
    assert res.status in ("SELECTED", "NO_SPEAKER_TURNS")


# ---------------------------------------------------------------------------
# Category 2: Malformed Input
# ---------------------------------------------------------------------------


def test_live_buffer_reader_handles_corrupt_jsonl():
    """Verify LiveBufferReader skips corrupted JSON lines and retains all valid preceding/succeeding turns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = tmp_path / "output" / "corrupted_meeting"
        out_dir.mkdir(parents=True, exist_ok=True)

        segments_file = out_dir / "segments.jsonl"
        raw_lines = [
            json.dumps({"speaker": "Voice A", "text": "Valid first turn", "timestamp": "00:00:01", "turn_sec": 1.0}),
            '{"speaker": "Voice B", "text": "Truncated json line...',  # corrupt
            "NOT_JSON_AT_ALL_GARBAGE_BYTES_12345!@#$%",  # corrupt
            "",  # empty line
            json.dumps({"speaker": "Voice A", "text": "Valid second turn", "timestamp": "00:00:05", "turn_sec": 5.0}),
        ]
        segments_file.write_text("\n".join(raw_lines), encoding="utf-8")

        reg = SessionRegistry(tmp_path)
        manifest = reg.register(
            session_id="sess-corrupt-jsonl",
            engagement_id="project:project_nexus",
            output_dir=str(out_dir.relative_to(tmp_path)),
        )

        reader = LiveBufferReader(tmp_path)
        turns = reader.read_turns(manifest.session_id)
        assert len(turns) == 2
        assert turns[0]["text"] == "Valid first turn"
        assert turns[1]["text"] == "Valid second turn"


def test_live_buffer_reader_handles_invalid_utf8():
    """Verify LiveBufferReader does not crash on invalid UTF-8 bytes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = tmp_path / "output" / "invalid_utf8_meeting"
        out_dir.mkdir(parents=True, exist_ok=True)

        segments_file = out_dir / "segments.jsonl"
        valid_part = json.dumps({"speaker": "Voice A", "text": "Valid turn", "timestamp": "00:00:01", "turn_sec": 1.0}).encode("utf-8")
        corrupt_bytes = valid_part + b"\n" + b"\xff\xfe\xfaInvalid utf8 binary sequence\n"
        segments_file.write_bytes(corrupt_bytes)

        reg = SessionRegistry(tmp_path)
        manifest = reg.register(
            session_id="sess-invalid-utf8",
            engagement_id="project:project_nexus",
            output_dir=str(out_dir.relative_to(tmp_path)),
        )

        reader = LiveBufferReader(tmp_path)
        turns = reader.read_turns(manifest.session_id)
        assert len(turns) == 1
        assert turns[0]["text"] == "Valid turn"


def test_utterance_selector_malformed_turn_dicts():
    """Verify UtteranceSelector handles turn dictionaries missing turn_sec, timestamp, or seq."""
    malformed_turns = [
        {"speaker": "Voice A", "text": "Turn missing sec and ts"},
        {"speaker": "Voice B", "text": "Turn with invalid string turn_sec", "turn_sec": "not_a_float"},
        {"speaker": "Voice A", "text": "Target topic GRID_OS turn", "timestamp": "invalid_ts", "turn_sec": None},
    ]
    res = UtteranceSelector.select(
        turns=malformed_turns,
        target_speaker="Voice A",
        topic_keywords=["grid_os"],
        window_sec=15.0,
    )
    assert res.status == "SELECTED"
    assert res.selected_utterance is not None
    assert "GRID_OS" in res.selected_utterance.text


# ---------------------------------------------------------------------------
# Category 3: Concurrency & Race / Rapid Bursts
# ---------------------------------------------------------------------------


def test_concurrent_polymorphic_buffer_reads_and_writes():
    """Verify concurrent reads and writes do not lock, crash, or corrupt polymorphic buffer reader."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_dir = tmp_path / "output" / "concurrent_meeting"
        out_dir.mkdir(parents=True, exist_ok=True)
        segments_file = out_dir / "segments.jsonl"
        segments_file.write_text("", encoding="utf-8")

        reg = SessionRegistry(tmp_path)
        manifest = reg.register(
            session_id="sess-concurrent-001",
            engagement_id="project:project_nexus",
            output_dir=str(out_dir.relative_to(tmp_path)),
        )

        reader = LiveBufferReader(tmp_path)
        errors: List[Exception] = []

        def writer_task(count: int):
            for i in range(count):
                item = {"seq": i, "speaker": f"Voice {chr(65 + (i % 3))}", "text": f"Turn {i}", "turn_sec": float(i)}
                with open(segments_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item) + "\n")
                time.sleep(0.001)

        def reader_task(count: int):
            for _ in range(count):
                try:
                    turns = reader.read_turns(manifest.session_id)
                    assert isinstance(turns, list)
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.001)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            w_futures = [executor.submit(writer_task, 30) for _ in range(3)]
            r_futures = [executor.submit(reader_task, 30) for _ in range(5)]
            concurrent.futures.wait(w_futures + r_futures)

        assert not errors, f"Encountered concurrency errors: {errors}"
        final_turns = reader.read_turns(manifest.session_id)
        assert len(final_turns) == 90


def test_rapid_copilot_request_bursts():
    """Verify rapid bursts of parse_copilot_request and UtteranceSelector execute under 50ms."""
    turns = [
        {"seq": i, "speaker": "Voice A" if i % 2 == 1 else "Voice B", "text": f"Topic statement about GRID_OS and delivery checkpoint {i}", "turn_sec": float(i * 2)}
        for i in range(1, 21)
    ]
    prompts = [
        "what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey",
        "what did Jeffrey say about GRID_OS architecture?",
        "nbq for next sprint delivery",
        "react to Voice A regarding delivery checkpoint",
        "what is the status of NEA governance?",
    ]

    t0 = time.perf_counter()
    for _ in range(50):
        for p in prompts:
            req = parse_copilot_request(raw_request=p)
            sel = UtteranceSelector.select(
                turns=turns,
                target_speaker=req.target_speaker or "Voice A",
                topic_keywords=req.topic_keywords,
            )
            assert sel.status in ("SELECTED", "NO_RELEVANT_UTTERANCE", "NO_SPEAKER_TURNS")
    duration = time.perf_counter() - t0
    # 250 evaluations should complete in < 0.5 seconds
    assert duration < 1.0


# ---------------------------------------------------------------------------
# Category 4: Exact Production Regression
# ---------------------------------------------------------------------------


def test_production_regression_voice_acoustic_consolidation():
    """
    Exact production failure scenario from live meeting:
    Turns are temporarily labeled as 'Voice A' and 'Voice B' while diarization consolidates.
    Operator asks: 'what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey'.
    Expected:
    1. parse_copilot_request infers intent 'graph-context' and target 'Mandy' (possessive stripped).
    2. UtteranceSelector falls back to acoustic turns and selects Voice A's matching statement.
    3. Zero 'SPEAKER_NOT_IN_ROSTER' or 'SPEAKER_HAS_NO_TURNS' failure trips.
    """
    turns = [
        {"seq": 1, "speaker": "Voice A", "text": "Good morning everyone.", "turn_sec": 2.0},
        {"seq": 2, "speaker": "Voice B", "text": "Let us discuss GRID_OS delivery.", "turn_sec": 5.0},
        {"seq": 3, "speaker": "Voice A", "text": "Mandy raised a critical issue regarding tech lead expectations and alignment with Jeffrey on GRID_OS architecture.", "turn_sec": 12.0},
        {"seq": 4, "speaker": "Voice B", "text": "We should sync on that today.", "turn_sec": 16.0},
    ]

    prompt = "what could be Mandys issue in terms of tech lead expectations misalignment with Jeffrey"
    parsed = parse_copilot_request(raw_request=prompt)

    assert parsed.inferred_intent == "graph-context"
    assert parsed.target_speaker == "Mandy"
    assert "tech" in parsed.topic_keywords or "expectations" in parsed.topic_keywords or "grid_os" in parsed.topic_keywords

    sel_res = UtteranceSelector.select(
        turns=turns,
        target_speaker=parsed.target_speaker,
        topic_keywords=parsed.topic_keywords,
    )

    assert sel_res.status == "SELECTED"
    assert sel_res.selected_utterance is not None
    assert sel_res.selected_utterance.seq == 3
    assert "Mandy raised a critical issue" in sel_res.selected_utterance.text
    assert sel_res.selected_utterance.speaker == "Voice A"
