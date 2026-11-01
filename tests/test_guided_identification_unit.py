"""Unit tests for Wave 3: Guided Fast-Path Diarization & Automatic Pre-HITL Identification."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import unittest.mock as mock

import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    _check_stereo_ch0_fastpath,
    _extract_and_bind_voiceprints,
    _match_cluster_centroids_against_roster,
    _save_voiceprints_sidecar,
    _try_load_cache,
)
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    generate_deterministic_embedding,
)
from dual_pipeline.canonical_pipeline.u0_ingress import _hydrate_speaker_artifacts
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
)


@pytest.fixture
def temp_voiceprint_store(tmp_path: Path) -> VoiceprintStore:
    db_path = tmp_path / "voiceprint_test.sqlite"
    return VoiceprintStore(db_path=db_path)


def test_live_voice_registry_preseeding(temp_voiceprint_store: VoiceprintStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-seeds voice clusters with enrolled voiceprints from VoiceprintStore."""
    emb_alice = generate_deterministic_embedding("alice", dim=256)
    emb_bob = generate_deterministic_embedding("bob", dim=256)

    temp_voiceprint_store.enroll_voiceprint("Alice Smith", emb_alice, project="GLOBAL", is_verified=True)
    temp_voiceprint_store.enroll_voiceprint("Bob Jones", emb_bob, project="GLOBAL", is_verified=True)

    monkeypatch.setattr(
        "dual_pipeline.audio_ingestion_core.voiceprint_store.VoiceprintStore",
        lambda *args, **kwargs: temp_voiceprint_store,
    )

    attendees = [{"name": "Alice Smith"}, {"name": "Bob Jones"}, {"name": "Silent Sam"}]
    registry = LiveVoiceRegistry(attendees=attendees)

    resolved_names = {vc.resolved_speaker for vc in registry.voice_clusters.values() if vc.resolved_speaker}
    assert "Alice Smith" in resolved_names
    assert "Bob Jones" in resolved_names
    assert "Silent Sam" not in resolved_names


def test_match_cluster_centroids_against_roster(temp_voiceprint_store: VoiceprintStore) -> None:
    """Matches cluster centroids against enrolled roster attendees."""
    emb_alice = generate_deterministic_embedding("alice", dim=256)
    emb_bob = generate_deterministic_embedding("bob", dim=256)

    temp_voiceprint_store.enroll_voiceprint("Alice Smith", emb_alice, project="GLOBAL", is_verified=True)
    temp_voiceprint_store.enroll_voiceprint("Bob Jones", emb_bob, project="GLOBAL", is_verified=True)

    metadata: Dict[str, Any] = {
        "attendees": ["Alice Smith", "Bob Jones"],
        "voiceprint_store_path": str(temp_voiceprint_store.db_path),
        "project": "GLOBAL",
    }

    clusters = {
        "SPEAKER_00": emb_alice,
        "SPEAKER_01": emb_bob,
    }

    matches = _match_cluster_centroids_against_roster(clusters, metadata)
    assert matches.get("SPEAKER_00") == "Alice Smith"
    assert matches.get("SPEAKER_01") == "Bob Jones"


