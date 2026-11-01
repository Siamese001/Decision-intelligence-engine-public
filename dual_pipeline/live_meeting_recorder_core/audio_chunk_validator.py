"""
dual_pipeline/live_meeting_recorder_core/audio_chunk_validator.py

Layer 1: Chunk Validation & Stream Health Sentinel.
Validates chunk integrity, prevents zero-byte / header-only WAV ingestion,
and distinguishes genuine acoustic silence from dead digital streams.
"""
from __future__ import annotations

import atexit
import logging
import math
import struct
import wave
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_chunk_validator")

_ACTIVE_RECORDERS: weakref.WeakSet[Any] = weakref.WeakSet()


def register_active_recorder(rec: Any) -> None:
    """Registers an active recorder instance for graceful process termination."""
    _ACTIVE_RECORDERS.add(rec)


def _cleanup_active_recorders() -> None:
    """Process termination handler to ensure background child processes are killed."""
    for rec in list(_ACTIVE_RECORDERS):
        try:
            if hasattr(rec, "capture_manager") and rec.capture_manager is not None:
                rec.capture_manager.stop_processes()
            elif getattr(rec, "running", False) and hasattr(rec, "stop"):
                rec.stop()
        except Exception as e:
            logger.error("[CLEANUP-ERROR] Failed to clean up active recorder: %s", e)


atexit.register(_cleanup_active_recorders)

# Constant: minimum size for a valid audio chunk (1 second of 16kHz 16-bit mono is 32,000 bytes)
MIN_VALID_CHUNK_BYTES = 4096
HEADER_ONLY_WAV_BYTES_MAX = 256
MIN_SPEECH_RMS_THRESHOLD = 120.0  # Calibrated acoustic silence floor (~ -48.7 dBFS)


class AudioChunkError(ValueError):
    """Base exception for invalid audio chunk data."""
    pass


class EmptyAudioChunkError(AudioChunkError):
    """Raised when an audio chunk contains zero audio frames or header-only content."""
    pass


class DeadAudioStreamError(AudioChunkError):
    """Raised when an audio chunk contains pure digital zeros (disconnected/unrouted audio)."""
    pass


@dataclass
class AudioChunkValidationResult:
    is_valid: bool
    file_size_bytes: int
    duration_sec: float
    rms: float
    is_dead_stream: bool
    is_silence: bool
    reason: str
    micro_dropouts: int = 0


