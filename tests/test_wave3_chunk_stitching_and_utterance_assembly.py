"""
Wave 3 Acceptance Tests: Shift-Left Chunk Stitching, Platform Diarization Verification & Utterance Assembly.
"""
from __future__ import annotations

import math
from pathlib import Path

from dual_pipeline.audio_ingestion_core.base import stitch_cross_chunk_clusters
from dual_pipeline.audio_ingestion_core.platform_transcript_fusion import (
    try_fuse_metadata_platform_transcript,
    verify_platform_turns_with_voiceprints,
)
from dual_pipeline.audio_ingestion_core.utterance_assembler import (
    AssemblyConfig,
    assemble_utterances,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    EMBEDDING_DIM,
    VoiceprintStore,
)


def _unit_vec(idx: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[idx % EMBEDDING_DIM] = 0.92
    v[(idx + 1) % EMBEDDING_DIM] = math.sqrt(1.0 - 0.92 * 0.92)
    return v


def test_wave3_cross_chunk_centroid_stitching_prevents_label_permutation() -> None:
    vec_speaker_a = _unit_vec(10)
    vec_speaker_b = _unit_vec(80)

    # Chunk 1 had SPEAKER_00 = A, SPEAKER_01 = B
    prev_centroids = {
        "SPEAKER_00": vec_speaker_a,
        "SPEAKER_01": vec_speaker_b,
    }
    # Chunk 2 permuted labels: SPEAKER_00 = B, SPEAKER_01 = A
    curr_centroids = {
        "SPEAKER_00": vec_speaker_b,
        "SPEAKER_01": vec_speaker_a,
    }
    curr_segments = [
        {"speaker": "SPEAKER_00", "start": 300.0, "end": 304.0, "text": "Continuing from Speaker B."},
        {"speaker": "SPEAKER_01", "start": 305.0, "end": 309.0, "text": "Reply from Speaker A."},
    ]

    stitched, relabel_map = stitch_cross_chunk_clusters(prev_centroids, curr_centroids, curr_segments)
    assert relabel_map == {"SPEAKER_00": "SPEAKER_01", "SPEAKER_01": "SPEAKER_00"}
    assert stitched[0]["speaker"] == "SPEAKER_01"
    assert stitched[0]["raw_chunk_speaker"] == "SPEAKER_00"
    assert stitched[1]["speaker"] == "SPEAKER_00"
    assert stitched[1]["raw_chunk_speaker"] == "SPEAKER_01"


def test_wave3_platform_transcript_fusion_verifies_and_overrides_room_bleed(tmp_path: Path) -> None:
    vp_db = tmp_path / "vp_platform.sqlite"
    store = VoiceprintStore(db_path=vp_db)

    vec_amit = _unit_vec(12)
    vec_clay = _unit_vec(95)

    store.enroll_voiceprint("Amit Ayer", vec_amit, project="sherpa", is_verified=True, audio_sha256="sha_a")
    store.enroll_voiceprint("Clay Palumbo", vec_clay, project="sherpa", is_verified=True, audio_sha256="sha_c")

    platform_raw = (
        "### [00:00:05]\n**Amit Ayer:** We will finalize the architecture today.\n\n"
        "### [00:00:12]\n**Amit Ayer:** I agree from the conference room mic.\n"
    )
    mlx_segs = [
        {"start": 5.0, "end": 9.0, "text": "We will finalize the architecture today."},
        {"start": 12.0, "end": 16.0, "text": "I agree from the conference room mic."},
    ]
    meta = {
        "project": "sherpa",
        "platform_transcript_text": platform_raw,
        "voiceprint_store_path": str(vp_db),
        "turn_embeddings": {
            "pturn_0001": vec_amit,
            "pturn_0002": vec_clay,  # Clay speaking into Amit's room mic
        },
    }

    fused = try_fuse_metadata_platform_transcript(mlx_segs, meta)
    assert fused is not None
    assert len(fused) == 2
    assert fused[0]["speaker"] == "Amit Ayer"
    assert not fused[0].get("acoustic_platform_mismatch")

    assert fused[1]["speaker"] == "Clay Palumbo"
    assert fused[1]["platform_claimed_speaker"] == "Amit Ayer"
    assert fused[1]["acoustic_platform_mismatch"] is True
    assert fused[1]["speaker_provenance"] == "voiceprint_override_platform_bleed"


def test_wave3_utterance_assembler_pacing_and_embedding_divergence_guards() -> None:
    vec_1 = _unit_vec(25)
    vec_2 = _unit_vec(140)

    # 1. Deliberate speaker (95 WPM) with 1.0s pause bridges into a single utterance
    deliberate_segs = [
        {"speaker": "Cameron Mercer", "start": 0.0, "end": 2.0, "text": "We should evaluate", "embedding": vec_1},
        {"speaker": "Cameron Mercer", "start": 3.0, "end": 5.0, "text": "the deployment timeline", "embedding": vec_1},
    ]
    cfg_slow = AssemblyConfig(speaker_pacing_map={"Cameron Mercer": 95.0})
    utts_slow = assemble_utterances(deliberate_segs, config=cfg_slow)
    assert len(utts_slow) == 1
    assert "evaluate the deployment timeline" in utts_slow[0]["text"]

    # Fast speaker (190 WPM) with 1.0s pause splits into 2 utterances
    cfg_fast = AssemblyConfig(speaker_pacing_map={"Cameron Mercer": 190.0})
    utts_fast = assemble_utterances(deliberate_segs, config=cfg_fast)
    assert len(utts_fast) == 2

    # 2. Embedding divergence guard splits same-labeled segments when embeddings diverge
    divergent_segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0, "text": "First speaker thought", "embedding": vec_1},
        {"speaker": "SPEAKER_00", "start": 2.4, "end": 4.5, "text": "Second speaker interjection", "embedding": vec_2},
    ]
    utts_div = assemble_utterances(divergent_segs)
    assert len(utts_div) == 2
    assert "acoustic_embedding_divergence" in utts_div[1]["boundary_reasons"]
