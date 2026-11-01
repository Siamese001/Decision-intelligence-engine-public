"""
dual_pipeline/canonical_pipeline/u0_live_artifacts.py

Auto-hydrates time, title, attendees, speaker_map, speaker_hints, and acoustic
clusters across container directories and transcript caches.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.naming import _DATE_DIR_RE, resolve_meeting_container_dir
from .u0_manifest_discovery import (
    _find_manifest_in_dirs,
    _hydrate_container_attachments,
    _needs_manifest_attendee_enrichment,
    _resolve_live_artifact_dirs,
)
from .u0_speaker_hints import _hydrate_acoustic_receipt, _load_hints_from_dirs
from .u0_time_hydration import _hydrate_time_from_container

logger = logging.getLogger("dual_pipeline.canonical_pipeline.u0_live_artifacts")


def _try_hydrate_voiceprints_file(vp_file: Path, metadata: Dict[str, Any]) -> bool:
    with contextlib.suppress(Exception):
        vp_payload = json.loads(vp_file.read_text(encoding="utf-8"))
        if vp_payload.get("audio_sha256"):
            metadata.setdefault("audio_sha256", vp_payload["audio_sha256"])
        centroids = vp_payload.get("centroids")
        vp_matches = dict(vp_payload.get("matches") or {})
        if isinstance(centroids, dict) and centroids:
            metadata.setdefault("cluster_centroids", centroids)
            metadata.setdefault("cluster_embeddings", centroids)
            from dual_pipeline.audio_ingestion_core.voiceprint_binding import (
                _match_cluster_centroids_against_roster,
            )
            for k, v in _match_cluster_centroids_against_roster(centroids, metadata).items():
                vp_matches.setdefault(k, v)
        if vp_matches:
            spk_map = metadata.setdefault("speaker_map", {})
            for k, v in vp_matches.items():
                spk_map.setdefault(k, v)
            vp_store = metadata.setdefault("voiceprint_matches", {})
            for k, v in vp_matches.items():
                vp_store.setdefault(k, v)
            return True
        if isinstance(centroids, dict) and centroids:
            return True
    return False


def _try_hydrate_cluster_map_file(c_file: Path, metadata: Dict[str, Any]) -> bool:
    with contextlib.suppress(Exception):
        c_map = json.loads(c_file.read_text(encoding="utf-8"))
        if isinstance(c_map, dict) and c_map:
            spk_map = metadata.setdefault("speaker_map", {})
            for k, v in c_map.items():
                spk_map.setdefault(k, v)
            return True
    return False


def _hydrate_sidecar_maps_from_dirs(vp_search: List[Path], metadata: Dict[str, Any]) -> bool:
    hydrated = False
    for s_dir in vp_search:
        if not s_dir.is_dir():
            continue
        for c_file in s_dir.glob("*.cluster_map.json"):
            if _try_hydrate_cluster_map_file(c_file, metadata):
                hydrated = True
        for vp_file in s_dir.glob("*.voiceprints.json"):
            if _try_hydrate_voiceprints_file(vp_file, metadata):
                hydrated = True
    return hydrated


def _derive_speaker_map_from_utterances(metadata: Dict[str, Any]) -> bool:
    if metadata.get("speaker_map") or not metadata.get("structured_utterances_path"):
        return False
    u_p = Path(metadata["structured_utterances_path"])
    if not u_p.is_file():
        return False
    with contextlib.suppress(Exception):
        u_data = json.loads(u_p.read_text(encoding="utf-8"))
        derived_map = {
            str(u.get("raw_speaker") or u.get("voice_id")): str(u.get("speaker"))
            for u in (u_data if isinstance(u_data, list) else [])
            if (u.get("raw_speaker") or u.get("voice_id"))
            and u.get("speaker")
            and str(u.get("raw_speaker") or u.get("voice_id")) != str(u.get("speaker"))
            and str(u.get("speaker")).lower() not in ("unknown", "speaker unknown")
        }
        if derived_map:
            metadata["speaker_map"] = derived_map
            return True
    return False


def _hydrate_speaker_artifacts(
    metadata: Dict[str, Any], search_dirs: Any, slug: str = ""
) -> Tuple[bool, bool, bool]:
    dirs = [Path(search_dirs)] if isinstance(search_dirs, (str, Path)) else [Path(d) for d in (search_dirs or [])]
    hydrated_hints = _load_hints_from_dirs(metadata, dirs)
    hydrated_map = False
    if not metadata.get("speaker_map"):
        from dual_pipeline.transcript_pipeline_core.speaker_map_loader import load_colocated_speaker_map
        target_path = metadata.get("input_path")
        if target_path and Path(str(target_path)).is_file():
            if sidecar_map := load_colocated_speaker_map(Path(str(target_path))):
                metadata["speaker_map"], hydrated_map = dict(sidecar_map), True
        if not hydrated_map:
            for s_dir in dirs:
                if sidecar_map := load_colocated_speaker_map(s_dir):
                    metadata["speaker_map"], hydrated_map = dict(sidecar_map), True
                    break
    if metadata.get("speaker_hints"):
        h_map = {h["voice_id"]: h["speaker_name"] for h in metadata["speaker_hints"] if isinstance(h, dict) and h.get("voice_id") and h.get("speaker_name")}
        if h_map:
            metadata.setdefault("speaker_map", {}).update(h_map)
            hydrated_map = True
    vp_search = list(dirs) + ([Path(metadata["transcript_cache_dir"])] if metadata.get("transcript_cache_dir") else [])
    if _hydrate_sidecar_maps_from_dirs(vp_search, metadata) or _derive_speaker_map_from_utterances(metadata):
        hydrated_map = True
    if metadata.get("voiceprint_matches"):
        spk_map = metadata.setdefault("speaker_map", {})
        for k, v in metadata["voiceprint_matches"].items():
            spk_map.setdefault(k, v)
        hydrated_map = True

    if metadata.get("speaker_map"):
        # Build appearance-order mapping from structured utterances/segments if not already in metadata
        label_map: Dict[str, str] = dict(metadata.get("cluster_label_map") or metadata.get("acoustic_speaker_map") or {})
        if not label_map:
            for path_key in ("structured_utterances_path", "structured_segments_path"):
                p_val = metadata.get(path_key)
                if p_val and Path(str(p_val)).is_file():
                    with contextlib.suppress(Exception):
                        from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
                        segs = json.loads(Path(str(p_val)).read_text(encoding="utf-8"))
                        if isinstance(segs, list):
                            label_map = BaseAudioTranscriber.build_cluster_appearance_map(segs)
                            if label_map:
                                metadata["cluster_label_map"] = label_map
                                break
        rev_label_map: Dict[str, str] = {
            v: k for k, v in label_map.items()
            if isinstance(k, str) and k.startswith("SPEAKER_") and isinstance(v, str) and v.startswith("Speaker ")
        }

        for k, v in list(metadata["speaker_map"].items()):
            if k.startswith("SPEAKER_"):
                if k in label_map and str(label_map[k]).startswith("Speaker "):
                    metadata["speaker_map"].setdefault(label_map[k], v)
                elif not label_map:
                    with contextlib.suppress(Exception):
                        num = int(k.split("_")[1])
                        metadata["speaker_map"].setdefault(f"Speaker {num + 1}", v)
                        if 0 <= num < 26:
                            metadata["speaker_map"].setdefault(f"Voice {chr(ord('A') + num)}", v)
            elif k.startswith("Voice "):
                try:
                    suffix = k[6:].strip()
                    idx = (ord(suffix.upper()) - ord("A")) if (len(suffix) == 1 and suffix.isalpha()) else (int(suffix) if suffix.isdigit() else -1)
                    if idx >= 0:
                        spk_n = f"Speaker {idx + 1}"
                        metadata["speaker_map"].setdefault(spk_n, v)
                        if spk_n in rev_label_map:
                            metadata["speaker_map"].setdefault(rev_label_map[spk_n], v)
                        elif not rev_label_map:
                            metadata["speaker_map"].setdefault(f"SPEAKER_{idx:02d}", v)
                        if 0 <= idx < 26:
                            metadata["speaker_map"].setdefault(f"Voice {chr(ord('A') + idx)}", v)
                except Exception:
                    pass
            elif k.startswith("Speaker "):
                try:
                    num = int(k.split(" ")[1])
                    if k in rev_label_map:
                        metadata["speaker_map"].setdefault(rev_label_map[k], v)
                    elif not rev_label_map:
                        metadata["speaker_map"].setdefault(f"SPEAKER_{num - 1:02d}", v)
                    if 0 <= num - 1 < 26:
                        metadata["speaker_map"].setdefault(f"Voice {chr(ord('A') + num - 1)}", v)
                except Exception:
                    pass
    if metadata.get("speaker_map"):
        from .u0_speaker_hitl_gate import are_all_clusters_resolved, is_generic_or_unknown_label
        all_resolved = are_all_clusters_resolved(metadata["speaker_map"])
        any_resolved = any(
            v and not is_generic_or_unknown_label(v)
            for v in metadata["speaker_map"].values()
        )
        if all_resolved:
            metadata["diarization_status"] = "DIARIZED_ACOUSTIC_RESOLVED"
        elif any_resolved:
            metadata["diarization_status"] = "PARTIAL_ACOUSTIC_RESOLVED"
        else:
            metadata.setdefault("diarization_status", "DIARIZED_ACOUSTIC")
        metadata.setdefault("has_acoustic_diarization", True)
    return hydrated_hints, hydrated_map, _hydrate_acoustic_receipt(metadata, dirs, slug)


def _load_inline_attribution_file(ca_p: Path, metadata: Dict[str, Any], hint_roles: Dict[str, str], hydrated: List[str]) -> None:
    if not ca_p.is_file():
        return
    from .u0_hint_reconciler import extract_role_hints_from_records
    with contextlib.suppress(Exception):
        ca_data = json.loads(ca_p.read_text(encoding="utf-8"))
        hint_roles.update(extract_role_hints_from_records(ca_data))
        if isinstance(ca_data, list) and not metadata.get("inline_chat_attributions"):
            metadata["inline_chat_attributions"] = ca_data
            hydrated.append("inline_chat_attributions")

def _load_copilot_feedback_file(fb_file: Path, metadata: Dict[str, Any], hydrated: List[str]) -> bool:
    if not fb_file.is_file() or metadata.get("copilot_feedback"):
        return False
    with contextlib.suppress(Exception):
        if fb_rows := [json.loads(line) for line in fb_file.read_text(encoding="utf-8").splitlines() if line.strip()]:
            metadata["copilot_feedback"], metadata["copilot_feedback_path"] = fb_rows, str(fb_file)
            hydrated.append("copilot_feedback")
            return True
    return False

def _hydrate_inline_attributions_and_copilot_feedback(
    metadata: Dict[str, Any], search_dirs: List[Path], manifest_data: Dict[str, Any], hint_roles: Dict[str, str], hydrated: List[str]
) -> None:
    for s_dir in search_dirs:
        for attr_fname in ("inline_chat_attributions.json", "chat_attributions.json"):
            _load_inline_attribution_file(s_dir / attr_fname, metadata, hint_roles, hydrated)
        for fb_file in sorted(s_dir.glob("*copilot_feedback*.jsonl")):
            if _load_copilot_feedback_file(fb_file, metadata, hydrated):
                break
    if manifest_data.get("copilot_prefetch") and not metadata.get("copilot_prefetch"):
        metadata["copilot_prefetch"] = manifest_data["copilot_prefetch"]
        hydrated.append("copilot_prefetch")

def _hydrate_entity_resolutions(metadata: Dict[str, Any], target_dir: Optional[Path], hydrated: List[str]) -> None:
    res_file = (target_dir / "entity_resolutions.json") if (target_dir and not metadata.get("entity_resolutions")) else None
    if res_file and res_file.is_file():
        with contextlib.suppress(Exception):
            if isinstance(res_data := json.loads(res_file.read_text(encoding="utf-8")), dict):
                metadata["entity_resolutions"] = res_data
                from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import _merge_explicit_resolutions
                _merge_explicit_resolutions(metadata, metadata.setdefault("entity_clarifications", []))
                hydrated.append("entity_resolutions")

def _is_cached_hydration_valid(metadata: Dict[str, Any], cache_key: str) -> bool:
    return bool(metadata.get("_live_container_hydrated_key") == cache_key and metadata.get("_live_container_hydrated"))


def _resolve_container_slug_and_bind(
    metadata: Dict[str, Any], container_dir: Optional[Path], eff_container: Path, target_path: Any
) -> str:
    from dual_pipeline.meeting_paths import extract_slug_from_meeting_filename
    slug = metadata.get("filename_slug") or (container_dir.name if container_dir else None) or (extract_slug_from_meeting_filename(str(target_path)) if target_path else None)
    if not slug and eff_container and not _DATE_DIR_RE.match(eff_container.name):
        slug = eff_container.name
    if container_dir:
        metadata["container_dir"] = str(container_dir)
        metadata["container_slug"] = container_dir.name
        metadata.setdefault("subfolder", container_dir.name)
        metadata.setdefault("standardized_slug", container_dir.name)
    return str(slug)


def hydrate_live_container_artifacts(metadata: Dict[str, Any], input_path: Union[str, Path, None] = None) -> List[str]:
    """
    Auto-hydrates time, title, attendees (with role/org), speaker_map, speaker_hints,
    and acoustic_clusters across both container_dir and transcript_cache/<project>/<date>/.
    """
    if metadata.get("is_document") or metadata.get("canonical_modality") == "DOCUMENT":
        return []
    target_path = input_path or metadata.get("input_path")
    cache_key = str(target_path or "")
    if _is_cached_hydration_valid(metadata, cache_key):
        return list(metadata.get("_live_container_hydrated_fields", []))
    container_dir = resolve_meeting_container_dir(target_path)
    search_dirs = _resolve_live_artifact_dirs(metadata, target_path, container_dir)
    if not container_dir and not search_dirs:
        return []
    eff_container = container_dir or search_dirs[0]
    slug = _resolve_container_slug_and_bind(metadata, container_dir, eff_container, target_path)
    hydrated: List[str] = []
    manifest_data = _find_manifest_in_dirs(search_dirs, slug)

    if _hydrate_time_from_container(metadata, manifest_data, search_dirs):
        hydrated.append("time")

    if not metadata.get("meeting_name") or metadata.get("meeting_name") == "Meeting":
        name_cand = manifest_data.get("meeting") or manifest_data.get("meeting_name") or manifest_data.get("title")
        if name_cand:
            metadata["meeting_name"] = name_cand
            metadata.setdefault("title", name_cand)
            hydrated.append("meeting_name")

    for field_key, man_keys in (("project", ("project",)), ("date", ("date", "date_dirname", "date_slash"))):
        if not metadata.get(field_key):
            val = next((manifest_data.get(k) for k in man_keys if manifest_data.get(k)), None)
            if val:
                metadata[field_key] = val
                hydrated.append(field_key)

    from dual_pipeline.transcript_metadata_helper import extract_date_from_text

    if not metadata.get("date"):
        for s_dir in search_dirs:
            for p_cand in [s_dir, *s_dir.parents]:
                if re.search(r"20\d{2}", p_cand.name):
                    d_slash, d_hyphen = extract_date_from_text(p_cand.name)
                    metadata["date"] = d_slash
                    metadata["date_slash"] = d_slash
                    metadata["date_filename"] = d_hyphen
                    hydrated.append("date")
                    break
            if metadata.get("date"):
                break
    elif metadata.get("date"):
        d_slash, d_hyphen = extract_date_from_text(str(metadata["date"]))
        metadata["date"] = d_slash
        metadata["date_slash"] = d_slash
        metadata["date_filename"] = d_hyphen

    if not metadata.get("project") and (container_dir or eff_container):
        c_proj = container_dir or eff_container
        parts = c_proj.resolve().parts
        if "output" in parts:
            out_idx = parts.index("output")
            if len(parts) > out_idx + 1:
                proj_cand = parts[out_idx + 1]
                if proj_cand not in ("transcript_cache", "attempts", "canonical", "fixtures"):
                    metadata["project"] = proj_cand
                    hydrated.append("project")

    from .u0_hint_reconciler import extract_manifest_roles, extract_role_hints_from_records

    roles = extract_manifest_roles(metadata.get("attendees"), manifest_data.get("attendees"))
    if not metadata.get("attendees") and manifest_data.get("attendees"):
        metadata["attendees"] = manifest_data["attendees"]
        hydrated.append("attendees")
    if roles:
        metadata.setdefault("pre_enrichment_roles", {}).update(roles)

    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        build_roster_from_attendees,
        extract_attendees_from_header_block,
    )

    if not metadata.get("attendees"):
        for s_dir in search_dirs:
            for t_file in sorted(s_dir.glob("01_*.md")):
                with contextlib.suppress(Exception):
                    t_content = t_file.read_text(encoding="utf-8")
                    hdr_att = extract_attendees_from_header_block(t_content)
                    if hdr_att:
                        metadata["attendees"] = hdr_att
                        metadata["roster"] = build_roster_from_attendees(hdr_att)
                        hydrated.append("attendees")
                        hydrated.append("roster")
                        break
            if metadata.get("attendees") and isinstance(metadata.get("attendees"), dict):
                break

    if metadata.get("attendees"):
        metadata.setdefault("roster", build_roster_from_attendees(metadata["attendees"]))

    h_hints, h_map, h_acoustic = _hydrate_speaker_artifacts(metadata, search_dirs, str(slug))
    if h_hints:
        hydrated.append("speaker_hints")
    hint_roles = extract_role_hints_from_records(metadata.get("speaker_hints"))
    _hydrate_inline_attributions_and_copilot_feedback(metadata, search_dirs, manifest_data, hint_roles, hydrated)
    if hint_roles:
        metadata.setdefault("pre_enrichment_roles", {}).update(hint_roles)
    if h_map:
        hydrated.append("speaker_map")
    if h_acoustic:
        hydrated.append("acoustic_clusters")

    from dual_pipeline.audio_ingestion_core.platform_transcript_fusion import (
        hydrate_companion_platform_transcript,
    )
    hydrate_companion_platform_transcript(metadata, search_dirs, hydrated)

    for s_dir in search_dirs:
        if (s_dir / "record_start_receipt.json").is_file():
            metadata["record_start_receipt_path"] = str(s_dir / "record_start_receipt.json")
            hydrated.append("record_start_receipt")
            break

    att_hydrated = _hydrate_container_attachments(metadata, eff_container)
    hydrated.extend(att_hydrated)
    _hydrate_entity_resolutions(metadata, container_dir or eff_container, hydrated)

    metadata["_live_container_hydrated"] = True
    metadata["_live_container_hydrated_key"] = cache_key
    metadata["_live_container_hydrated_fields"] = list(hydrated)
    logger.info("Hydrated %d live container fields: %s", len(hydrated), hydrated)
    return hydrated


__all__ = [
    "_hydrate_speaker_artifacts",
    "_resolve_live_artifact_dirs",
    "_needs_manifest_attendee_enrichment",
    "_find_manifest_in_dirs",
    "_hydrate_container_attachments",
    "hydrate_live_container_artifacts",
]
