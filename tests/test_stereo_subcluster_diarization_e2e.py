"""
End-to-end verification for Wave 2:
- Channel-Aware Stereo Diarization: Isolating Ch1 (local mic) to SPEAKER_00 and partitioning
  Ch0 (remote audio) into balanced remote speaker clusters (SPEAKER_01, SPEAKER_02).
- Merged-Cluster Sub-Splitting: Detecting the 72:3 remote cluster starvation collapse and
  re-clustering merged remote turns into balanced speaker clusters using per-turn acoustic
  embeddings anchored by operator quote hints and third-person name exclusions.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from dual_pipeline.audio_ingestion_core.subcluster_splitter import (
    align_and_diarize_dual_channel_stereo,
    detect_starved_or_merged_clusters,
    detect_stereo_channel_activity,
    split_merged_remote_cluster,
)


def _write_stereo_fixture_wav(
    wav_path: Path,
    sr: int = 16000,
    duration_sec: int = 12,
    dual_active: bool = True,
) -> Path:
    """Creates a 16kHz 2-channel stereo WAV where Ch1 has local speech (t=4..8s) and Ch0 has two remote voices (t=0..4s at 220Hz, t=8..12s at 660Hz)."""
    total_samples = sr * duration_sec
    t = np.arange(total_samples, dtype=np.float32) / sr
    ch0 = np.zeros(total_samples, dtype=np.float32)
    ch1 = np.zeros(total_samples, dtype=np.float32)

    # Remote speaker A (Miles, low pitch 220Hz) on Ch0 from 0..4s
    s0, e0 = 0, 4 * sr
    ch0[s0:e0] = 8000.0 * np.sin(2.0 * np.pi * 220.0 * t[s0:e0])

    # Local speaker (Amit, 440Hz) on Ch1 from 4..8s (or Ch1 if single-channel active)
    s1, e1 = 4 * sr, 8 * sr
    if dual_active:
        ch1[s1:e1] = 9000.0 * np.sin(2.0 * np.pi * 440.0 * t[s1:e1])
    else:
        ch0[s1:e1] = 9000.0 * np.sin(2.0 * np.pi * 440.0 * t[s1:e1])

    # Remote speaker B (Skyler, higher pitch 880Hz) on Ch0 from 8..12s
    s2, e2 = 8 * sr, 12 * sr
    ch0[s2:e2] = 8000.0 * np.sin(2.0 * np.pi * 880.0 * t[s2:e2])

    stereo = np.stack([ch0, ch1], axis=1).astype(np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())
    return wav_path


def test_dual_channel_stereo_isolates_local_and_splits_remote_channel(tmp_path: Path) -> None:
    """Verifies dual-active stereo WAV binds Ch1 to SPEAKER_00 (local) and splits Ch0 into SPEAKER_01 and SPEAKER_02."""
    wav_path = _write_stereo_fixture_wav(tmp_path / "stereo_meeting.wav", dual_active=True)
    activity = detect_stereo_channel_activity(wav_path)
    assert activity["is_dual_active_stereo"] is True

    raw_segments: List[Dict[str, Any]] = [
        {"start": 0.2, "end": 1.8, "text": "The point I'm making, even implicitly, is about the numbers."},
        {"start": 2.0, "end": 3.8, "text": "It's just bizarre because like what does the data scientist do? They explain numbers."},
        {"start": 4.2, "end": 5.8, "text": "Let me pull up the SOW accuracy target on my screen."},
        {"start": 6.0, "end": 7.8, "text": "We need to coach the team through the client narrative."},
        {"start": 8.2, "end": 9.8, "text": "I know Miles is working on the presentation slides right now."},
        {"start": 10.0, "end": 11.8, "text": "I can review the random forest confidence levers with the team."},
    ]
    metadata: Dict[str, Any] = {
        "attendees": ["Amit Ayer", "Miles Erickson", "Skyler Carlson"],
        "speaker_hints": [
            {"speaker_name": "Miles Erickson", "quote": "implicitly"},
            {"speaker_name": "Miles Erickson", "quote": "what does the data scientist do"},
        ],
    }

    aligned = align_and_diarize_dual_channel_stereo(
        raw_segments,
        wav_path,
        expected_roster_count=3,
        metadata=metadata,
    )
    assert aligned is not None
    assert metadata["diarization_method"] == "stereo_channel_isolated"

    # Local channel segments (indices 2, 3) must be isolated to SPEAKER_00
    assert aligned[2]["speaker"] == "SPEAKER_00"
    assert aligned[3]["speaker"] == "SPEAKER_00"

    # Remote channel segments (indices 0, 1 = Miles at 220Hz; indices 4, 5 = Skyler at 880Hz) must be separated into SPEAKER_01 and SPEAKER_02
    assert aligned[0]["speaker"] == "SPEAKER_01"
    assert aligned[1]["speaker"] == "SPEAKER_01"
    assert aligned[4]["speaker"] == "SPEAKER_02"
    assert aligned[5]["speaker"] == "SPEAKER_02"


def test_collapsed_72_vs_3_remote_cluster_subsplit_with_hints_and_third_person_cues() -> None:
    """Reproduces the exact 10/21/2026 67:72:3 collapse and verifies sub-cluster splitting into balanced remote clusters."""
    rng = np.random.default_rng(42)
    base_miles = np.zeros(256, dtype=np.float32)
    base_miles[0] = 1.0
    base_skyler = np.zeros(256, dtype=np.float32)
    base_skyler[1] = 1.0
    base_amit = np.zeros(256, dtype=np.float32)
    base_amit[2] = 1.0

    segments: List[Dict[str, Any]] = []
    t_cursor = 0.0

    # 67 turns of SPEAKER_00 (Amit Ayer)
    for i in range(67):
        v = base_amit + 0.05 * rng.normal(size=256).astype(np.float32)
        segments.append({
            "start": t_cursor,
            "end": t_cursor + 4.0,
            "speaker": "SPEAKER_00",
            "raw_speaker": "SPEAKER_00",
            "text": f"Amit turn {i} discussing client SOW expectations.",
            "embedding": v.tolist(),
        })
        t_cursor += 5.0

    # 72 turns of SPEAKER_01 where 36 are actually Miles Erickson and 36 are Skyler Carlson
    for i in range(72):
        is_miles = (i % 2 == 0)
        base_v = base_miles if is_miles else base_skyler
        v = base_v + 0.08 * rng.normal(size=256).astype(np.float32)
        if i == 0:
            txt = "Talking about. No, the point that I'm making, even implicitly, why is it calling to all the same things?"
        elif i == 2:
            txt = "I know, wait until we start changing things, right? He's old."
        elif i == 4:
            txt = "It's just bizarre because like what does the data scientist do? They explain numbers."
        elif i == 1:
            txt = "I know Miles is working on that deck for the leadership review."
        else:
            txt = f"Remote participant turn {i} on the call."
        segments.append({
            "start": t_cursor,
            "end": t_cursor + 4.0,
            "speaker": "SPEAKER_01",
            "raw_speaker": "SPEAKER_01",
            "text": txt,
            "embedding": v.tolist(),
        })
        t_cursor += 5.0

    # 3 tiny starved turns of SPEAKER_02 (< 5% of total 142 turns -> 2.1%)
    for i in range(3):
        v = base_skyler + 0.05 * rng.normal(size=256).astype(np.float32)
        segments.append({
            "start": t_cursor,
            "end": t_cursor + 1.0,
            "speaker": "SPEAKER_02",
            "raw_speaker": "SPEAKER_02",
            "text": "Right.",
            "embedding": v.tolist(),
        })
        t_cursor += 2.0

    diag = detect_starved_or_merged_clusters(segments, expected_roster_count=3)
    assert diag["is_starved"] is True
    assert "SPEAKER_02" in diag["starved_clusters"]

    metadata: Dict[str, Any] = {
        "attendees": ["Amit Ayer", "Miles Erickson", "Skyler Carlson"],
        "speaker_hints": [
            {"speaker_name": "Miles Erickson", "quote": "implicitly"},
            {"speaker_name": "Miles Erickson", "quote": "he is old"},
            {
                "speaker_name": "Miles Erickson",
                "quote": "It's just bizarre because like what does the data scientist do they explain numbers",
            },
        ],
    }

    updated_segments, split_meta = split_merged_remote_cluster(segments, metadata=metadata)
    assert split_meta["split_performed"] is True
    counts_after = split_meta["counts_after"]
    total = sum(counts_after.values())

    # Verify both remote clusters now have > 10% turn share (actually ~25% each: 36 and 39 turns)
    assert counts_after["SPEAKER_00"] == 67
    assert counts_after["SPEAKER_01"] == 36
    assert counts_after["SPEAKER_02"] == 39
    assert (counts_after["SPEAKER_01"] / total) > 0.20
    assert (counts_after["SPEAKER_02"] / total) > 0.20
