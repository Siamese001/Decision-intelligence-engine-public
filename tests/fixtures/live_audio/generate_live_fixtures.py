#!/usr/bin/env python3
"""
tests/fixtures/live_audio/generate_live_fixtures.py

Deterministic generator for live audio benchmarking and characterization fixtures.
Generates:
1. two_speaker_meeting.wav (+ .txt, + .rttm)
2. three_speaker_meeting.wav (+ .txt, + .rttm)
3. silent_invitee_meeting.wav (+ .txt, + .rttm)
4. adversarial_phrases_meeting.wav (+ .txt, + .rttm)
5. mid_word_boundary_meeting.wav (+ .txt, + .rttm)
6. long_silence_meeting.wav (+ .txt, + .rttm)

Uses macOS `say` when available, with a deterministic numpy fallback for portable CI.
All WAVs are standard 16kHz mono 16-bit signed PCM.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import numpy as np
import scipy.io.wavfile as wavfile

FIXTURES_DIR = Path(__file__).resolve().parent
SAMPLE_RATE = 16000


def _generate_synthetic_tone_speech(duration_sec: float, pitch_hz: float, seed: int = 42) -> np.ndarray:
    """Fallback speech-like modulated harmonic waveform."""
    t = np.linspace(0, duration_sec, int(SAMPLE_RATE * duration_sec), endpoint=False)
    rng = np.random.RandomState(seed)
    # Formants
    f1 = np.sin(2 * np.pi * pitch_hz * t)
    f2 = 0.5 * np.sin(2 * np.pi * (pitch_hz * 2.1) * t)
    f3 = 0.25 * np.sin(2 * np.pi * (pitch_hz * 3.2) * t)
    # Modulation (syllabic envelope ~4Hz)
    env = 0.5 * (1.0 + np.sin(2 * np.pi * 4.0 * t))
    signal = (f1 + f2 + f3) * env
    noise = rng.normal(0, 0.02, len(t))
    audio = signal + noise
    max_val = np.max(np.abs(audio)) or 1.0
    scaled = (audio / max_val * 24000).astype(np.int16)
    return scaled


def _render_utterance(text: str, voice: str, seed: int = 42) -> np.ndarray:
    """Renders text to 16kHz mono int16 audio using `say` if available, else synthetic waveform."""
    if shutil.which("say"):
        tmp_wav = FIXTURES_DIR / f"_tmp_{os.getpid()}_{seed}.wav"
        try:
            cmd = ["say", "-v", voice, "-o", str(tmp_wav), "--data-format=LEI16@16000", text]
            subprocess.run(cmd, check=True, capture_output=True)
            if tmp_wav.is_file() and tmp_wav.stat().st_size > 44:
                rate, data = wavfile.read(tmp_wav)
                if data.ndim > 1:
                    data = data[:, 0]
                if rate != SAMPLE_RATE:
                    # Basic down/up-sampling
                    num_samples = int(len(data) * SAMPLE_RATE / rate)
                    indices = np.linspace(0, len(data) - 1, num_samples).astype(int)
                    data = data[indices]
                return data.astype(np.int16)
        except Exception:
            pass
        finally:
            if tmp_wav.exists():
                tmp_wav.unlink(missing_ok=True)

    # Fallback if say is unavailable or fails
    duration = max(1.5, len(text.split()) * 0.35)
    pitch_map = {"Samantha": 220.0, "Daniel": 140.0, "Fred": 110.0}
    pitch = pitch_map.get(voice, 150.0)
    return _generate_synthetic_tone_speech(duration, pitch, seed=seed)


def _build_meeting(
    turns: List[Tuple[str, str, str]],  # (speaker_name, voice_name, text)
    pauses_sec: List[float],
    target_duration_sec: float = 30.0,
) -> Tuple[np.ndarray, str, str]:
    """
    Assembles turns into a continuous audio stream, generating reference text and RTTM.
    RTTM format: SPEAKER <file_id> 1 <start_sec> <dur_sec> <NA> <NA> <speaker_name> <NA> <NA>
    """
    total_audio = []
    current_sec = 0.0
    ref_lines = []
    rttm_lines = []

    for idx, (spk, voice, text) in enumerate(turns):
        pause = pauses_sec[idx] if idx < len(pauses_sec) else 0.5
        if pause > 0:
            silence_samples = int(pause * SAMPLE_RATE)
            total_audio.append(np.zeros(silence_samples, dtype=np.int16))
            current_sec += pause

        turn_audio = _render_utterance(text, voice, seed=idx + 100)
        turn_dur = len(turn_audio) / SAMPLE_RATE

        start_time_str = f"[{int(current_sec // 60):02d}:{int(current_sec % 60):02d}]"
        ref_lines.append(f"{start_time_str} {spk}: {text}")
        rttm_lines.append(
            f"SPEAKER meeting 1 {current_sec:.3f} {turn_dur:.3f} <NA> <NA> {spk} <NA> <NA>"
        )

        total_audio.append(turn_audio)
        current_sec += turn_dur

    concatenated = np.concatenate(total_audio) if total_audio else np.zeros(0, dtype=np.int16)
    # Pad to target duration if shorter
    target_samples = int(target_duration_sec * SAMPLE_RATE)
    if len(concatenated) < target_samples:
        padding = np.zeros(target_samples - len(concatenated), dtype=np.int16)
        concatenated = np.concatenate([concatenated, padding])

    return concatenated, "\n".join(ref_lines) + "\n", "\n".join(rttm_lines) + "\n"


def generate_all_fixtures() -> dict[str, Path]:
    """Generates all baseline and adversarial fixtures."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    created = {}

    # 1. Two-Speaker Meeting (~30s)
    turns_2spk = [
        ("Samantha", "Samantha", "Good morning everyone. Let us review the sprint deliverables for Project Nexus."),
        ("Daniel", "Daniel", "Good morning Samantha. The streaming architecture and graph index models are ready for review."),
        ("Samantha", "Samantha", "Excellent. Let us verify the latency metrics and release gating criteria."),
        ("Daniel", "Daniel", "Agreed. The automated test suite completed with zero regression warnings."),
    ]
    audio, ref, rttm = _build_meeting(turns_2spk, [0.5, 0.8, 0.6, 0.7], target_duration_sec=30.0)
    p_wav = FIXTURES_DIR / "two_speaker_meeting.wav"
    wavfile.write(p_wav, SAMPLE_RATE, audio)
    (FIXTURES_DIR / "two_speaker_meeting.txt").write_text(ref, encoding="utf-8")
    (FIXTURES_DIR / "two_speaker_meeting.rttm").write_text(rttm, encoding="utf-8")
    created["two_speaker"] = p_wav

    # 2. Three-Speaker Meeting (~45s)
    turns_3spk = [
        ("Samantha", "Samantha", "Welcome to the technical alignment call. We have Daniel and Fred on the line."),
        ("Daniel", "Daniel", "Thanks Samantha. I have finalized the core audio capture pipeline updates."),
        ("Fred", "Fred", "And on the infrastructure side, the isolated test runner and memory gates are qualified."),
        ("Samantha", "Samantha", "Great progress. Daniel, how does the CoreAudio thread handle Bluetooth dropouts?"),
        ("Daniel", "Daniel", "The watchdog intercepts stalls within two hundred milliseconds and triggers fallback."),
        ("Fred", "Fred", "That matches our deployment specification. We are ready to proceed."),
    ]
    audio, ref, rttm = _build_meeting(turns_3spk, [0.5, 0.6, 0.7, 0.5, 0.6, 0.8], target_duration_sec=45.0)
    p_wav = FIXTURES_DIR / "three_speaker_meeting.wav"
    wavfile.write(p_wav, SAMPLE_RATE, audio)
    (FIXTURES_DIR / "three_speaker_meeting.txt").write_text(ref, encoding="utf-8")
    (FIXTURES_DIR / "three_speaker_meeting.rttm").write_text(rttm, encoding="utf-8")
    created["three_speaker"] = p_wav

    # 3. Silent Invitee Meeting (~30s, roster has Samantha, Daniel, Fred, but Fred is silent)
    turns_silent = [
        ("Samantha", "Samantha", "Hello Daniel and Fred. We are waiting on Fred to confirm audio connectivity."),
        ("Daniel", "Daniel", "I see Fred on the roster, but he appears to be on mute."),
        ("Samantha", "Samantha", "Understood. We will proceed with the two of us for now."),
    ]
    audio, ref, rttm = _build_meeting(turns_silent, [0.5, 0.8, 0.7], target_duration_sec=30.0)
    p_wav = FIXTURES_DIR / "silent_invitee_meeting.wav"
    wavfile.write(p_wav, SAMPLE_RATE, audio)
    (FIXTURES_DIR / "silent_invitee_meeting.txt").write_text(ref, encoding="utf-8")
    (FIXTURES_DIR / "silent_invitee_meeting.rttm").write_text(rttm, encoding="utf-8")
    created["silent_invitee"] = p_wav

    # 4. Adversarial Phrases Meeting (~30s, contains terms that trigger regex drops)
    turns_adv = [
        ("Samantha", "Samantha", "Please subscribe to our newsletter and visit our grid.example.com portal for updates."),
        ("Daniel", "Daniel", "We are discussing the project nexus roadmap and partner_advisory deliverables."),
        ("Samantha", "Samantha", "Ensure all participants bookmark the documentation at portal.io today."),
    ]
    audio, ref, rttm = _build_meeting(turns_adv, [0.5, 0.7, 0.8], target_duration_sec=30.0)
    p_wav = FIXTURES_DIR / "adversarial_phrases_meeting.wav"
    wavfile.write(p_wav, SAMPLE_RATE, audio)
    (FIXTURES_DIR / "adversarial_phrases_meeting.txt").write_text(ref, encoding="utf-8")
    (FIXTURES_DIR / "adversarial_phrases_meeting.rttm").write_text(rttm, encoding="utf-8")
    created["adversarial_phrases"] = p_wav

    # 5. Mid-Word Chunk Boundary Meeting (~30s, speech crosses exactly 15.0s mark)
    # 13.5s silence, then a long utterance spanning from 13.5s to 17.5s (crossing 15.0s)
    silence_start = np.zeros(int(13.5 * SAMPLE_RATE), dtype=np.int16)
    crossing_utterance = _render_utterance(
        "Interoperability between heterogeneous pipeline components requires atomic synchronization guarantees.",
        "Daniel",
        seed=555,
    )
    tail_silence = np.zeros(max(0, int(30.0 * SAMPLE_RATE) - len(silence_start) - len(crossing_utterance)), dtype=np.int16)
    mid_audio = np.concatenate([silence_start, crossing_utterance, tail_silence])[: int(30.0 * SAMPLE_RATE)]
    p_wav = FIXTURES_DIR / "mid_word_boundary_meeting.wav"
    wavfile.write(p_wav, SAMPLE_RATE, mid_audio)
    ref_mid = (
        "[00:13] Daniel: Interoperability between heterogeneous pipeline components "
        "requires atomic synchronization guarantees.\n"
    )
    rttm_mid = f"SPEAKER meeting 1 13.500 {len(crossing_utterance)/SAMPLE_RATE:.3f} <NA> <NA> Daniel <NA> <NA>\n"
    (FIXTURES_DIR / "mid_word_boundary_meeting.txt").write_text(ref_mid, encoding="utf-8")
    (FIXTURES_DIR / "mid_word_boundary_meeting.rttm").write_text(rttm_mid, encoding="utf-8")
    created["mid_word_boundary"] = p_wav

    # 6. Long Silence Meeting (180s = 3 minutes of digital silence)
    p_wav = FIXTURES_DIR / "long_silence_meeting.wav"
    silence_180s = np.zeros(SAMPLE_RATE * 180, dtype=np.int16)
    wavfile.write(p_wav, SAMPLE_RATE, silence_180s)
    (FIXTURES_DIR / "long_silence_meeting.txt").write_text("# Pure Silence (180s)\n", encoding="utf-8")
    (FIXTURES_DIR / "long_silence_meeting.rttm").write_text("", encoding="utf-8")
    created["long_silence"] = p_wav

    return created


if __name__ == "__main__":
    fixtures = generate_all_fixtures()
    print(f"[SUCCESS] Generated {len(fixtures)} live audio fixtures in {FIXTURES_DIR}:")
    for k, p in fixtures.items():
        size_kb = p.stat().st_size / 1024
        print(f"  - {k:22}: {p.name} ({size_kb:.1f} KB)")
