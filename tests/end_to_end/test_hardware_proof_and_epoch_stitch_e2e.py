"""
Wave 2 E2E Test: RIFF-Parsed Dual-Channel Proof, Route-Change Multi-Epoch Stitching, and Gate F Wall-Clock Anchoring.

Verifies:
1. Dynamic RIFF 'data' chunk offset parsing handles 78-byte FFmpeg WAV headers (with 34-byte LIST/INFO)
   without swapping Ch0 (SCK) and Ch1 (Mic) or mistaking 'Lavf' metadata for microphone speech.
2. CoreAudio route-change epoch transitions (chunk_00000.wav -> chunk_e1_00000.wav) stitch into a
   single continuous master WAV without overwriting prior chunks.
3. UpstreamAudioQAGatekeeper Gate F anchors expected_duration_s to wall_clock_duration_seconds from
   session metadata instead of tautologically equating expected_duration_s = actual_duration_s.
"""
from __future__ import annotations

import hashlib
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager
from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
    find_wav_data_offset,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


def _build_78byte_ffmpeg_stereo_wav(ch0_samples: list[int], ch1_samples: list[int]) -> bytes:
    """Constructs a 78-byte header WAV matching FFmpeg's 34-byte LIST/INFO ('ISFT Lavf60.16.100') layout."""
    assert len(ch0_samples) == len(ch1_samples)
    interleaved = []
    for s0, s1 in zip(ch0_samples, ch1_samples):
        interleaved.append(s0)
        interleaved.append(s1)
    pcm_data = struct.pack(f"<{len(interleaved)}h", *interleaved)

    fmt_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 16000, 16000 * 4, 4, 16)
    # 26-byte payload + 8-byte header = 34-byte LIST chunk -> 12 + 24 + 34 + 8 = 78 bytes before PCM data!
    list_payload = b"INFOISFT" + struct.pack("<I", 14) + b"Lavf60.16.100\x00"
    assert len(list_payload) == 26
    list_chunk = b"LIST" + struct.pack("<I", len(list_payload)) + list_payload
    data_header = b"data" + struct.pack("<I", len(pcm_data))

    riff_body = b"WAVE" + fmt_chunk + list_chunk + data_header + pcm_data
    wav_bytes = b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body
    assert find_wav_data_offset(wav_bytes) == 78
    return wav_bytes


def _write_pcm16_chunk(path: Path, freq_hz: float, duration_sec: float = 1.0, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    samples = (np.sin(2 * np.pi * freq_hz * t) * 10000).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def test_78byte_ffmpeg_wav_header_parses_exact_channel_polarity() -> None:
    """Verifies 78-byte FFmpeg WAV header does not swap Ch0 (SCK) and Ch1 (Mic)."""
    n_frames = 400
    # Case 1: Active Ch0 (SCK = 8000/-8000), Dead Ch1 (Mic = 0) -> MUST FAIL CLOSED with MIC_DIGITAL_ZERO!
    ch0_active = [8000 if (i % 2 == 0) else -8000 for i in range(n_frames)]
    ch1_dead = [0] * n_frames
    dead_mic_wav = _build_78byte_ffmpeg_stereo_wav(ch0_active, ch1_dead)

    with pytest.raises(AudioCaptureUnprovenError) as exc_info:
        AudioCaptureProofVerifier.verify_mic_non_digital_zero(
            pcm_bytes=dead_mic_wav,
            is_stereo=True,
            mock_mode=False,
        )
    assert exc_info.value.reason_code == AudioReasonCode.MIC_DIGITAL_ZERO

    # Case 2: Dead Ch0 (SCK = 0), Active Ch1 (Mic = 6000/-6000) -> MUST PASS!
    ch0_silent = [0] * n_frames
    ch1_active = [6000 if (i % 2 == 0) else -6000 for i in range(n_frames)]
    live_mic_wav = _build_78byte_ffmpeg_stereo_wav(ch0_silent, ch1_active)

    res = AudioCaptureProofVerifier.verify_mic_non_digital_zero(
        pcm_bytes=live_mic_wav,
        is_stereo=True,
        mock_mode=False,
    )
    assert res["proven"] is True
    assert res["header_data_offset"] == 78
    assert res["mic_rms"] >= 5900.0
    assert res["sck_rms"] == 0.0


def test_multi_epoch_route_change_chunks_stitch_without_overwrite(tmp_path: Path) -> None:
    """Verifies chunk_00000..00001 + chunk_e1_00000..00001 stitch into a continuous 4-second master WAV."""
    chunks_dir = tmp_path / "chunks"
    master_wav = tmp_path / "master.wav"

    cm = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        mock_mode=True,
        segment_seconds=1,
    )
    assert Path(cm._get_chunk_pattern()).name == "chunk_%05d.wav"
    _write_pcm16_chunk(chunks_dir / "chunk_00000.wav", freq_hz=300.0, duration_sec=1.0)
    _write_pcm16_chunk(chunks_dir / "chunk_00001.wav", freq_hz=400.0, duration_sec=1.0)

    # Simulate CoreAudio route change advancing to epoch 1
    new_epoch = cm.advance_capture_epoch()
    assert new_epoch == 1
    assert Path(cm._get_chunk_pattern()).name == "chunk_e1_%05d.wav"
    _write_pcm16_chunk(chunks_dir / "chunk_e1_00000.wav", freq_hz=500.0, duration_sec=1.0)
    _write_pcm16_chunk(chunks_dir / "chunk_e1_00001.wav", freq_hz=600.0, duration_sec=1.0)

    receipt = AudioMasterStitcher.stitch_master_wav(
        chunks_dir=chunks_dir,
        target_master_wav=master_wav,
        min_bytes=1000,
        pad_missing_or_corrupt=True,
    )
    assert receipt.success is True
    assert receipt.valid_chunks == 4
    assert receipt.total_chunks == 4
    assert abs(receipt.duration_s - 4.0) < 0.05


def test_gate_f_anchors_expected_duration_to_wall_clock_metadata() -> None:
    """Verifies Gate F fails closed when actual audio duration is truncated vs wall_clock_duration_seconds."""
    span = SampleSpan(start_sample=0, end_sample=16000 * 10)
    turn = ForensicTurnSignal(
        turn_id="t1",
        span=span,
        transcript_verbatim="We are reviewing the architecture.",
        identity_state="human_confirmed",
        roster_person_id="p_alice",
        identity_evidence_id="ev_alice",
    )
    sig_sha = hashlib.sha256(b"test_audio_payload").hexdigest()
    envelope = ForensicSignalEnvelope(
        recording_id="rec_gate_f_test",
        source_hashes=(sig_sha,),
        canonical_sample_rate_hz=16000,
        timeline_length_samples=16000 * 10,  # Only 10 seconds of audio captured
        turns=(turn,),
        signal_digest=sig_sha,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    # Pass metadata indicating the live session ran for 60 seconds wall-clock, but only 10s of audio survived (16.7%)
    report = gatekeeper.evaluate(
        envelope,
        expected_duration_s=None,
        metadata={"wall_clock_duration_seconds": 60.0},
    )
    assert report.verdict == "REJECT"
    gate_f_codes = [f.code for f in report.findings if f.gate_id == "Gate_F_Audio_Completeness"]
    assert "AUDIO_DURATION_INCOMPLETE" in gate_f_codes
