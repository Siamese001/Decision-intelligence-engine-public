"""Acoustic cache validation, loading, and bundle persistence."""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def compute_audio_digest(audio_file_path: str) -> Tuple[str, str]:
    """Computes full SHA-256 and 16-character prefix digest of an audio file."""
    hasher = hashlib.sha256()
    with open(audio_file_path, "rb") as af:
        for chunk in iter(lambda: af.read(4096), b""):
            hasher.update(chunk)
    full_sha256 = hasher.hexdigest()
    return full_sha256, full_sha256[:16]


def resolve_transcript_cache_dir(audio_file_path: str, metadata: Optional[Dict[str, Any]]) -> Path:
    """Determines target directory for transcript cache and associated sidecars."""
    audio_p = Path(audio_file_path).expanduser().resolve()
    cache_dir = Path(__file__).resolve().parents[2] / "output" / "transcript_cache"
    if metadata and metadata.get("transcript_cache_dir"):
        cache_dir = Path(metadata["transcript_cache_dir"])
    elif "transcript_cache" in audio_p.parts or any(audio_p.parent.glob("*.raw_asr.json")):
        cache_dir = audio_p.parent
        if metadata is not None:
            metadata["transcript_cache_dir"] = str(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def build_acoustic_cache_key(
    audio_hash: str,
    model_name: str,
    language: Optional[str],
    without_timestamps: bool,
    mock: bool,
    diarization_engine: Any = None,
    backend: str = "mlx-whisper",
) -> str:
    """Generates deterministic 16-character acoustic cache key."""
    diar_meta = ""
    if diarization_engine:
        d_cls = diarization_engine.__class__.__name__
        d_model = getattr(diarization_engine, "model_id", "pinned")
        d_rev = getattr(diarization_engine, "revision", "default")
        diar_meta = f":{d_cls}:{d_model}:{d_rev}:align_v3"
    backend_suffix = f":{backend}" if backend and backend != "mlx-whisper" else ""
    key_str = f"{audio_hash}:{model_name}:{language}:{without_timestamps}:{mock}{diar_meta}{backend_suffix}"
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]



def _load_optional_utterances(metadata: Dict[str, Any], utterance_file: Path) -> None:
    if not utterance_file.is_file():
        return
    metadata["structured_utterances_path"] = str(utterance_file)
    with contextlib.suppress(Exception):
        u_list = json.loads(utterance_file.read_text(encoding="utf-8"))
        if isinstance(u_list, list):
            metadata["structured_utterances"] = u_list


