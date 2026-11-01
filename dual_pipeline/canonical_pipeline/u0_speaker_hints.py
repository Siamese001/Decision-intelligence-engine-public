"""
dual_pipeline/canonical_pipeline/u0_speaker_hints.py

Speaker hint ingestion, acoustic evidence receipt hydration, and pre-HITL hint resolution.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.canonical_pipeline.u0_speaker_hints")


def _hint_dedup_key(h: Dict[str, Any]) -> Any:
    return h.get("event_id") or (
        h.get("speaker_name") or h.get("speaker"),
        h.get("quote") or h.get("quote_text"),
    )


def _load_hints_from_dirs(metadata: Dict[str, Any], search_dirs: List[Path]) -> bool:
    hydrated_hints = False
    all_hints = list(metadata.get("speaker_hints") or [])
    seen_keys = {_hint_dedup_key(h) for h in all_hints if isinstance(h, dict)}
    cand_dirs = [
        d
        for s in search_dirs
        for d in ([s, *(s / sub for sub in (".cache", "referenced_artifacts", "attachments") if (s / sub).is_dir())])
    ]
    for c_dir in cand_dirs:
        for fn in ("speaker_hints.json", "speaker_hints_queue.json"):
            h_file = c_dir / fn
            if not h_file.is_file():
                continue
            with contextlib.suppress(Exception):
                data = json.loads(h_file.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else (data.get("hints") or data.get("queue") or [])
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and str(it.get("status", "")).upper() not in (
                            "EXPIRED",
                            "REJECTED",
                            "DROPPED",
                        ):
                            if (dk := _hint_dedup_key(it)) not in seen_keys:
                                seen_keys.add(dk)
                                all_hints.append(it)
                    hydrated_hints = True
    if all_hints:
        metadata["speaker_hints"] = all_hints
        turn_hints = list(metadata.get("turn_hints") or [])
        seen_th = {_hint_dedup_key(th) for th in turn_hints if isinstance(th, dict)}
        for h in all_hints:
            if isinstance(h, dict) and (q := (h.get("quote") or h.get("quote_text"))) and (
                spk := (h.get("speaker_name") or h.get("speaker"))
            ):
                if (th_key := (h.get("event_id") or (spk, q))) not in seen_th:
                    seen_th.add(th_key)
                    turn_hints.append({
                        "event_id": h.get("event_id"),
                        "quote": q,
                        "speaker": spk,
                        "speaker_name": spk,
                        "voice_id": h.get("voice_id"),
                        "status": h.get("status", "QUEUED"),
                        "turn_only": bool(h.get("turn_only", False)),
                    })
        if turn_hints:
            metadata["turn_hints"] = turn_hints
            hydrated_hints = True
    return hydrated_hints


def _hydrate_acoustic_receipt(metadata: Dict[str, Any], search_dirs: List[Path], slug: str) -> bool:
    for s_dir in search_dirs:
        candidates = sorted(s_dir.glob("*acoustic_evidence_receipt.json"))
        matching = [c for c in candidates if slug and slug.lower() in c.name.lower()]
        for rc_file in (matching or candidates):
            with contextlib.suppress(Exception):
                rc_data = json.loads(rc_file.read_text(encoding="utf-8"))
                if isinstance(rc_data, dict):
                    metadata["acoustic_evidence_receipt_path"] = str(rc_file)
                    metadata["acoustic_evidence_receipt"] = rc_data
                    if (clusters := (rc_data.get("clusters") or rc_data.get("voice_clusters"))) is not None:
                        metadata["acoustic_clusters"] = clusters
                    return True
    return False


def _replace_raw_transcript_speaker_aliases(metadata: Dict[str, Any], cluster_map: Dict[str, str]) -> None:
    raw_transcript = metadata.get("raw_transcript")
    eff_replacements = {**dict(metadata.get("speaker_map") or {}), **cluster_map}
    if not raw_transcript or not eff_replacements:
        return
    for clus, name in eff_replacements.items():
        if clus and name and str(clus) != str(name):
            raw_transcript = re.sub(rf"\b{re.escape(str(clus))}\b", str(name), raw_transcript)
    metadata["raw_transcript"] = raw_transcript


def _compute_utterance_resolution_status(utterances: List[Dict[str, Any]], s_map: Dict[str, Any]) -> str:
    from .u0_speaker_hitl_gate import are_all_clusters_resolved, is_generic_or_unknown_label
    active_utt_clusters = {
        str(u.get("raw_speaker") or u.get("speaker") or "").strip()
        for u in utterances
        if isinstance(u, dict) and (u.get("raw_speaker") or u.get("speaker"))
    }
    all_utt_resolved = all(
        not is_generic_or_unknown_label(u.get("speaker"))
        for u in utterances
        if isinstance(u, dict) and u.get("speaker")
    ) and are_all_clusters_resolved(s_map, required_clusters=active_utt_clusters)
    return "DIARIZED_ACOUSTIC_RESOLVED" if all_utt_resolved else "PARTIAL_ACOUSTIC_RESOLVED"


def resolve_pre_hitl_speaker_hints(metadata: Dict[str, Any]) -> Dict[str, str]:
    """Resolves speaker clusters to attendee names via quote hints and enrolled voiceprints before U0 preflight checks."""
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_utterances

    hints = metadata.get("turn_hints") or metadata.get("speaker_hints")
    if not hints:
        container = metadata.get("container_dir")
        if container:
            cand_p = Path(container) / "speaker_hints_queue.json"
            if cand_p.is_file():
                with contextlib.suppress(Exception):
                    hints = json.loads(cand_p.read_text(encoding="utf-8"))
                    metadata["speaker_hints"] = hints

    utterances = metadata.get("structured_utterances")
    u_path = metadata.get("structured_utterances_path")
    if not utterances and u_path:
        p_u = Path(u_path)
        if p_u.is_file():
            with contextlib.suppress(Exception):
                utterances = json.loads(p_u.read_text(encoding="utf-8"))
                metadata["structured_utterances"] = utterances

    cluster_map: Dict[str, str] = {}
    label_map: Dict[str, str] = dict(metadata.get("cluster_label_map") or metadata.get("acoustic_speaker_map") or {})
    if not label_map and utterances and isinstance(utterances, list):
        from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
        label_map = BaseAudioTranscriber.build_cluster_appearance_map(utterances)
        if label_map:
            metadata["cluster_label_map"] = label_map

    if hints and utterances and isinstance(utterances, list):
        utterances, cluster_map = apply_quote_hints_to_utterances(utterances, hints)
        if cluster_map:
            s_map = metadata.setdefault("speaker_map", {})
            for ck, cv in cluster_map.items():
                s_map[ck] = cv
                if ck in label_map and str(label_map[ck]).startswith("Speaker "):
                    s_map[label_map[ck]] = cv
            metadata["diarization_status"] = _compute_utterance_resolution_status(utterances, s_map)
            metadata["has_acoustic_diarization"] = True
            logger.info("[U0-PREFLIGHT] Resolved speaker clusters from quote hints: %s (status=%s)", cluster_map, metadata["diarization_status"])
            if u_path and Path(u_path).is_file():
                try:
                    Path(u_path).write_text(json.dumps(utterances, indent=2), encoding="utf-8")
                    from dual_pipeline.transcript_pipeline_core.cache_seal import (
                        create_cache_seal,
                        find_associated_sidecars,
                    )
                    s_file, c_file, _, seal_file = find_associated_sidecars(u_path)
                    if s_file and s_file.is_file():
                        audio_sha = metadata.get("audio_sha256", "")
                        cache_key = ""
                        if seal_file and seal_file.is_file():
                            with contextlib.suppress(Exception):
                                s_data = json.loads(seal_file.read_text(encoding="utf-8"))
                                audio_sha = audio_sha or s_data.get("audio_sha256", "")
                                cache_key = s_data.get("cache_key", "")
                        create_cache_seal(
                            structured_file=s_file,
                            cache_file=c_file,
                            utterance_file=Path(u_path),
                            audio_sha256=audio_sha,
                            cache_key=cache_key,
                        )
                        stem_pfx = s_file.name.split(".segments")[0]
                        c_map_file = s_file.parent / f"{stem_pfx}.cluster_map.json"
                        c_map_file.write_text(json.dumps(metadata.get("speaker_map", {}), indent=2), encoding="utf-8")
                except Exception as e:
                    logger.debug("Failed updating structured utterances on disk: %s", e)

    vp_matches = metadata.get("voiceprint_matches")
    if vp_matches and isinstance(vp_matches, dict):
        for clus, match_data in vp_matches.items():
            if isinstance(match_data, (tuple, list)) and len(match_data) >= 2:
                name, score = match_data[0], float(match_data[1])
                if score >= 0.82 and name:
                    cluster_map[clus] = name
                    metadata.setdefault("speaker_map", {})[clus] = name

    raw_transcript = metadata.get("raw_transcript")
    if raw_transcript and hints and isinstance(hints, list):
        from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import apply_quote_hints_to_turns
        raw_transcript, _ = apply_quote_hints_to_turns(raw_transcript, hints)
        metadata["raw_transcript"] = raw_transcript
    _replace_raw_transcript_speaker_aliases(metadata, cluster_map)

    return cluster_map


__all__ = [
    "_hint_dedup_key",
    "_load_hints_from_dirs",
    "_hydrate_acoustic_receipt",
    "resolve_pre_hitl_speaker_hints",
]