def test_extract_and_bind_voiceprints_populates_metadata(temp_voiceprint_store: VoiceprintStore) -> None:
    """Extracts centroids and updates metadata with speaker_map and DIARIZED_ACOUSTIC_RESOLVED."""
    emb_alice = generate_deterministic_embedding("alice", dim=256)
    emb_bob = generate_deterministic_embedding("bob", dim=256)

    temp_voiceprint_store.enroll_voiceprint("Alice Smith", emb_alice, project="GLOBAL", is_verified=True)
    temp_voiceprint_store.enroll_voiceprint("Bob Jones", emb_bob, project="GLOBAL", is_verified=True)

    metadata: Dict[str, Any] = {
        "attendees": ["Alice Smith", "Bob Jones"],
        "voiceprint_store_path": str(temp_voiceprint_store.db_path),
        "project": "GLOBAL",
        "mock_cluster_centroids": {
            "SPEAKER_00": emb_alice,
            "SPEAKER_01": emb_bob,
        },
    }

    segments = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0, "text": "Hello world."},
        {"speaker": "SPEAKER_01", "start": 5.5, "end": 10.0, "text": "Hi Alice."},
    ]

    mock_engine = mock.MagicMock()
    mock_engine.extract_cluster_centroids.return_value = metadata["mock_cluster_centroids"]

    centroids, matches = _extract_and_bind_voiceprints(
        mock_engine, "dummy.wav", segments, metadata, mock=False
    )

    assert matches.get("SPEAKER_00") == "Alice Smith"
    assert matches.get("SPEAKER_01") == "Bob Jones"
    assert metadata["speaker_map"]["SPEAKER_00"] == "Alice Smith"
    assert metadata["speaker_map"]["Speaker 1"] == "Alice Smith"
    assert metadata["speaker_map"]["SPEAKER_01"] == "Bob Jones"
    assert metadata["speaker_map"]["Speaker 2"] == "Bob Jones"
    assert metadata["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"
    assert metadata["has_acoustic_diarization"] is True


def test_try_load_cache_restores_voiceprints(tmp_path: Path) -> None:
    """Sealed cache loading restores voiceprint_matches and sets DIARIZED_ACOUSTIC_RESOLVED."""
    from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal

    cache_file = tmp_path / "test_123.transcript_cache.json"
    structured_file = tmp_path / "test_123.segments.json"
    utterance_file = tmp_path / "test_123.utterances.json"
    vp_file = tmp_path / "test_123.voiceprints.json"

    long_cached_text = "# Cached transcript with sufficient length to satisfy the cache loading length check of 50 characters."
    cache_file.write_text(long_cached_text, encoding="utf-8")
    structured_file.write_text(json.dumps([{"speaker": "SPEAKER_00", "text": "Hi"}]), encoding="utf-8")
    utterance_file.write_text(json.dumps([]), encoding="utf-8")
    create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
        audio_sha256="abc12345",
        cache_key="test_123",
    )
    vp_file.write_text(
        json.dumps({
            "audio_sha256": "abc12345",
            "project": "GLOBAL",
            "matches": {"SPEAKER_00": "Alice Smith"},
            "centroids": {"SPEAKER_00": [0.1] * 256},
        }),
        encoding="utf-8",
    )

    metadata: Dict[str, Any] = {}
    cached_text = _try_load_cache(cache_file, structured_file, utterance_file, metadata)

    assert cached_text == long_cached_text
    assert metadata["voiceprint_matches"]["SPEAKER_00"] == "Alice Smith"
    assert metadata["speaker_map"]["SPEAKER_00"] == "Alice Smith"
    assert metadata["speaker_map"]["Speaker 1"] == "Alice Smith"
    assert metadata["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"


def test_check_stereo_ch0_fastpath(temp_voiceprint_store: VoiceprintStore, tmp_path: Path) -> None:
    """Verifies that Ch0 matching an enrolled remote attendee triggers fast-path bypass."""
    emb_alice = generate_deterministic_embedding("alice", dim=256)
    temp_voiceprint_store.enroll_voiceprint("Alice Smith", emb_alice, project="GLOBAL", is_verified=True)

    ch0_wav = tmp_path / "ch0.wav"
    ch0_wav.write_bytes(b"RIFF" + b"\x00" * 5000)

    aligned = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0},
        {"speaker": "SPEAKER_01", "start": 4.5, "end": 8.0},
    ]

    mock_engine = mock.MagicMock()
    mock_engine.extract_cluster_centroids.return_value = {"SPEAKER_00": emb_alice}

    metadata = {
        "attendees": ["Alice Smith", "Host Operator"],
        "voiceprint_store_path": str(temp_voiceprint_store.db_path),
        "project": "GLOBAL",
    }

    fastpath_spk = _check_stereo_ch0_fastpath(mock_engine, ch0_wav, aligned, metadata)
    assert fastpath_spk == "Alice Smith"


