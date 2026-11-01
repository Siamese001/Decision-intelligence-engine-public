"""
tests/end_to_end/test_cross_worktree_voiceprint_learning_e2e.py

Wave 4 E2E tests:
- Canonical cross-worktree voiceprint_memory.sqlite resolution (<primary_root>/.local/voiceprint_memory.sqlite)
  across main, .worktrees/<slug>, and external git worktrees with zero output/ writes.
- Session 1 (Worktree A, Project Alpha): 48 kHz recording + signed Ed25519 HITL approval -> 256-D enrollment
  and SpeakerPriorsRegistry update.
- Session 2 (Worktree B, Project Beta): Immediate recognition of Session 1 voices at start recording
  (LiveVoiceRegistry) and batch VoiceprintStore matching (D-REC-02), with immutable 256-D centroids.
"""
from __future__ import annotations

import math
import sqlite3
import struct
import wave
from pathlib import Path

import numpy as np

from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationEngine
from dual_pipeline.audio_ingestion_core.voiceprint_store import (
    VoiceprintStore,
    resolve_canonical_voiceprint_db_path,
)
from dual_pipeline.hitl_governance_core.crypto import (
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.speaker_attribution_core.voiceprint_auto_enrollment import (
    auto_enroll_voiceprints,
)
from dual_pipeline.transcript_metadata_core.speaker_priors_registry import (
    load_learned_speaker_priors,
)


def _write_48khz_tone_wav(path: Path, freq_hz: float, duration_sec: float = 2.0, sr: int = 48000) -> None:
    n_samples = int(duration_sec * sr)
    buf = bytearray()
    for i in range(n_samples):
        t = i / float(sr)
        sample = int(14000.0 * (math.sin(2.0 * math.pi * freq_hz * t) + 0.35 * math.sin(2.0 * math.pi * (freq_hz * 2.1) * t)))
        buf.extend(struct.pack("<h", max(-32768, min(32767, sample))))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


def test_canonical_db_path_unifies_main_internal_and_external_worktrees(tmp_path: Path, monkeypatch) -> None:
    """Verifies main, .worktrees/<slug>, and external git worktrees resolve to <primary_root>/.local/voiceprint_memory.sqlite."""
    monkeypatch.delenv("NEE_VOICEPRINT_DB_PATH", raising=False)
    primary_root = tmp_path / "repo_main"
    (primary_root / ".git" / "worktrees" / "ext_wt").mkdir(parents=True)
    internal_wt = primary_root / ".worktrees" / "feat_alpha"
    internal_wt.mkdir(parents=True)

    external_wt = tmp_path / "external_worktrees" / "ext_wt"
    external_wt.mkdir(parents=True)
    (external_wt / ".git").write_text(
        f"gitdir: {primary_root / '.git' / 'worktrees' / 'ext_wt'}\n",
        encoding="utf-8",
    )

    p_main = resolve_canonical_voiceprint_db_path(base_file=primary_root / "dual_pipeline" / "audio" / "vp.py")
    p_int = resolve_canonical_voiceprint_db_path(base_file=internal_wt / "dual_pipeline" / "audio" / "vp.py")
    p_ext = resolve_canonical_voiceprint_db_path(base_file=external_wt / "dual_pipeline" / "audio" / "vp.py")

    expected = primary_root / ".local" / "voiceprint_memory.sqlite"
    assert p_main == expected
    assert p_int == expected
    assert p_ext == expected
    assert "output" not in expected.parts
    assert not (primary_root / "output").exists()


def test_cross_worktree_and_cross_project_voiceprint_closed_loop_e2e(tmp_path: Path, monkeypatch) -> None:
    """Verifies Session 1 HITL enrollment in Worktree A is recognized in Worktree B across projects."""
    primary_root = tmp_path / "primary_repo"
    (primary_root / ".git").mkdir(parents=True)
    wt_a = primary_root / ".worktrees" / "feat_session_one"
    wt_b = primary_root / ".worktrees" / "feat_session_two"
    wt_a.mkdir(parents=True)
    wt_b.mkdir(parents=True)

    canonical_db = resolve_canonical_voiceprint_db_path(base_file=wt_a / "dual_pipeline" / "audio" / "vp.py")
    monkeypatch.setenv("NEE_VOICEPRINT_DB_PATH", str(canonical_db))

    wav_s1 = wt_a / "s1_48khz.wav"
    _write_48khz_tone_wav(wav_s1, freq_hz=260.0, duration_sec=2.0, sr=48000)

    engine = DiarizationEngine(mock=True)
    emb_elena = engine.extract_turn_embedding(wav_s1, 0.0, 2.0)
    assert len(emb_elena) == 256

    priv_key, pub_key = generate_hitl_keypair()
    signed_receipt = create_signed_hitl_approval_entry(
        subject_sha256="a" * 64,
        case_ids=["case-elena-01"],
        decision="APPROVE",
        operator_id="operator_test",
        private_key=priv_key,
        public_key=pub_key,
    )

    priors_file = tmp_path / "learned_speaker_priors.json"
    meta_s1 = {
        "project": "PROJECT_ALPHA",
        "date_filename": "2026-10-29_Alpha_Sync",
        "audio_sha256": "sha_session_1_48k",
        "acoustic_clusters": {"SPEAKER_00": {"centroid": emb_elena}},
        "speaker_attribution_receipt": signed_receipt,
        "speaker_priors_path": str(priors_file),
        "structured_segments": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 18.0}],
    }
    enrolled = auto_enroll_voiceprints(meta_s1, {"SPEAKER_00": "Elena Rostova"})
    assert len(enrolled) == 1
    assert enrolled[0]["speaker"] == "Elena Rostova"

    # Verify SpeakerPriorsRegistry updated
    priors_data = load_learned_speaker_priors(priors_file)
    assert priors_data["speaker_aliases"].get("SPEAKER_00") == "Elena Rostova"

    # Independent read-only SQLite verification on canonical_db
    conn = sqlite3.connect(f"file:{canonical_db}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        assert integrity == "ok"
        row = conn.execute(
            "SELECT canonical_name, project, dimension, is_verified FROM speaker_voiceprints;"
        ).fetchone()
        assert row == ("Elena Rostova", "PROJECT_ALPHA", 256, 1)
    finally:
        conn.close()

    # Session 2 in Worktree B under PROJECT_BETA (cross-project recognition per D-REC-02)
    db_from_wt_b = resolve_canonical_voiceprint_db_path(base_file=wt_b / "dual_pipeline" / "audio" / "vp.py")
    assert db_from_wt_b == canonical_db
    store_b = VoiceprintStore(db_path=db_from_wt_b)

    roster_matches = store_b.match_roster(
        {"SPEAKER_01": emb_elena},
        roster_names=["Elena Rostova", "Marcus Vance"],
        project="PROJECT_BETA",
    )
    assert "SPEAKER_01" in roster_matches
    assert roster_matches["SPEAKER_01"]["canonical_name"] == "Elena Rostova"
    assert roster_matches["SPEAKER_01"]["is_decisive"] is True

    # Start recording in Session 2: LiveVoiceRegistry pre-seeds 256-D centroid and keeps it immutable
    live_reg = LiveVoiceRegistry(attendees=["Elena Rostova", "Marcus Vance"])
    elena_cluster = next(c for c in live_reg.voice_clusters.values() if c.resolved_speaker == "Elena Rostova")
    assert elena_cluster.is_pre_enrolled is True
    assert len(elena_cluster.centroid) == 256
    orig_centroid = list(elena_cluster.centroid)

    spk_name, _, conf = live_reg.resolve_chunk_speaker(
        wav_s1,
        chunk_idx=0,
        text="Checking cross-worktree voiceprint recall.",
        acoustic_vector=np.asarray(emb_elena, dtype=np.float32),
        strict_256d=True,
    )
    assert spk_name == "Elena Rostova"
    assert conf >= 0.95
    assert elena_cluster.centroid == orig_centroid
    assert not (primary_root / "output").exists()
