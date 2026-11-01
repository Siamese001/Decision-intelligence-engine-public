"""Adversarial & Edge-Case Verification for Platform Per-Channel Diarization Fusion.

Covers mandatory edge-case categories:
1. Boundary payloads (empty inputs, single-word rapid interjections, zero-turn files).
2. Malformed inputs (corrupted timestamps, out-of-order clock drift, unparseable lines).
3. Invariant bypass attempts (preventing generic/unknown speaker labels from enrolling voiceprints or overriding valid speakers).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.audio_ingestion_core.platform_transcript_fusion import (
    extract_platform_per_channel_turns,
    fuse_mlx_segments_with_platform_diarization,
    load_platform_transcript_text,
    parse_platform_diarized_transcript,
    try_fuse_metadata_platform_transcript,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import assemble_utterances
from dual_pipeline.audio_ingestion_core.voiceprint_harvester import harvest_voiceprints_from_meeting
from dual_pipeline.audio_ingestion_core.voiceprint_store import VoiceprintStore


def test_boundary_payloads_empty_and_single_word_interjections() -> None:
    """Category 1 (Boundary payload): Empty strings, whitespace, and 1-word rapid interjections."""
    assert parse_platform_diarized_transcript("") == []
    assert parse_platform_diarized_transcript("   \n\n  ") == []
    assert load_platform_transcript_text(None) == ""
    assert load_platform_transcript_text("/nonexistent/path/to/transcript.txt") == "/nonexistent/path/to/transcript.txt"

    mlx_fallback = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Fallback segment."}]
    assert fuse_mlx_segments_with_platform_diarization(mlx_fallback, []) == mlx_fallback
    assert try_fuse_metadata_platform_transcript(mlx_fallback, None) is None
    assert try_fuse_metadata_platform_transcript(mlx_fallback, {}) is None

    single_word_turns = parse_platform_diarized_transcript(
        "00:00:10\n"
        "Robson N. Silva: Seven.\n"
        "00:00:10\n"
        "Alex Brannan: Five.\n"
        "00:00:10\n"
        "Robson N. Silva: Agreed.\n"
    )
    assert len(single_word_turns) == 3
    # Verify monotonic sub-second timestamp progression when turns share identical second block
    assert single_word_turns[0]["start"] < single_word_turns[1]["start"] < single_word_turns[2]["start"]
    assembled = assemble_utterances(single_word_turns)
    assert len(assembled) == 3
    assert [u["text"] for u in assembled] == ["Seven.", "Five.", "Agreed."]


def test_malformed_inputs_corrupted_timestamps_and_clock_drift() -> None:
    """Category 2 (Malformed input): Non-monotonic clock backward jumps, header noise, and malformed lines."""
    malformed_raw = (
        "# Header Noise\n"
        "**Date:** 10/27/2026\n"
        "|| invalid table row ||\n"
        "[bad:timestamp]\n"
        "00:10:00\n"
        "Rusty Gattmann: First valid turn at ten minutes.\n"
        "00:02:00\n"
        "Craig Finch: Clock jumped backward to two minutes in corrupted export.\n"
        "Random unattributed line without speaker colon\n"
    )
    turns = parse_platform_diarized_transcript(malformed_raw)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Rusty Gattmann"
    assert turns[1]["speaker"] == "Craig Finch"
    # Monotonicity guard clamps backward clock drift so start >= previous turn start
    assert turns[1]["start"] >= turns[0]["start"]
    assert turns[1]["end"] > turns[1]["start"]


def test_invariant_bypass_generic_and_unknown_speaker_rejection(tmp_path: Path) -> None:
    """Category 3 (Invariant bypass attempt): Rejects generic/placeholder speakers from fusion and voiceprint enrollment."""
    bypass_raw = (
        "00:01:00\n"
        "Unknown: Should be rejected.\n"
        "00:01:02\n"
        "Speaker 1: Should also be rejected.\n"
        "00:01:05\n"
        "SPEAKER_00: Raw cluster ID must not pass as human channel speaker.\n"
        "00:01:08\n"
        "Voice A: Generic voice label must be rejected.\n"
        "00:01:10\n"
        "Unattributed: Placeholder must be rejected.\n"
    )
    assert parse_platform_diarized_transcript(bypass_raw) == []

    m_dir = tmp_path / "bypass_meeting"
    m_dir.mkdir(parents=True)
    (m_dir / "google_meet_transcript.txt").write_text(bypass_raw, encoding="utf-8")
    (m_dir / "00_master.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    v_map, v_turns, _ = extract_platform_per_channel_turns(m_dir)
    assert v_map == {}
    assert v_turns == []

    store = VoiceprintStore(db_path=tmp_path / "bypass_vp.sqlite")
    res = harvest_voiceprints_from_meeting(meeting_dir=m_dir, store=store)
    assert res["status"] == "SKIPPED_NO_TRANSCRIPT"
    assert res["enrolled"] == []
