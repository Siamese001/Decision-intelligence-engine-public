"""
Wave 2 Adversarial Test Suite: Acoustic Tap Planar Layout, RIFF Proof Boundaries, Scoped Contention Reaping, and Silent Watchdog.

Covers mandatory adversarial categories:
1. Malformed / non-standard input (RIFF header without 'data' subchunk, 78B LIST/INFO header with 0 PCM samples).
2. Boundary payload (0.0 RMS digital-zero chunks fed into _process_chunk_file triggering starvation alarm).
3. Invariant bypass attempt (foreign non-NEE FFmpeg process protected from contention reaper; mixed_loopback strategy dispatch).
4. Rollback & recovery (post-exit RIFF header repair on both master_wav and chunk_*.wav).
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
    find_wav_data_offset,
)
from dual_pipeline.live_meeting_recorder_core.chunk_health import ChunkHealth
from dual_pipeline.live_meeting_recorder_core.process_cleanup import _find_foreign_pid
from pipeline_a_intake.mac_system_audio import AudioDeviceInfo, AudioDeviceType


def test_riff_header_without_data_chunk_fails_closed() -> None:
    """Adversarial malformed input: RIFF header containing only fmt + LIST/INFO ('Lavf') and no 'data' chunk."""
    fmt_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 16000, 16000 * 4, 4, 16)
    # 500 bytes of non-zero ASCII metadata ('Lavf' repeated) without any 'data' chunk
    list_payload = b"INFOISFT" + struct.pack("<I", 480) + (b"Lavf60.16.100!!" * 32)
    list_chunk = b"LIST" + struct.pack("<I", len(list_payload)) + list_payload
    riff_body = b"WAVE" + fmt_chunk + list_chunk
    header_only_wav = b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body

    # Must return len(header_only_wav) so zero metadata bytes are mistaken for PCM samples
    assert find_wav_data_offset(header_only_wav) == len(header_only_wav)

    with pytest.raises(AudioCaptureUnprovenError) as exc_info:
        AudioCaptureProofVerifier.verify_mic_non_digital_zero(
            pcm_bytes=header_only_wav,
            is_stereo=True,
            mock_mode=False,
        )
    assert exc_info.value.reason_code == AudioReasonCode.PCM_BYTE_RATE_DEFICIT


def test_mixed_loopback_strategy_dispatches_to_mixed_loopback_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies strategy == 'mixed_loopback' calls _start_mixed_loopback_capture instead of falling through to mic."""
    cm = LiveAudioCaptureManager(
        chunks_dir=tmp_path / "chunks",
        master_wav=tmp_path / "master.wav",
        mock_mode=True,
    )
    called_with = []
    monkeypatch.setattr(cm, "_start_mixed_loopback_capture", lambda dev: called_with.append(("mixed_loopback", dev)))
    monkeypatch.setattr(cm, "_start_microphone_capture", lambda dev: called_with.append(("microphone", dev)))

    loop_dev = AudioDeviceInfo(
        device_id="2",
        name="BlackHole 2ch",
        device_type=AudioDeviceType.LOOPBACK,
        avfoundation_index=2,
    )
    cm._dispatch_strategy("mixed_loopback", loop_dev)
    assert called_with == [("mixed_loopback", loop_dev)]


def test_contention_reaper_protects_unrelated_user_ffmpeg_jobs() -> None:
    """Verifies _find_foreign_pid marks unrelated user FFmpeg jobs as nee_owned=False while marking NEE jobs True."""
    user_line = "99123 /opt/homebrew/bin/ffmpeg -f avfoundation -i :0 /Users/operator/Movies/family_video.mp4"
    pid_user, owned_user = _find_foreign_pid(user_line, my_pid=1000, device_index=":0")
    assert pid_user == 99123
    assert owned_user is False

    nee_line = (
        "99124 /opt/homebrew/bin/ffmpeg -f avfoundation -i :0 "
        "/Users/operator/Git/repo/output/proj/10-29-2026/sync/.cache/chunks/chunk_%05d.wav"
    )
    pid_nee, owned_nee = _find_foreign_pid(nee_line, my_pid=1000, device_index=":0")
    assert pid_nee == 99124
    assert owned_nee is True


def test_digital_zero_chunks_trigger_starvation_alarm_before_early_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies digital-zero chunks in non-mock evaluation feed the starvation detector and emit AUDIO_STARVATION_ALARM."""
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(lock_dir))

    rec = LiveMeetingRecorder(
        project_name="StarvationTest",
        meeting_name="Silent Mic Check",
        output_dir=tmp_path / "out",
        mock_mode=True,
        isolated_process=False,
        enforce_worktree=False,
        segment_seconds=6,
    )
    try:
        # Switch mock_mode off for chunk evaluation so digital-zero chunks have health.is_valid == False
        rec.mock_mode = False
        ChunkHealth.clear_cache()

        for idx in (0, 1, 2):
            c_path = rec.chunks_dir / f"chunk_{idx:05d}.wav"
            with wave.open(str(c_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * (16000 * 6))
            rec._process_chunk_file(c_path)

        assert hasattr(rec, "starvation_alarms")
        assert len(rec.starvation_alarms) >= 1
        assert rec.starvation_alarms[0]["event"] == "AUDIO_STARVATION_ALARM"
    finally:
        rec._release_slug_lock()


def test_finalize_riff_repairs_both_master_and_chunk_wavs(tmp_path: Path) -> None:
    """Verifies _finalize_riff repairs unfinalized 0xFFFFFFFF RIFF size headers on both master and chunk WAVs."""
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    master_wav = tmp_path / "master.wav"

    def _write_unfinalized_ffmpeg_wav(path: Path, pcm_bytes: bytes) -> None:
        fmt_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        data_hdr = b"data" + struct.pack("<I", 0xFFFFFFFF)
        raw = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" + fmt_chunk + data_hdr + pcm_bytes
        path.write_bytes(raw)

    pcm_payload = struct.pack("<800h", *([1200, -1200] * 400))
    _write_unfinalized_ffmpeg_wav(master_wav, pcm_payload)
    chunk0 = chunks_dir / "chunk_00000.wav"
    _write_unfinalized_ffmpeg_wav(chunk0, pcm_payload)

    cm = LiveAudioCaptureManager(chunks_dir=chunks_dir, master_wav=master_wav, mock_mode=True)
    cm._finalize_riff()

    for wav_p in (master_wav, chunk0):
        raw_after = wav_p.read_bytes()
        riff_size = struct.unpack("<I", raw_after[4:8])[0]
        data_size = struct.unpack("<I", raw_after[40:44])[0]
        assert riff_size == len(raw_after) - 8
        assert data_size == len(pcm_payload)


def test_swift_tap_source_implements_planar_and_antialias_fir() -> None:
    """Verifies mac_audio_tap.swift contains planar non-interleaved handling, anti-alias FIR filter, and remainder carry."""
    swift_path = Path(__file__).resolve().parent.parent / "tools" / "mac_audio_tap" / "mac_audio_tap.swift"
    src = swift_path.read_text(encoding="utf-8")
    assert "kAudioFormatFlagIsNonInterleaved" in src
    assert "antiAliasFilter48to16" in src
    assert "remainderMono" in src
    assert "realAudioSamplesEmitted" in src
    assert "heartbeatSilenceSamplesEmitted" in src
