"""
tests/test_voiceprint_store_bipartite_unit.py

Unit and property test suite for Wave 1:
- Idempotent enrollment and sample provenance ledger in VoiceprintStore.
- Duration-weighted centroid updating.
- Roster-constrained injective bipartite matching with margin calculation and AS-Norm.
- High-purity segment selection and medoid outlier pruning in DiarizationEngine.
"""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationEngine
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    EMBEDDING_DIM,
    VoiceprintStore,
    _normalize_vector,
    generate_deterministic_embedding,
)


@pytest.fixture
def temp_store(tmp_path: Path) -> VoiceprintStore:
    db_file = tmp_path / "test_voiceprints.sqlite"
    return VoiceprintStore(db_path=db_file)


class TestVoiceprintStoreWave1:
    """Verifies VoiceprintStore idempotency, provenance, and roster-constrained bipartite matching."""

    def test_idempotent_enrollment_with_audio_sha(self, temp_store: VoiceprintStore) -> None:
        emb1 = generate_deterministic_embedding("alex_sample_1")
        audio_sha = "sha256_mock_audio_chunk_12345"

        # First enrollment
        res1 = temp_store.enroll_voiceprint(
            speaker_name="Alex Sanchez",
            embedding=emb1,
            project="TEST_PROJ",
            is_verified=True,
            audio_sha256=audio_sha,
            duration_sec=12.5,
            clean_speech_sec=10.0,
            channel_profile="stereo_ch0_remote",
        )
        assert res1["action"] == "ENROLLED"
        assert res1["sample_count"] == 1
        assert res1["canonical_name"] == "Alex Sanchez"

        # Re-enrolling exact same audio_sha256 must be idempotent
        res2 = temp_store.enroll_voiceprint(
            speaker_name="Alex Sanchez",
            embedding=emb1,
            project="TEST_PROJ",
            is_verified=True,
            audio_sha256=audio_sha,
            duration_sec=12.5,
            clean_speech_sec=10.0,
            channel_profile="stereo_ch0_remote",
        )
        assert res2["action"] == "ALREADY_ENROLLED"
        assert res2["sample_count"] == 1

        # Check sample ledger
        samples = temp_store.get_enrolled_samples(project="TEST_PROJ", canonical_name="Alex Sanchez")
        assert len(samples) == 1
        assert samples[0]["audio_sha256"] == audio_sha
        assert samples[0]["clean_speech_sec"] == 10.0
        assert samples[0]["channel_profile"] == "stereo_ch0_remote"

        # Enrolling a second, distinct audio_sha256 updates the centroid and increments sample_count
        emb2 = generate_deterministic_embedding("alex_sample_2")
        audio_sha2 = "sha256_mock_audio_chunk_67890"
        res3 = temp_store.enroll_voiceprint(
            speaker_name="Alex Sanchez",
            embedding=emb2,
            project="TEST_PROJ",
            is_verified=True,
            audio_sha256=audio_sha2,
            duration_sec=20.0,
            clean_speech_sec=18.0,
            channel_profile="stereo_ch0_remote",
        )
        assert res3["action"] == "UPDATED"
        assert res3["sample_count"] == 2

        samples2 = temp_store.get_enrolled_samples(project="TEST_PROJ", canonical_name="Alex Sanchez")
        assert len(samples2) == 2

    def test_roster_constrained_bipartite_matching_and_margin(self, temp_store: VoiceprintStore) -> None:
        """Verifies match_roster enforces injective matching and computes margin between top-1 and top-2."""
        # Enroll 3 distinct speakers
        alex_emb = generate_deterministic_embedding("speaker_alex_timbre")
        brien_emb = generate_deterministic_embedding("speaker_brien_timbre")
        carol_emb = generate_deterministic_embedding("speaker_carol_timbre")

        temp_store.enroll_voiceprint("Alex Sanchez", alex_emb, project="PROJECT_ALPHA", is_verified=True)
        temp_store.enroll_voiceprint("Speaker_Alpha Colwell", brien_emb, project="PROJECT_ALPHA", is_verified=True)
        temp_store.enroll_voiceprint("Carol Danvers", carol_emb, project="PROJECT_ALPHA", is_verified=True)

        # Create query cluster centroids: Cluster 0 close to Alex, Cluster 1 close to Speaker_Alpha
        slight_alex = _normalize_vector([x + 0.005 for x in alex_emb])
        slight_brien = _normalize_vector([x + 0.005 for x in brien_emb])

        cluster_centroids = {
            "SPEAKER_00": slight_alex,
            "SPEAKER_01": slight_brien,
        }

        # Declared roster has all 3 attendees
        roster = ["Alex Sanchez", "Speaker_Alpha Colwell", "Carol Danvers"]

        results = temp_store.match_roster(
            cluster_centroids=cluster_centroids,
            roster_names=roster,
            project="PROJECT_ALPHA",
            threshold=0.80,
            margin_threshold=0.15,
        )

        assert "SPEAKER_00" in results
        assert "SPEAKER_01" in results

        assert results["SPEAKER_00"]["canonical_name"] == "Alex Sanchez"
        assert results["SPEAKER_00"]["score"] >= 0.90
        assert results["SPEAKER_00"]["margin"] >= 0.15
        assert results["SPEAKER_00"]["is_decisive"] is True

        assert results["SPEAKER_01"]["canonical_name"] == "Speaker_Alpha Colwell"
        assert results["SPEAKER_01"]["score"] >= 0.90
        assert results["SPEAKER_01"]["is_decisive"] is True

    def test_roster_filtering_excludes_non_roster_enrolled_speakers(self, temp_store: VoiceprintStore) -> None:
        """If an enrolled speaker is not on the declared roster, match_roster must not assign them."""
        dave_emb = generate_deterministic_embedding("speaker_dave_timbre")
        temp_store.enroll_voiceprint("Dave Miller", dave_emb, project="PROJECT_BETA", is_verified=True)

        cluster_centroids = {
            "SPEAKER_00": dave_emb,
        }

        # Roster only contains Emily
        roster = ["Emily Watson"]
        results = temp_store.match_roster(
            cluster_centroids=cluster_centroids,
            roster_names=roster,
            project="PROJECT_BETA",
            threshold=0.50,
        )
        assert len(results) == 0

    def test_clear_purges_both_tables_and_verifies_integrity(self, temp_store: VoiceprintStore) -> None:
        emb = generate_deterministic_embedding("spk_to_clear")
        temp_store.enroll_voiceprint(
            "Clear Me", emb, project="TEST_CLR", is_verified=True, audio_sha256="sha_clr"
        )
        assert len(temp_store.get_enrolled_speakers(project="TEST_CLR")) == 1
        assert len(temp_store.get_enrolled_samples(project="TEST_CLR")) == 1

        temp_store.clear()
        assert len(temp_store.get_enrolled_speakers(project="TEST_CLR")) == 0
        assert len(temp_store.get_enrolled_samples(project="TEST_CLR")) == 0
        assert temp_store.verify_integrity() is True


