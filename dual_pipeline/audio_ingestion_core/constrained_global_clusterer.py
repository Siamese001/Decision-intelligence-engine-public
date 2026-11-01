"""
dual_pipeline/audio_ingestion_core/constrained_global_clusterer.py

Wave 5 Sub-200ms Constrained Global Clusterer over pre-extracted 256-D turn embeddings.
Enforces:
- Hard Ch0 != Ch1 Cannot-Link constraint (zero cross-channel cluster mixing).
- Must-Link / Anchor constraints from 256-D VoiceprintStore centroids and operator hints.
- Automatic speaker count discovery when roster is unknown or empty.
- Sub-200ms vectorized CPU execution for 600+ turns with zero post-stop neural model wait.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.constrained_global_clusterer")


@dataclass
class ConstrainedClusteringResult:
    """Output of sub-200ms constrained global clustering at stop recording / U0 exit."""
    turns: List[Dict[str, Any]]
    cluster_centroids: Dict[str, List[float]]
    speaker_map: Dict[str, str] = field(default_factory=dict)
    ch1_cluster_id: str = "SPEAKER_00"
    remote_cluster_ids: List[str] = field(default_factory=list)
    cannot_link_violations: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_turn_embeddings_jsonl(jsonl_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Loads pre-extracted 256-D turn records from turn_embeddings.jsonl."""
    p = Path(jsonl_path)
    if not p.is_file():
        return []
    records: List[Dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and not rec.get("bleed_suppressed"):
                    records.append(rec)
            except Exception:
                continue
    return records


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 1e-8, norms, 1.0)
    return (mat / norms).astype(np.float32)


