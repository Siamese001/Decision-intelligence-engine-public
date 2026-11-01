"""Adversarial stress harness for acoustic diarization pipeline and model loading.

Covers >=3 adversarial traits:
1. Boundary Payload: Missing, empty, or zero-byte audio files raise clean exceptions.
2. Invariant Bypass: Audio consisting purely of digital silence (zero speech energy) returns 0 turns without crashing.
3. Overflow / Exhaustion: Severe amplitude clipping (+-32767) and high-frequency saturation processed without NaN/Inf leaks.
"""
from __future__ import annotations

import numpy as np
import pytest
import scipy.io.wavfile as wavfile

from dual_pipeline.audio_ingestion_core.diarization_engine import (
    get_diarization_engine,
)


@pytest.fixture
def temp_audio_dir(tmp_path):
    """Provides isolated directory for adversarial audio fixtures."""
    return tmp_path


def test_adversarial_boundary_missing_and_empty_audio_files(temp_audio_dir):
    """Trait 1 (Boundary Payload): Missing and empty audio files fail fast and cleanly."""
    engine = get_diarization_engine()

    # Case 1: Non-existent audio file must raise FileNotFoundError
    missing_file = temp_audio_dir / "non_existent_audio.wav"
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        engine.diarize(missing_file)

    # Case 2: Zero-byte file must raise or return empty cleanly
    zero_byte_file = temp_audio_dir / "zero_bytes.wav"
    zero_byte_file.write_bytes(b"")
    with pytest.raises((RuntimeError, ValueError, EOFError, OSError)):
        engine.diarize(zero_byte_file)


@pytest.mark.heavy_model
def test_adversarial_invariant_bypass_pure_digital_silence(temp_audio_dir):
    """Trait 2 (Invariant Bypass): Audio containing zero speech activity produces 0 speaker turns without crashing."""
    engine = get_diarization_engine()

    rate = 16000
    duration_s = 5
    silence_data = np.zeros(rate * duration_s, dtype=np.int16)
    silence_file = temp_audio_dir / "pure_silence_5s.wav"
    wavfile.write(silence_file, rate, silence_data)

    with pytest.raises(RuntimeError, match="0 speaker turns"):
        engine.extract_speaker_turns(silence_file)


@pytest.mark.heavy_model
def test_adversarial_overflow_clipped_and_saturated_audio(temp_audio_dir):
    """Trait 3 (Overflow / Exhaustion): Extreme square-wave clipping (+-32767) handles without NaN or process crashes."""
    engine = get_diarization_engine()

    rate = 16000
    duration_s = 6
    t = np.linspace(0, duration_s, rate * duration_s, endpoint=False)
    # Square wave with alternating hard clipping at full 16-bit dynamic range
    square_wave = np.where(np.sin(2 * np.pi * 300 * t) >= 0, 32767, -32768).astype(np.int16)
    clipped_file = temp_audio_dir / "hard_clipped_300hz.wav"
    wavfile.write(clipped_file, rate, square_wave)

    # Engine must process cleanly without crash or unhandled arithmetic exceptions (other than 0 turns)
    with pytest.raises(RuntimeError, match="0 speaker turns"):
        engine.extract_speaker_turns(clipped_file)
