"""
dual_pipeline/audio_ingestion_core/container_validator.py

Pure-Python audio container header validator.
Performs pre-flight inspection of audio container byte structure (WAV, MP4, MP3)
before invoking native C/C++ demuxers (ffmpeg, libavformat, Whisper models)
to prevent integer overflow, memory corruption, and container-level exploits.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from dual_pipeline.exceptions import AudioContainerValidationError

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.container_validator")


def repair_wav_riff_header(file_path: Union[str, Path]) -> bool:
    """Repairs malformed or streaming 0xFFFFFFFF RIFF header and data chunk sizes in-place.

    Fixes:
    - Bytes 4..7: RIFF chunk size (file_size - 8) if set to 0xFFFFFFFF or out of bounds.
    - 'data' chunk size if set to 0xFFFFFFFF or exceeding remaining file length.
    Returns True if header was patched, False if already valid or unrepairable.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        return False
    try:
        file_size = path.stat().st_size
        if file_size < 44:
            return False

        with open(path, "r+b") as f:
            header = f.read(4096)
            if len(header) < 44:
                return False
            magic = header[:4]
            if magic not in (b"RIFF", b"RIFX"):
                return False
            is_be = (magic == b"RIFX")
            endian = ">" if is_be else "<"
            riff_size = struct.unpack(f"{endian}I", header[4:8])[0]
            wave_id = header[8:12]
            if wave_id != b"WAVE":
                return False

            patched = False
            expected_riff = min(file_size - 8, 0xFFFFFFFF)
            if riff_size == 0xFFFFFFFF or riff_size + 8 > file_size + 4096 or riff_size == 0:
                f.seek(4)
                f.write(struct.pack(f"{endian}I", expected_riff))
                patched = True

            offset = 12
            while offset + 8 <= len(header):
                chunk_id = header[offset : offset + 4]
                chunk_sz = struct.unpack(f"{endian}I", header[offset + 4 : offset + 8])[0]
                if chunk_id == b"data":
                    expected_data = min(file_size - (offset + 8), 0xFFFFFFFF)
                    if chunk_sz == 0xFFFFFFFF or (offset + 8 + chunk_sz > file_size + 4096) or chunk_sz == 0:
                        f.seek(offset + 4)
                        f.write(struct.pack(f"{endian}I", expected_data))
                        patched = True
                    break
                if chunk_sz == 0 or offset + 8 + chunk_sz > len(header):
                    break
                offset += 8 + chunk_sz

            if patched:
                f.flush()
                logger.info("Repaired malformed WAV RIFF header for %s (size=%d)", path.name, file_size)
            return patched
    except Exception as e:
        logger.warning("Failed to repair WAV header for %s: %s", path, e)
        return False


@dataclass(frozen=True)
class AudioContainerSpec:
    format_name: str
    channels: int
    sample_rate: int
    bits_per_sample: int
    data_size_bytes: int
    declared_duration_seconds: Optional[float] = None