def _decode_pcm_bytes(raw: bytes, sw: int) -> np.ndarray:
    if sw == 2:
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if sw == 4:
        arr_f32 = np.frombuffer(raw, dtype=np.float32)
        if len(arr_f32) > 0 and np.all(np.isfinite(arr_f32)) and float(np.max(np.abs(arr_f32))) <= 10.0:
            return arr_f32.astype(np.float32)
        return np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    return (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0


def _select_mono_channel(audio_np: np.ndarray, n_ch: int, channel: Optional[int]) -> np.ndarray:
    if n_ch <= 1:
        return audio_np
    reshaped = audio_np.reshape(-1, n_ch)
    if channel in (0, 1) and int(channel) < n_ch:
        return reshaped[:, int(channel)]
    rms0 = float(np.sqrt(np.mean(reshaped[:, 0] ** 2)))
    rms1 = float(np.sqrt(np.mean(reshaped[:, 1] ** 2)))
    return reshaped[:, 1] if rms1 > rms0 else reshaped[:, 0]


def extract_turn_embedding_from_wav_slice(
    audio_path: Union[str, Path],
    start_sec: float = 0.0,
    end_sec: Optional[float] = None,
    channel: Optional[int] = None,
    emb_model: Any = None,
) -> List[float]:
    """Extracts a unit-normalized 16 kHz 256-D embedding for a single WAV turn slice."""
    import wave
    from dual_pipeline.live_meeting_recorder_core.acoustic_features import (
        extract_256d_acoustic_embedding,
        resample_to_16khz,
    )
    from .voiceprint_store import _normalize_vector

    p = Path(audio_path)
    if not p.is_file():
        raise FileNotFoundError(f"Audio file not found for 256-D turn embedding: {p}")

    with wave.open(str(p), "rb") as wf:
        sr, n_ch, sw, total = wf.getframerate(), wf.getnchannels(), wf.getsampwidth(), wf.getnframes()
        s_f = max(0, int(float(start_sec) * sr))
        e_f = min(total, int(float(end_sec) * sr)) if end_sec is not None else total
        if e_f <= s_f:
            e_f = min(total, s_f + max(1, int(sr * 1.0)))
        wf.setpos(s_f)
        raw = wf.readframes(max(1, e_f - s_f))

    mono = _select_mono_channel(_decode_pcm_bytes(raw, sw), n_ch, channel)
    audio_16k = resample_to_16khz(mono, sr, 16000)
    if emb_model is not None:
        try:
            import torch
            wav_tensor = torch.from_numpy(audio_16k).unsqueeze(0).unsqueeze(0).float()
            with torch.no_grad():
                raw_emb = emb_model(wav_tensor)
            vec = raw_emb.detach().cpu().numpy().reshape(-1) if isinstance(raw_emb, torch.Tensor) else np.asarray(raw_emb).reshape(-1)
            if vec.size >= 256:
                return _normalize_vector(vec[:256].tolist(), target_dim=256)
        except Exception as exc:
            logger.debug("WeSpeaker turn embedding fallback to 16kHz 256-D spectral: %s", exc)
    vec256 = extract_256d_acoustic_embedding(audio_16k, sr=16000)
    return _normalize_vector(vec256.tolist(), target_dim=256)


def extract_signal_centroids_fallback(
    engine: Any,
    audio_path: Path,
    turns: Sequence[Dict[str, Any]],
    clusters: List[str],
    max_segments_per_cluster: int,
) -> Dict[str, List[float]]:
    from .voiceprint_store import _normalize_vector
    centroids_sig: Dict[str, List[float]] = {}
    for spk in clusters:
        spk_turns = [t for t in turns if t.get("speaker") == spk][:max_segments_per_cluster]
        vecs = []
        for t in spk_turns:
            try:
                st = float(t.get("start", 0.0))
                en = float(t.get("end", st + float(t.get("duration", 1.0))))
                vecs.append(np.asarray(engine.extract_turn_embedding(audio_path, st, en, channel=t.get("channel")), dtype=np.float32))
            except Exception:
                continue
        if vecs:
            centroids_sig[spk] = _normalize_vector(np.mean(vecs, axis=0).tolist(), target_dim=256)
    return centroids_sig


def _compute_cosine_silhouette(mat: np.ndarray, centroids: np.ndarray) -> float:
    if centroids.shape[0] < 2 or mat.shape[0] < centroids.shape[0] + 1:
        return 0.0
    sims = np.dot(mat, centroids.T)
    sorted_sims = np.sort(sims, axis=1)
    best = sorted_sims[:, -1]
    second = sorted_sims[:, -2]
    return float(np.mean(best - second))


def _discover_remote_centroids(
    mat: np.ndarray,
    anchored_centroids: List[np.ndarray],
    expected_remote_count: Optional[int],
    max_remote_speakers: int,
    new_cluster_sim_threshold: float,
) -> np.ndarray:
    """Initializes remote cluster centroids from Must-Link anchors + farthest-first discovery with Silhouette selection."""
    n_turns = mat.shape[0]
    if n_turns == 0:
        return np.zeros((0, 256), dtype=np.float32)
    centroids: List[np.ndarray] = [c.copy() for c in anchored_centroids] or [mat[0].copy()]
    target_k = max(len(centroids), min(max_remote_speakers, int(expected_remote_count or max_remote_speakers)))
    unconstrained = expected_remote_count is None or expected_remote_count <= 0
    candidate_stacks: List[Tuple[float, np.ndarray]] = []
    min_sil_k = max(2, len(anchored_centroids))

    while len(centroids) < target_k and len(centroids) < n_turns:
        c_stack = _normalize_rows(np.stack(centroids, axis=0))
        if unconstrained and len(centroids) >= min_sil_k:
            candidate_stacks.append((_compute_cosine_silhouette(mat, c_stack), c_stack))
        max_sims_per_turn = np.max(np.dot(mat, c_stack.T), axis=1)
        worst_idx = int(np.argmin(max_sims_per_turn))
        worst_sim = float(max_sims_per_turn[worst_idx])
        if worst_sim >= 0.98 or (unconstrained and worst_sim >= new_cluster_sim_threshold):
            break
        centroids.append(mat[worst_idx].copy())

    final_stack = _normalize_rows(np.stack(centroids, axis=0))
    if unconstrained and len(centroids) >= 2:
        candidate_stacks.append((_compute_cosine_silhouette(mat, final_stack), final_stack))
        return max(candidate_stacks, key=lambda item: item[0])[1]
    return final_stack


def _partition_turns_by_channel(raw_turns: List[Dict[str, Any]]) -> Tuple[List[int], List[int]]:
    ch1_indices: List[int] = []
    ch0_indices: List[int] = []
    for idx, t in enumerate(raw_turns):
        emb = t.get("embedding")
        if not isinstance(emb, (list, tuple, np.ndarray)) or len(emb) != 256:
            raise ValueError(f"Turn {idx} missing valid 256-D embedding")
        if int(t.get("channel", 0)) == 1:
            ch1_indices.append(idx)
        else:
            ch0_indices.append(idx)
    return ch1_indices, ch0_indices


def _assign_ch1_cluster(
    raw_turns: List[Dict[str, Any]],
    out_turns: List[Dict[str, Any]],
    ch1_indices: List[int],
    ch1_operator_name: Optional[str],
    cluster_centroids: Dict[str, List[float]],
    speaker_map: Dict[str, str],
) -> None:
    if not ch1_indices:
        return
    ch1_mat = _normalize_rows(np.asarray([raw_turns[i]["embedding"] for i in ch1_indices], dtype=np.float32))
    ch1_mean = _normalize_rows(np.mean(ch1_mat, axis=0, keepdims=True))[0]
    cluster_centroids["SPEAKER_00"] = [round(float(x), 6) for x in ch1_mean]
    if ch1_operator_name:
        speaker_map["SPEAKER_00"] = ch1_operator_name
    label = ch1_operator_name or "SPEAKER_00"
    for i in ch1_indices:
        out_turns[i]["cluster_id"] = "SPEAKER_00"
        out_turns[i]["speaker"] = label
        out_turns[i]["raw_speaker"] = "SPEAKER_00"


def _build_temporal_cannot_link_pairs(
    raw_turns: List[Dict[str, Any]], ch0_indices: List[int], ch0_mat: np.ndarray
) -> List[Tuple[int, int]]:
    pairs: List[Tuple[int, int]] = []
    n = len(ch0_indices)
    for a in range(n):
        t_a = raw_turns[ch0_indices[a]]
        st_a = float(t_a.get("start_sec", t_a.get("start", 0.0)))
        en_a = float(t_a.get("end_sec", t_a.get("end", st_a)))
        for b in range(a + 1, min(n, a + 6)):
            t_b = raw_turns[ch0_indices[b]]
            st_b = float(t_b.get("start_sec", t_b.get("start", 0.0)))
            en_b = float(t_b.get("end_sec", t_b.get("end", st_b)))
            if min(en_a, en_b) - max(st_a, st_b) > 0.25 and float(np.dot(ch0_mat[a], ch0_mat[b])) < 0.72:
                pairs.append((a, b))
    return pairs


def _enforce_cannot_link_assignments(
    assignments: np.ndarray,
    sims: np.ndarray,
    cl_pairs: List[Tuple[int, int]],
    pinned_local_idx: Dict[int, int],
) -> None:
    for idx_a, idx_b in cl_pairs:
        if assignments[idx_a] == assignments[idx_b] and idx_b not in pinned_local_idx:
            order = np.argsort(sims[idx_b])[::-1]
            assignments[idx_b] = int(order[1] if order[0] == assignments[idx_a] else order[0])


def _update_kmeans_centroids(
    ch0_mat: np.ndarray, assignments: np.ndarray, centroids_mat: np.ndarray, anchor_vecs: List[np.ndarray]
) -> None:
    for k_i in range(centroids_mat.shape[0]):
        members = ch0_mat[assignments == k_i]
        if len(members) == 0:
            continue
        mean_vec = np.mean(members, axis=0, keepdims=True)
        if k_i < len(anchor_vecs):
            mean_vec = (0.68 * anchor_vecs[k_i] + 0.32 * mean_vec[0]).reshape(1, 256)
        centroids_mat[k_i] = _normalize_rows(mean_vec)[0]


def _refine_remote_kmeans(
    ch0_mat: np.ndarray,
    centroids_mat: np.ndarray,
    anchor_vecs: List[np.ndarray],
    pinned_local_idx: Dict[int, int],
    cannot_link_pairs: Optional[List[Tuple[int, int]]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    k_rem = centroids_mat.shape[0]
    assignments = np.zeros(ch0_mat.shape[0], dtype=np.int32)
    cl_pairs = cannot_link_pairs or []
    for _ in range(5):
        sims = np.dot(ch0_mat, centroids_mat.T)
        assignments = np.argmax(sims, axis=1).astype(np.int32)
        for local_i, k_pin in pinned_local_idx.items():
            assignments[local_i] = k_pin
        if k_rem >= 2 and cl_pairs:
            _enforce_cannot_link_assignments(assignments, sims, cl_pairs, pinned_local_idx)
        _update_kmeans_centroids(ch0_mat, assignments, centroids_mat, anchor_vecs)
    return assignments, centroids_mat


def _build_pinned_index_map(
    ch0_indices: List[int],
    raw_turns: List[Dict[str, Any]],
    must_link: Dict[int, str],
    speaker_map: Dict[str, str],
    k_rem: int,
) -> Dict[int, int]:
    name_to_k = {v.lower(): k_i for k_i, (_, v) in enumerate(speaker_map.items()) if k_i < k_rem}
    pinned: Dict[int, int] = {}
    for local_i, global_i in enumerate(ch0_indices):
        hint_spk = must_link.get(global_i) or raw_turns[global_i].get("live_match_speaker")
        if hint_spk and str(hint_spk).lower() in name_to_k:
            pinned[local_i] = name_to_k[str(hint_spk).lower()]
    return pinned


def _assign_ch0_clusters(
    raw_turns: List[Dict[str, Any]],
    out_turns: List[Dict[str, Any]],
    ch0_indices: List[int],
    ch1_has_turns: bool,
    enrolled: Dict[str, Sequence[float]],
    ch1_operator_name: Optional[str],
    expected_roster_count: Optional[int],
    must_link: Dict[int, str],
    similarity_threshold: float,
    max_remote_speakers: int,
    cluster_centroids: Dict[str, List[float]],
    speaker_map: Dict[str, str],
) -> List[str]:
    if not ch0_indices:
        return []
    ch0_mat = _normalize_rows(np.asarray([raw_turns[i]["embedding"] for i in ch0_indices], dtype=np.float32))
    remote_enrolled = {
        name: _normalize_rows(np.asarray(vec, dtype=np.float32).reshape(1, 256))[0]
        for name, vec in enrolled.items()
        if not ch1_operator_name or name.lower() != ch1_operator_name.lower()
    }
    anchor_names = list(remote_enrolled.keys())
    anchor_vecs = [remote_enrolled[n] for n in anchor_names]
    exp_remote = (
        max(1, expected_roster_count - (1 if ch1_has_turns or ch1_operator_name else 0))
        if expected_roster_count is not None and expected_roster_count >= 1
        else None
    )
    centroids_mat = _discover_remote_centroids(
        ch0_mat, anchor_vecs, exp_remote, max_remote_speakers, similarity_threshold
    )
    k_rem = centroids_mat.shape[0]
    remote_cluster_ids = [f"SPEAKER_{k + 1:02d}" for k in range(k_rem)]
    for k_idx, r_name in enumerate(anchor_names[:k_rem]):
        speaker_map[remote_cluster_ids[k_idx]] = r_name

    pinned_local_idx = _build_pinned_index_map(ch0_indices, raw_turns, must_link, speaker_map, k_rem)
    cl_pairs = _build_temporal_cannot_link_pairs(raw_turns, ch0_indices, ch0_mat)
    assignments, centroids_mat = _refine_remote_kmeans(ch0_mat, centroids_mat, anchor_vecs, pinned_local_idx, cl_pairs)
    final_sims = np.dot(ch0_mat, centroids_mat.T)

    for local_i, global_i in enumerate(ch0_indices):
        k_i = int(assignments[local_i])
        cid = remote_cluster_ids[k_i]
        out_turns[global_i]["cluster_id"] = cid
        out_turns[global_i]["raw_speaker"] = cid
        out_turns[global_i]["speaker"] = speaker_map.get(cid, cid)
        out_turns[global_i]["confidence"] = round(float(final_sims[local_i, k_i]), 4)

    for k_i, cid in enumerate(remote_cluster_ids):
        if np.any(assignments == k_i):
            cluster_centroids[cid] = [round(float(x), 6) for x in centroids_mat[k_i]]
    return remote_cluster_ids


def run_constrained_global_clustering(
    turns_or_jsonl: Union[str, Path, Sequence[Dict[str, Any]]],
    enrolled_centroids: Optional[Dict[str, Sequence[float]]] = None,
    ch1_operator_name: Optional[str] = None,
    expected_roster_count: Optional[int] = None,
    must_link_turn_speakers: Optional[Dict[int, str]] = None,
    similarity_threshold: float = 0.75,
    max_remote_speakers: int = 8,
) -> ConstrainedClusteringResult:
    """Clusters pre-extracted 256-D turn embeddings in <200ms CPU time with Ch0 != Ch1 Cannot-Link."""
    t0 = time.perf_counter()
    raw_turns = (
        load_turn_embeddings_jsonl(turns_or_jsonl)
        if isinstance(turns_or_jsonl, (str, Path))
        else [dict(t) for t in turns_or_jsonl if not t.get("bleed_suppressed")]
    )
    if not raw_turns:
        return ConstrainedClusteringResult(turns=[], cluster_centroids={}, elapsed_ms=0.0)

    ch1_indices, ch0_indices = _partition_turns_by_channel(raw_turns)
    cluster_centroids: Dict[str, List[float]] = {}
    speaker_map: Dict[str, str] = {}
    out_turns = [dict(t) for t in raw_turns]

    _assign_ch1_cluster(raw_turns, out_turns, ch1_indices, ch1_operator_name, cluster_centroids, speaker_map)
    remote_cluster_ids = _assign_ch0_clusters(
        raw_turns,
        out_turns,
        ch0_indices,
        bool(ch1_indices),
        enrolled_centroids or {},
        ch1_operator_name,
        expected_roster_count,
        dict(must_link_turn_speakers or {}),
        similarity_threshold,
        max_remote_speakers,
        cluster_centroids,
        speaker_map,
    )

    ch1_clusters = {out_turns[i]["cluster_id"] for i in ch1_indices}
    ch0_clusters = {out_turns[i]["cluster_id"] for i in ch0_indices}
    violations = len(ch1_clusters.intersection(ch0_clusters))
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    return ConstrainedClusteringResult(
        turns=out_turns,
        cluster_centroids=cluster_centroids,
        speaker_map=speaker_map,
        ch1_cluster_id="SPEAKER_00",
        remote_cluster_ids=remote_cluster_ids,
        cannot_link_violations=violations,
        elapsed_ms=elapsed_ms,
    )