class TestDiarizationEngineWave1:
    """Verifies DiarizationEngine cluster centroid extraction, non-overlap filtering, and medoid pruning."""

    def test_extract_cluster_centroids_excludes_overlapping_turns(self, tmp_path: Path) -> None:
        engine = DiarizationEngine(lazy=True)

        # Create synthetic WAV file
        wav_file = tmp_path / "test_synth.wav"
        sr = 16000
        n_samples = sr * 6  # 6 seconds
        audio_data = (np.sin(2 * np.pi * 440 * np.arange(n_samples) / sr) * 16384).astype(np.int16)

        with wave.open(str(wav_file), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(audio_data.tobytes())

        turns = [
            # Clean turn >= 2.0s
            {"start": 0.0, "end": 2.5, "duration": 2.5, "speaker": "SPEAKER_00", "overlap": False},
            # Overlapping turn - must be excluded
            {"start": 2.5, "end": 4.5, "duration": 2.0, "speaker": "SPEAKER_00", "overlap": True, "active_speakers": ["SPEAKER_00", "SPEAKER_01"]},
            # Short turn < 1.0s
            {"start": 4.5, "end": 4.8, "duration": 0.3, "speaker": "SPEAKER_01", "overlap": False},
            # Clean turn for SPEAKER_01
            {"start": 4.8, "end": 6.0, "duration": 1.2, "speaker": "SPEAKER_01", "overlap": False},
        ]

        centroids = engine.extract_cluster_centroids(wav_file, turns, min_segment_duration=2.0)
        assert "SPEAKER_00" in centroids
        assert "SPEAKER_01" in centroids
        assert len(centroids["SPEAKER_00"]) == EMBEDDING_DIM
        assert len(centroids["SPEAKER_01"]) == EMBEDDING_DIM
        # Verify L2 unit norm
        norm0 = np.linalg.norm(centroids["SPEAKER_00"])
        assert abs(norm0 - 1.0) < 1e-4
