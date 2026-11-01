"""
tests/test_media_extractor.py

Unit and integration tests for pipeline_a_intake/media_extractor.py:
1. Validates FFmpeg executable resolution.
2. Validates SHA-256 media fingerprinting.
3. Tests audio normalization (16kHz, mono, 16-bit PCM, 80Hz rumble filter).
4. Tests content-addressed caching across repeated calls.
5. Tests error handling on missing files.
6. Tests media property probing on synthetic media.
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from pipeline_a_intake.media_extractor import (
    compute_media_file_hash,
    ensure_ffmpeg,
    extract_normalized_audio,
    probe_media_properties,
)


def _create_synthetic_wav(
    path: Path,
    sample_rate: int = 44100,
    channels: int = 2,
    duration_sec: float = 1.0,
    amplitude: int = 4000,
) -> None:
    """Helper to synthesize test WAV with custom sample rate and channels."""
    n_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            val = amplitude if (i % 32 < 16) else -amplitude
            for _ in range(channels):
                frames.extend(struct.pack("<h", val))
        w.writeframes(frames)


class TestMediaExtractor:
    @pytest.fixture(autouse=True)
    def setup_tmp(self, tmp_path):
        self.tmp_path = tmp_path
        self.cache_dir = tmp_path / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def test_ensure_ffmpeg(self):
        exe = ensure_ffmpeg()
        assert exe is not None
        assert len(exe) > 0

    def test_compute_media_file_hash(self):
        test_file = self.tmp_path / "sample.bin"
        test_file.write_bytes(b"deterministic media content 12345")
        hash1 = compute_media_file_hash(test_file)
        assert len(hash1) == 64
        # Deterministic check
        hash2 = compute_media_file_hash(test_file)
        assert hash1 == hash2

    def test_extract_normalized_audio_from_stereo_44k(self):
        raw_wav = self.tmp_path / "test_stereo_44k.wav"
        _create_synthetic_wav(raw_wav, sample_rate=44100, channels=2, duration_sec=1.5)

        out_wav = extract_normalized_audio(
            media_path=raw_wav,
            output_dir=self.cache_dir,
            target_sr=16000,
            highpass_hz=80,
        )

        assert out_wav.is_file()
        assert out_wav.suffix == ".wav"

        # Inspect normalized output
        with wave.open(str(out_wav), "rb") as w:
            assert w.getframerate() == 16000
            assert w.getnchannels() == 1  # Downmixed to mono
            assert w.getsampwidth() == 2  # 16-bit PCM
            n_frames = w.getnframes()
            dur = n_frames / 16000.0
            assert 1.3 <= dur <= 1.7

    def test_extract_normalized_audio_caching(self):
        raw_wav = self.tmp_path / "cached_source.wav"
        _create_synthetic_wav(raw_wav, sample_rate=22050, channels=1, duration_sec=1.0)

        # First extraction
        out1 = extract_normalized_audio(
            media_path=raw_wav,
            output_dir=self.cache_dir,
        )
        mtime1 = out1.stat().st_mtime_ns

        # Second extraction (should return cached asset without re-generating)
        out2 = extract_normalized_audio(
            media_path=raw_wav,
            output_dir=self.cache_dir,
            force_reextract=False,
        )
        assert out1 == out2
        assert out2.stat().st_mtime_ns == mtime1

    def test_probe_media_properties(self):
        raw_wav = self.tmp_path / "probe_target.wav"
        _create_synthetic_wav(raw_wav, sample_rate=48000, channels=2, duration_sec=2.0)

        props = probe_media_properties(raw_wav)
        assert props["channels"] == 2
        assert props["sample_rate"] == 48000
        assert 1.8 <= props["duration_seconds"] <= 2.2
        assert props["has_video"] is False
        assert props["extension"] == ".wav"

    def test_missing_file_raises_not_found(self):
        with pytest.raises(FileNotFoundError):
            extract_normalized_audio(self.tmp_path / "nonexistent.mp4")

        with pytest.raises(FileNotFoundError):
            probe_media_properties(self.tmp_path / "nonexistent.wav")
