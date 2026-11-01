"""End-to-End lifecycle tests for Cross-Session Speaker Voiceprint Learning (Wave 4).

Validates closed-loop progression:
1. Meeting 1: Historical audio + verified Output 01 -> Voiceprint harvesting & enrollment.
2. Meeting 2: New multi-speaker audio -> Voiceprint matching & auto-identification without HITL.
3. Transcript synthesis -> Resolved Output 01 with canonical speaker attributions.
4. Sealed cache replay -> Instant recovery from *.voiceprints.json sidecar.
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
from typing import Any, Dict
import unittest.mock as mock
import wave

import numpy as np
import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    _extract_and_bind_voiceprints,
    _save_voiceprints_sidecar,
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
from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal


def _create_synthetic_wav(path: Path, duration_sec: float = 6.0, sample_rate: int = 16000) -> None:
    """Creates a hermetic synthetic 16kHz mono WAV file."""
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        raw_bytes = bytearray()
        for i in range(num_samples):
            val = int(32767.0 * 0.4 * np.sin(2.0 * np.pi * 440.0 * (i / sample_rate)))
            raw_bytes.extend(struct.pack("<h", val))
        wf.writeframes(raw_bytes)


HISTORICAL_OUTPUT_01 = """# Output 01
**Generated Filename:** `01_Arch_Review_hitl_resolved_transcript.md`

## Participants

| Participant | Role | Transcript coverage |
|---|---|---|
| Alice Smith | Enterprise Architect | Discrete turns present |
| Charlie Brown | Tech Lead | Discrete turns present |

## Attribution Summary

| Attendee | Organization | Role | Attributed Turns | Confidence Tier | Anchor Cue / Attribution Grounding |
|---|---|---|---|---|---|
| Alice Smith | Apex Global Enterprises | Enterprise Architect | 8 turns | Confirmed via Operator HITL | BAA catalog architecture design |
| Charlie Brown | PartnerAdvisory | Tech Lead | 6 turns | High | Infrastructure pipeline deployment remarks |

## Verbatim Attributed Transcript

### [00:00:00]

**Alice Smith:** Welcome everybody. Let's discuss the BAA catalog enterprise architecture.

### [00:00:15]

