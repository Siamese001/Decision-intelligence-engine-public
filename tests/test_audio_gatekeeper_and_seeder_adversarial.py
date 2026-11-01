"""Adversarial edge-case tests for Wave 3 audio gatekeeper, master ASR seeder, and recovery."""
from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.canonical_pipeline.content_loader import _mint_standalone_audio_certificate
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import _rebuild_recovered_turns


def test_master_asr_seeder_does_not_seal_tier_a_cache_when_undiaraized(tmp_path: Path) -> None:
    """Boundary/invariant bypass: Live chunk segments without completed diarization must not seal Tier A cache."""
    seg_jsonl = tmp_path / "segments.jsonl"
    seg_jsonl.write_text(
        json.dumps({"start_sec": 0.0, "end_sec": 12.0, "text": "Live chunk preview text.", "speaker": "Voice A"}) + "\n",
        encoding="utf-8",
    )
    reg = LiveVoiceRegistry()
    reg.bind_speaker_hint("Voice A", "Amit")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    recorder = SimpleNamespace(
        segments_jsonl=seg_jsonl,
        output_dir=tmp_path,
        clean_slug="test_call",
        voice_registry=reg,
        transcript_cache_dir=cache_dir,
        processed_chunks=[0],
        whisper_model="mlx-community/whisper-large-v3-turbo",
        language="en",
        master_wav=tmp_path / "master.wav",
        mock_mode=False,
        _acoustic_diarization_complete=False,
    )
    receipt = SimpleNamespace(
        master_wav_sha256="a" * 64,
        total_chunks=1,
        duration_s=12.0,
    )

    ok = seed_master_asr_from_recorder(recorder, receipt)
    assert ok is True
    # Sealed .transcript_cache.json must NOT be created when _acoustic_diarization_complete is False
    assert list(cache_dir.glob("*.transcript_cache.json")) == []
    # Voiceprints sidecar with operator hints MUST be preserved for downstream batch diarization
    vp_files = list(cache_dir.glob("*.voiceprints.json"))
    assert len(vp_files) == 1
    vp_data = json.loads(vp_files[0].read_text(encoding="utf-8"))
    assert vp_data["matches"].get("Voice A") == "Amit"


def test_standalone_audio_certificate_blocks_rejected_envelope_and_never_forges_hitl(tmp_path: Path) -> None:
    """Invariant bypass attempt: Standalone audio must not forge hitl_approved=True or swallow REJECT when enforced."""
    wav_path = tmp_path / "silent_or_short.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)

    # Multi-attendee call with single speaker triggers collapse/warn or reject
    meta = {
        "attendees": ["Amit", "Speaker_Beta", "Speaker_Alpha"],
        "declared_duration_seconds": 120.0,
        "expected_duration_seconds": 120.0,
        "enforce_upstream_audio_qa": True,
        "hitl_approved": False,
    }
    with pytest.raises(ValueError, match="Upstream Audio QA Gatekeeper REJECTED"):
        _mint_standalone_audio_certificate(wav_path, "**Speaker 1** [00:00:01]: Short.", meta)


def test_rebuild_recovered_turns_preserves_speaker_labels_without_synthetic_voice_round_robin() -> None:
    """Exact production regression: _rebuild_recovered_turns must not overwrite speakers with Voice A..D."""
    recovered_text = (
        "**Amit** [00:00:01]: Let us review the ingestion architecture.\n\n"
        "**Speaker_Beta** [00:00:06]: Looks solid from the platform side.\n"
    )
    turns = _rebuild_recovered_turns(recovered_text)
    assert len(turns) == 2
    assert turns[0].speaker_cluster_ids == ("Amit",)
    assert turns[1].speaker_cluster_ids == ("Speaker_Beta",)
