"""
Wave 5 Acceptance Tests: Constrained Global Clustering, Voiceprint-Seeded Sub-Cluster Splitting & Alignment Priors.
"""
from __future__ import annotations

import math

from dual_pipeline.audio_ingestion_core.constrained_global_clusterer import (
    run_constrained_global_clustering,
)
from dual_pipeline.audio_ingestion_core.continuity_smoother import (
    _can_smooth_into_neighbor,
)
from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    AcousticInterval,
    _align_single_word,
)
from dual_pipeline.audio_ingestion_core.stereo_alignment import (
    compute_live_to_batch_cluster_bridge,
)
from dual_pipeline.audio_ingestion_core.subcluster_splitter import (
    split_merged_remote_cluster,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import EMBEDDING_DIM


def _unit_vec(idx: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[idx % EMBEDDING_DIM] = 0.92
    v[(idx + 1) % EMBEDDING_DIM] = math.sqrt(1.0 - 0.92 * 0.92)
    return v


def test_wave5_constrained_global_clustering_silhouette_and_temporal_cannot_link() -> None:
    vec_op = _unit_vec(5)
    vec_rem1 = _unit_vec(50)
    vec_rem2 = _unit_vec(140)

    turns = [
        {"channel": 1, "start_sec": 0.0, "end_sec": 3.0, "embedding": vec_op, "text": "Hello team."},
        {"channel": 0, "start_sec": 4.0, "end_sec": 7.0, "embedding": vec_rem1, "text": "Hi Amit."},
        # Overlapping simultaneous turn with rem1
        {"channel": 0, "start_sec": 5.0, "end_sec": 8.0, "embedding": vec_rem2, "text": "Good morning."},
        {"channel": 0, "start_sec": 9.0, "end_sec": 12.0, "embedding": vec_rem1, "text": "Let us begin."},
        {"channel": 0, "start_sec": 13.0, "end_sec": 16.0, "embedding": vec_rem2, "text": "Sounds good."},
    ]

    res = run_constrained_global_clustering(
        turns,
        ch1_operator_name="Amit Ayer",
        expected_roster_count=None,  # Silhouette auto-discovery
    )
    assert res.turns[0]["speaker"] == "Amit Ayer"
    # Simultaneous overlapping turns 1 and 2 must be placed in distinct remote clusters
    assert res.turns[1]["cluster_id"] != res.turns[2]["cluster_id"]
    assert len(res.remote_cluster_ids) >= 2


def test_wave5_voiceprint_seeded_subcluster_splitter_separates_collapsed_remote() -> None:
    vec_stephan = _unit_vec(30)
    vec_clay = _unit_vec(110)

    segments = [
        {"speaker": "SPEAKER_00", "raw_speaker": "SPEAKER_00", "channel": 1, "start": 0.0, "end": 2.0, "text": "Operator turn 1"},
        {"speaker": "SPEAKER_00", "raw_speaker": "SPEAKER_00", "channel": 1, "start": 2.5, "end": 4.5, "text": "Operator turn 2"},
        {"speaker": "SPEAKER_01", "raw_speaker": "SPEAKER_01", "channel": 0, "start": 5.0, "end": 8.0, "text": "Remote turn A1", "embedding": vec_stephan},
        {"speaker": "SPEAKER_01", "raw_speaker": "SPEAKER_01", "channel": 0, "start": 9.0, "end": 12.0, "text": "Remote turn B1", "embedding": vec_clay},
        {"speaker": "SPEAKER_01", "raw_speaker": "SPEAKER_01", "channel": 0, "start": 13.0, "end": 16.0, "text": "Remote turn A2", "embedding": vec_stephan},
        {"speaker": "SPEAKER_01", "raw_speaker": "SPEAKER_01", "channel": 0, "start": 17.0, "end": 20.0, "text": "Remote turn B2", "embedding": vec_clay},
    ]
    meta = {
        "attendees": ["Amit Ayer", "Cameron Mercer", "Clay Palumbo"],
        "operator_name": "Amit Ayer",
        "enrolled_centroids": {
            "Cameron Mercer": vec_stephan,
            "Clay Palumbo": vec_clay,
        },
    }

    updated, receipt = split_merged_remote_cluster(segments, metadata=meta)
    assert receipt["split_performed"] is True
    assert updated[2]["speaker"] != updated[3]["speaker"]
    assert updated[2]["speaker"] == updated[4]["speaker"]
    assert updated[3]["speaker"] == updated[5]["speaker"]


def test_wave5_continuity_smoother_backchannel_and_hybrid_alignment_priors() -> None:
    # 1. Backchannel interjection must not smooth into neighbor
    w_interj = {"word": "Yeah", "is_interjection": True, "channel": 0}
    w_neighbor = {"word": "architecture", "channel": 0}
    assert _can_smooth_into_neighbor(w_interj, w_neighbor) is False

    # 2. Variance-conditioned cosine distance tightens smoothing threshold
    w_high_var = {"word": "test", "speaker_variance": 0.25, "cosine_distance_to_adjacent": 0.28}
    assert _can_smooth_into_neighbor(w_high_var, w_neighbor, max_cosine_dist=0.35) is False

    # 3. Hybrid live-to-batch cluster bridge resolves via 256-D cosine similarity even with zero temporal overlap
    vec_v1 = _unit_vec(72)
    bridge = compute_live_to_batch_cluster_bridge(
        batch_segments=[],
        live_turns=[],
        speaker_map={"Voice A": "Lan Tran"},
        batch_centroids={"SPEAKER_02": vec_v1},
        live_centroids={"Voice A": vec_v1},
    )
    assert bridge.get("SPEAKER_02") == "Lan Tran"

    # 4. Transition prior tie-breaking in _align_single_word
    intervals = [
        AcousticInterval(interval_id="i1", speaker_cluster="SPEAKER_01", start_time_sec=0.0, end_time_sec=2.0),
        AcousticInterval(interval_id="i2", speaker_cluster="SPEAKER_02", start_time_sec=0.0, end_time_sec=2.0),
    ]
    word = {"word": "agreed", "start": 0.5, "end": 1.0, "transition_priors": {"SPEAKER_02": 0.90, "SPEAKER_01": 0.10}}
    _, chosen = _align_single_word(word, intervals, len(intervals), 0, current_speaker="SPEAKER_00")
    assert chosen == "SPEAKER_02"
