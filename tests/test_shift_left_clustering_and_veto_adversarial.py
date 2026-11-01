"""
tests/test_shift_left_clustering_and_veto_adversarial.py

Wave 5 Adversarial & Edge-Case Suite:
- 15% Ch0 -> Ch1 bleed suppression vs genuine overlapping local speech.
- 0.5s "No, wait." interjection blocked from <=0.8s sandwich smoothing across channel or cosine > 0.35.
- fill_nearest gated on <0.3s gaps in assign_speakers_with_whisperx.
- Unknown speaker counts and empty GraphDB rosters in Stage-0 preload & constrained global clustering.
- Malformed non-256D turn embedding rejection.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.constrained_global_clusterer import (
    run_constrained_global_clustering,
)
from dual_pipeline.audio_ingestion_core.continuity_smoother import (
    smooth_intra_segment_continuity,
)
from dual_pipeline.audio_ingestion_core.speaker_alignment import (
    AcousticInterval,
    _align_single_word,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    generate_deterministic_embedding,
)
from dual_pipeline.audio_ingestion_core.whisperx_aligner import (
    _min_interval_gap,
)
from dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader import (
    preload_stage0_speaker_context,
)
from dual_pipeline.live_meeting_recorder_core.streaming_turn_embedder import (
    is_ch0_to_ch1_bleed,
)


def test_15_percent_ch0_to_ch1_bleed_suppression_adversarial() -> None:
    """Verifies 15% room bleed from Ch0 into Ch1 is suppressed while independent Ch1 speech is retained."""
    t = np.linspace(0.0, 2.0, 32000, endpoint=False, dtype=np.float32)
    ch0_signal = 0.5 * np.sin(2.0 * np.pi * 220.0 * t)
    ch1_bleed = 0.15 * ch0_signal

    assert is_ch0_to_ch1_bleed(ch0_signal, ch1_bleed) is True

    # Independent local speech on Ch1 with strong amplitude is NOT suppressed
    ch1_active = 0.45 * np.sin(2.0 * np.pi * 130.0 * t)
    assert is_ch0_to_ch1_bleed(ch0_signal, ch1_active) is False


def test_half_second_veto_interjection_blocked_from_sandwich_smoothing() -> None:
    """Verifies a 0.5s 'No, wait.' interjection on Ch1 or with cosine_dist > 0.35 is not smoothed into Ch0."""
    intervals = [
        AcousticInterval("i0", "SPEAKER_01", 0.0, 3.0, channel=0),
        AcousticInterval("i1", "SPEAKER_01", 3.6, 7.0, channel=0),
    ]
    # Word in the 0.6s gap [3.05..3.55] on channel 1 (local veto)
    veto_word_ch1: Dict[str, Any] = {
        "word": "No,",
        "start": 3.05,
        "end": 3.55,
        "channel": 1,
    }
    _, spk_ch1 = _align_single_word(veto_word_ch1, intervals, len(intervals), 0, "SPEAKER_01")
    assert spk_ch1 == "Unknown"

    # Word in the 0.6s gap on same channel but with acoustic cosine distance 0.55 (> 0.35 threshold)
    veto_word_cos: Dict[str, Any] = {
        "word": "wait.",
        "start": 3.05,
        "end": 3.55,
        "channel": 0,
        "cosine_distance_to_adjacent": 0.55,
    }
    _, spk_cos = _align_single_word(veto_word_cos, intervals, len(intervals), 0, "SPEAKER_01")
    assert spk_cos == "Unknown"

    # Intra-segment continuity smoother also respects channel and embedding cosine distance > 0.35
    emb_a = generate_deterministic_embedding("speaker_a", dim=256)
    emb_b = generate_deterministic_embedding("speaker_b", dim=256)
    words: List[Dict[str, Any]] = [
        {"word": "We", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_01", "channel": 0, "embedding": emb_a},
        {"word": "No", "start": 0.5, "end": 0.9, "speaker": "Unknown", "channel": 1, "embedding": emb_b},
        {"word": "proceed", "start": 1.0, "end": 1.5, "speaker": "SPEAKER_01", "channel": 0, "embedding": emb_a},
    ]
    smooth_intra_segment_continuity(words, {"start": 0.0, "end": 1.5}, intervals, lambda s, i: s)
    assert words[1]["speaker"] == "Unknown"


def test_whisperx_fill_nearest_gated_on_0_3s_gap() -> None:
    """Verifies _min_interval_gap gates fill_nearest when gap > 0.30s or channel mismatches."""
    intervals = [
        AcousticInterval("i0", "SPEAKER_01", 0.0, 2.0, channel=0),
        AcousticInterval("i1", "SPEAKER_01", 4.0, 6.0, channel=0),
    ]
    # Gap of 0.8s (> 0.30s) -> must not be filled
    assert _min_interval_gap(2.8, 3.2, intervals, channel=0) > 0.30
    # Gap of 0.15s (<= 0.30s) on matching channel -> eligible
    assert _min_interval_gap(2.0, 2.15, intervals, channel=0) <= 0.30
    # Channel mismatch (channel 1 vs channel 0 interval) -> must not be filled
    assert _min_interval_gap(2.05, 2.15, intervals, channel=1) > 0.30


def test_unknown_speaker_count_and_empty_graphdb_roster(tmp_path: Path) -> None:
    """Verifies Stage-0 preload and constrained global clustering handle empty rosters and unknown K."""
    empty_graph = tmp_path / "empty_graph.sqlite"
    stage0 = preload_stage0_speaker_context(
        attendees=[],
        project="EMPTY_PROJ",
        graph_db_path=empty_graph,
    )
    assert stage0.roster_names == []
    assert stage0.min_speakers == 1
    assert stage0.max_speakers == 8

    emb1 = generate_deterministic_embedding("unknown_rem_1", dim=256)
    emb2 = generate_deterministic_embedding("unknown_rem_2", dim=256)
    turns = [
        {"turn_id": "t1", "channel": 0, "start_sec": 0.0, "end_sec": 2.0, "embedding": emb1},
        {"turn_id": "t2", "channel": 0, "start_sec": 2.0, "end_sec": 4.0, "embedding": emb2},
        {"turn_id": "t3", "channel": 0, "start_sec": 4.0, "end_sec": 6.0, "embedding": emb1},
    ]
    res = run_constrained_global_clustering(turns, expected_roster_count=None)
    assert len(res.remote_cluster_ids) == 2
    assert res.turns[0]["cluster_id"] == res.turns[2]["cluster_id"]
    assert res.turns[0]["cluster_id"] != res.turns[1]["cluster_id"]


def test_constrained_clusterer_rejects_malformed_non_256d_turns() -> None:
    """Verifies run_constrained_global_clustering raises ValueError on non-256D turn embeddings."""
    bad_turns = [{"turn_id": "bad_1", "channel": 0, "start_sec": 0.0, "end_sec": 1.0, "embedding": [0.1] * 21}]
    with pytest.raises(ValueError, match="256-D"):
        run_constrained_global_clustering(bad_turns)
