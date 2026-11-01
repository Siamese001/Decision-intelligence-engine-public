"""
Wave 4 Acceptance Tests: Live Streaming Diarization, Mid-Call Hint Propagation & Hardware Bleed Learning.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from dual_pipeline.audio_ingestion_core.streaming_diarizer import (
    StreamingDiarizer,
    VADSilenceFilter,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    EMBEDDING_DIM,
    VoiceprintStore,
)
from dual_pipeline.live_meeting_recorder_core.acoustic_features import (
    extract_256d_acoustic_embedding,
)
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
    LiveVoiceRegistry,
    VoiceCluster,
)
from dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader import (
    Stage0SpeakerPreloadResult,
)
from dual_pipeline.live_meeting_recorder_core.streaming_turn_embedder import (
    StreamingTurnEmbedder,
)


def _unit_vec(idx_a: int, idx_b: int, w_a: float = 0.90) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[idx_a % EMBEDDING_DIM] = w_a
    v[idx_b % EMBEDDING_DIM] = math.sqrt(max(0.0, 1.0 - w_a * w_a))
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def test_wave4_live_voice_registry_ema_adaptation_and_midcall_hint_backfill(tmp_path: Path) -> None:
    vp_db = tmp_path / "vp_live.sqlite"
    store = VoiceprintStore(db_path=vp_db)

    base_vec = _unit_vec(10, 11, 0.92)
    shifted_vec = _unit_vec(10, 11, 0.84)

    reg = LiveVoiceRegistry()
    reg.voice_clusters["Voice A"] = VoiceCluster(
        voice_id="Voice A",
        resolved_speaker="Amit Ayer",
        centroid=list(base_vec),
        sample_count=1,
        confidence=1.0,
        is_pre_enrolled=True,
    )

    # EMA adaptation on high-similarity chunk
    spk, vid, conf = reg._update_cluster_centroid(
        reg.voice_clusters["Voice A"],
        np.asarray(shifted_vec, dtype=np.float32),
        best_sim=0.94,
        last_seen_sec=15.0,
    )
    assert spk == "Amit Ayer"
    assert vid == "Voice A"
    assert reg.voice_clusters["Voice A"].centroid != base_vec

    # Mid-call operator hint on Voice B backfills retroactive segments and enrolls to VoiceprintStore
    vec_b = _unit_vec(55, 56, 0.90)
    reg.voice_clusters["Voice B"] = VoiceCluster(
        voice_id="Voice B",
        resolved_speaker=None,
        centroid=list(vec_b),
        sample_count=3,
        confidence=0.88,
    )
    retro_segs = [
        {"voice_id": "Voice B", "speaker": "Voice B", "text": "Here is the update."},
        {"voice_id": "Voice A", "speaker": "Amit Ayer", "text": "Thanks."},
    ]
    reg.bind_speaker_hint(
        voice_id="Voice B",
        speaker_name="Cameron Mercer",
        quote="Here is the update.",
        retroactive_segments=retro_segs,
        persist_to_store=True,
        voiceprint_store=store,
        project="sherpa",
    )
    assert retro_segs[0]["speaker"] == "Cameron Mercer"
    assert retro_segs[0]["speaker_provenance"] == "live_operator_hint_backfill"
    enrolled = store.get_voiceprint("Cameron Mercer", project="sherpa")
    assert enrolled is not None
    assert enrolled["is_verified"] is True


def test_wave4_streaming_turn_embedder_bleed_learning_and_hint_propagation(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "turn_embeddings.jsonl"
    stage0 = Stage0SpeakerPreloadResult(
        operator_name="Amit Ayer",
        ch1_speaker="Amit Ayer",
        roster_names=["Amit Ayer", "Clay Palumbo"],
        enrolled_centroids={},
        global_prior_centroids={},
    )
    embedder = StreamingTurnEmbedder(output_jsonl_path=jsonl_path, stage0_context=stage0, bleed_ratio_ceiling=0.30)

    # Simulate correlated Ch0 -> Ch1 room bleed with ratio ~0.36
    t = np.linspace(0.0, 1.0, 4000, dtype=np.float32)
    win_ch0 = 0.25 * np.sin(2.0 * np.pi * 220.0 * t)
    win_ch1 = 0.36 * win_ch0
    new_ceil = embedder.learn_hardware_bleed_ratio(win_ch0, win_ch1)
    assert new_ceil > 0.30

    # Write a turn record with unknown match, then propagate mid-call speaker binding
    vec_clay = _unit_vec(88, 89, 0.91)
    rec_dict = {
        "turn_id": "turn_00001",
        "chunk_index": 0,
        "channel": 0,
        "start_sec": 0.0,
        "end_sec": 2.5,
        "duration_sec": 2.5,
        "embedding": vec_clay,
        "rms_energy": 0.08,
        "bleed_suppressed": False,
        "live_match_speaker": None,
        "live_match_score": 0.0,
        "text": "Let us review the metrics.",
    }
    jsonl_path.write_text(json.dumps(rec_dict) + "\n", encoding="utf-8")

    updated = embedder.propagate_speaker_binding("Clay Palumbo", new_centroid=vec_clay)
    assert updated == 1
    assert "Clay Palumbo" in stage0.enrolled_centroids
    reloaded = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert reloaded["live_match_speaker"] == "Clay Palumbo"
    assert reloaded["live_match_score"] >= 0.95


def test_wave4_streaming_diarizer_256d_clustering_and_vad_noise_floor() -> None:
    sr = 16000
    t = np.linspace(0.0, 2.0, sr * 2, endpoint=False, dtype=np.float32)
    sig_speaker_a = (0.35 * np.sin(2.0 * np.pi * 210.0 * t) + 0.15 * np.cos(2.0 * np.pi * 430.0 * t)).astype(np.float32)
    emb_a = extract_256d_acoustic_embedding(sig_speaker_a, sr=sr).tolist()

    diarizer = StreamingDiarizer(
        window_seconds=2.0,
        step_seconds=2.0,
        sample_rate=sr,
        enrolled_centroids={"Amit Ayer": emb_a},
    )

    # Adapt VAD noise floor across low-energy ambient chunks
    vad = VADSilenceFilter(energy_threshold=0.025)
    for _ in range(8):
        vad.adapt_noise_floor(np.full(1024, 0.003, dtype=np.float32))
    assert vad.energy_threshold < 0.025

    # Ingest 2 windows of Speaker A -> both must resolve to "Amit Ayer" (not alternating SPEAKER_00 / SPEAKER_01!)
    diarizer.ingest_audio_chunk(sig_speaker_a)
    turns_1 = diarizer.process_next_window()
    assert len(turns_1) == 1
    assert turns_1[0].speaker_id == "Amit Ayer"

    diarizer.ingest_audio_chunk(sig_speaker_a)
    turns_2 = diarizer.process_next_window()
    assert len(turns_2) == 1
    assert turns_2[0].speaker_id == "Amit Ayer"
