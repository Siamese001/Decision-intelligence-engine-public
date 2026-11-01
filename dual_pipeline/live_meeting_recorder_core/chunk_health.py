"""
dual_pipeline/live_meeting_recorder_core/chunk_health.py

Consolidated single-pass chunk health evaluation & caching.
Eliminates redundant file opens, audio reads, and separate RMS/VAD calculations.
Calculates duration, numpy-based linear RMS, dBFS, silence detection, and validity once per chunk.
"""
from __future__ import annotations

import math
import re
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Tuple

import numpy as np


def find_wav_data_offset(wav_bytes: bytes) -> int:
    """Dynamically parses RIFF/WAVE subchunks (fmt, LIST/INFO, JUNK, data) to find the exact PCM data offset."""
    if len(wav_bytes) < 12 or not wav_bytes.startswith(b"RIFF") or wav_bytes[8:12] != b"WAVE":
        return 0
    offset, total_len = 12, len(wav_bytes)
    while offset + 8 <= total_len:
        chunk_id = wav_bytes[offset : offset + 4]
        chunk_size = struct.unpack("<I", wav_bytes[offset + 4 : offset + 8])[0]
        if chunk_id == b"data":
            return offset + 8
        step = 8 + chunk_size + (chunk_size & 1)
        if step <= 0:
            break
        offset += step
    return total_len


def build_epoch_indexed_chunk_map(chunk_files: List[Path]) -> Tuple[int, int, Dict[int, Path]]:
    """Builds a contiguous logical index map across single or multi-epoch chunk files."""
    epochs_map: Dict[int, Dict[int, Path]] = {}
    for p in chunk_files:
        m = re.search(r"^chunk_(?:e(\d+)_)?(\d+)\.wav$", p.name)
        if m:
            epoch = int(m.group(1)) if m.group(1) is not None else 0
            epochs_map.setdefault(epoch, {})[int(m.group(2))] = p
    if not epochs_map:
        return 0, -1, {}
    sorted_epochs = sorted(epochs_map.keys())
    if len(sorted_epochs) == 1 and sorted_epochs[0] == 0:
        indexed_paths = epochs_map[0]
        return min(indexed_paths.keys()), max(indexed_paths.keys()), indexed_paths
    indexed_paths = {}
    logical_idx = 0
    for epoch in sorted_epochs:
        ep_paths = epochs_map[epoch]
        for idx in range(min(ep_paths.keys()), max(ep_paths.keys()) + 1):
            if idx in ep_paths:
                indexed_paths[logical_idx] = ep_paths[idx]
            logical_idx += 1
    return 0, logical_idx - 1, indexed_paths


@dataclass(frozen=True)
class ChunkHealth:
    path: Path
    file_size: int
    duration_sec: float
    rms: float
    rms_dbfs: float
    is_valid: bool
    is_silence: bool
    is_audible: bool
    error: Optional[str] = None

    @property
    def rms_normalized(self) -> float:
        """Returns linear RMS normalized to [0.0, 1.0] full-scale float range."""
        return min(1.0, max(0.0, float(self.rms) / 32768.0))

    _CACHE: ClassVar[Dict[Tuple[str, int, int], "ChunkHealth"]] = {}

    @classmethod
    def clear_cache(cls) -> None:
        """Clears the in-memory chunk health cache."""
        cls._CACHE.clear()

    @staticmethod
    def _raw_bytes_to_samples(raw_bytes: bytes, sample_width: int) -> np.ndarray:
        if sample_width == 2:
            return np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
        if sample_width == 1:
            return (np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) - 128.0) * 256.0
        if sample_width == 4:
            return np.frombuffer(raw_bytes, dtype=np.int32).astype(np.float32) / 65536.0
        return np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)

    @classmethod
    def _compute_audio_metrics(
        cls,
        raw_bytes: bytes,
        sample_width: int,
        silence_rms_threshold: float,
        silence_dbfs_threshold: float,
        allow_digital_zero: bool,
    ) -> Tuple[float, float, bool, bool, bool]:
        samples = cls._raw_bytes_to_samples(raw_bytes, sample_width)
        rms = 0.0 if len(samples) == 0 else float(np.sqrt(np.mean(samples**2)))
        rms_dbfs = 20.0 * math.log10(max(rms, 1e-9) / 32768.0)
        is_digital_zero = (rms < 1e-6)
        is_silence = is_digital_zero or (rms < silence_rms_threshold) or (rms_dbfs < silence_dbfs_threshold)
        is_audible = not is_silence
        is_valid = True if allow_digital_zero else (not is_digital_zero)
        return rms, rms_dbfs, is_valid, is_silence, is_audible

    @classmethod
    def evaluate(
        cls,
        path: Path,
        silence_rms_threshold: float = 100.0,
        silence_dbfs_threshold: float = -45.0,
        allow_digital_zero: bool = False,
    ) -> "ChunkHealth":
        """Evaluates chunk health in a single file open with in-memory caching."""
        if not path.exists():
            return cls(
                path=path,
                file_size=0,
                duration_sec=0.0,
                rms=0.0,
                rms_dbfs=-100.0,
                is_valid=False,
                is_silence=True,
                is_audible=False,
                error="File does not exist",
            )

        try:
            stat = path.stat()
            cache_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
            if cache_key in cls._CACHE:
                return cls._CACHE[cache_key]
        except Exception:
            stat = None
            cache_key = None

        file_size = stat.st_size if stat else 0
        if file_size < 44:  # WAV header is 44 bytes
            health = cls(
                path=path,
                file_size=file_size,
                duration_sec=0.0,
                rms=0.0,
                rms_dbfs=-100.0,
                is_valid=False,
                is_silence=True,
                is_audible=False,
                error=f"File too small for WAV header ({file_size} bytes)",
            )
            if cache_key:
                cls._CACHE[cache_key] = health
            return health

        try:
            with wave.open(str(path), "rb") as wf:
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                if framerate <= 0:
                    framerate = 16000
                duration_sec = n_frames / float(framerate)
                raw_bytes = wf.readframes(n_frames)

            if len(raw_bytes) == 0:
                health = cls(
                    path=path,
                    file_size=file_size,
                    duration_sec=duration_sec,
                    rms=0.0,
                    rms_dbfs=-100.0,
                    is_valid=False,
                    is_silence=True,
                    is_audible=False,
                    error="Zero audio frames in WAV",
                )
                if cache_key:
                    cls._CACHE[cache_key] = health
                return health

            rms, rms_dbfs, is_valid, is_silence, is_audible = cls._compute_audio_metrics(
                raw_bytes, sample_width, silence_rms_threshold, silence_dbfs_threshold, allow_digital_zero
            )

            health = cls(
                path=path,
                file_size=file_size,
                duration_sec=duration_sec,
                rms=rms,
                rms_dbfs=rms_dbfs,
                is_valid=is_valid,
                is_silence=is_silence,
                is_audible=is_audible,
                error=None,
            )
            if cache_key:
                cls._CACHE[cache_key] = health
            return health

        except Exception as e:
            health = cls(
                path=path,
                file_size=file_size,
                duration_sec=0.0,
                rms=0.0,
                rms_dbfs=-100.0,
                is_valid=False,
                is_silence=True,
                is_audible=False,
                error=f"Error reading WAV: {e}",
            )
            if cache_key:
                cls._CACHE[cache_key] = health
            return health
