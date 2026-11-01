"""Auto-enrollment helper for speaker attribution engine."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from dual_pipeline.transcript_stages_core.constants import _GENERIC_SPEAKER_PATTERN

logger = logging.getLogger(__name__)


def _resolve_cluster_embedding(
    centroids: Dict[str, Any],
    rev_label_map: Dict[str, str],
    cluster_raw: str,
    norm_key: str,
) -> Tuple[str, Optional[Any]]:
    raw_key = str(cluster_raw).strip()
    if not raw_key.startswith("SPEAKER_"):
        mapped = rev_label_map.get(raw_key.lower()) or rev_label_map.get(str(norm_key).strip().lower())
        if mapped:
            raw_key = mapped
    emb = (
        centroids.get(raw_key)
        or centroids.get(cluster_raw)
        or centroids.get(norm_key)
        or centroids.get(f"SPEAKER_{cluster_raw}")
    )
    if emb is None:
        m = re.match(r"^(?:Speaker|Voice)\s+(\d+)$", str(norm_key).strip(), re.IGNORECASE)
        if m:
            idx = int(m.group(1)) - 1
            raw_key = f"SPEAKER_{idx:02d}"
            emb = centroids.get(raw_key) or centroids.get(f"SPEAKER_{idx}")
    return raw_key, emb


def _compute_speaker_duration(metadata: Dict[str, Any], keys: set[str]) -> float:
    segs = metadata.get("structured_utterances") or metadata.get("structured_segments") or []
    if not isinstance(segs, list):
        return 1.0
    total = 0.0
    norm_keys = {k.strip().lower() for k in keys if k}
    for s in segs:
        if not isinstance(s, dict):
            continue
        spk = str(s.get("speaker") or s.get("raw_speaker") or "").strip().lower()
        if spk in norm_keys:
            dur = float(s.get("end", 0.0) or 0.0) - float(s.get("start", 0.0) or 0.0)
            if dur > 0:
                total += dur
    return round(total, 2) if total >= 1.0 else 1.0


def _has_valid_signed_receipt(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    from dual_pipeline.hitl_governance_core.crypto import verify_hitl_approval_signature
    if receipt.get("signature") and isinstance(receipt.get("payload"), dict):
        pub_pem = str(receipt.get("public_key_pem") or "")
        if pub_pem and verify_hitl_approval_signature(receipt["payload"], str(receipt["signature"]), pub_pem):
            return True
    return bool(verify_hitl_approval_signature(receipt))


def _extract_centroid_dict(metadata: Dict[str, Any]) -> Dict[str, Any]:
    raw_c = (
        metadata.get("cluster_centroids")
        or metadata.get("cluster_embeddings")
        or metadata.get("acoustic_clusters")
        or (metadata.get("voiceprints") or {}).get("centroids")
    )
    if not isinstance(raw_c, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for k, v in raw_c.items():
        if isinstance(v, dict):
            emb = v.get("centroid") or v.get("embedding")
            if isinstance(emb, (list, tuple)):
                normalized[str(k)] = list(emb)
        elif isinstance(v, (list, tuple)):
            normalized[str(k)] = list(v)
    return normalized


def _is_speaker_hitl_verified(
    metadata: Dict[str, Any], cluster_raw: str, norm_key: str, canonical_name: str
) -> bool:
    prov_map = metadata.get("speaker_map_provenance")
    if isinstance(prov_map, dict):
        sp_prov = str(prov_map.get(cluster_raw) or prov_map.get(norm_key) or prov_map.get(canonical_name) or "").upper()
        if sp_prov in ("ROSTER_PRIOR", "HEURISTIC", "UNVERIFIED", "LLM_INFERRED"):
            return False
    verified_speakers = metadata.get("hitl_verified_speakers") or metadata.get("verified_speaker_map")
    if isinstance(verified_speakers, (dict, set, list, tuple)):
        if any(k in verified_speakers for k in (cluster_raw, norm_key, canonical_name)):
            return True
    receipt = metadata.get("speaker_attribution_receipt") or metadata.get("hitl_attribution_receipt")
    if _has_valid_signed_receipt(receipt):
        return True
    from dual_pipeline.speaker_attribution_core.collapse_detector import verify_collapse_approval_in_ledger
    return bool(verify_collapse_approval_in_ledger(metadata))


def _resolve_audio_sha(metadata: Dict[str, Any]) -> str:
    return (
        metadata.get("audio_sha256")
        or metadata.get("input_hash")
        or (metadata.get("voiceprints") or {}).get("audio_sha256")
        or hashlib.sha256(str(metadata.get("source_id") or metadata.get("date_filename") or "session").encode("utf-8")).hexdigest()[:16]
    )


def _build_reverse_label_map(metadata: Dict[str, Any]) -> Dict[str, str]:
    label_map = metadata.get("cluster_label_map") or metadata.get("acoustic_speaker_map") or {}
    return {
        str(v).strip().lower(): str(k).strip()
        for k, v in label_map.items()
        if isinstance(k, str) and k.startswith("SPEAKER_") and isinstance(v, str)
    }


def _maybe_update_speaker_priors(metadata: Dict[str, Any], cluster_raw: str, canonical_name: str) -> None:
    priors_path = metadata.get("speaker_priors_path")
    if priors_path is None and not metadata.get("update_speaker_priors"):
        return
    from pathlib import Path
    from dual_pipeline.transcript_metadata_core.speaker_priors_registry import (
        record_verified_session_dynamics,
        register_learned_speaker_alias,
    )
    p_obj = Path(priors_path) if priors_path else None
    register_learned_speaker_alias(str(cluster_raw), str(canonical_name), priors_path=p_obj)
    verified_list = list((metadata.get("speaker_map") or {}).values()) or [canonical_name]
    ordered_turns = metadata.get("ordered_dialogue_turns") or ()
    wpm_map = metadata.get("speaker_wpm") if isinstance(metadata.get("speaker_wpm"), dict) else None
    record_verified_session_dynamics(verified_list, ordered_turns=ordered_turns, speaker_wpm=wpm_map, priors_path=p_obj)


def _maybe_emit_override_learning_event(
    metadata: Dict[str, Any], rej_spk: str, canonical_name: str, cluster_raw: str, ch_prof: str, audio_sha: str
) -> None:
    rt_root = metadata.get("learning_runtime_root") or metadata.get("runtime_root")
    if not rt_root and not metadata.get("emit_audio_learning_events"):
        return
    from pathlib import Path
    from dual_pipeline.transcript_metadata_core.speaker_priors_registry import record_audio_voice_learning_event
    record_audio_voice_learning_event(
        {
            "event_id": f"ovr-{audio_sha[:10]}-{cluster_raw}",
            "source_id": str(metadata.get("source_id") or metadata.get("date_filename") or audio_sha[:12]),
            "parameter_name": str(metadata.get("learning_target_parameter") or "audio_ingestion.voiceprint_similarity_threshold"),
            "rejected_speaker": rej_spk,
            "true_speaker": canonical_name,
            "cluster_id": cluster_raw,
            "channel_profile": ch_prof,
        },
        runtime_root=Path(rt_root) if rt_root else None,
    )


def _infer_cluster_channel_profile(metadata: Dict[str, Any], raw_cluster_key: str, canonical_name: str) -> str:
    ch_map = metadata.get("cluster_channel_profiles") or {}
    if isinstance(ch_map, dict) and raw_cluster_key in ch_map:
        return str(ch_map[raw_cluster_key]).strip().lower()
    op_name = str(metadata.get("operator_name") or metadata.get("ch0_speaker") or "").strip().lower()
    if metadata.get("is_stereo_separated") or metadata.get("stereo_mode"):
        if op_name and canonical_name.strip().lower() == op_name:
            return "stereo_ch0"
        return "stereo_ch1"
    return str(metadata.get("channel_profile") or "mono").strip().lower()


def _enroll_single_verified_cluster(
    store: Any,
    metadata: Dict[str, Any],
    centroids: Dict[str, Any],
    rev_label_map: Dict[str, str],
    enrolled_clusters: set[str],
    cluster_raw: str,
    canonical_name: str,
    normalize_key_fn: Optional[Callable[[str], str]],
    project: str,
    audio_sha: str,
    overrides: Any,
    emb_dim: int,
) -> Optional[Dict[str, Any]]:
    if not canonical_name or _GENERIC_SPEAKER_PATTERN.fullmatch(str(canonical_name).strip()):
        return None
    norm_key = normalize_key_fn(cluster_raw) if normalize_key_fn else cluster_raw
    if not _is_speaker_hitl_verified(metadata, str(cluster_raw), str(norm_key), str(canonical_name)):
        return None
    raw_cluster_key, emb = _resolve_cluster_embedding(centroids, rev_label_map, cluster_raw, norm_key)
    if not isinstance(emb, (list, tuple)) or len(emb) != emb_dim or raw_cluster_key in enrolled_clusters:
        return None
    enrolled_clusters.add(raw_cluster_key)
    dur = _compute_speaker_duration(metadata, {raw_cluster_key, str(cluster_raw), str(norm_key), str(canonical_name)})
    ch_prof = _infer_cluster_channel_profile(metadata, raw_cluster_key, str(canonical_name))
    rej_spk = (overrides.get(cluster_raw) or overrides.get(raw_cluster_key) or overrides.get(norm_key)) if isinstance(overrides, dict) else None
    try:
        if rej_spk and str(rej_spk).strip().lower() != str(canonical_name).strip().lower():
            store.record_negative_voiceprint(
                rejected_speaker=str(rej_spk), embedding=emb, project=project,
                true_speaker=str(canonical_name), audio_sha256=audio_sha,
            )
            _maybe_emit_override_learning_event(metadata, str(rej_spk), str(canonical_name), str(cluster_raw), ch_prof, audio_sha)
        res = store.enroll_voiceprint(
            speaker_name=canonical_name, embedding=emb, project=project, is_verified=True,
            verification_source=f"hitl_attribution:{metadata.get('date_filename', 'session')}",
            audio_sha256=audio_sha, duration_sec=dur, clean_speech_sec=dur, channel_profile=ch_prof,
        )
        _maybe_update_speaker_priors(metadata, str(cluster_raw), str(canonical_name))
        return {"speaker": canonical_name, "cluster": raw_cluster_key, "channel_profile": ch_prof, "status": res.get("action", "ENROLLED")}
    except Exception as inner_exc:
        logger.debug("Skipping cluster %s enrollment: %s", raw_cluster_key, inner_exc)
        return None


def auto_enroll_voiceprints(
    metadata: Dict[str, Any],
    speaker_map: Dict[str, str],
    voiceprint_store: Optional[Any] = None,
    normalize_key_fn: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    """Auto-enrolls verified speaker clusters into VoiceprintStore after resolution and updates SpeakerPriorsRegistry."""
    centroids = _extract_centroid_dict(metadata)
    if not centroids:
        return []
    audio_sha = _resolve_audio_sha(metadata)
    project = (metadata.get("project") or "GLOBAL").strip().upper()
    overrides = metadata.get("hitl_speaker_overrides") or metadata.get("rejected_speaker_map") or {}
    enrolled: List[Dict[str, Any]] = []
    try:
        from dual_pipeline.audio_ingestion_core.voiceprint_store import EMBEDDING_DIM, VoiceprintStore
        store = voiceprint_store or VoiceprintStore(db_path=metadata.get("voiceprint_store_path"))
        rev_label_map = _build_reverse_label_map(metadata)
        enrolled_clusters: set[str] = set()
        for cluster_raw, canonical_name in speaker_map.items():
            entry = _enroll_single_verified_cluster(
                store, metadata, centroids, rev_label_map, enrolled_clusters,
                str(cluster_raw), str(canonical_name), normalize_key_fn,
                project, audio_sha, overrides, EMBEDDING_DIM,
            )
            if entry:
                enrolled.append(entry)
    except Exception as e:
        logger.warning(f"Voiceprint auto-enrollment skipped: {e}")
    return enrolled
