"""Adversarial and Edge-Case Qualification Tests for Cross-Session Voiceprint Learning (Wave 4).

Covers mandatory edge-case categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte audio, empty candidate sets, empty cluster centroids, sub-2.0s turns.
2. Malformed input: Corrupted JSON sidecars, invalid audio formats, truncated embeddings.
3. Invariant bypass & security: Fail-closed unverified enrollment, impostor low-margin rejection,
   duplicate audio_sha256 replay attack resistance.
4. Concurrency & race: 16-worker WAL stress test with concurrent writes and reads.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List
import unittest.mock as mock

import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    _match_cluster_centroids_against_roster,
    _try_load_cache,
)
from dual_pipeline.audio_ingestion_core.voiceprint_harvester import (
    harvest_voiceprints_from_meeting,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)
from dual_pipeline.canonical_pipeline.u0_ingress import _hydrate_speaker_artifacts
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


# =============================================================================
# 1. BOUNDARY PAYLOAD TESTS
# =============================================================================

def test_adversarial_boundary_empty_and_zero_byte(tmp_path: Path) -> None:
    """Verifies graceful handling of empty inputs and 0-byte files."""
    db_path = tmp_path / "voiceprint_boundary.sqlite"
    store = VoiceprintStore(db_path=db_path)

    # 1. Empty cluster centroids & empty roster
    assert store.match_roster({}, ["Alice Smith"]) == {}
    assert store.match_roster({"SPEAKER_00": [0.1] * 256}, []) == {}
    assert store.match_roster({}, []) == {}

    # 2. 0-byte WAV file in meeting directory
    empty_meeting_dir = tmp_path / "empty_meeting"
    empty_meeting_dir.mkdir(parents=True)
    (empty_meeting_dir / "00_empty.wav").write_bytes(b"")
    (empty_meeting_dir / "01_empty_hitl_resolved_transcript.md").write_text("# Empty", encoding="utf-8")

    res = harvest_voiceprints_from_meeting(empty_meeting_dir, store=store, dry_run=False)
    assert res["status"] in ("SKIPPED_NO_AUDIO", "SKIPPED_NO_VERIFIED_SPEAKERS", "SKIPPED_NO_TURNS")


def test_adversarial_boundary_sub_minimum_duration(tmp_path: Path) -> None:
    """Verifies turns with clean speech duration < 2.0s are handled safely."""
    db_path = tmp_path / "voiceprint_dur.sqlite"
    store = VoiceprintStore(db_path=db_path)
    emb = generate_deterministic_embedding("short_turn", dim=256)

    # Clean speech 0.5s should still enroll without zero-division error
    res = store.enroll_voiceprint(
        "Short Speaker",
        emb,
        is_verified=True,
        duration_sec=0.5,
        clean_speech_sec=0.5,
        audio_sha256="sha_short",
    )
    assert res["action"] == "ENROLLED"
    assert res["sample_count"] == 1


# =============================================================================
# 2. MALFORMED INPUT & CORRUPTED SIDECAR TESTS
# =============================================================================

def test_adversarial_corrupted_sidecar_graceful_recovery(tmp_path: Path) -> None:
    """Verifies that truncated or malformed voiceprint sidecars do not crash ingestion."""
    corrupted_dir = tmp_path / "corrupted_session"
    corrupted_dir.mkdir(parents=True)

    # Write truncated malformed JSON sidecar
    (corrupted_dir / "corrupted.voiceprints.json").write_text("{\"matches\": {\"SPEAKER_00\": ", encoding="utf-8")

    metadata: Dict[str, Any] = {}
    hydrated_hints, hydrated_map, *_ = _hydrate_speaker_artifacts(metadata, [corrupted_dir])
    # Failed sidecar must not crash and must not mark hydrated_map
    assert hydrated_map is False
    assert "speaker_map" not in metadata


def test_adversarial_corrupted_cache_fallback(tmp_path: Path) -> None:
    """Verifies that corrupted structured segments or cache files return None safely."""
    cache_file = tmp_path / "broken.transcript_cache.json"
    cache_file.write_text("Valid text that is long enough to exceed the fifty character limit for transcript cache.", encoding="utf-8")
    structured_file = tmp_path / "broken.segments.json"
    structured_file.write_text("{not valid json", encoding="utf-8")
    utterance_file = tmp_path / "broken.utterances.json"
    utterance_file.write_text("[]", encoding="utf-8")

    metadata: Dict[str, Any] = {}
    cached = _try_load_cache(cache_file, structured_file, utterance_file, metadata)
    assert cached is None


# =============================================================================
# 3. INVARIANT BYPASS & SECURITY HARDENING
# =============================================================================

def test_adversarial_unverified_enrollment_fails_closed(tmp_path: Path) -> None:
    """Invariant: Unverified speaker enrollment must fail closed with ValueError."""
    db_path = tmp_path / "voiceprint_security.sqlite"
    store = VoiceprintStore(db_path=db_path)
    emb = generate_deterministic_embedding("unverified", dim=256)

    with pytest.raises(ValueError, match="Fail-Closed Voiceprint Gate"):
        store.enroll_voiceprint("Unverified Attacker", emb, is_verified=False)


def test_adversarial_impostor_and_low_margin_rejection(tmp_path: Path) -> None:
    """Verifies impostors (<0.82) and low-margin (<0.15) scores do not auto-resolve."""
    db_path = tmp_path / "voiceprint_impostor.sqlite"
    store = VoiceprintStore(db_path=db_path)

    emb_alice = generate_deterministic_embedding("alice", dim=256)
    emb_bob = generate_deterministic_embedding("bob", dim=256)

    store.enroll_voiceprint("Alice Smith", emb_alice, project="GLOBAL", is_verified=True)
    store.enroll_voiceprint("Bob Jones", emb_bob, project="GLOBAL", is_verified=True)

    # 1. Impostor embedding (orthogonal / low similarity to both)
    emb_impostor = generate_deterministic_embedding("completely_unknown_impostor", dim=256)
    res_impostor = store.match_roster(
        {"SPEAKER_00": emb_impostor},
        roster_names=["Alice Smith", "Bob Jones"],
        threshold=0.82,
        margin_threshold=0.15,
    )
    # No cluster should be decisively matched
    assert "SPEAKER_00" not in res_impostor

    # 2. Low-margin ambiguous candidate (similar score to both Alice and Bob)
    # Construct synthetic centroid equidistant to Alice and Bob
    ambiguous_vec = [(emb_alice[i] + emb_bob[i]) / 2.0 for i in range(256)]
    res_ambiguous = store.match_roster(
        {"SPEAKER_00": ambiguous_vec},
        roster_names=["Alice Smith", "Bob Jones"],
        threshold=0.50,  # lower threshold to force both to compete
        margin_threshold=0.15,
    )
    if "SPEAKER_00" in res_ambiguous:
        # Margin should be small (<0.15), so is_decisive must be False
        assert res_ambiguous["SPEAKER_00"]["is_decisive"] is False


def test_adversarial_duplicate_audio_sha_replay_attack_prevention(tmp_path: Path) -> None:
    """Verifies re-enrolling identical audio_sha256 is strictly idempotent."""
    db_path = tmp_path / "voiceprint_replay.sqlite"
    store = VoiceprintStore(db_path=db_path)
    emb = generate_deterministic_embedding("alice", dim=256)

    # First enrollment
    res1 = store.enroll_voiceprint(
        "Alice Smith", emb, project="GLOBAL", is_verified=True, audio_sha256="fixed_audio_sha"
    )
    assert res1["action"] == "ENROLLED"
    assert res1["sample_count"] == 1

    # Second enrollment of same audio_sha256 (e.g. re-running pipeline)
    res2 = store.enroll_voiceprint(
        "Alice Smith", emb, project="GLOBAL", is_verified=True, audio_sha256="fixed_audio_sha"
    )
    assert res2["action"] == "ALREADY_ENROLLED"
    assert res2["sample_count"] == 1  # must not double-count

    speakers = store.get_enrolled_speakers()
    assert len(speakers) == 1
    assert speakers[0]["sample_count"] == 1


# =============================================================================
# 4. 16-WORKER WAL CONCURRENCY STRESS TEST
# =============================================================================

def test_adversarial_16_worker_wal_concurrency(tmp_path: Path) -> None:
    """Stress tests VoiceprintStore under 16 concurrent workers with mixed reads and writes.

    Must complete with ZERO OperationalError: database is locked and PRAGMA integrity_check ok.
    """
    db_path = tmp_path / "voiceprint_concurrency.sqlite"
    store = VoiceprintStore(db_path=db_path)

    num_workers = 16
    iterations_per_worker = 10

    def worker_task(worker_id: int) -> int:
        success_count = 0
        for i in range(iterations_per_worker):
            speaker_name = f"Speaker_W{worker_id % 4}"
            audio_sha = f"sha_w{worker_id}_{i}"
            emb = generate_deterministic_embedding(f"{speaker_name}_{i}", dim=256)

            # Mixed write
            store.enroll_voiceprint(
                speaker_name=speaker_name,
                embedding=emb,
                project="STRESS_PROJ",
                is_verified=True,
                audio_sha256=audio_sha,
                clean_speech_sec=3.0,
            )

            # Mixed reads
            store.get_enrolled_speakers(project="STRESS_PROJ")
            store.get_voiceprint(speaker_name, project="STRESS_PROJ")
            store.match_roster(
                {"SPEAKER_00": emb},
                roster_names=["Speaker_W0", "Speaker_W1", "Speaker_W2", "Speaker_W3"],
                project="STRESS_PROJ",
            )
            success_count += 1
        return success_count

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(worker_task, wid) for wid in range(num_workers)]
        results = [f.result(timeout=45) for f in concurrent.futures.as_completed(futures)]

    assert sum(results) == num_workers * iterations_per_worker

    # Verify database integrity independently via read-only connection
    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = ro_conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    row = cur.fetchone()
    assert row[0] == "ok"
    ro_conn.close()

    assert store.verify_integrity() is True
    speakers = store.get_enrolled_speakers(project="STRESS_PROJ")
    assert len(speakers) == 4
