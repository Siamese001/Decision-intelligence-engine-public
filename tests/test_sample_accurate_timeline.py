"""Tests for Wave 4: Sample-Accurate Timeline Accounting & Speculative Turn Preview.
Validates sub-10ms precision across multiple audio chunks, exact timing fields in segments.jsonl,
speculative preview broadcast over WebSocket, and idempotency.
"""
from __future__ import annotations

import contextlib
import json
import math
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder


def create_synthetic_wav(
    path: Path, num_frames: int, sample_rate: int = 16000, frequency: float = 440.0
) -> Path:
    """Generates a synthetic 16-bit mono PCM WAV file with audible sine wave."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        samples = [
            int(2000 * math.sin(2 * math.pi * frequency * i / sample_rate))
            for i in range(num_frames)
        ]
        w.writeframes(struct.pack(f"{num_frames}h", *samples))
    return path


def safe_stop(rec: LiveMeetingRecorder) -> None:
    with contextlib.suppress(Exception):
        rec.stop()


def test_sample_accurate_cumulative_timeline(tmp_path: Path):
    """Sub-10ms precision across multiple chunks against cumulative duration."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # 160,000 frames @ 16kHz = 10.00s
    # 159,200 frames @ 16kHz = 9.95s
    # 160,800 frames @ 16kHz = 10.05s
    c0 = create_synthetic_wav(tmp_path / "chunk_00000.wav", 160_000)
    c1 = create_synthetic_wav(tmp_path / "chunk_00001.wav", 159_200)
    c2 = create_synthetic_wav(tmp_path / "chunk_00002.wav", 160_800)

    rec = LiveMeetingRecorder(
        meeting_name="Timeline Precision Test",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        rec.process_chunk(c0, 0)
        rec.process_chunk(c1, 1)
        rec.process_chunk(c2, 2)

        # Cumulative elapsed should equal exactly 10.0 + 9.95 + 10.05 = 30.0s (precision < 10ms)
        assert abs(rec._cumulative_elapsed_seconds - 30.0) < 0.01

        # Check recorded chunk timings
        assert rec._chunk_timings[0] == pytest.approx((0.0, 10.0), abs=0.001)
        assert rec._chunk_timings[1] == pytest.approx((10.0, 9.95), abs=0.001)
        assert rec._chunk_timings[2] == pytest.approx((19.95, 10.05), abs=0.001)

        # Check segments.jsonl
        assert rec.segments_jsonl.exists()
        with open(rec.segments_jsonl, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 3

        # Seg 0
        assert lines[0]["chunk_idx"] == 0
        assert lines[0]["start_sec"] == 0.0
        assert lines[0]["duration_sec"] == 10.0
        assert lines[0]["end_sec"] == 10.0
        assert lines[0]["seconds"] == 0
        assert lines[0]["is_final"] is True

        # Seg 1
        assert lines[1]["chunk_idx"] == 1
        assert lines[1]["start_sec"] == 10.0
        assert lines[1]["duration_sec"] == 9.95
        assert lines[1]["end_sec"] == 19.95
        assert lines[1]["seconds"] == 10
        assert lines[1]["is_final"] is True

        # Seg 2
        assert lines[2]["chunk_idx"] == 2
        assert lines[2]["start_sec"] == 19.95
        assert lines[2]["duration_sec"] == 10.05
        assert lines[2]["end_sec"] == 30.0
        assert lines[2]["seconds"] == 20
        assert lines[2]["is_final"] is True
    finally:
        safe_stop(rec)


def test_speculative_preview_emission(tmp_path: Path):
    """Speculative preview emits over WebSocket without persisting to segments or markdown."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    c0 = create_synthetic_wav(tmp_path / "chunk_00000.wav", 80_000)  # 5s

    rec = LiveMeetingRecorder(
        meeting_name="Preview Test",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        # Wave 6: speculative preview is eliminated
        res = rec.emit_speculative_preview(c0, 0)
        assert res is None
        assert rec.ws_server is None
    finally:
        safe_stop(rec)


def test_process_latest_chunk_no_speculative_preview(tmp_path: Path):
    """Growing chunk does not trigger speculative preview (M_1 = 1.0 ASR budget)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    c0 = create_synthetic_wav(tmp_path / "chunk_00000.wav", 80_000)

    rec = LiveMeetingRecorder(
        meeting_name="Latest Chunk Preview Test",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        rec.process_chunk = MagicMock()
        last_file_sizes: dict[str, int] = {}

        # Simulating first check of a growing chunk
        rec._process_latest_chunk(c0, last_file_sizes)

        # process_chunk should NOT be called on a growing chunk
        rec.process_chunk.assert_not_called()
    finally:
        safe_stop(rec)


def test_timeline_idempotence_and_resumption(tmp_path: Path):
    """Repeated calls for the same chunk_idx return cached timeline without drift."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    c0 = create_synthetic_wav(tmp_path / "chunk_00000.wav", 160_000)

    rec = LiveMeetingRecorder(
        meeting_name="Idempotence Test",
        output_dir=out_dir,
        mock_mode=True,
        enforce_worktree=False,
    )
    try:
        t1 = rec._compute_chunk_timeline(0, c0)
        t2 = rec._compute_chunk_timeline(0, c0)
        assert t1 == t2
        assert rec._cumulative_elapsed_seconds == pytest.approx(10.0, abs=0.001)

        # Test resumption starting at chunk_idx = 5
        c5 = create_synthetic_wav(tmp_path / "chunk_00005.wav", 160_000)
        rec2 = LiveMeetingRecorder(
            meeting_name="Resumption Timeline Test",
            output_dir=out_dir,
            mock_mode=True,
            segment_seconds=10,
            enforce_worktree=False,
        )
        try:
            start_sec, dur, end_sec = rec2._compute_chunk_timeline(5, c5)
            # Baseline should start at 5 * 10 = 50.0
            assert start_sec == pytest.approx(50.0, abs=0.001)
            assert dur == pytest.approx(10.0, abs=0.001)
            assert end_sec == pytest.approx(60.0, abs=0.001)
            assert rec2._cumulative_elapsed_seconds == pytest.approx(60.0, abs=0.001)
        finally:
            safe_stop(rec2)
    finally:
        safe_stop(rec)