**Charlie Brown:** On the infrastructure side, the pipeline is fully deployed and ready for qualification.
"""


def test_cross_session_voiceprint_learning_full_lifecycle(tmp_path: Path) -> None:
    """Verifies complete end-to-end voiceprint harvesting, auto-identification, and cache replay."""
    project = "TEST_PROJECT"
    store_db = tmp_path / "voiceprint_memory.sqlite"
    store = VoiceprintStore(db_path=store_db)

    # -------------------------------------------------------------------------
    # STAGE 1: Historical Meeting 1 Harvest & Enrollment
    # -------------------------------------------------------------------------
    m1_dir = tmp_path / "output" / project / "2026-03-01" / "arch_review"
    m1_dir.mkdir(parents=True)

    wav_m1 = m1_dir / "00_Arch_Review_master_audio.wav"
    _create_synthetic_wav(wav_m1, duration_sec=8.0)
    (m1_dir / "01_Arch_Review_hitl_resolved_transcript.md").write_text(HISTORICAL_OUTPUT_01, encoding="utf-8")

    emb_alice = generate_deterministic_embedding(f"{wav_m1.stem}:Alice Smith", dim=256)
    emb_charlie = generate_deterministic_embedding(f"{wav_m1.stem}:Charlie Brown", dim=256)

    # Mock diarization engine centroid extraction for Meeting 1
    mock_diar_engine = mock.MagicMock()
    mock_diar_engine.extract_cluster_centroids.return_value = {
        "Alice Smith": emb_alice,
        "Charlie Brown": emb_charlie,
    }

    with mock.patch("dual_pipeline.audio_ingestion_core.diarization_engine.get_diarization_engine", return_value=mock_diar_engine):
        harvest_res = harvest_voiceprints_from_meeting(m1_dir, store=store, project=project, dry_run=False)

    assert harvest_res["enrolled_count"] == 2
    enrolled_names = {s["canonical_name"] for s in store.get_enrolled_speakers(project=project)}
    assert "Alice Smith" in enrolled_names
    assert "Charlie Brown" in enrolled_names

    # -------------------------------------------------------------------------
    # STAGE 2: Future Meeting 2 Auto-Identification (Zero HITL)
    # -------------------------------------------------------------------------
    m2_dir = tmp_path / "output" / project / "2026-03-05" / "sprint_planning"
    m2_dir.mkdir(parents=True)

    wav_m2 = m2_dir / "00_Sprint_Planning_master_audio.wav"
    _create_synthetic_wav(wav_m2, duration_sec=8.0)

    m2_metadata: Dict[str, Any] = {
        "attendees": ["Alice Smith", "Charlie Brown"],
        "voiceprint_store_path": str(store_db),
        "transcript_cache_dir": str(m2_dir),
        "project": project,
        "title": "Sprint Planning",
        "mock_cluster_centroids": {
            "SPEAKER_00": emb_alice,
            "SPEAKER_01": emb_charlie,
        },
    }

    segments_m2 = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0, "text": "Sprint planning session kickoff."},
        {"speaker": "SPEAKER_01", "start": 4.0, "end": 8.0, "text": "All tasks are estimated and assigned."},
    ]

    mock_engine_m2 = mock.MagicMock()
    mock_engine_m2.extract_cluster_centroids.return_value = m2_metadata["mock_cluster_centroids"]

    # Extraction and automatic voiceprint roster matching in transcriber
    centroids_m2, matches_m2 = _extract_and_bind_voiceprints(
        mock_engine_m2, str(wav_m2), segments_m2, m2_metadata, mock=False,
    )

    assert matches_m2.get("SPEAKER_00") == "Alice Smith"
    assert matches_m2.get("SPEAKER_01") == "Charlie Brown"
    assert m2_metadata["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"

    # Persist cache files and voiceprints sidecar
    safe_stem = "Sprint_Planning"
    cache_key = "testcachekey123"
    cache_file = m2_dir / f"{safe_stem}_{cache_key}.transcript_cache.json"
    structured_file = m2_dir / f"{safe_stem}_{cache_key}.segments.json"
    utterance_file = m2_dir / f"{safe_stem}_{cache_key}.utterances.json"

    formatted_transcript = (
        "### [00:00] **SPEAKER_00:** Sprint planning session kickoff.\n"
        "### [00:04] **SPEAKER_01:** All tasks are estimated and assigned.\n"
    )
    cache_file.write_text(formatted_transcript, encoding="utf-8")
    structured_file.write_text(json.dumps(segments_m2, indent=2), encoding="utf-8")
    utterance_file.write_text(json.dumps(segments_m2, indent=2), encoding="utf-8")
    create_cache_seal(
        structured_file=structured_file,
        cache_file=cache_file,
        utterance_file=utterance_file,
        audio_sha256="m2_sha256",
        cache_key=cache_key,
    )

    sidecar_path = _save_voiceprints_sidecar(
        cache_dir=m2_dir,
        safe_stem=safe_stem,
        cache_key=cache_key,
        audio_hash="m2_sha256",
        project=project,
        centroids=centroids_m2,
        matches=matches_m2,
    )
    assert sidecar_path is not None and sidecar_path.is_file()

    # Stage U0 Ingress hydration
    m2_ingress_meta: Dict[str, Any] = {"transcript_cache_dir": str(m2_dir), "project": project}
    hydrated_hints, hydrated_map, *_ = _hydrate_speaker_artifacts(m2_ingress_meta, [m2_dir])
    assert hydrated_map is True
    assert m2_ingress_meta["speaker_map"]["SPEAKER_00"] == "Alice Smith"
    assert m2_ingress_meta["speaker_map"]["SPEAKER_01"] == "Charlie Brown"
    assert m2_ingress_meta["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"

    # Stage L1 Speaker Attribution resolves autonomously without interactive resolver
    attr_engine = AudioSpeakerAttributionEngine(voiceprint_store=store)
    m2_roster = [
        {"name": "Alice Smith", "role": "Architect", "organization": "NEE"},
        {"name": "Charlie Brown", "role": "Tech Lead", "organization": "PartnerAdvisory"},
    ]
    m2_ingress_meta["roster"] = m2_roster

    # Interactive resolver is None -> if ambiguous, it would raise HITLRequiredError
    res_m2 = attr_engine.analyze_and_resolve(
        formatted_transcript, m2_ingress_meta, interactive_resolver=None, autonomous_if_decisive=True,
    )
    assert res_m2.speaker_map["Speaker 1"] == "Alice Smith"
    assert res_m2.speaker_map["Speaker 2"] == "Charlie Brown"
    assert "Alice Smith" in res_m2.resolved_transcript
    assert "Charlie Brown" in res_m2.resolved_transcript
    assert "SPEAKER_00" not in res_m2.resolved_transcript

    # -------------------------------------------------------------------------
    # STAGE 3: Sealed Cache Replay (Instant Recovery)
    # -------------------------------------------------------------------------
    replay_meta: Dict[str, Any] = {"project": project}
    cached_content = _try_load_cache(cache_file, structured_file, utterance_file, replay_meta)

    assert cached_content == formatted_transcript
    assert replay_meta.get("cache_seal_verified") is True
    assert replay_meta["voiceprint_matches"]["SPEAKER_00"] == "Alice Smith"
    assert replay_meta["voiceprint_matches"]["SPEAKER_01"] == "Charlie Brown"
    assert replay_meta["diarization_status"] == "DIARIZED_ACOUSTIC_RESOLVED"
    assert store.verify_integrity() is True