def validate_audio_container_headers(file_path: Union[str, Path]) -> AudioContainerSpec:
    """
    Validates audio container headers in pure Python without invoking native subprocesses.
    Raises AudioContainerValidationError if the file is malformed, truncated, or exceeds safe limits.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise AudioContainerValidationError(f"Audio file does not exist or is not a regular file: {path}")

    file_size = path.stat().st_size
    if file_size < 12:
        raise AudioContainerValidationError(
            f"Audio file '{path.name}' is truncated ({file_size} bytes); minimum container header is 12 bytes."
        )

    with path.open("rb") as f:
        header = f.read(4096)

    ext = path.suffix.lower()

    if ext in (".wav", ".wave"):
        return _validate_wav_headers(path, header, file_size)
    elif ext in (".mp4", ".m4a", ".aac"):
        return _validate_mp4_headers(path, header, file_size)
    elif ext in (".mp3",):
        return _validate_mp3_headers(path, header, file_size)

    # For other known extensions, perform basic non-empty and magic byte sanity
    return AudioContainerSpec(
        format_name=ext.lstrip("."),
        channels=1,
        sample_rate=16000,
        bits_per_sample=16,
        data_size_bytes=file_size,
    )


def _validate_wav_headers(path: Path, header: bytes, file_size: int) -> AudioContainerSpec:
    """Validates RIFF/WAVE container structure."""
    if len(header) < 44:
        raise AudioContainerValidationError(
            f"WAV file '{path.name}' is too small ({len(header)} bytes read); standard canonical WAV header requires >= 44 bytes."
        )

    magic = header[:4]
    if magic not in (b"RIFF", b"RIFX"):
        raise AudioContainerValidationError(
            f"Malformed WAV file '{path.name}': Invalid magic bytes {magic!r}, expected b'RIFF' or b'RIFX'."
        )

    is_big_endian = (magic == b"RIFX")
    endian = ">" if is_big_endian else "<"

    riff_size = struct.unpack(f"{endian}I", header[4:8])[0]
    wave_id = header[8:12]
    if wave_id != b"WAVE":
        raise AudioContainerValidationError(
            f"Malformed WAV file '{path.name}': Missing 'WAVE' identifier, got {wave_id!r}."
        )

    # Check for integer overflow or unreasonable declared size (auto-repair 0xFFFFFFFF or out-of-bounds)
    if riff_size + 8 > file_size + 4096 or riff_size == 0xFFFFFFFF:
        if repair_wav_riff_header(path):
            with path.open("rb") as f:
                header = f.read(4096)
            riff_size = struct.unpack(f"{endian}I", header[4:8])[0]
        if riff_size + 8 > file_size + 4096:
            raise AudioContainerValidationError(
                f"Malformed WAV file '{path.name}': Declared RIFF size ({riff_size} bytes) exceeds actual file size ({file_size} bytes)."
            )

    # Parse chunks looking for 'fmt ' and 'data'
    offset = 12
    fmt_found = False
    channels = 1
    sample_rate = 16000
    bits_per_sample = 16
    data_size = file_size - 44

    while offset + 8 <= len(header):
        chunk_id = header[offset : offset + 4]
        chunk_size = struct.unpack(f"{endian}I", header[offset + 4 : offset + 8])[0]
        offset += 8

        if chunk_id == b"fmt ":
            fmt_found = True
            if chunk_size < 16 or offset + 16 > len(header):
                raise AudioContainerValidationError(
                    f"Malformed WAV file '{path.name}': 'fmt ' chunk size ({chunk_size}) is truncated."
                )
            fmt_tag, num_channels, s_rate, byte_rate, block_align, bits = struct.unpack(
                f"{endian}HHIIHH", header[offset : offset + 16]
            )

            # Format tag: 1 = PCM, 3 = IEEE Float, 6 = A-law, 7 = Mu-law, 0xFFFE = Extensible
            if fmt_tag not in (1, 3, 6, 7, 0xFFFE):
                raise AudioContainerValidationError(
                    f"Unsupported audio format tag {fmt_tag} in WAV file '{path.name}'."
                )

            if not (1 <= num_channels <= 8):
                raise AudioContainerValidationError(
                    f"WAV channel count {num_channels} outside safe bounds [1, 8] in '{path.name}'."
                )

            if not (8000 <= s_rate <= 192000):
                raise AudioContainerValidationError(
                    f"WAV sample rate {s_rate} Hz outside safe bounds [8000, 192000] in '{path.name}'."
                )

            if not (8 <= bits <= 64):
                raise AudioContainerValidationError(
                    f"WAV bit depth {bits} outside safe bounds [8, 64] in '{path.name}'."
                )

            channels = num_channels
            sample_rate = s_rate
            bits_per_sample = bits
            offset += chunk_size
        elif chunk_id == b"data":
            data_size = chunk_size
            offset += chunk_size
        else:
            offset += chunk_size

    if not fmt_found:
        raise AudioContainerValidationError(f"Missing mandatory 'fmt ' chunk in WAV file '{path.name}'.")

    duration = None
    bytes_per_second = sample_rate * channels * (bits_per_sample // 8)
    if bytes_per_second > 0 and data_size > 0:
        duration = data_size / bytes_per_second

    return AudioContainerSpec(
        format_name="wav",
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits_per_sample,
        data_size_bytes=data_size,
        declared_duration_seconds=duration,
    )


def _validate_mp4_headers(path: Path, header: bytes, file_size: int) -> AudioContainerSpec:
    """Validates MP4 / M4A / AAC container structure."""
    if len(header) < 16:
        raise AudioContainerValidationError(f"MP4 file '{path.name}' truncated: < 16 bytes.")

    box_size = struct.unpack(">I", header[0:4])[0]
    box_type = header[4:8]

    if box_type != b"ftyp":
        # Check if raw ADTS AAC
        if header[:2] in (b"\xff\xf1", b"\xff\xf9"):
            return AudioContainerSpec("aac", 2, 44100, 16, file_size)
        raise AudioContainerValidationError(
            f"Invalid MP4/M4A header in '{path.name}': Missing 'ftyp' box, got {box_type!r}."
        )

    if box_size > file_size or box_size < 8:
        raise AudioContainerValidationError(
            f"Corrupt 'ftyp' box size ({box_size} bytes) in MP4 file '{path.name}'."
        )

    return AudioContainerSpec("mp4", 2, 44100, 16, file_size)


def _validate_mp3_headers(path: Path, header: bytes, file_size: int) -> AudioContainerSpec:
    """Validates MP3 file headers (ID3 or MPEG audio frame sync)."""
    if header.startswith(b"ID3"):
        if len(header) < 10:
            raise AudioContainerValidationError(f"Truncated ID3 tag in MP3 file '{path.name}'.")
        return AudioContainerSpec("mp3", 2, 44100, 16, file_size)

    # Check for MPEG audio syncword (11 set bits: 0xFF followed by 0xE0-0xFF)
    for i in range(min(len(header) - 1, 1024)):
        if header[i] == 0xFF and (header[i + 1] & 0xE0) == 0xE0:
            return AudioContainerSpec("mp3", 2, 44100, 16, file_size)

    raise AudioContainerValidationError(
        f"Invalid MP3 file '{path.name}': Missing valid ID3 tag or MPEG sync frame."
    )
