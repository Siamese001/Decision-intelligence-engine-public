"""
tests/test_copilot_live_transcript_rca_wave3.py

Wave 3 unit and integration tests for Dual-Channel Voice Registry,
1:1 Counterparty Attribution, and Session Manifest Path Population.
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.acoustic_features import _read_wav_data
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry


def _write_stereo_wav(path: Path, ch0_amp: int, ch1_amp: int, sr: int = 16000, dur: float = 0.5) -> None:
    n_frames = int(sr * dur)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n_frames):
            v0 = ch0_amp if (i % 32 < 16) else -ch0_amp
            v1 = ch1_amp if (i % 32 < 16) else -ch1_amp
            frames.extend(struct.pack("<hh", v0, v1))
        wf.writeframes(frames)


def test_read_wav_data_selects_dominant_channel_when_ch0_silent(tmp_path: Path) -> None:
    wav_path = tmp_path / "ch0_silent.wav"
    _write_stereo_wav(wav_path, ch0_amp=0, ch1_amp=4000)
    data, sr = _read_wav_data(wav_path, max_duration_sec=1.0)
    assert data is not None
    assert sr == 16000
    assert float(np.max(np.abs(data))) == 1.0
    assert float(np.mean(np.abs(data))) > 0.95


def test_preseed_enrolled_voiceprints_populates_centroid(monkeypatch) -> None:
    vec = [1.0 if i == 0 else 0.0 for i in range(24)]

    class FakeStore:
        def get_voiceprint(self, name: str):
            if name == "Daniel Brankin":
                return {"embedding": vec}
            return None

    monkeypatch.setattr(
        "dual_pipeline.audio_ingestion_core.voiceprint_store.VoiceprintStore",
        lambda *a, **kw: FakeStore(),
    )
    reg = LiveVoiceRegistry(attendees=["Daniel Brankin", "Amit Ayer"])
    dan_clusters = [c for c in reg.voice_clusters.values() if c.resolved_speaker == "Daniel Brankin"]
    assert len(dan_clusters) == 1
    assert dan_clusters[0].centroid == vec


def test_single_mic_bleed_and_1on1_counterparty_resolution(tmp_path: Path) -> None:
    wav_bleed = tmp_path / "bleed.wav"
    _write_stereo_wav(wav_bleed, ch0_amp=0, ch1_amp=2500)

    reg = LiveVoiceRegistry(attendees=["Daniel Brankin", "Amit Ayer"], similarity_threshold=0.78)
    vec_host = np.zeros(24, dtype=np.float32)
    vec_host[0] = 1.0
    vec_remote = np.zeros(24, dtype=np.float32)
    vec_remote[1] = 1.0

    # Chunk 0: Host speaks into Ch1 (Ch0=0) -> initializes Voice Host
    spk0, vid0, _ = reg.resolve_chunk_speaker(
        wav_path=wav_bleed,
        chunk_idx=0,
        text="Welcome Daniel, let us review Jarvis.",
        start_sec=0.0,
        duration_sec=2.0,
        acoustic_vector=vec_host,
    )
    assert spk0 == "Host"
    assert vid0 == "Voice Host"

    # Chunk 1: Remote speaker picked up via Ch1 bleed (Ch0=0, orthogonal voice vector) -> separates & resolves to Daniel Brankin
    spk1, vid1, _ = reg.resolve_chunk_speaker(
        wav_path=wav_bleed,
        chunk_idx=1,
        text="SAP integration is on for tomorrow.",
        start_sec=2.0,
        duration_sec=2.0,
        acoustic_vector=vec_remote,
    )
    assert vid1 != "Voice Host"
    assert spk1 == "Daniel Brankin"


def test_remote_dominant_channel_resolves_1on1_counterparty(tmp_path: Path) -> None:
    wav_remote = tmp_path / "remote.wav"
    _write_stereo_wav(wav_remote, ch0_amp=3000, ch1_amp=20)

    reg = LiveVoiceRegistry(attendees=["Daniel Brankin", "Amit Ayer"], similarity_threshold=0.78)
    vec_remote = np.zeros(24, dtype=np.float32)
    vec_remote[2] = 1.0
    spk, vid, _ = reg.resolve_chunk_speaker(
        wav_path=wav_remote,
        chunk_idx=0,
        text="We are excited for this UI/UX thing.",
        start_sec=0.0,
        duration_sec=2.0,
        acoustic_vector=vec_remote,
    )
    assert vid.startswith("Voice ")
    assert vid != "Voice Host"
    assert spk == "Daniel Brankin"


def test_recorder_populates_session_manifest_paths_at_init(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "dual_pipeline.live_meeting_recorder.acquire_global_mlx_lock",
        lambda *a, **kw: None,
    )
    rec = LiveMeetingRecorder(
        project_name="project_jarvis",
        meeting_name="Daniel Brankin and Amit Ayer",
        attendees=["Daniel Brankin", "Amit Ayer"],
        output_dir=tmp_path / "out",
        dry_run=True,
        mock_mode=True,
        enforce_worktree=False,
    )
    assert rec.session_manifest_record.output_dir is not None
    assert rec.session_manifest_record.cache_dir is not None
    assert rec.session_manifest_record.segments_jsonl is not None
    assert rec.session_manifest_record.transcript_md is not None
    rec.session_registry.unregister(rec.session_id)