def _extract_match_name(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return str(v.get("name", ""))
    if isinstance(v, (list, tuple)) and v:
        return str(v[0])
    return str(v)


def _apply_matches_to_speaker_map(
    matches: Dict[str, Any], spk_map: Dict[str, str], label_map: Dict[str, str]
) -> None:
    for k, v in matches.items():
        val = _extract_match_name(v)
        spk_map[k] = val
        mapped = label_map.get(k, "")
        if mapped.startswith("Speaker "):
            spk_map.setdefault(mapped, val)
        elif k.startswith("SPEAKER_") and not label_map:
            with contextlib.suppress(ValueError, IndexError):
                spk_map.setdefault(f"Speaker {int(k.split('_')[1]) + 1}", val)


def _restore_voiceprints_sidecar(
    metadata: Dict[str, Any], vp_file: Path, label_map: Dict[str, str]
) -> None:
    if not vp_file.is_file():
        return
    with contextlib.suppress(Exception):
        vp_data = json.loads(vp_file.read_text(encoding="utf-8"))
        if vp_data.get("audio_sha256"):
            metadata.setdefault("audio_sha256", vp_data["audio_sha256"])
        centroids = vp_data.get("centroids")
        matches = dict(vp_data.get("matches") or {})
        if isinstance(centroids, dict) and centroids:
            metadata["cluster_centroids"] = centroids
            metadata["cluster_embeddings"] = centroids
            from .voiceprint_binding import _match_cluster_centroids_against_roster
            for k, v in _match_cluster_centroids_against_roster(centroids, metadata).items():
                matches.setdefault(k, v)
        if matches:
            metadata["voiceprint_matches"] = dict(matches)
            _apply_matches_to_speaker_map(matches, metadata.setdefault("speaker_map", {}), label_map)
            metadata["diarization_status"] = "DIARIZED_ACOUSTIC_RESOLVED"
            metadata["has_acoustic_diarization"] = True
        else:
            metadata.setdefault("diarization_status", "DIARIZED_ACOUSTIC")


def _restore_cache_metadata(
    metadata: Dict[str, Any],
    cache_file: Path,
    structured_file: Path,
    utterance_file: Path,
    structured_turns: List[Dict[str, Any]],
    seal_meta: Dict[str, Any],
) -> None:
    """Restores sidecar mappings and flags into metadata upon cache hit."""
    metadata.update({
        "structured_segments_path": str(structured_file),
        "structured_segments": structured_turns,
        "turn_count": len(structured_turns),
        "has_acoustic_diarization": True,
    })
    metadata.setdefault("diarization_status", "DIARIZED_ACOUSTIC")
    _load_optional_utterances(metadata, utterance_file)
    if seal_meta.get("sealed"):
        metadata["cache_seal_verified"] = True

    from .base import BaseAudioTranscriber
    appearance_map = BaseAudioTranscriber.build_cluster_appearance_map(structured_turns)
    if appearance_map:
        metadata.setdefault("cluster_label_map", appearance_map)
        metadata.setdefault("acoustic_speaker_map", appearance_map)

    parent_d = cache_file.parent
    stem_pfx = cache_file.name.split(".transcript_cache")[0]
    cluster_map_file = parent_d / f"{stem_pfx}.cluster_map.json"
    if cluster_map_file.is_file():
        with contextlib.suppress(Exception):
            cmap = json.loads(cluster_map_file.read_text(encoding="utf-8"))
            if isinstance(cmap, dict) and cmap:
                metadata["cluster_label_map"] = cmap
                metadata["acoustic_speaker_map"] = cmap
                metadata.setdefault("speaker_map", cmap)

    label_map = metadata.get("cluster_label_map") or appearance_map or {}
    vp_file = structured_file.with_name(structured_file.name.replace(".segments.json", ".voiceprints.json"))
    _restore_voiceprints_sidecar(metadata, vp_file, label_map)


def try_load_transcript_cache(
    cache_file: Path,
    structured_file: Path,
    utterance_file: Path,
    metadata: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Loads and verifies transcript cache and associated structured sidecars."""
    if metadata and metadata.get("force_rerun"):
        logger.info("force_rerun active: bypassing sealed transcript cache %s", cache_file)
        return None
    if not (cache_file.is_file() and structured_file.is_file()):
        return None
    cached = cache_file.read_text(encoding="utf-8")
    if len(cached) <= 50:
        return None

    from dual_pipeline.exceptions import CacheIntegrityViolationError
    from dual_pipeline.transcript_pipeline_core.cache_seal import verify_cache_seal

    try:
        seal_meta = verify_cache_seal(
            structured_file=structured_file,
            cache_file=cache_file,
            utterance_file=utterance_file,
        )
        if seal_meta.get("cache_tier") == "LIVE_PREVIEW" and not (metadata and metadata.get("allow_live_preview_cache")):
            logger.info(
                "Bypassing LIVE_PREVIEW cache %s; authoritative master transcription/diarization required.",
                cache_file.name,
            )
            return None
    except CacheIntegrityViolationError:
        logger.error("Cryptographic transcript cache seal verification failed for %s", structured_file)
        raise
    except Exception as e:
        logger.warning("Corrupted cache file during seal verification; invalidating cache: %s", e)
        return None

    try:
        structured_turns = json.loads(structured_file.read_text(encoding="utf-8"))
        if metadata is not None:
            _restore_cache_metadata(
                metadata=metadata,
                cache_file=cache_file,
                structured_file=structured_file,
                utterance_file=utterance_file,
                structured_turns=structured_turns,
                seal_meta=seal_meta,
            )
        logger.info("Using hash-verified cached transcript and structured turns: %s", cache_file)
        return cached
    except Exception as e:
        logger.warning("Corrupted structured segments sidecar; invalidating cache: %s", e)
        return None


def _is_preview_seal_blocked(s_data: Dict[str, Any], metadata: Optional[Dict[str, Any]]) -> bool:
    return bool(s_data.get("cache_tier") == "LIVE_PREVIEW" and not (metadata and metadata.get("allow_live_preview_cache")))


def _try_load_from_seal_entry(
    cache_dir: Path,
    s_data: Dict[str, Any],
    audio_hash: str,
    full_audio_sha256: str,
    metadata: Optional[Dict[str, Any]],
) -> Optional[str]:
    if _is_preview_seal_blocked(s_data, metadata):
        return None
    cand_sha = s_data.get("audio_sha256", "")
    if not cand_sha or not (cand_sha in (audio_hash, full_audio_sha256) or full_audio_sha256.startswith(cand_sha)):
        return None
    cand_s = cache_dir / s_data.get("segments_file", "")
    cand_c = cache_dir / s_data.get("transcript_cache_file", "")
    cand_u = cache_dir / s_data.get("utterances_file", "")
    if cand_s.is_file() and cand_c.is_file():
        return try_load_transcript_cache(cand_c, cand_s, cand_u, metadata)
    return None


def find_matching_sealed_cache(
    cache_dir: Path,
    audio_hash: str,
    full_audio_sha256: str,
    metadata: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Scans cache directory for a matching sealed cache by audio digest."""
    if metadata and metadata.get("force_rerun"):
        logger.info("force_rerun active: bypassing sealed cache search")
        return None
    for seal_cand in sorted(cache_dir.glob("*.cache_seal.json")):
        try:
            s_data = json.loads(seal_cand.read_text(encoding="utf-8"))
            cached = _try_load_from_seal_entry(cache_dir, s_data, audio_hash, full_audio_sha256, metadata)
            if cached is not None:
                return cached
        except Exception as exc:
            logger.debug("Skipping unreadable cache seal candidate %s: %s", seal_cand, exc)
    return None


def save_transcript_cache_bundle(
    cache_dir: Path,
    safe_stem: str,
    cache_key: str,
    audio_hash: str,
    formatted_text: str,
    structured_turns: List[Dict[str, Any]],
    structured_utterances: List[Dict[str, Any]],
    cluster_centroids: Dict[str, Any],
    voiceprint_matches: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
) -> Optional[Path]:
    """Saves formatted transcript, structured turns, utterances, seal, and voiceprints."""
    from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal
    from .voiceprint_binding import _save_voiceprints_sidecar

    cache_file = cache_dir / f"{safe_stem}_{cache_key}.transcript_cache.json"
    structured_file = cache_dir / f"{safe_stem}_{cache_key}.segments.json"
    utterance_file = cache_dir / f"{safe_stem}_{cache_key}.utterances.json"

    try:
        cache_file.write_text(formatted_text, encoding="utf-8")
        structured_file.write_text(json.dumps(structured_turns, indent=2), encoding="utf-8")
        utterance_file.write_text(json.dumps(structured_utterances, indent=2), encoding="utf-8")

        project = (metadata.get("project") if metadata else None) or "GLOBAL"
        _save_voiceprints_sidecar(
            cache_dir=cache_dir,
            safe_stem=safe_stem,
            cache_key=cache_key,
            audio_hash=audio_hash,
            project=project,
            centroids=cluster_centroids,
            matches=voiceprint_matches,
        )

        seal_file = create_cache_seal(
            structured_file=structured_file,
            cache_file=cache_file,
            utterance_file=utterance_file,
            audio_sha256=audio_hash,
            cache_key=cache_key,
        )

        if metadata is not None:
            metadata["structured_segments_path"] = str(structured_file)
            metadata["structured_utterances_path"] = str(utterance_file)
            metadata["cache_seal_path"] = str(seal_file)
            metadata["turn_count"] = len(structured_turns)
            metadata["utterance_count"] = len(structured_utterances)

        logger.info(
            "Saved local transcript cache to %s, structured turns to %s, and cache seal to %s",
            cache_file, structured_file, seal_file,
        )
        return cache_file
    except Exception as e:
        logger.warning("Failed to save transcript cache: %s", e)
        return None