def test_u0_ingress_hydrates_speaker_map_from_voiceprints(tmp_path: Path) -> None:
    """Verifies that u0_ingress hydrates speaker_map and diarization_status from *.voiceprints.json."""
    vp_file = tmp_path / "meeting_session.voiceprints.json"
    vp_file.write_text(
        json.dumps({
            "matches": {
                "SPEAKER_00": "Alice Smith",
                "SPEAKER_01": "Bob Jones",
            }
        }),
        encoding="utf-8",
    )

    metadata: Dict[str, Any] = {}
    hydrated_hints, hydrated_map, *_ = _hydrate_speaker_artifacts(metadata, [tmp_path])

    assert hydrated_map is True
    assert metadata["speaker_map"]["SPEAKER_00"] == "Alice Smith"
    assert metadata["speaker_map"]["Speaker 1"] == "Alice Smith"
    assert metadata["speaker_map"]["SPEAKER_01"] == "Bob Jones"
    assert metadata["speaker_map"]["Speaker 2"] == "Bob Jones"
    assert metadata["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"
    assert metadata["has_acoustic_diarization"] is True


def test_speaker_attribution_resolves_autonomously_with_voiceprint_map() -> None:
    """Verifies that AudioSpeakerAttributionEngine resolves without HITL when speaker_map is hydrated."""
    engine = AudioSpeakerAttributionEngine()
    transcript = (
        "### [00:00] **Speaker 1:** We deployed the BAA catalog architecture yesterday.\n"
        "### [00:15] **Speaker 2:** Great, infrastructure pipeline deployment looks stable.\n"
    )
    metadata = {
        "speaker_map": {
            "Speaker 1": "Alice Smith",
            "Speaker 2": "Charlie Brown",
            "SPEAKER_00": "Alice Smith",
            "SPEAKER_01": "Charlie Brown",
        },
        "roster": [
            {"name": "Alice Smith", "role": "Architect", "organization": "NEE"},
            {"name": "Charlie Brown", "role": "Tech Lead", "organization": "PartnerAdvisory"},
        ],
        "project": "GLOBAL",
    }

    # Should resolve cleanly with zero HITL prompts
    res = engine.analyze_and_resolve(transcript, metadata, interactive_resolver=None, autonomous_if_decisive=True)
    assert res.speaker_map["Speaker 1"] == "Alice Smith"
    assert res.speaker_map["Speaker 2"] == "Charlie Brown"
    assert "Alice Smith" in res.resolved_transcript
    assert "Charlie Brown" in res.resolved_transcript


def test_sequential_meetings_auto_resolve_without_hitl(temp_voiceprint_store: VoiceprintStore, tmp_path: Path) -> None:
    """Verifies that Meeting 1 enrolls speakers, and Meeting 2 auto-identifies without HITL."""
    emb_alice = generate_deterministic_embedding("alice", dim=256)
    emb_bob = generate_deterministic_embedding("bob", dim=256)

    # Meeting 1: Harvest / enroll verified voiceprints
    temp_voiceprint_store.enroll_voiceprint(
        "Alice Smith", emb_alice, project="GLOBAL", is_verified=True, audio_sha256="meeting1_sha",
    )
    temp_voiceprint_store.enroll_voiceprint(
        "Bob Jones", emb_bob, project="GLOBAL", is_verified=True, audio_sha256="meeting1_sha",
    )

    # Meeting 2: Ingest audio sharing the same speakers
    meeting2_dir = tmp_path / "meeting2"
    meeting2_dir.mkdir(parents=True)
    m2_metadata = {
        "attendees": ["Alice Smith", "Bob Jones"],
        "voiceprint_store_path": str(temp_voiceprint_store.db_path),
        "transcript_cache_dir": str(meeting2_dir),
        "project": "GLOBAL",
        "mock_cluster_centroids": {
            "SPEAKER_00": emb_alice,
            "SPEAKER_01": emb_bob,
        },
    }

    mock_engine = mock.MagicMock()
    mock_engine.extract_cluster_centroids.return_value = m2_metadata["mock_cluster_centroids"]
    segments = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0, "text": "Meeting 2 begins."},
        {"speaker": "SPEAKER_01", "start": 4.5, "end": 9.0, "text": "Bob here, sounds good."},
    ]

    centroids, matches = _extract_and_bind_voiceprints(
        mock_engine, "meeting2.wav", segments, m2_metadata, mock=False,
    )
    assert matches == {"SPEAKER_00": "Alice Smith", "SPEAKER_01": "Bob Jones"}

    sidecar_path = _save_voiceprints_sidecar(
        cache_dir=meeting2_dir,
        safe_stem="meeting2",
        cache_key="key123",
        audio_hash="m2_sha",
        project="GLOBAL",
        centroids=centroids,
        matches=matches,
    )
    assert sidecar_path is not None and sidecar_path.is_file()

    fresh_meta = {"transcript_cache_dir": str(meeting2_dir)}
    hydrated_hints, hydrated_map, *_ = _hydrate_speaker_artifacts(fresh_meta, [meeting2_dir])
    assert hydrated_map is True
    assert fresh_meta["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"

    attr_engine = AudioSpeakerAttributionEngine()
    transcript_m2 = (
        "### [00:00] **Speaker 1:** Meeting 2 begins.\n"
        "### [00:04] **Speaker 2:** Bob here, sounds good.\n"
    )
    m2_roster = [
        {"name": "Alice Smith", "role": "Architect", "organization": "NEE"},
        {"name": "Bob Jones", "role": "PM", "organization": "PartnerAdvisory"},
    ]
    m2_metadata["roster"] = m2_roster
    m2_result = attr_engine.analyze_and_resolve(
        transcript_m2, m2_metadata, interactive_resolver=None, autonomous_if_decisive=True,
    )
    assert m2_result.speaker_map["Speaker 1"] == "Alice Smith"
    assert m2_result.speaker_map["Speaker 2"] == "Bob Jones"
    assert "Alice Smith" in m2_result.resolved_transcript
    assert "Bob Jones" in m2_result.resolved_transcript

