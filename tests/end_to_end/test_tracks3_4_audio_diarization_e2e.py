"""End-to-end verification of Wave 3 Tracks 3 & 4 audio ingestion, diarization, and stereo overlap re-decode."""
from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.acoustic_airgap import write_acoustic_receipt
from dual_pipeline.audio_ingestion_core.acoustic_cache import build_acoustic_cache_key
from dual_pipeline.audio_ingestion_core.stereo_alignment import (
    align_segments_via_stereo_energy,
    redecode_overlapping_stereo_windows,
)
from dual_pipeline.audio_ingestion_core.subcluster_splitter import bridge_live_voice_clusters_to_batch
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary
from pipeline_a_intake.audio_chunker import stitch_and_align_segments


def _write_synthetic_stereo_wav(path: Path, sr: int = 16000) -> None:
    """Writes a 6-second 2-channel stereo WAV with Ch0 remote, Ch1 local, and simultaneous overlap."""
    t = np.linspace(0, 6.0, sr * 6, endpoint=False)
    ch0 = np.zeros_like(t)
    ch1 = np.zeros_like(t)
    # 0-2s: Ch0 (remote) active
    ch0[: sr * 2] = 2500.0 * np.sin(2 * np.pi * 440 * t[: sr * 2])
    # 2-4s: Ch1 (local) active
    ch1[sr * 2 : sr * 4] = 2500.0 * np.sin(2 * np.pi * 330 * t[sr * 2 : sr * 4])
    # 4-6s: Both Ch0 and Ch1 overlapping
    ch0[sr * 4 :] = 2200.0 * np.sin(2 * np.pi * 440 * t[sr * 4 :])
    ch1[sr * 4 :] = 2100.0 * np.sin(2 * np.pi * 330 * t[sr * 4 :])
    interleaved = np.empty(sr * 12, dtype=np.int16)
    interleaved[0::2] = ch0.astype(np.int16)
    interleaved[1::2] = ch1.astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(interleaved.tobytes())


def test_stereo_energy_alignment_and_hybrid_overlap_redecode_e2e(tmp_path: Path) -> None:
    """Verifies DEC-INGEST-02 hybrid single-pass stereo alignment + overlap window re-decoding."""
    wav_path = tmp_path / "stereo_meeting.wav"
    _write_synthetic_stereo_wav(wav_path)

    raw_segments = [
        {"start": 0.1, "end": 1.9, "text": "Remote update on architecture."},
        {"start": 2.1, "end": 3.9, "text": "Local host response on budget."},
        {"start": 4.1, "end": 5.9, "text": "Garbled simultaneous crosstalk window."},
    ]

    aligned = align_segments_via_stereo_energy(raw_segments, wav_path)
    assert len(aligned) == 3
    assert aligned[0]["channel"] == 0
    assert aligned[0]["overlap"] is False
    assert aligned[1]["channel"] == 1
    assert aligned[1]["overlap"] is False
    assert aligned[2]["overlap"] is True
    assert aligned[2]["overlap_redecode_required"] is True

    redecoded = redecode_overlapping_stereo_windows(
        aligned,
        wav_path,
        transcribe_window_fn=lambda _p, ch, _st, _en: (
            "Remote speaker finishing the point." if ch == 0 else "Host interjecting with timeline."
        ),
    )
    assert len(redecoded) == 4
    assert redecoded[2]["speaker"] == "SPEAKER_00"
    assert redecoded[2]["channel"] == 1
    assert redecoded[2]["text"] == "Host interjecting with timeline."
    assert redecoded[3]["speaker"] == "SPEAKER_01"
    assert redecoded[3]["channel"] == 0
    assert redecoded[3]["text"] == "Remote speaker finishing the point."


