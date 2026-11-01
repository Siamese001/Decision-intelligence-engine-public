"""Evidence preservation and diagnostic utilities for PRF diarization qualification."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_cache_root() -> Path:
    current = Path(__file__).resolve().parents[2]
    if (current / "output/transcript_cache").is_dir() and any((current / "output/transcript_cache").glob("*Prf*")):
        return current
    if current.parent.name == ".worktrees":
        primary = current.parent.parent
        if (primary / "output/transcript_cache").is_dir():
            return primary
    return current


REPO_ROOT = resolve_repo_root()
CACHE_ROOT = resolve_cache_root()


def sha256_file(path: Path) -> str:
    """Computes SHA-256 digest of a file."""
    if not path.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_revision() -> str:
    """Returns current git commit hash."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN_REVISION"


def validate_previous_receipt(prev_path: Path, expected_prev_wave: int) -> Dict[str, Any]:
    """Validates prerequisite previous-wave receipt."""
    if not prev_path.exists():
        raise FileNotFoundError(f"Prerequisite Wave {expected_prev_wave} receipt not found: {prev_path}")

    with open(prev_path, "r", encoding="utf-8") as f:
        prev_data = json.load(f)

    expected_wave_str = f"Wave {expected_prev_wave}"
    if prev_data.get("wave") != expected_wave_str:
        raise ValueError(
            f"Previous receipt wave mismatch: expected '{expected_wave_str}', got '{prev_data.get('wave')}'"
        )

    outcome = prev_data.get("terminal_outcome")
    if outcome not in ("PASS", "QUALIFIED"):
        raise ValueError(f"Previous receipt did not PASS/QUALIFY: outcome is '{outcome}'")

    return {
        "previous_receipt_path": str(prev_path),
        "previous_receipt_digest": sha256_file(prev_path),
        "previous_wave": expected_wave_str,
        "previous_terminal_outcome": outcome,
        "validation_status": "VALIDATED",
    }


def revalidate_manifest(manifest_path: Path) -> Tuple[Dict[str, str], List[str], str]:
    """Revalidates files in evidence manifest against recorded hashes."""
    if not manifest_path.exists():
        return {}, [f"Evidence manifest not found: {manifest_path}"], ""

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    manifest_digest = sha256_file(manifest_path)
    source_hashes: Dict[str, str] = {}
    manifest_errors: List[str] = []

    for item in manifest_data.get("files", []):
        p = Path(item["path"])
        if not p.exists():
            manifest_errors.append(f"Missing file: {p}")
            continue
        actual_digest = sha256_file(p)
        actual_bytes = p.stat().st_size
        if actual_digest != item["sha256"]:
            manifest_errors.append(f"SHA-256 mismatch for {p}: expected {item['sha256']}, got {actual_digest}")
        if actual_bytes != item["bytes"]:
            manifest_errors.append(f"Byte count mismatch for {p}: expected {item['bytes']}, got {actual_bytes}")
        source_hashes[str(p)] = actual_digest

    return source_hashes, manifest_errors, manifest_digest


def _check_staging_pcm_continuity(master_pcm: bytes, staging_chunks: List[Path]) -> Tuple[bool, bool]:
    prefix_match = True
    truncated_105 = False
    offset = 0

    for cpath in staging_chunks:
        with open(cpath, "rb") as cf:
            cdata = cf.read()
        data_idx = cdata.find(b"data")
        pcm_chunk = cdata[data_idx + 8 :] if data_idx != -1 else cdata[44:]
        m_slice = master_pcm[offset : offset + len(pcm_chunk)]
        if m_slice != pcm_chunk[: len(m_slice)]:
            prefix_match = False
            break
        offset += len(pcm_chunk)

    chunk105 = next((c for c in staging_chunks if c.name == "chunk_00105.wav"), None)
    if chunk105:
        try:
            with wave.open(str(chunk105), "rb") as w105:
                if w105.getnframes() > 10000000:
                    truncated_105 = True
        except Exception:
            truncated_105 = True

    return prefix_match, truncated_105


def analyze_channel_and_headers() -> Dict[str, Any]:
    """Diagnoses audio channel configuration, sample rate, and staging chunks."""
    master_wav = CACHE_ROOT / "output/transcript_cache/nea/10-06-2026/00_10-06-2026_Prf_Scope_Sow_Clement_Venkat_master_154116.wav"
    mono_wav = CACHE_ROOT / "output/transcript_cache/00_10-06-2026_Prf_Scope_Sow_Clement_Venk_9233e352db9b_16k_mono.wav"
    staging_dir = CACHE_ROOT / "output/transcript_cache/nea/10-06-2026/.staging_Prf_Scope_Sow_Clement_Venkat_154116"

    if not master_wav.exists():
        return {}

    with wave.open(str(master_wav), "rb") as wm:
        m_frames = wm.getnframes()
        m_rate = wm.getframerate()
        m_channels = wm.getnchannels()
        m_duration = m_frames / m_rate
        master_pcm = wm.readframes(m_frames)

    mono_frames = 0
    mono_duration = 0.0
    if mono_wav.exists():
        with wave.open(str(mono_wav), "rb") as w_mono:
            mono_frames = w_mono.getnframes()
            mono_duration = mono_frames / w_mono.getframerate()

    staging_chunks = sorted(staging_dir.glob("chunk_*.wav")) if staging_dir.exists() else []
    prefix_match, chunk105_trunc = _check_staging_pcm_continuity(master_pcm, staging_chunks)

    return {
        "master_wav_path": str(master_wav),
        "master_sha256": sha256_file(master_wav),
        "channels": m_channels,
        "sample_rate": m_rate,
        "duration_seconds": round(m_duration, 5),
        "normalized_mono_path": str(mono_wav),
        "normalized_mono_duration_seconds": round(mono_duration, 5),
        "duration_parity": m_frames == mono_frames,
        "staging_chunk_count": len(staging_chunks),
        "staging_chunk_pcm_prefix_match": prefix_match,
        "staging_truncated_header_detected": chunk105_trunc,
        "channel_isolation_status": "STEREO_DOWNMIXED_TO_MONO_LOSS_OF_SEPARATION",
    }