class AudioChunkValidator:
    """
    Validates rolling audio chunk files produced by FFmpeg capture pipelines.
    Guarantees that empty, truncated, or dead-stream WAV files are immediately flagged.
    """

    @staticmethod
    def validate_chunk(
        wav_path: Path,
        min_bytes: int = MIN_VALID_CHUNK_BYTES,
        allow_digital_zero: bool = False,
        is_bluetooth: bool = False,
    ) -> AudioChunkValidationResult:
        """
        Inspects WAV file size, headers, frame count, RMS, and sample variance.
        """
        p = Path(wav_path)
        if not p.exists():
            return AudioChunkValidationResult(
                is_valid=False,
                file_size_bytes=0,
                duration_sec=0.0,
                rms=0.0,
                is_dead_stream=True,
                is_silence=True,
                reason=f"File does not exist: {p}",
            )

        size = p.stat().st_size
        if size < min_bytes:
            return AudioChunkValidationResult(
                is_valid=False,
                file_size_bytes=size,
                duration_sec=0.0,
                rms=0.0,
                is_dead_stream=True,
                is_silence=True,
                reason=f"File size ({size} bytes) below minimum threshold ({min_bytes} bytes).",
            )

        try:
            with wave.open(str(p), "rb") as w:
                nframes = w.getnframes()
                framerate = w.getframerate()
                nchannels = w.getnchannels()
                sampwidth = w.getsampwidth()

                if nframes == 0 or framerate == 0:
                    return AudioChunkValidationResult(
                        is_valid=False,
                        file_size_bytes=size,
                        duration_sec=0.0,
                        rms=0.0,
                        is_dead_stream=True,
                        is_silence=True,
                        reason="WAV file has 0 frames or invalid sample rate.",
                    )

                duration = nframes / float(framerate)
                raw_data = w.readframes(nframes)

        except Exception as e:
            return AudioChunkValidationResult(
                is_valid=False,
                file_size_bytes=size,
                duration_sec=0.0,
                rms=0.0,
                is_dead_stream=True,
                is_silence=True,
                reason=f"Failed to parse WAV header: {e}",
            )

        # Inspect raw samples for digital zero vs acoustic noise
        total_samples = nframes * nchannels
        fmt = f"{total_samples}h" if sampwidth == 2 else None
        if not fmt:
            # Reject non-16-bit
            return AudioChunkValidationResult(
                is_valid=False,
                file_size_bytes=size,
                duration_sec=duration,
                rms=0.0,
                is_dead_stream=True,
                is_silence=True,
                reason=f"Unsupported sample width: {sampwidth} bytes (only 16-bit PCM supported).",
            )

        try:
            samples = struct.unpack(fmt, raw_data)
            max_amp = max(abs(s) for s in samples)
            rms = math.sqrt(sum(s**2 for s in samples) / float(total_samples))
        except Exception as e:
            return AudioChunkValidationResult(
                is_valid=False,
                file_size_bytes=size,
                duration_sec=duration,
                rms=0.0,
                is_dead_stream=True,
                is_silence=True,
                reason=f"Failed to decode samples: {e}",
            )

        # Pure digital zero detection
        is_dead = (max_amp == 0 and rms == 0.0)
        if is_dead and not allow_digital_zero:
            return AudioChunkValidationResult(
                is_valid=False,
                file_size_bytes=size,
                duration_sec=duration,
                rms=0.0,
                is_dead_stream=True,
                is_silence=True,
                reason="Audio contains 100% digital zeros (dead stream or unrouted audio).",
            )

        # Calibrated silence floor (Bluetooth headsets have lower acoustic floor)
        silence_floor = 80.0 if is_bluetooth else MIN_SPEECH_RMS_THRESHOLD
        is_silence = rms < silence_floor

        # Micro-dropout detection (runs of flatline zeros between 10ms and 500ms)
        micro_dropouts = 0
        if not is_dead and total_samples > 0:
            zero_run = 0
            for s in samples:
                if abs(s) <= 5:
                    zero_run += 1
                else:
                    if 160 <= zero_run <= 8000:
                        micro_dropouts += 1
                    zero_run = 0
            if 160 <= zero_run <= 8000:
                micro_dropouts += 1

        return AudioChunkValidationResult(
            is_valid=True,
            file_size_bytes=size,
            duration_sec=duration,
            rms=rms,
            is_dead_stream=is_dead,
            is_silence=is_silence,
            reason="Valid audio chunk.",
            micro_dropouts=micro_dropouts,
        )


def validate_final_chunk(chunks_dir: Path, segment_seconds: int) -> None:
    """Validates the final audio chunk integrity and logs warning if truncated.

    Prunes trailing 0-byte stubs left by abrupt process termination if valid
    prior audio chunks exist. Raises EmptyAudioChunkError only if zero valid
    audio was captured.
    """
    chunk_files = sorted(chunks_dir.glob("chunk_*.wav"))
    if not chunk_files:
        raise EmptyAudioChunkError(f"No audio chunks found in {chunks_dir}")

    # Prune trailing 0-byte stubs if valid prior chunks exist
    while chunk_files:
        last_chunk = chunk_files[-1]
        try:
            size = last_chunk.stat().st_size
        except OSError as e:
            raise RuntimeError(f"Failed to inspect final chunk {last_chunk.name}: {e}") from e

        if size == 0:
            if len(chunk_files) > 1:
                logger.warning(
                    "[FINAL-CHUNK-PRUNED] Trailing 0-byte stub %s pruned (valid prior chunks exist).",
                    last_chunk.name,
                )
                try:
                    last_chunk.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("[FINAL-CHUNK-PRUNE-FAILED] Could not unlink 0-byte stub %s: %s", last_chunk.name, e)
                chunk_files.pop()
                continue
            else:
                raise EmptyAudioChunkError(f"Final chunk {last_chunk.name} is 0 bytes.")
        else:
            break

    if not chunk_files:
        raise EmptyAudioChunkError(f"No audio chunks found in {chunks_dir}")

    last_chunk = chunk_files[-1]
    size = last_chunk.stat().st_size
    expected_size = segment_seconds * 16000 * 2  # 16kHz 16-bit mono = 32000 bytes/sec
    if size < (expected_size * 0.5):
        logger.warning(
            "[FINAL-CHUNK-TRUNCATION] Final chunk %s is %d bytes (< 50%% expected %d bytes).",
            last_chunk.name, size, expected_size,
        )