def test_chunk_stitching_one_to_one_bipartite_speaker_lock() -> None:
    """Verifies _map_overlap_speakers enforces 1-to-1 speaker lock across chunk boundaries."""
    chunks_data = [
        {
            "chunk_index": 0,
            "slice_start": 0.0,
            "authoritative_start": 0.0,
            "authoritative_end": 30.0,
            "raw_segments": [
                {"start": 10.0, "end": 28.0, "speaker": "Speaker 1", "text": "First speaker."},
                {"start": 28.0, "end": 30.0, "speaker": "Speaker 2", "text": "Second speaker."},
            ],
        },
        {
            "chunk_index": 1,
            "slice_start": 20.0,
            "authoritative_start": 30.0,
            "authoritative_end": 60.0,
            "raw_segments": [
                # Both local Speaker A and Speaker B overlap with global Speaker 1,
                # but Speaker B also overlaps with Speaker 2. Greedy max() without 1-to-1 lock
                # would collapse both onto Speaker 1!
                {"start": 0.0, "end": 8.0, "speaker": "Speaker A", "text": "Cont 1."},
                {"start": 5.0, "end": 10.0, "speaker": "Speaker B", "text": "Cont 2."},
                {"start": 12.0, "end": 20.0, "speaker": "Speaker A", "text": "Turn A in chunk 1."},
                {"start": 21.0, "end": 29.0, "speaker": "Speaker B", "text": "Turn B in chunk 1."},
            ],
        },
    ]
    stitched = stitch_and_align_segments(chunks_data, overlap_seconds=10.0)
    chunk1_speakers = {s["speaker"] for s in stitched if s["start"] >= 30.0}
    assert chunk1_speakers == {"Speaker 1", "Speaker 2"}


def test_live_voice_registry_centroids_and_temporal_bridge(tmp_path: Path) -> None:
    """Verifies LiveVoiceRegistry exposes centroids and bridges Voice A/B to SPEAKER_00/01."""
    reg = LiveVoiceRegistry()
    vec_a = np.ones(16, dtype=np.float32)
    vec_b = np.array([1.0 if i % 2 == 0 else -1.0 for i in range(16)], dtype=np.float32)
    reg.resolve_chunk_speaker(tmp_path / "none.wav", chunk_idx=0, text="Hello team", start_sec=0.0, duration_sec=5.0, acoustic_vector=vec_a)
    reg.resolve_chunk_speaker(tmp_path / "none.wav", chunk_idx=1, text="Good morning", start_sec=5.0, duration_sec=5.0, acoustic_vector=vec_b)
    reg.bind_speaker_hint("Voice A", "Amit")
    reg.bind_speaker_hint("Voice B", "Speaker_Beta")

    centroids = reg.get_all_centroids()
    assert "Voice A" in centroids and "Voice B" in centroids
    assert len(centroids["Voice A"]) == 16

    bridged = bridge_live_voice_clusters_to_batch(
        batch_segments=[
            {"start": 0.5, "end": 4.5, "speaker": "SPEAKER_00"},
            {"start": 5.5, "end": 9.5, "speaker": "SPEAKER_01"},
        ],
        live_turns=[
            {"start": 0.0, "end": 5.0, "speaker": "Voice A"},
            {"start": 5.0, "end": 10.0, "speaker": "Voice B"},
        ],
        speaker_map=reg.get_speaker_map(),
    )
    assert bridged == {"SPEAKER_00": "Amit", "SPEAKER_01": "Speaker_Beta"}


def test_backend_cache_key_and_hitl_required_preservation(tmp_path: Path) -> None:
    """Verifies backend-scoped cache keys, provenance receipts, and HITL_REQUIRED status preservation."""
    k_mlx = build_acoustic_cache_key("abc12345", "whisper-large-v3", "en", False, False, backend="mlx-whisper")
    k_fw = build_acoustic_cache_key("abc12345", "whisper-large-v3", "en", False, False, backend="faster-whisper")
    assert k_mlx != k_fw

    rec_path = write_acoustic_receipt("sample_call", "whisper-large-v3", output_dir=tmp_path, backend="faster-whisper")
    assert rec_path is not None and rec_path.is_file()
    payload = json.loads(rec_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "faster-whisper"

    meta = {"diarization_status": "HITL_REQUIRED", "has_acoustic_diarization": False}
    assert U0IngestionBoundary._check_audio_diarization("**SPEAKER_00** [00:00:01]: Hello", meta) == "HITL_REQUIRED"
    assert U0IngestionBoundary._detect_diarization_status("**SPEAKER_00** [00:00:01]: Hello", meta) == "HITL_REQUIRED"
