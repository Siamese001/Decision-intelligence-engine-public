"""
End-to-end verification for Wave 1 (Hotspot #4):
- Stereo 2-channel isolation (Ch1 = Local -> SPEAKER_00, Ch0 = Remote -> SPEAKER_01..SPEAKER_03) across 4 speakers (1 local + 3 remote).
- Word-level candidate_speakers polarity consistency in _standardize_stereo_segment.
- WhisperX intra-segment backchannel preservation (lock_sentence_speaker=False) and shorter-interjection tie-breaking in speaker_alignment.py.
- Pre-hint collapse detection, interactive HITL resolution signing canonical subject_sha256, and Gate 1.7 verification.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    AcousticInterval,
    align_segments_with_diarization,
)
from dual_pipeline.audio_ingestion_core.subcluster_splitter import (
    align_and_diarize_dual_channel_stereo,
    detect_stereo_channel_activity,
)
from dual_pipeline.speaker_attribution_core.attribution_engine import AudioSpeakerAttributionEngine
from dual_pipeline.transcript_qa_core.gate1_alr import check_ambiguity_lock_register_and_speaker_hitl
from pipeline_a_intake.audio_chunker import stitch_and_align_segments


def _write_four_speaker_stereo_wav(wav_path: Path, sr: int = 16000) -> Path:
    """Creates a 16-second 2-channel WAV: Ch1 has local speaker (t=0..4s at 400Hz), Ch0 has 3 remote speakers (t=4..8s at 200Hz, t=8..12s at 600Hz, t=12..16s at 1100Hz)."""
    total_samples = sr * 16
    t = np.arange(total_samples, dtype=np.float32) / sr
    ch0 = np.zeros(total_samples, dtype=np.float32)
    ch1 = np.zeros(total_samples, dtype=np.float32)

    ch1[0 : 4 * sr] = 9000.0 * np.sin(2.0 * np.pi * 400.0 * t[0 : 4 * sr])
    ch0[4 * sr : 8 * sr] = 8000.0 * np.sin(2.0 * np.pi * 200.0 * t[4 * sr : 8 * sr])
    ch0[8 * sr : 12 * sr] = 8000.0 * np.sin(2.0 * np.pi * 600.0 * t[8 * sr : 12 * sr])
    ch0[12 * sr : 16 * sr] = 8000.0 * np.sin(2.0 * np.pi * 1100.0 * t[12 * sr : 16 * sr])

    stereo = np.stack([ch0, ch1], axis=1).astype(np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())
    return wav_path


def test_stereo_four_speaker_partitioning_and_word_candidate_polarity(tmp_path: Path) -> None:
    """Verifies Ch1 local operator (SPEAKER_00) and 3 remote speakers on Ch0 (SPEAKER_01, SPEAKER_02, SPEAKER_03) with consistent word candidate_speakers."""
    wav_path = _write_four_speaker_stereo_wav(tmp_path / "four_speaker_stereo.wav")
    act = detect_stereo_channel_activity(wav_path)
    assert act["is_dual_active_stereo"] is True

    rng = np.random.default_rng(7)
    emb_r1 = np.zeros(256, dtype=np.float32)
    emb_r1[0] = 1.0
    emb_r2 = np.zeros(256, dtype=np.float32)
    emb_r2[1] = 1.0
    emb_r3 = np.zeros(256, dtype=np.float32)
    emb_r3[2] = 1.0

    segments: List[Dict[str, Any]] = [
        {
            "start": 0.5,
            "end": 1.8,
            "text": "Let us review the architecture roadmap today.",
            "words": [{"word": "Let", "start": 0.5, "end": 0.8}, {"word": "us", "start": 0.9, "end": 1.8}],
        },
        {
            "start": 2.0,
            "end": 3.5,
            "text": "We have three workstreams on the agenda.",
            "words": [{"word": "We", "start": 2.0, "end": 2.5}, {"word": "have", "start": 2.6, "end": 3.5}],
        },
        {
            "start": 4.2,
            "end": 5.8,
            "text": "Data platform ingestion is on schedule.",
            "embedding": (emb_r1 + 0.01 * rng.normal(size=256)).tolist(),
        },
        {
            "start": 6.0,
            "end": 7.8,
            "text": "Pipeline latency dropped by forty percent.",
            "embedding": (emb_r1 + 0.01 * rng.normal(size=256)).tolist(),
        },
        {
            "start": 8.2,
            "end": 9.8,
            "text": "Security governance controls passed verification.",
            "embedding": (emb_r2 + 0.01 * rng.normal(size=256)).tolist(),
        },
        {
            "start": 10.0,
            "end": 11.8,
            "text": "Zero credential leaks were found in staging.",
            "embedding": (emb_r2 + 0.01 * rng.normal(size=256)).tolist(),
        },
        {
            "start": 12.2,
            "end": 13.8,
            "text": "Deployment cutover is ready for Friday.",
            "embedding": (emb_r3 + 0.01 * rng.normal(size=256)).tolist(),
        },
        {
            "start": 14.0,
            "end": 15.8,
            "text": "All stakeholder signoffs are complete.",
            "embedding": (emb_r3 + 0.01 * rng.normal(size=256)).tolist(),
        },
    ]
    meta: Dict[str, Any] = {
        "local_speaker": "Operator Host",
        "attendees": ["Operator Host", "Remote One", "Remote Two", "Remote Three"],
        "speaker_hints": [
            {"speaker_name": "Remote One", "quote": "Data platform ingestion"},
            {"speaker_name": "Remote Two", "quote": "Security governance controls"},
            {"speaker_name": "Remote Three", "quote": "Deployment cutover is ready"},
        ],
    }

    aligned = align_and_diarize_dual_channel_stereo(
        segments,
        wav_path,
        expected_roster_count=4,
        metadata=meta,
    )
    assert aligned is not None
    assert aligned[0]["speaker"] == "SPEAKER_00"
    assert aligned[1]["speaker"] == "SPEAKER_00"
    # Verify word-level candidate_speakers polarity matches SPEAKER_00 for local Ch1
    w0_cands = {c["speaker"]: c["support_score"] for c in aligned[0]["words"][0]["candidate_speakers"]}
    assert w0_cands["SPEAKER_00"] > w0_cands["SPEAKER_01"]

    remote_speakers = {s["speaker"] for s in aligned[2:]}
    assert remote_speakers == {"SPEAKER_01", "SPEAKER_02", "SPEAKER_03"}


def test_overlapping_backchannel_tie_break_and_chunk_word_offset() -> None:
    """Verifies overlapping short backchannel wins tie-break and chunk stitcher offsets word timestamps."""
    intervals = [
        AcousticInterval(interval_id="i0", speaker_cluster="SPEAKER_00", start_time_sec=0.0, end_time_sec=10.0),
        AcousticInterval(interval_id="i1", speaker_cluster="SPEAKER_01", start_time_sec=4.0, end_time_sec=4.6),
    ]
    segments = [
        {
            "start": 0.0,
            "end": 8.0,
            "text": "We should deploy today Yeah definitely and monitor metrics",
            "words": [
                {"word": "We", "start": 0.5, "end": 1.0},
                {"word": "should", "start": 1.0, "end": 1.5},
                {"word": "deploy", "start": 1.5, "end": 2.0},
                {"word": "today", "start": 2.0, "end": 2.5},
                {"word": "Yeah", "start": 4.05, "end": 4.30},
                {"word": "definitely", "start": 4.30, "end": 4.55},
                {"word": "and", "start": 5.5, "end": 6.0},
                {"word": "monitor", "start": 6.0, "end": 6.5},
                {"word": "metrics", "start": 6.5, "end": 7.0},
            ],
        }
    ]
    aligned = align_segments_with_diarization(segments, intervals)
    speakers = [s["speaker"] for s in aligned]
    assert "SPEAKER_01" in speakers
    assert speakers == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]

    chunks_data = [
        {
            "chunk_index": 0,
            "slice_start": 0.0,
            "authoritative_start": 0.0,
            "authoritative_end": 10.0,
            "raw_segments": [
                {"start": 1.0, "end": 3.0, "speaker": "Speaker 1", "text": "First", "words": [{"word": "First", "start": 1.0, "end": 3.0}]},
                {"start": 11.0, "end": 12.0, "speaker": "Speaker 1", "text": "TrailingOverlap", "words": [{"word": "TrailingOverlap", "start": 11.0, "end": 12.0}]},
            ],
        },
        {
            "chunk_index": 1,
            "slice_start": 5.0,
            "authoritative_start": 10.0,
            "authoritative_end": 20.0,
            "raw_segments": [
                {"start": 6.0, "end": 7.0, "speaker": "Speaker 1", "text": "TrailingOverlap", "words": [{"word": "TrailingOverlap", "start": 6.0, "end": 7.0}]},
            ],
        },
    ]
    stitched = stitch_and_align_segments(chunks_data, overlap_seconds=5.0)
    assert len(stitched) == 2
    assert stitched[1]["start"] == 11.0
    assert stitched[1]["words"][0]["start"] == 11.0
    assert stitched[1]["words"][0]["end"] == 12.0


def test_interactive_collapse_resolution_signs_subject_sha256_and_passes_gate1_7(tmp_path: Path) -> None:
    """Verifies interactive HITL resolution of single-speaker collapse signs the canonical subject_sha256 verified by Gate 1.7."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    ledger_file.write_text(json.dumps({"operator_decisions": []}), encoding="utf-8")

    turns = "\n".join(
        f"[00:0{i}:00] Speaker 1: We are reviewing the architecture milestone {i} for the platform."
        for i in range(1, 10)
    ) + "\n[00:10:00] Speaker 2: Sounds good."

    meta: Dict[str, Any] = {
        "project": "apex_trans",
        "meeting_name": "Architecture_Sync",
        "date": "10/24/2026",
        "attendees": ["Alice Vance (Lead)", "Bob Sterling (Architect)"],
        "hitl_ledger_path": str(ledger_file),
    }

    engine = AudioSpeakerAttributionEngine()
    res = engine.analyze_and_resolve(
        turns,
        meta,
        interactive_resolver=lambda q, opts: opts[0],
    )
    assert res.speaker_map
    assert meta.get("subject_sha256")
    assert meta.get("collapse_approval_receipt", {}).get("subject_sha256") == meta["subject_sha256"]

    f1_doc = (
        "# Architecture Sync\n\n"
        f"{res.resolved_transcript}\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Ambiguity ID | Category | Description | Resolution | Status |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| AMB-01 | Speaker Attribution | Alice Vance acoustic cluster attribution | Operator Verified | LOCKED |\n"
        "| AMB-02 | Speaker Attribution | Bob Sterling acoustic cluster attribution | Operator Verified | LOCKED |\n"
        "| AMB-03 | Lexicon | NERC-CIP terminology | Verified | LOCKED |\n"
    )
    passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_doc, meta, ledger_path=ledger_file)
    assert passed is True, f"Gate 1.7 failed unexpectedly: {failures}"
    assert details["hitl_receipt_bound"] is True
