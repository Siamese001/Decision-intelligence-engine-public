"""Audio-keyed ASR cache for lossless transcription reuse.

Keys acoustic transcription segments strictly by sha256(audio) + model digest,
decoupling acoustic speech-to-text from downstream attendee rosters and prompt text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

CACHE_DIR_DEFAULT = Path(__file__).resolve().parents[2] / "output" / "transcript_cache"


def get_cache_dir(custom_dir: Optional[Path] = None) -> Path:
    """Returns directory used for audio ASR cache sidecars."""
    target = custom_dir or CACHE_DIR_DEFAULT
    target.mkdir(parents=True, exist_ok=True)
    return target


def compute_audio_sha256(audio_path: Union[str, Path]) -> str:
    """Computes SHA-256 digest over raw audio file content."""
    hasher = hashlib.sha256()
    with open(str(audio_path), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_audio_asr_cache_key(
    audio_sha256: str,
    model_name: str,
    language: Optional[str] = None,
    mock: bool = False,
) -> str:
    """Derives a deterministic cache key solely from audio digest and ASR model parameters."""
    raw = f"{audio_sha256[:16]}:{model_name}:{language or 'auto'}:{mock}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_audio_asr_cache(
    audio_sha256: str,
    model_name: str,
    language: Optional[str] = None,
    stem: str = "audio",
    cache_dir: Optional[Path] = None,
    mock: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """Loads raw acoustic segments from disk cache if present and valid."""
    c_dir = get_cache_dir(cache_dir)
    lang_candidates = [language]
    if not language or language == "auto":
        lang_candidates.append("en")
    elif language == "en":
        lang_candidates.append(None)

    target_file: Optional[Path] = None
    for lang_cand in lang_candidates:
        key = build_audio_asr_cache_key(audio_sha256, model_name, lang_cand, mock=mock)
        cand_p = c_dir / f"{stem}_{key}.raw_asr.json"
        if cand_p.is_file():
            target_file = cand_p
            break
        glob_cands = list(c_dir.glob(f"*_{key}.raw_asr.json"))
        if glob_cands:
            target_file = glob_cands[0]
            break
    if target_file is None:
        return None
    try:
        data = json.loads(target_file.read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) > 0:
            logger.info("Audio-keyed ASR cache hit: %s (sha256=%s)", target_file.name, audio_sha256[:12])
            return data
    except Exception as e:
        logger.warning("Corrupted audio ASR cache file %s: %s", target_file, e)
    return None


def save_audio_asr_cache(
    audio_sha256: str,
    raw_segments: List[Dict[str, Any]],
    model_name: str,
    language: Optional[str] = None,
    stem: str = "audio",
    cache_dir: Optional[Path] = None,
    mock: bool = False,
) -> Path:
    """Persists raw acoustic segments to disk cache keyed by audio digest."""
    c_dir = get_cache_dir(cache_dir)
    key = build_audio_asr_cache_key(audio_sha256, model_name, language, mock=mock)
    target_file = c_dir / f"{stem}_{key}.raw_asr.json"
    tmp_file = target_file.with_suffix(".raw_asr.json.tmp")
    tmp_file.write_text(json.dumps(raw_segments, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_file.replace(target_file)
    logger.info("Saved audio-keyed ASR cache: %s (%d segments)", target_file.name, len(raw_segments))
    return target_file


def seed_master_from_chunk_segments(
    chunk_segments: List[Dict[str, Any]],
    master_sha256: str,
    model_name: str,
    language: Optional[str] = None,
    stem: str = "audio",
    cache_dir: Optional[Path] = None,
    mock: bool = False,
) -> Path:
    """Seeds the master audio ASR cache from pre-transcribed live chunks."""
    return save_audio_asr_cache(
        audio_sha256=master_sha256,
        raw_segments=chunk_segments,
        model_name=model_name,
        language=language,
        stem=stem,
        cache_dir=cache_dir,
        mock=mock,
    )


def build_audio_diarization_cache_key(
    audio_sha256: str,
    model_name: str,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> str:
    """Derives a deterministic cache key solely from audio digest and diarization parameters."""
    raw = f"{audio_sha256[:16]}:{model_name}:{min_speakers}:{max_speakers}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_audio_diarization_cache(
    audio_sha256: str,
    model_name: str,
    stem: str = "audio",
    cache_dir: Optional[Path] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, List[float]]]]:
    """Loads acoustic diarization turns and centroids from disk cache if present and valid."""
    c_dir = get_cache_dir(cache_dir)
    key = build_audio_diarization_cache_key(audio_sha256, model_name, min_speakers, max_speakers)
    target_file = c_dir / f"{stem}_{key}.diarization.json"
    if not target_file.is_file():
        candidates = list(c_dir.glob(f"*_{key}.diarization.json"))
        if candidates:
            target_file = candidates[0]
        else:
            return None
    try:
        data = json.loads(target_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "turns" in data:
            logger.info("Audio-keyed diarization cache hit: %s (sha256=%s)", target_file.name, audio_sha256[:12])
            return data["turns"], data.get("centroids", {})
        elif isinstance(data, list):
            logger.info("Audio-keyed diarization cache hit: %s (sha256=%s)", target_file.name, audio_sha256[:12])
            return data, {}
    except Exception as e:
        logger.warning("Corrupted audio diarization cache file %s: %s", target_file, e)
    return None


def save_audio_diarization_cache(
    audio_sha256: str,
    turns: List[Dict[str, Any]],
    model_name: str,
    stem: str = "audio",
    cache_dir: Optional[Path] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    centroids: Optional[Dict[str, List[float]]] = None,
) -> Path:
    """Persists acoustic diarization turns and cluster centroids to disk cache keyed by audio digest."""
    c_dir = get_cache_dir(cache_dir)
    key = build_audio_diarization_cache_key(audio_sha256, model_name, min_speakers, max_speakers)
    target_file = c_dir / f"{stem}_{key}.diarization.json"
    payload = {
        "audio_sha256": audio_sha256,
        "model_name": model_name,
        "turns": turns,
        "centroids": centroids or {},
    }
    tmp_file = target_file.with_suffix(".diarization.json.tmp")
    tmp_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_file.replace(target_file)
    logger.info("Saved audio-keyed diarization cache: %s (%d turns)", target_file.name, len(turns))
    return target_file


