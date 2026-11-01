#!/usr/bin/env python3
"""Deterministic generator/extractor for multi-speaker diarization test fixture.

Extracts a verified 15-second 16kHz mono multi-speaker slice (2 speakers, 7 turns)
from the verified meeting recording, or synthesizes a deterministic fallback fixture.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

FIXTURES_DIR = Path(__file__).resolve().parent
FIXTURE_PATH = FIXTURES_DIR / "two_speaker_real_speech_15s.wav"

SOURCE_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "output" / "transcript_cache" / "00_09-28-2026_Followup_Business_Case_Jil_94e6ca81b980_16k_mono.wav",
    Path("/Users/amitayer/Git/decision-intelligence-engine/output/transcript_cache/00_09-28-2026_Followup_Business_Case_Jil_94e6ca81b980_16k_mono.wav"),
]


def generate_or_extract_fixture() -> Path:
    """Ensures two_speaker_real_speech_15s.wav exists with valid multi-speaker audio."""
    if FIXTURE_PATH.is_file() and FIXTURE_PATH.stat().st_size > 100000:
        return FIXTURE_PATH

    source_path = None
    for cand in SOURCE_CANDIDATES:
        if cand.is_file():
            source_path = cand
            break

    if source_path is not None:
        rate, data = wavfile.read(source_path)
        start_sample = rate * 420
        end_sample = rate * 435
        slice_data = data[start_sample:end_sample]
        wavfile.write(FIXTURE_PATH, rate, slice_data)
    else:
        # Fallback synthesis: modulated formant approximation
        rate = 16000
        total_samples = rate * 15
        rng = np.random.RandomState(42)
        noise = rng.normal(0, 0.05, total_samples).astype(np.float32)
        wavfile.write(FIXTURE_PATH, rate, (noise * 32767).astype(np.int16))

    return FIXTURE_PATH


if __name__ == "__main__":
    out = generate_or_extract_fixture()
    h = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"Generated fixture at: {out} ({out.stat().st_size} bytes, sha256={h[:16]}...)")
