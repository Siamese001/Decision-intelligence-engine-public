"""
Wave 1 Acceptance Tests: Shift-Left Stage 0/U0 Acoustic Roster Preflight & Co-Attendance Priors.
"""
from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.audio_ingestion_core.voiceprint_store import EMBEDDING_DIM, VoiceprintStore
from dual_pipeline.live_meeting_recorder_core.acoustic_features import extract_256d_acoustic_embedding
from dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader import (
    preload_stage0_speaker_context,
    run_acoustic_roster_preflight,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    extract_candidate_attendees,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def _make_unit_vec(seed_idx: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[seed_idx % EMBEDDING_DIM] = 0.9
    vec[(seed_idx + 1) % EMBEDDING_DIM] = 0.43589
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _write_synthetic_wav(path: Path, freq_hz: float = 220.0, duration_s: float = 2.5, sr: int = 16000) -> list[float]:
    n_samples = int(sr * duration_s)
    samples = [int(12000 * math.sin(2.0 * math.pi * freq_hz * (i / sr)) + 4000 * math.cos(2.0 * math.pi * (freq_hz * 2.3) * (i / sr))) for i in range(n_samples)]
    raw_pcm = struct.pack(f"<{n_samples}h", *samples)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(raw_pcm)
    emb = extract_256d_acoustic_embedding(path, sr=sr, max_duration_sec=2.0)
    return [float(x) for x in emb.tolist()]


def test_wave1_stage0_preflight_infers_roster_and_boosts_co_attendance(tmp_path: Path) -> None:
    vp_db = tmp_path / "voiceprints.sqlite"
    store = VoiceprintStore(db_path=vp_db)

    vec_amit = _make_unit_vec(5)
    vec_stephan = _make_unit_vec(19)
    vec_unmatched = _make_unit_vec(120)

    store.enroll_voiceprint(
        speaker_name="Amit Ayer",
        embedding=vec_amit,
        project="project_beacon",
        duration_sec=18.0,
        clean_speech_sec=18.0,
        is_verified=True,
    )
    store.enroll_voiceprint(
        speaker_name="Cameron Mercer",
        embedding=vec_stephan,
        project="project_beacon",
        duration_sec=16.0,
        clean_speech_sec=16.0,
        is_verified=True,
    )
    store.enroll_voiceprint(
        speaker_name="Unrelated Speaker",
        embedding=vec_unmatched,
        project="other_proj",
        duration_sec=12.0,
        clean_speech_sec=12.0,
        is_verified=True,
    )

    mem_path = tmp_path / "speaker_memory.json"
    mem_payload = {
        "amit ayer": {
            "canonical_name": "Amit Ayer",
            "common_interlocutors": {"Cameron Mercer": 6},
        },
        "Cameron Mercer": {
            "canonical_name": "Cameron Mercer",
            "common_interlocutors": {"Amit Ayer": 6},
        },
    }
    mem_path.write_text(json.dumps(mem_payload), encoding="utf-8")

    inferred = run_acoustic_roster_preflight(
        sample_embeddings=[vec_amit, vec_stephan],
        project="project_beacon",
        voiceprint_db_path=vp_db,
        speaker_memory_path=mem_path,
    )
    names = [c["name"] for c in inferred]
    assert "Amit Ayer" in names
    assert "Cameron Mercer" in names
    assert "Unrelated Speaker" not in names

    by_name = {c["name"]: c for c in inferred}
    assert by_name["Cameron Mercer"]["co_attendance_boost"] >= 0.06
    assert by_name["Cameron Mercer"]["confidence"] > by_name["Cameron Mercer"]["acoustic_similarity"] or by_name["Cameron Mercer"]["confidence"] == 0.98


def test_wave1_wav_preflight_and_stage0_preloader_integration(tmp_path: Path) -> None:
    wav_path = tmp_path / "sample_call.wav"
    wav_emb = _write_synthetic_wav(wav_path, freq_hz=240.0)

    vp_db = tmp_path / "voiceprints_wav.sqlite"
    store = VoiceprintStore(db_path=vp_db)
    store.enroll_voiceprint(
        speaker_name="Clay Palumbo",
        embedding=wav_emb,
        project="project_beacon",
        duration_sec=20.0,
        clean_speech_sec=20.0,
        is_verified=True,
    )

    preload_res = preload_stage0_speaker_context(
        attendees=[],
        project="project_beacon",
        operator_name="Amit Ayer",
        voiceprint_db_path=vp_db,
        audio_path=wav_path,
    )
    assert "Clay Palumbo" in preload_res.roster_names
    assert "Amit Ayer" in preload_res.roster_names
    assert any(c["name"] == "Clay Palumbo" for c in preload_res.inferred_candidates)
    assert "Clay Palumbo" in preload_res.enrolled_centroids


def test_wave1_u0_and_attendee_gate_replace_unknown_fallback_and_preserve_hitl_gate(tmp_path: Path) -> None:
    vp_db = tmp_path / "vp_u0.sqlite"
    store = VoiceprintStore(db_path=vp_db)
    vec_a = _make_unit_vec(31)
    vec_b = _make_unit_vec(77)
    store.enroll_voiceprint("Amit Ayer", vec_a, project="lens", duration_sec=15.0, clean_speech_sec=15.0, is_verified=True)
    store.enroll_voiceprint("Alex Sanchez", vec_b, project="lens", duration_sec=15.0, clean_speech_sec=15.0, is_verified=True)

    meta = {
        "_from_audio_transcription": True,
        "is_audio": True,
        "project": "lens",
        "meeting_name": "LENS Sync",
        "date": "10/31/2026",
        "time": "09:00 AM",
        "preflight_sample_embeddings": [vec_a, vec_b],
        "voiceprint_db_path": str(vp_db),
    }

    U0IngestionBoundary._ensure_metadata_defaults(meta, "")
    cands = extract_candidate_attendees(meta, "")
    assert "Unknown Speaker(s)" not in cands
    assert set(cands) == {"Amit Ayer", "Alex Sanchez"}

    # Mandatory HITL confirmation must still halt when unconfirmed
    with pytest.raises(HITLRequiredError) as exc_info:
        verify_mandatory_attendee_confirmation(meta, "")

    err = exc_info.value
    assert err.hitl_type == "attendee_confirmation"
    assert "Amit Ayer" in str(err)
    assert "Alex Sanchez" in str(err)
    rec_opt = err.options[0]
    assert rec_opt["attendees"] == cands
    assert rec_opt["confidence"] >= 0.85
    assert len(rec_opt["inferred_acoustic_candidates"]) == 2

    # Verify MLX transcriber speaker bounds consume preflight candidates
    transcriber = LocalMLXAudioTranscriber(mock=True)
    expected, min_spk, max_spk = transcriber._compute_speaker_bounds(meta)
    assert expected == 2
    assert min_spk == 2
    assert max_spk == 2
