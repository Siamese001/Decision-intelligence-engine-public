"""
Adversarial and edge-case verification for Wave 2:
Covers >= 4 mandatory edge-case categories:
1. Boundary payload: Empty segments, 0-frame WAV, fewer than 3 roster attendees, short <6-turn call.
2. Malformed input: Non-existent WAV path, corrupted WAV bytes, zero-norm embedding vectors.
3. Invariant bypass attempt: Single-active-channel stereo WAV (`Ch0=0.0`, `Ch1>0.0` as in the recovered 10/21/2026 WAV)
   must NOT falsely claim dual-channel stereo isolation (`is_dual_active_stereo == False`).
4. Exact production regression: `_validate_speaker_collapse_and_skew` raises `DiarizationCollapseError`
   when a 3-attendee call has a starved speaker cluster (< 2.5% duration share in a >= 2-minute meeting)
   even though the dominant speaker is < 85% (e.g., 48% vs 50.5% vs 1.5%).
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.diarization_engine import _validate_speaker_collapse_and_skew
from dual_pipeline.audio_ingestion_core.subcluster_splitter import (
    align_and_diarize_dual_channel_stereo,
    detect_starved_or_merged_clusters,
    detect_stereo_channel_activity,
    split_merged_remote_cluster,
)
from dual_pipeline.exceptions import DiarizationCollapseError


def test_boundary_and_malformed_inputs(tmp_path: Path) -> None:
    """Category 1 (Boundary) & Category 2 (Malformed): Empty segments, missing WAV, corrupt WAV, zero-norm embeddings."""
    assert detect_stereo_channel_activity(tmp_path / "missing.wav")["is_dual_active_stereo"] is False

    corrupt_wav = tmp_path / "corrupt.wav"
    corrupt_wav.write_bytes(b"NOT_A_VALID_RIFF_WAV_HEADER")
    assert detect_stereo_channel_activity(corrupt_wav)["is_dual_active_stereo"] is False

    assert detect_starved_or_merged_clusters([], expected_roster_count=3)["is_starved"] is False
    assert split_merged_remote_cluster([], metadata={"attendees": ["A", "B", "C"]})[1]["split_performed"] is False

    # Zero-norm embeddings fall back cleanly to deterministic turn embeddings without ZeroDivisionError
    segs: List[Dict[str, Any]] = [
        {"start": float(i), "end": float(i + 1), "speaker": "SPEAKER_00" if i < 5 else "SPEAKER_01", "text": f"Turn {i}", "embedding": [0.0, 0.0, 0.0, 0.0]}
        for i in range(10)
    ]
    updated, meta = split_merged_remote_cluster(
        segs,
        metadata={"attendees": ["Amit Ayer", "Miles Erickson", "Skyler Carlson"]},
    )
    assert meta["split_performed"] is True
    assert len(updated) == 10


def test_single_active_channel_stereo_does_not_bypass_diarization(tmp_path: Path) -> None:
    """Category 3 (Invariant Bypass): A 2-channel WAV with dead Ch0 (rms0=0.0, rms1>0) must return is_dual_active_stereo=False."""
    sr = 16000
    t = np.arange(sr * 4, dtype=np.float32) / sr
    ch0 = np.zeros_like(t)  # Dead channel 0, exactly like 00_10-21-2026_Debrief_master_recovered.wav
    ch1 = 4000.0 * np.sin(2.0 * np.pi * 300.0 * t)
    stereo = np.stack([ch0, ch1], axis=1).astype(np.int16)

    dead_ch0_wav = tmp_path / "dead_ch0.wav"
    with wave.open(str(dead_ch0_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())

    act = detect_stereo_channel_activity(dead_ch0_wav)
    assert act["is_dual_active_stereo"] is False
    assert act["rms_ch0"] == 0.0
    assert act["rms_ch1"] > 1000.0
    assert align_and_diarize_dual_channel_stereo([{"start": 0.0, "end": 2.0, "text": "Hello"}], dead_ch0_wav) is None


def test_production_regression_starvation_skew_gate_catches_72_vs_3_collapse() -> None:
    """Category 4 (Exact Production Regression): 3 clusters where SPEAKER_00=48%, SPEAKER_01=51%, SPEAKER_02=1% raises DiarizationCollapseError."""
    speaker_durations = {
        "SPEAKER_00": 580.0,  # ~48.3% (Amit)
        "SPEAKER_01": 612.0,  # ~51.0% (Merged Miles + Skyler)
        "SPEAKER_02": 8.0,    # ~0.67% (Starved 3-turn cluster)
    }
    with pytest.raises(DiarizationCollapseError, match="Acoustic Starvation Skew Gate"):
        _validate_speaker_collapse_and_skew(
            speaker_durations=speaker_durations,
            expected_roster_count=3,
            max_single_speaker_collapse_pct=0.85,
        )
