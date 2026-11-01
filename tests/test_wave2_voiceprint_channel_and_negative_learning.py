"""
Wave 2 Acceptance Tests: Voiceprint Store Channel Conditioning, Variance Thresholds & Contrastive Unlearning.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from dual_pipeline.audio_ingestion_core.voiceprint_harvester import (
    _enroll_verified_speaker_centroids,
    _load_verified_turns,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    EMBEDDING_DIM,
    VoiceprintStore,
    compute_adaptive_threshold,
)
from dual_pipeline.speaker_attribution_core.voiceprint_auto_enrollment import (
    auto_enroll_voiceprints,
)


def _unit_vec(idx_a: int, idx_b: int, weight_a: float = 0.85) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[idx_a % EMBEDDING_DIM] = weight_a
    vec[idx_b % EMBEDDING_DIM] = math.sqrt(max(0.0, 1.0 - weight_a * weight_a))
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def test_wave2_channel_conditioned_enrollment_and_variance_thresholds(tmp_path: Path) -> None:
    db_path = tmp_path / "vp_wave2.sqlite"
    store = VoiceprintStore(db_path=db_path)

    ch0_vec = _unit_vec(10, 11, 0.90)
    ch1_vec = _unit_vec(10, 12, 0.80)

    res1 = store.enroll_voiceprint(
        speaker_name="Amit Ayer",
        embedding=ch0_vec,
        project="project_beacon",
        is_verified=True,
        audio_sha256="sha_call_1",
        channel_profile="stereo_ch0",
        clean_speech_sec=25.0,
    )
    assert res1["channel_profile"] == "stereo_ch0"

    res2 = store.enroll_voiceprint(
        speaker_name="Amit Ayer",
        embedding=ch1_vec,
        project="project_beacon",
        is_verified=True,
        audio_sha256="sha_call_2",
        channel_profile="stereo_ch1",
        clean_speech_sec=25.0,
    )
    assert res2["sample_count"] == 2
    assert res2["intra_speaker_variance"] > 0.0

    # Verify adaptive threshold scales with sample maturity and variance
    low_var_thresh = compute_adaptive_threshold(0.82, sample_count=8, variance=0.01)
    high_var_thresh = compute_adaptive_threshold(0.82, sample_count=2, variance=0.25)
    assert low_var_thresh < 0.82
    assert high_var_thresh > low_var_thresh

    # Match with channel conditioning boost
    match_ch0 = store.match_voiceprint(ch0_vec, project="project_beacon", channel_profile="stereo_ch0")
    assert match_ch0 is not None
    assert match_ch0[0] == "Amit Ayer"
    assert match_ch0[1] >= 0.90


def test_wave2_contrastive_negative_unlearning_and_auto_enrollment_override(tmp_path: Path) -> None:
    db_path = tmp_path / "vp_neg.sqlite"
    store = VoiceprintStore(db_path=db_path)

    ambiguous_vec = _unit_vec(40, 41, 0.88)
    # Initially enroll ambiguous_vec under Wrong Speaker
    store.enroll_voiceprint(
        speaker_name="Calvin Gilmartin",
        embedding=ambiguous_vec,
        project="edo",
        is_verified=True,
        audio_sha256="sha_contaminated",
        clean_speech_sec=10.0,
    )

    # Operator overrides SPEAKER_00 from Calvin Gilmartin -> Cameron Mercer
    meta = {
        "project": "edo",
        "audio_sha256": "sha_contaminated",
        "is_stereo_separated": True,
        "operator_name": "Amit Ayer",
        "cluster_centroids": {"SPEAKER_00": ambiguous_vec},
        "hitl_verified_speakers": {"SPEAKER_00", "Cameron Mercer"},
        "hitl_speaker_overrides": {"SPEAKER_00": "Calvin Gilmartin"},
        "structured_utterances": [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 14.0},
        ],
    }
    enrolled = auto_enroll_voiceprints(meta, {"SPEAKER_00": "Cameron Mercer"}, voiceprint_store=store)
    assert len(enrolled) == 1
    assert enrolled[0]["speaker"] == "Cameron Mercer"
    assert enrolled[0]["channel_profile"] == "stereo_ch1"

    negs = store.get_negative_voiceprints("Calvin Gilmartin")
    assert len(negs) == 1
    assert negs[0]["true_speaker"] == "Cameron Mercer"

    # Re-matching ambiguous_vec against roster must now decisively select Cameron Mercer and penalize Calvin Gilmartin
    roster_matches = store.match_roster(
        {"SPEAKER_00": ambiguous_vec},
        roster_names=["Calvin Gilmartin", "Cameron Mercer"],
        project="edo",
        channel_profile="stereo_ch1",
    )
    assert "SPEAKER_00" in roster_matches
    assert roster_matches["SPEAKER_00"]["canonical_name"] == "Cameron Mercer"
    assert roster_matches["SPEAKER_00"]["is_decisive"] is True

    # Independent fresh read-only SQLite check
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        neg_cnt = conn.execute("SELECT COUNT(*) FROM speaker_voiceprint_negatives WHERE rejected_speaker = 'Calvin Gilmartin';").fetchone()[0]
        assert neg_cnt == 1
        ch_prof = conn.execute("SELECT channel_profile FROM speaker_voiceprints WHERE canonical_name = 'Cameron Mercer';").fetchone()[0]
        assert ch_prof in ("stereo_ch1", "mono")
    finally:
        conn.close()


def test_wave2_harvester_filters_overlap_turns_and_outlier_centroids(tmp_path: Path) -> None:
    db_path = tmp_path / "vp_harvest.sqlite"
    store = VoiceprintStore(db_path=db_path)

    clean_vec = _unit_vec(70, 71, 0.92)
    outlier_vec = _unit_vec(190, 191, 0.92)

    # Enroll mature voiceprint (sample_count = 2)
    store.enroll_voiceprint("Lan Tran", clean_vec, project="edo", is_verified=True, audio_sha256="sha_1")
    store.enroll_voiceprint("Lan Tran", clean_vec, project="edo", is_verified=True, audio_sha256="sha_2")

    meeting_dir = tmp_path / "meeting_01"
    meeting_dir.mkdir()
    utt_file = meeting_dir / "test.utterances.json"
    utt_file.write_text(
        json.dumps([
            {"speaker": "Lan Tran", "start": 0.0, "end": 4.0, "is_overlap": True},
            {"speaker": "Lan Tran", "start": 5.0, "end": 5.8, "is_overlap": False},
            {"speaker": "Lan Tran", "start": 10.0, "end": 16.0, "is_overlap": False, "channel_profile": "stereo_ch1"},
        ]),
        encoding="utf-8",
    )

    turns = _load_verified_turns(meeting_dir, "", {"Lan Tran": {"canonical_name": "Lan Tran"}})
    assert len(turns) == 1
    assert turns[0]["duration"] == 6.0
    assert turns[0]["channel_profile"] == "stereo_ch1"

    # Attempt to enroll contaminated outlier centroid -> must be skipped
    enrolled_list = _enroll_verified_speaker_centroids(
        verified_map={"Lan Tran": {"canonical_name": "Lan Tran"}},
        centroids={"Lan Tran": outlier_vec},
        turns=turns,
        vp_store=store,
        proj="EDO",
        meeting_name="meeting_01",
        audio_sha="sha_outlier",
        max_duration_sec=60.0,
        dry_run=False,
    )
    assert len(enrolled_list) == 1
    assert enrolled_list[0]["action"] == "SKIPPED_OUTLIER"
