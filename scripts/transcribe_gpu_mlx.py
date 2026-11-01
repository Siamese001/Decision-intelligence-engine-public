#!/usr/bin/env python3
"""
scripts/transcribe_gpu_mlx.py

High-performance Apple Silicon Metal GPU audio transcriber using mlx-whisper.
Runs 100% on-premise with zero cloud egress, offloading inference to the Metal GPU.

Usage:
    python scripts/transcribe_gpu_mlx.py [audio_file_path] [--model MODEL] [--no-cache]
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TranscribeGPU")


def get_audio_duration(audio_path: str) -> float:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", audio_path]
    p = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr)
    if not match:
        raise ValueError(f"Could not parse duration from ffmpeg output for {audio_path}")
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def main():
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] scripts/transcribe_gpu_mlx.py has been decommissioned.\n"
        "Direct script execution is strictly prohibited by Master Invariant 10.\n"
        "Use the canonical CLI entrypoint instead: 'python nee.py ingest-meeting ...' or 'python nee.py record-meeting ...'\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
