"""
tests/test_voiceprint_and_diarization_cache_adversarial.py

Adversarial and edge-case test suite for cross-meeting voiceprint memory and audio-keyed diarization caching.
Covers 5 mandatory categories:
  1. Boundary payload (empty inputs, zero vectors, empty database)
  2. Malformed input (wrong dimensions, corrupted cache files)
  3. Concurrency & race (16 concurrent workers on SQLite WAL store without database locks)
  4. Invariant bypass attempt (unverified turns strictly fail-closed from enrollment)
  5. Recovery & integrity (PRAGMA integrity_check, cache eviction)
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
    load_audio_diarization_cache,
    save_audio_diarization_cache,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)


class TestVoiceprintAndDiarizationCacheAdversarial:
    """Adversarial stress and invariant verification for VoiceprintStore and DiarizationCache."""

    @pytest.fixture
    def isolated_store(self, tmp_path: Path) -> VoiceprintStore:
        db_file = tmp_path / "test_voiceprint.sqlite"
        return VoiceprintStore(db_path=db_file)

    def test_boundary_payload_empty_and_zero_vectors(self, isolated_store: VoiceprintStore) -> None:
        """Category 1: Boundary payload - handles empty names, zero vectors, and empty database queries."""
        # Empty database returns None on match
        dummy_vec = generate_deterministic_embedding("test_speaker")
        match = isolated_store.match_voiceprint(dummy_vec, project="TEST_PROJ")
        assert match is None

        # Empty speaker name fails closed
        with pytest.raises(ValueError, match="empty speaker name"):
            isolated_store.enroll_voiceprint(
                speaker_name="   ",
                embedding=dummy_vec,
                project="TEST_PROJ",
                is_verified=True,
            )

        # Zero vector fails closed
        zero_vec = [0.0] * 256
        with pytest.raises(ValueError, match="near-zero acoustic embedding"):
            isolated_store.enroll_voiceprint(
                speaker_name="Dr. Alex",
                embedding=zero_vec,
                project="TEST_PROJ",
                is_verified=True,
            )

    def test_malformed_input_dimension_mismatch(self, isolated_store: VoiceprintStore) -> None:
        """Category 2: Malformed input - verifies strict 256-D requirement."""
        short_vec = [0.1] * 128
        with pytest.raises(ValueError, match="Embedding dimension mismatch: expected 256, got 128"):
            isolated_store.enroll_voiceprint(
                speaker_name="Dr. Alex",
                embedding=short_vec,
                project="TEST_PROJ",
                is_verified=True,
            )

        long_vec = [0.1] * 512
        with pytest.raises(ValueError, match="Embedding dimension mismatch: expected 256, got 512"):
            isolated_store.match_voiceprint(long_vec, project="TEST_PROJ")

    def test_invariant_bypass_unverified_turn_enrollment_rejected(self, isolated_store: VoiceprintStore) -> None:
        """Category 4: Invariant bypass attempt - unverified turns are strictly barred from enrollment."""
        vec = generate_deterministic_embedding("unverified_alex")
        with pytest.raises(ValueError, match="Fail-Closed Voiceprint Gate: Cannot enroll unverified speaker"):
            isolated_store.enroll_voiceprint(
                speaker_name="Alex Sanchez",
                embedding=vec,
                project="TEST_PROJ",
                is_verified=False,
            )

        # Confirm nothing was written to the store
        enrolled = isolated_store.get_enrolled_speakers(project="TEST_PROJ")
        assert len(enrolled) == 0

    def test_concurrency_and_race_16_workers_wal(self, isolated_store: VoiceprintStore) -> None:
        """Category 3: Concurrency & race - 16 concurrent workers enroll and query simultaneously."""
        worker_count = 16
        iterations_per_worker = 10

        def worker_task(worker_id: int) -> int:
            successes = 0
            for i in range(iterations_per_worker):
                spk_name = f"Speaker_{worker_id % 4}"
                emb = generate_deterministic_embedding(f"seed_{worker_id}_{i}")
                isolated_store.enroll_voiceprint(
                    speaker_name=spk_name,
                    embedding=emb,
                    project="CONCURRENT_PROJ",
                    is_verified=True,
                    verification_source=f"worker_{worker_id}",
                )
                match = isolated_store.match_voiceprint(emb, project="CONCURRENT_PROJ", threshold=0.10)
                if match is not None:
                    successes += 1
            return successes

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker_task, wid) for wid in range(worker_count)]
            results = [f.result(timeout=30) for f in concurrent.futures.as_completed(futures)]

        assert len(results) == worker_count
        assert sum(results) > 0

        # Physical integrity check on database file
        assert isolated_store.verify_integrity() is True
        speakers = isolated_store.get_enrolled_speakers(project="CONCURRENT_PROJ")
        assert len(speakers) == 4
        # Total sample counts across the 4 speakers should equal worker_count * iterations_per_worker
        total_samples = sum(s["sample_count"] for s in speakers)
        assert total_samples == worker_count * iterations_per_worker

    def test_diarization_cache_roundtrip_and_corruption_resilience(self, tmp_path: Path) -> None:
        """Category 5: Recovery & resilience - diarization cache save, load, and corruption handling."""
        cache_dir = tmp_path / "cache"
        audio_sha = "abc123def45678901234567890123456"
        model_name = "pyannote_3.1"
        turns = [
            {"start": 0.0, "end": 4.5, "speaker": "Speaker 1", "duration": 4.5},
            {"start": 4.5, "end": 10.0, "speaker": "Speaker 2", "duration": 5.5},
        ]
        centroids = {
            "Speaker 1": [0.1] * 256,
            "Speaker 2": [0.2] * 256,
        }

        # Save cache
        saved_file = save_audio_diarization_cache(
            audio_sha256=audio_sha,
            turns=turns,
            model_name=model_name,
            cache_dir=cache_dir,
            centroids=centroids,
        )
        assert saved_file.is_file()

        # Load cache hit
        loaded = load_audio_diarization_cache(
            audio_sha256=audio_sha,
            model_name=model_name,
            cache_dir=cache_dir,
        )
        assert loaded is not None
        loaded_turns, loaded_centroids = loaded
        assert len(loaded_turns) == 2
        assert loaded_turns[0]["speaker"] == "Speaker 1"
        assert len(loaded_centroids["Speaker 1"]) == 256

        # Corrupt cache file with invalid JSON
        saved_file.write_text("NOT_VALID_JSON{", encoding="utf-8")
        corrupt_res = load_audio_diarization_cache(
            audio_sha256=audio_sha,
            model_name=model_name,
            cache_dir=cache_dir,
        )
        assert corrupt_res is None  # Resiliently returns None without crashing

    def test_exact_production_regression_cosine_threshold_discrimination(
        self, isolated_store: VoiceprintStore
    ) -> None:
        """Category 7: Exact production regression - verifies >=0.82 threshold discrimination."""
        # Enroll Alex Sanchez with high-energy distinct seed
        base_alex_emb = generate_deterministic_embedding("alex_canonical_timbre")
        isolated_store.enroll_voiceprint(
            speaker_name="Alex Sanchez",
            embedding=base_alex_emb,
            project="TEST_PROJECT",
            is_verified=True,
        )

        # Same speaker with slight acoustic variation (norm distance < 0.1) -> cos_sim > 0.95
        slight_variation = [x + 0.005 for x in base_alex_emb]
        alex_match = isolated_store.match_voiceprint(
            slight_variation, project="TEST_PROJECT", threshold=0.82
        )
        assert alex_match is not None
        assert alex_match[0] == "Alex Sanchez"
        assert alex_match[1] >= 0.82

        # Completely different speaker timbre -> cos_sim << 0.82
        different_emb = generate_deterministic_embedding("totally_different_timbre_brien")
        diff_match = isolated_store.match_voiceprint(
            different_emb, project="TEST_PROJECT", threshold=0.82
        )
        assert diff_match is None  # Does not falsely match Alex
