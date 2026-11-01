"""
dual_pipeline/live_meeting_recorder_core/stage0_speaker_preloader.py

Wave 5 Stage-0 Speaker Preloader (<150ms budget at `start recording`).
Resolves meeting attendees from ReadOnlyGraphSnapshot or session metadata,
binds Ch1 to the configured operator (removing hardcoded 'amit' assumptions),
preloads verified 256-D voiceprints from canonical VoiceprintStore (D-REC-02),
and bounds expected speaker counts (min_speakers, max_speakers).
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.stage0_speaker_preloader")


@dataclass
class Stage0SpeakerPreloadResult:
    """Structured Stage-0 preload package produced at `start recording`."""
    operator_name: str
    ch1_speaker: str
    roster_names: List[str] = field(default_factory=list)
    enrolled_centroids: Dict[str, List[float]] = field(default_factory=dict)
    global_prior_centroids: Dict[str, List[float]] = field(default_factory=dict)
    inferred_candidates: List[Dict[str, Any]] = field(default_factory=list)
    min_speakers: int = 1
    max_speakers: int = 8
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



def _extract_attendee_name(item: Any) -> str:
    return str(item.get("name") if isinstance(item, dict) else item or "").strip()


def _find_host_role_name(attendees: Sequence[Any]) -> Optional[str]:
    for item in attendees:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        name = str(item.get("name") or "").strip()
        if name and role in ("host", "operator", "organizer", "local"):
            return name
    return None


def _resolve_operator_name(
    attendees: Sequence[Any],
    explicit_operator: Optional[str] = None,
) -> str:
    """Resolves local Ch1 operator name from explicit arg, NEE_OPERATOR_NAME, or roster role."""
    if explicit_operator and explicit_operator.strip():
        return explicit_operator.strip()
    from storage.credentials import CredentialManager
    env_op = (CredentialManager.get_credential("NEE_OPERATOR_NAME") or "").strip()
    if env_op:
        return env_op
    host_name = _find_host_role_name(attendees)
    if host_name:
        return host_name
    for item in attendees:
        name = _extract_attendee_name(item)
        if name:
            return name
    return "Operator"


def _query_graph_roster_candidates(
    project: Optional[str],
    meeting_title: Optional[str],
    graph_db_path: Optional[Union[str, Path]] = None,
) -> List[str]:
    """Queries ReadOnlyGraphSnapshot for project/meeting person nodes within a tight <50ms budget."""
    if not graph_db_path:
        return []
    db_p = Path(graph_db_path)
    if not db_p.is_file():
        return []
    names: List[str] = []
    try:
        from storage.connection import create_sqlite_conn
        with create_sqlite_conn(db_p, readonly=True) as conn:
            cur = conn.execute(
                "SELECT canonical_name FROM node WHERE node_type IN ('person', 'Person', 'STAKEHOLDER') LIMIT 25;"
            )
            for row in cur.fetchall():
                val = str(row[0] or "").strip()
                if val and val not in names:
                    names.append(val)
    except Exception as exc:
        logger.debug("Stage-0 graph roster lookup skipped: %s", exc)
    return names


def _get_store_vec(store: Any, name: str, proj: str, dim: int) -> Optional[List[float]]:
    vp = store.get_voiceprint(name, project=proj)
    emb = vp.get("embedding") if vp else None
    if isinstance(emb, list) and len(emb) == dim:
        return [float(x) for x in emb]
    return None


def _load_voiceprint_centroids(
    roster_names: List[str],
    project: str,
    voiceprint_db_path: Optional[Union[str, Path]],
) -> tuple[Dict[str, List[float]], Dict[str, List[float]]]:
    enrolled_centroids: Dict[str, List[float]] = {}
    global_prior_centroids: Dict[str, List[float]] = {}
    try:
        from dual_pipeline.audio_ingestion_core.voiceprint_store import EMBEDDING_DIM, VoiceprintStore
        store = VoiceprintStore(db_path=voiceprint_db_path)
        for r_name in roster_names:
            vec = _get_store_vec(store, r_name, project, EMBEDDING_DIM)
            if vec:
                enrolled_centroids[r_name] = vec
        for spk_meta in store.get_enrolled_speakers():
            c_name = str(spk_meta.get("canonical_name") or "").strip()
            if not c_name or c_name in enrolled_centroids:
                continue
            vec = _get_store_vec(store, c_name, spk_meta.get("project") or "GLOBAL", EMBEDDING_DIM)
            if vec:
                global_prior_centroids[c_name] = vec
    except Exception as exc:
        logger.debug("Stage-0 voiceprint preload diagnostic: %s", exc)
    return enrolled_centroids, global_prior_centroids


def _extract_chunk_embedding(chunk: Any, sr: int) -> Optional[List[float]]:
    import numpy as np
    from dual_pipeline.live_meeting_recorder_core.acoustic_features import extract_256d_acoustic_embedding
    if float(np.sqrt(np.mean(chunk ** 2))) < 0.012:
        return None
    emb = extract_256d_acoustic_embedding(chunk, sr=sr, max_duration_sec=2.0)
    return [float(x) for x in emb.tolist()] if (len(emb) == 256 and float(np.linalg.norm(emb)) > 1e-6) else None


def _extract_preflight_embeddings_from_wav(
    audio_path: Optional[Union[str, Path]],
    max_windows: int = 8,
) -> List[List[float]]:
    if not audio_path or not Path(str(audio_path)).is_file():
        return []
    embeddings: List[List[float]] = []
    try:
        from dual_pipeline.live_meeting_recorder_core.acoustic_features import _read_wav_data
        data, sr = _read_wav_data(Path(str(audio_path)), max_duration_sec=16.0)
        if data is None or sr <= 0 or len(data) == 0:
            return []
        win_len = max(int(sr * 2.0), 512)
        for start in range(0, len(data), win_len):
            chunk = data[start : start + win_len]
            if len(chunk) < int(sr * 0.5) or len(embeddings) >= max_windows:
                break
            vec = _extract_chunk_embedding(chunk, sr)
            if vec is not None:
                embeddings.append(vec)
    except Exception as exc:
        logger.debug("Preflight WAV window extraction skipped: %s", exc)
    return embeddings


def _merge_profile_interlocutors(co_map: Dict[str, Dict[str, int]], key: str, prof: Any) -> None:
    is_dict = isinstance(prof, dict)
    c_name = str((prof.get("name") or prof.get("canonical_name")) if is_dict else getattr(prof, "name", None) or key).strip().lower()
    inter = (prof.get("frequent_interlocutors") or prof.get("common_interlocutors")) if is_dict else getattr(prof, "frequent_interlocutors", None)
    if c_name and isinstance(inter, dict) and inter:
        co_map.setdefault(c_name, {}).update({str(k).strip().lower(): int(v) for k, v in inter.items() if v})


def _load_co_attendance_map(
    speaker_memory_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Dict[str, int]]:
    co_map: Dict[str, Dict[str, int]] = {}
    try:
        from dual_pipeline.speaker_memory import SpeakerMemory
        mem = SpeakerMemory(memory_path=Path(speaker_memory_path) if speaker_memory_path else None)
        prof_dict = getattr(mem, "profiles", None) or getattr(mem, "_profiles", {})
        for key, prof in prof_dict.items():
            _merge_profile_interlocutors(co_map, str(key), prof)
        if speaker_memory_path and Path(speaker_memory_path).is_file():
            import json
            raw = json.loads(Path(speaker_memory_path).read_text(encoding="utf-8"))
            entries = raw.get("profiles", raw) if isinstance(raw, dict) else {}
            for key, pdata in (entries.items() if isinstance(entries, dict) else ()):
                _merge_profile_interlocutors(co_map, str(key), pdata)
    except Exception as exc:
        logger.debug("Co-attendance map load skipped: %s", exc)
    return co_map


def _score_enrolled_candidate_against_windows(
    vec: Sequence[float],
    windows: Sequence[Sequence[float]],
) -> float:
    import math
    v_norm = math.sqrt(sum(float(x) * float(x) for x in vec))
    if v_norm < 1e-9:
        return 0.0
    best_sim = 0.0
    for win in windows:
        if len(win) == len(vec):
            w_norm = math.sqrt(sum(float(y) * float(y) for y in win))
            if w_norm > 1e-9:
                sim = sum(float(a) * float(b) for a, b in zip(vec, win)) / (v_norm * w_norm)
                if sim > best_sim:
                    best_sim = sim
    return round(best_sim, 4)


def _compute_co_attendance_boost(
    cand_name: str,
    anchor_names: Sequence[str],
    co_map: Dict[str, Dict[str, int]],
    graph_roster: Sequence[str],
) -> float:
    c_low = cand_name.strip().lower()
    boost = 0.0
    cand_links = co_map.get(c_low, {})
    for anchor in anchor_names:
        a_low = anchor.strip().lower()
        if a_low != c_low and (cand_links.get(a_low, 0) > 0 or co_map.get(a_low, {}).get(c_low, 0) > 0):
            boost += 0.06
            break
    if any(g.strip().lower() == c_low for g in graph_roster):
        boost += 0.04
    return round(min(0.10, boost), 4)


def _scan_store_for_preflight_hits(
    windows: Sequence[Sequence[float]], project: str, voiceprint_db_path: Optional[Union[str, Path]], similarity_floor: float
) -> List[Dict[str, Any]]:
    raw_hits: List[Dict[str, Any]] = []
    try:
        from dual_pipeline.audio_ingestion_core.voiceprint_store import EMBEDDING_DIM, VoiceprintStore
        store = VoiceprintStore(db_path=voiceprint_db_path)
        seen: set[str] = set()
        for spk_meta in store.get_enrolled_speakers():
            c_name = str(spk_meta.get("canonical_name") or "").strip()
            s_proj = str(spk_meta.get("project") or "GLOBAL").strip()
            if not c_name or c_name.lower() in seen:
                continue
            vec = _get_store_vec(store, c_name, project, EMBEDDING_DIM) or _get_store_vec(store, c_name, s_proj, EMBEDDING_DIM)
            sim = _score_enrolled_candidate_against_windows(vec, windows) if vec else 0.0
            if sim >= similarity_floor:
                seen.add(c_name.lower())
                raw_hits.append({"name": c_name, "acoustic_similarity": sim, "project": s_proj})
    except Exception as exc:
        logger.debug("Acoustic roster preflight scan skipped: %s", exc)
    return raw_hits


def run_acoustic_roster_preflight(
    audio_path: Optional[Union[str, Path]] = None,
    sample_embeddings: Optional[Sequence[Sequence[float]]] = None,
    project: str = "GLOBAL",
    voiceprint_db_path: Optional[Union[str, Path]] = None,
    speaker_memory_path: Optional[Union[str, Path]] = None,
    graph_db_path: Optional[Union[str, Path]] = None,
    known_attendees: Optional[Sequence[Any]] = None,
    similarity_floor: float = 0.72,
) -> List[Dict[str, Any]]:
    """Scans initial high-SNR audio windows against VoiceprintStore and co-attendance priors."""
    windows = [list(w) for w in (sample_embeddings or []) if len(w) == 256] or (_extract_preflight_embeddings_from_wav(audio_path) if audio_path else [])
    if not windows:
        return []
    raw_hits = _scan_store_for_preflight_hits(windows, project, voiceprint_db_path, similarity_floor)
    if not raw_hits:
        return []
    graph_roster = _query_graph_roster_candidates(project, None, graph_db_path)
    co_map = _load_co_attendance_map(speaker_memory_path)
    known_list = [_extract_attendee_name(a) for a in (known_attendees or []) if _extract_attendee_name(a)]
    anchor_names = known_list + [h["name"] for h in raw_hits if h["acoustic_similarity"] >= 0.80]
    results = [
        {
            "name": hit["name"],
            "confidence": round(min(0.98, max(0.55, hit["acoustic_similarity"] + (boost := _compute_co_attendance_boost(hit["name"], anchor_names, co_map, graph_roster)))), 4),
            "acoustic_similarity": hit["acoustic_similarity"],
            "co_attendance_boost": boost,
            "project": hit["project"],
        }
        for hit in raw_hits
    ]
    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def _dedupe_roster_names(raw_att: Sequence[Any], inferred: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for a in raw_att:
        n = _extract_attendee_name(a)
        if n and n not in names:
            names.append(n)
    for inf in inferred:
        if inf["name"] not in names:
            names.append(inf["name"])
    return names


def preload_stage0_speaker_context(
    attendees: Optional[Sequence[Any]] = None,
    project: str = "GLOBAL",
    meeting_title: Optional[str] = None,
    operator_name: Optional[str] = None,
    voiceprint_db_path: Optional[Union[str, Path]] = None,
    graph_db_path: Optional[Union[str, Path]] = None,
    audio_path: Optional[Union[str, Path]] = None,
    sample_embeddings: Optional[Sequence[Sequence[float]]] = None,
    speaker_memory_path: Optional[Union[str, Path]] = None,
) -> Stage0SpeakerPreloadResult:
    """Executes <150ms Stage-0 speaker preload at `start recording` or pre-ASR preflight."""
    t0 = time.perf_counter()
    raw_att = list(attendees or [])
    init_names = _dedupe_roster_names(raw_att, [])
    inferred = run_acoustic_roster_preflight(
        audio_path=audio_path, sample_embeddings=sample_embeddings, project=project,
        voiceprint_db_path=voiceprint_db_path, speaker_memory_path=speaker_memory_path,
        graph_db_path=graph_db_path, known_attendees=init_names,
    ) if (audio_path or sample_embeddings) else []
    roster_names = _dedupe_roster_names(raw_att, inferred) or (_query_graph_roster_candidates(project, meeting_title, graph_db_path) if graph_db_path else [])
    resolved_op = _resolve_operator_name(raw_att, explicit_operator=operator_name)
    if resolved_op and resolved_op not in roster_names and roster_names:
        roster_names.insert(0, resolved_op)

    enrolled_centroids, global_prior_centroids = _load_voiceprint_centroids(roster_names, project, voiceprint_db_path)
    has_multi = len(roster_names) >= 2
    return Stage0SpeakerPreloadResult(
        operator_name=resolved_op, ch1_speaker=resolved_op, roster_names=roster_names,
        enrolled_centroids=enrolled_centroids, global_prior_centroids=global_prior_centroids,
        inferred_candidates=inferred, min_speakers=2 if has_multi else 1,
        max_speakers=max(2, len(roster_names) + 1) if has_multi else 8,
        elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 3),
    )
