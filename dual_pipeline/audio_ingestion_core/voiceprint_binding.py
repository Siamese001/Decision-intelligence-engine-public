"""Attendee roster flattening, voiceprint centroid extraction, and identity binding."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _clean_attendee_entry(raw: Any) -> str:
    """Cleans an attendee string entry by stripping surrounding whitespace."""
    if raw is None:
        return ""
    return str(raw).strip()


def _split_attendee_string(att: str) -> List[str]:
    """Splits a comma-delimited attendee string while preserving commas inside parentheses."""
    if not att:
        return []
    parts = re.split(r",\s*(?![^()]*\))", att)
    result: List[str] = []
    for p in parts:
        cleaned = _clean_attendee_entry(p)
        if cleaned:
            result.append(cleaned)
    return result


def _flatten_attendee_roster(metadata: Optional[Dict[str, Any]]) -> List[str]:
    """Extracts and flattens attendee names from string, list, or org-grouped dict formats.

    Preserves parenthetical annotations for accurate downstream attribution.
    """
    if not metadata or not isinstance(metadata, dict):
        return []
    att = metadata.get("attendees")
    if not att:
        att = metadata.get("roster", [])
    if isinstance(att, str):
        return _split_attendee_string(att)
    if isinstance(att, dict):
        flattened: List[str] = []
        for k, v in att.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        name = item.get("name")
                        if name:
                            flattened.append(_clean_attendee_entry(name))
                    elif item:
                        flattened.append(_clean_attendee_entry(item))
            elif isinstance(v, str):
                flattened.extend(_split_attendee_string(v))
            elif v:
                flattened.append(_clean_attendee_entry(k))
        return [x for x in flattened if x]
    if isinstance(att, list):
        flattened_list: List[str] = []
        for item in att:
            if isinstance(item, dict):
                name = item.get("name")
                if name:
                    flattened_list.append(_clean_attendee_entry(name))
            elif isinstance(item, str) or item:
                flattened_list.append(_clean_attendee_entry(item))
        return [x for x in flattened_list if x]
    return []


def _resolve_channel_profile(metadata: Dict[str, Any]) -> str:
    raw = metadata.get("channel_profile") or ("stereo" if metadata.get("is_stereo_separated") else "mono")
    return str(raw).strip().lower()


def _match_cluster_centroids_against_roster(
    cluster_centroids: Dict[str, Sequence[float]],
    metadata: Optional[Dict[str, Any]],
    threshold: float = 0.82,
    margin_threshold: Optional[float] = None,
) -> Dict[str, str]:
    """Scores cluster centroids against enrolled voiceprints across projects with roster priority (D-REC-02)."""
    if not cluster_centroids or not metadata:
        return {}
    attendees = _flatten_attendee_roster(metadata)
    include_global = bool(metadata.get("allow_global_voiceprint_fallback", False))
    if not attendees and not include_global:
        return {}

    from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
    eff_margin = float(margin_threshold) if margin_threshold is not None else float(get_hitl_ambiguity_margin_threshold())

    roster_candidates: List[str] = list(attendees)
    for a in attendees:
        stripped = re.sub(r"\s*\([^)]*\)", "", a).strip()
        if stripped and stripped not in roster_candidates:
            roster_candidates.append(stripped)

    matches: Dict[str, str] = {}
    try:
        from .voiceprint_store import VoiceprintStore
        store = VoiceprintStore(db_path=metadata.get("voiceprint_store_path"))
        roster_matches = store.match_roster(
            cluster_centroids,
            roster_names=roster_candidates,
            project=metadata.get("project") or "GLOBAL",
            threshold=threshold,
            margin_threshold=eff_margin,
            include_global_fallback=include_global,
            channel_profile=_resolve_channel_profile(metadata),
        )
        for cid, m_info in roster_matches.items():
            if m_info.get("is_decisive") and m_info.get("canonical_name"):
                matches[cid] = str(m_info["canonical_name"])
    except Exception as e:
        logger.warning("Voiceprint roster matching error: %s", e)
    return matches


def _populate_metadata_voiceprint_maps(
    metadata: Dict[str, Any],
    segments: List[Dict[str, Any]],
    centroids: Dict[str, List[float]],
    matches: Dict[str, str],
) -> None:
    from .base import BaseAudioTranscriber
    appearance_map = BaseAudioTranscriber.build_cluster_appearance_map(segments)
    if appearance_map:
        metadata.setdefault("cluster_label_map", appearance_map)
        metadata.setdefault("acoustic_speaker_map", appearance_map)
    label_map = metadata.get("cluster_label_map") or appearance_map or {}
    if centroids:
        metadata["cluster_centroids"] = centroids
    if not matches:
        return
    metadata["voiceprint_matches"] = matches
    spk_map = metadata.setdefault("speaker_map", {})
    for k, v in matches.items():
        spk_map[k] = v
        if k in label_map and str(label_map[k]).startswith("Speaker "):
            spk_map.setdefault(label_map[k], v)
        elif k.startswith("SPEAKER_") and not label_map:
            try:
                spk_map.setdefault(f"Speaker {int(k.split('_')[1]) + 1}", v)
            except (ValueError, IndexError):
                pass
    status = "DIARIZED_ACOUSTIC_RESOLVED" if (centroids and len(matches) >= len(centroids)) else "PARTIAL_ACOUSTIC_RESOLVED"
    metadata.setdefault("diarization_status", status)
    metadata.setdefault("has_acoustic_diarization", True)


def _extract_and_bind_voiceprints(
    diarization_engine: Any,
    audio_path: str,
    segments: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    mock: bool = False,
) -> Tuple[Dict[str, List[float]], Dict[str, str]]:
    """Extracts cluster centroids and binds decisive voiceprint matches to metadata."""
    centroids: Dict[str, List[float]] = {}
    matches: Dict[str, str] = {}
    if diarization_engine is not None and not mock:
        try:
            centroids = diarization_engine.extract_cluster_centroids(audio_path, segments)
        except Exception as e:
            logger.debug("Cluster centroid extraction diagnostic: %s", e)
    elif mock or (metadata and metadata.get("mock_cluster_centroids")):
        if metadata and metadata.get("mock_cluster_centroids"):
            centroids = dict(metadata["mock_cluster_centroids"])
        else:
            from .voiceprint_store import generate_deterministic_embedding
            clusters = sorted(set(s.get("speaker", "SPEAKER_00") for s in segments))
            stem = Path(audio_path).stem
            centroids = {spk: generate_deterministic_embedding(f"{stem}:{spk}", dim=256) for spk in clusters}

    if centroids and metadata:
        matches = _match_cluster_centroids_against_roster(centroids, metadata)
        if metadata.get("voiceprint_matches"):
            matches.update(metadata["voiceprint_matches"])

    if metadata is not None:
        _populate_metadata_voiceprint_maps(metadata, segments, centroids, matches)
    return centroids, matches


def _save_voiceprints_sidecar(
    cache_dir: Path,
    safe_stem: str,
    cache_key: str,
    audio_hash: str,
    project: str,
    centroids: Dict[str, List[float]],
    matches: Dict[str, str],
) -> Optional[Path]:
    """Writes *.voiceprints.json sidecar alongside transcript cache."""
    try:
        vp_file = cache_dir / f"{safe_stem}_{cache_key}.voiceprints.json"
        vp_payload = {
            "audio_sha256": audio_hash,
            "project": project,
            "centroids": centroids,
            "matches": matches,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        vp_file.write_text(json.dumps(vp_payload, indent=2), encoding="utf-8")
        return vp_file
    except Exception as e:
        logger.warning("Failed to save voiceprints sidecar: %s", e)
        return None


def handle_diarization_collapse(
    sanitized_segments: List[Dict[str, Any]],
    diarization: Any,
    metadata: Optional[Dict[str, Any]],
    error: Exception,
    align_func: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Handles DiarizationCollapseError, sets HITL flags, and attempts best-effort alignment."""
    logger.warning("Diarization collapse detected: %s. Emitting HITL_REQUIRED status.", error)
    if metadata is not None:
        metadata["diarization_status"] = "HITL_REQUIRED"
        metadata["hitl_required"] = True
        metadata["diarization_error"] = str(error)
        metadata["single_speaker_collapse"] = True
        evidence = getattr(error, "evidence", None)
        if evidence:
            metadata["collapse_evidence"] = evidence

    if diarization is not None:
        aligned_collapse = None
        if align_func is not None:
            try:
                aligned_collapse = align_func(sanitized_segments, diarization)
            except Exception as align_err:
                logger.warning("Failed to align segments with diarization on collapse: %s", align_err)
        if aligned_collapse is not None and not any(
            str(s.get("speaker") or "").strip().lower() in ("", "unknown", "speaker unknown", "speaker")
            for s in aligned_collapse
        ):
            return aligned_collapse
        try:
            from .speaker_alignment import extract_acoustic_intervals
            from .whisperx_aligner import assign_speakers_with_whisperx
            intervals = extract_acoustic_intervals(diarization)
            if intervals:
                return assign_speakers_with_whisperx(
                    sanitized_segments,
                    intervals,
                    fill_nearest=True,
                    lock_sentence_speaker=False,
                )
        except Exception as wx_err:
            logger.warning("Fallback WhisperX speaker assignment failed on collapse: %s", wx_err)
        if aligned_collapse is not None:
            return aligned_collapse

    for s in sanitized_segments:
        if not s.get("speaker"):
            s["speaker"] = "Speaker"
    return sanitized_segments

