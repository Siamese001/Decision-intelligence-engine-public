"""FFmpeg discovery and audio chunking helpers."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import imageio_ffmpeg

from config.credentials import CredentialManager
from pipeline_a_intake.audio_chunker import (
    _CHUNK_DURATION_SECONDS,
    _MAX_FILE_SIZE_BYTES,
    _OVERLAP_SECONDS,
    clean_speaker_label,
    detect_silence_cutpoints,
    get_audio_duration,
    slice_audio_chunk,
    stitch_and_align_segments,
)

logger = logging.getLogger(__name__)


def ensure_ffmpeg() -> str:
    """
    Ensures a working ffmpeg executable is discovered and on PATH.
    Checks system PATH, .venv/bin, and imageio_ffmpeg bundled binaries.
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    # Check .venv/bin/ffmpeg
    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "bin"
    venv_ffmpeg = venv_bin / "ffmpeg"
    if venv_ffmpeg.exists():
        CredentialManager.prepend_path(venv_bin)
        return str(venv_ffmpeg)

    try:
        img_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if img_exe and Path(img_exe).exists():
            p = Path(img_exe)
            if p.name == "ffmpeg":
                bin_dir = str(p.parent)
                CredentialManager.prepend_path(bin_dir)
                return img_exe
            # Symlink or copy imageio_ffmpeg binary into .venv/bin/ffmpeg
            venv_bin.mkdir(parents=True, exist_ok=True)
            if not venv_ffmpeg.exists():
                try:
                    Path(venv_ffmpeg).symlink_to(img_exe)
                except Exception:
                    shutil.copy2(img_exe, venv_ffmpeg)
            CredentialManager.prepend_path(venv_bin)
            return str(venv_ffmpeg)
    except Exception as e:
        logger.warning(f"Could not auto-resolve ffmpeg from imageio_ffmpeg: {e}")
    return "ffmpeg"


# Prepend bundled .venv/bin to PATH and ensure ffmpeg is available
ensure_ffmpeg()

__all__ = [
    "ensure_ffmpeg",
    "clean_speaker_label",
    "detect_silence_cutpoints",
    "stitch_and_align_segments",
    "get_audio_duration",
    "slice_audio_chunk",
    "_CHUNK_DURATION_SECONDS",
    "_OVERLAP_SECONDS",
    "_MAX_FILE_SIZE_BYTES",
]
