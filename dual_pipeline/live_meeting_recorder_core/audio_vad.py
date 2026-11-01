"""
dual_pipeline/live_meeting_recorder_core/audio_vad.py

Acoustic Voice Activity Detection (VAD) and speech boundary segmenter.
Replaces fixed-time slicing with energy-aware speech boundary detection,
preventing silent audio chunks from triggering autoregressive Whisper hallucinations
(e.g., 'Thank you for watching.', 'Quack.', 'Please subscribe.').
"""

from __future__ import annotations

import logging
import math
import shutil
import struct
import wave
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_vad")


class AudioVADSegmenter:
    """
    Acoustic energy & Voice Activity Detection segmenter for speech-aware chunking.
    Computes frame-level and chunk-level Root-Mean-Square (RMS) speech energy,
    detects active speech versus ambient room silence, and filters out silence intervals.
    """

    def __init__(
        self,
        energy_threshold: float = 0.005,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 400,
        sample_rate: int = 16000,
    ) -> None:
        self.energy_threshold = energy_threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.sample_rate = sample_rate

    @staticmethod
    def calculate_wav_rms(wav_path: Path) -> float:
        """Calculates normalized RMS energy for a 16-bit linear PCM WAV file."""
        p = Path(wav_path)
        if not p.exists():
            raise FileNotFoundError(f"WAV file not found: {p}")
        if p.stat().st_size < 44:
            raise ValueError(f"WAV file {p.name} too small to contain valid header ({p.stat().st_size} bytes)")

        try:
            with wave.open(str(p), "rb") as wf:
                n_frames = wf.getnframes()
                if n_frames == 0:
                    raise ValueError(f"WAV file {p.name} contains 0 frames")
                frames = wf.readframes(n_frames)
                n_samples = len(frames) // 2
                if n_samples == 0:
                    raise ValueError(f"WAV file {p.name} contains 0 decoded samples")
                samples = struct.unpack(f"<{n_samples}h", frames[: n_samples * 2])
                sum_sq = sum(float(s * s) for s in samples)
                mean_sq = sum_sq / float(n_samples)
                # Normalize against full scale 32768.0
                return math.sqrt(mean_sq) / 32768.0
        except Exception as e:
            logger.error(f"Error calculating RMS for {wav_path}: {e}")
            raise RuntimeError(f"Failed to calculate RMS for {wav_path}: {e}") from e

    def is_speech_chunk(self, wav_path: Path, threshold: Optional[float] = None) -> bool:
        """Determines if a chunk contains active speech exceeding the energy threshold."""
        th = self.energy_threshold if threshold is None else threshold
        rms = self.calculate_wav_rms(wav_path)
        return rms >= th

    def filter_speech_chunks(
        self,
        chunk_paths: List[Path],
        threshold: Optional[float] = None,
        quarantine_dir: Optional[Path] = None,
    ) -> Tuple[List[Path], List[Path]]:
        """
        Partitions chunk paths into active speech chunks and silence chunks.
        Optionally copies suppressed chunks to quarantine_dir for zero-loss recovery.
        Returns: (active_chunks, silent_chunks)
        """
        active: List[Path] = []
        silent: List[Path] = []
        for cp in chunk_paths:
            if self.is_speech_chunk(cp, threshold=threshold):
                active.append(cp)
            else:
                silent.append(cp)
                if quarantine_dir is not None:
                    try:
                        q_path = Path(quarantine_dir)
                        q_path.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(cp), str(q_path / cp.name))
                    except Exception as e:
                        logger.debug(f"Failed to quarantine silent chunk {cp}: {e}")
        logger.info(
            f"VAD filtered {len(chunk_paths)} chunks: {len(active)} active speech, "
            f"{len(silent)} silence chunks suppressed."
        )
        return active, silent

    def detect_speech_boundaries(
        self,
        wav_path: Path,
        frame_duration_ms: int = 30,
    ) -> List[Tuple[float, float]]:
        """
        Scans a continuous WAV file to identify speech interval boundaries (start_sec, end_sec).
        Merges short silence gaps (< min_silence_duration_ms) and drops bursts (< min_speech_duration_ms).
        """
        frames = _extract_speech_frames(wav_path, frame_duration_ms, self.energy_threshold)
        return _cluster_speech_intervals(
            frames,
            frame_duration_ms,
            self.min_speech_duration_ms,
            self.min_silence_duration_ms,
        )


def _extract_speech_frames(
    wav_path: Path,
    frame_duration_ms: int,
    energy_threshold: float,
) -> List[bool]:
    p = Path(wav_path)
    if not p.exists():
        raise FileNotFoundError(f"WAV file not found: {p}")
    if p.stat().st_size < 44:
        raise ValueError(f"WAV file {p.name} too small to contain valid header ({p.stat().st_size} bytes)")

    try:
        with wave.open(str(p), "rb") as wf:
            sample_rate = wf.getframerate()
            n_channels = wf.getnchannels()
            frames_per_buffer = int(sample_rate * (frame_duration_ms / 1000.0))
            buffer_size = frames_per_buffer * n_channels * 2

            is_speech_frames: List[bool] = []
            while True:
                buf = wf.readframes(frames_per_buffer)
                if not buf or len(buf) < buffer_size:
                    break
                n_s = len(buf) // 2
                samples = struct.unpack(f"<{n_s}h", buf[: n_s * 2])
                sum_sq = sum(float(s * s) for s in samples)
                rms = math.sqrt(sum_sq / float(n_s)) / 32768.0
                is_speech_frames.append(rms >= energy_threshold)
            return is_speech_frames
    except Exception as e:
        logger.error(f"Error computing speech boundaries for {wav_path}: {e}")
        raise RuntimeError(f"Failed to compute speech boundaries for {wav_path}: {e}") from e


def _append_interval_if_valid(
    intervals: List[Tuple[float, float]],
    start_f: int,
    end_f: int,
    min_speech_f: int,
    frame_duration_ms: int,
) -> None:
    if (end_f - start_f) >= min_speech_f:
        intervals.append((
            round(start_f * frame_duration_ms / 1000.0, 2),
            round(end_f * frame_duration_ms / 1000.0, 2),
        ))


def _cluster_speech_intervals(
    is_speech_frames: List[bool],
    frame_duration_ms: int,
    min_speech_ms: int,
    min_silence_ms: int,
) -> List[Tuple[float, float]]:
    if not is_speech_frames:
        return []

    intervals: List[Tuple[float, float]] = []
    in_speech = False
    start_f = 0
    silence_run = 0
    min_silence_f = int(min_silence_ms / frame_duration_ms)
    min_speech_f = int(min_speech_ms / frame_duration_ms)

    for i, active in enumerate(is_speech_frames):
        if active:
            if not in_speech:
                in_speech = True
                start_f = i
            silence_run = 0
            continue

        if not in_speech:
            continue

        silence_run += 1
        if silence_run >= min_silence_f:
            end_f = i - silence_run + 1
            _append_interval_if_valid(intervals, start_f, end_f, min_speech_f, frame_duration_ms)
            in_speech = False
            silence_run = 0

    if in_speech:
        _append_interval_if_valid(
            intervals, start_f, len(is_speech_frames), min_speech_f, frame_duration_ms
        )

    return intervals
