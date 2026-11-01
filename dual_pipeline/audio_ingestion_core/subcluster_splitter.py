"""
dual_pipeline/audio_ingestion_core/subcluster_splitter.py

Channel-Aware Stereo Isolation & Merged-Cluster Sub-Splitting Engine (Wave 2).
"""
from __future__ import annotations

import logging
import re
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np

from dual_pipeline.parameters import get_starvation_turn_ratio_threshold

logger = logging.getLogger("dual_pipeline.audio_ingestion_core.subcluster_splitter")

STARVATION_TURN_RATIO_THRESHOLD = get_starvation_turn_ratio_threshold()


def _read_multi_window_stereo_frames(wf: wave.Wave_read, n_frames: int) -> np.ndarray:
    sr = wf.getframerate()
    win_frames = sr * 120
    if n_frames <= sr * 300:
        raw = wf.readframes(n_frames)
        return np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float32)
    chunks: List[np.ndarray] = []
    for start_f in (0, max(0, (n_frames - win_frames) // 2), max(0, n_frames - win_frames)):
        wf.setpos(start_f)
        raw = wf.readframes(min(win_frames, n_frames - start_f))
        if raw:
            chunks.append(np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float32))
    return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 2), dtype=np.float32)


def _compute_stereo_rms_and_corr(wf: wave.Wave_read, min_active_rms: float) -> Dict[str, Any]:
    n_ch = wf.getnchannels()
    sampwidth = wf.getsampwidth()
    n_frames = wf.getnframes()
    if n_ch != 2 or sampwidth != 2 or n_frames == 0:
        return {"is_dual_active_stereo": False, "channels": n_ch, "rms_ch0": 0.0, "rms_ch1": 0.0}
    data = _read_multi_window_stereo_frames(wf, n_frames)
    if len(data) == 0:
        return {"is_dual_active_stereo": False, "channels": n_ch, "rms_ch0": 0.0, "rms_ch1": 0.0}
    rms0 = float(np.sqrt(np.mean(data[:, 0] ** 2)))
    rms1 = float(np.sqrt(np.mean(data[:, 1] ** 2)))
    corr = 0.0
    if rms0 >= min_active_rms and rms1 >= min_active_rms:
        c_mat = np.corrcoef(data[:, 0], data[:, 1])
        corr = float(abs(c_mat[0, 1])) if c_mat.shape == (2, 2) else 1.0
    is_independent = bool(rms0 >= min_active_rms and rms1 >= min_active_rms and corr < 0.92)
    return {
        "is_dual_active_stereo": is_independent,
        "channels": 2,
        "rms_ch0": round(rms0, 2),
        "rms_ch1": round(rms1, 2),
        "channel_correlation": round(corr, 4),
    }


def detect_stereo_channel_activity(
    audio_path: Union[str, Path],
    min_active_rms: float = 50.0,
) -> Dict[str, Any]:
    """Inspects a WAV file to determine if both stereo channels carry independent signal."""
    p = Path(audio_path)
    if not p.is_file() or p.suffix.lower() not in (".wav", ".wave"):
        return {"is_dual_active_stereo": False, "channels": 1, "rms_ch0": 0.0, "rms_ch1": 0.0}
    try:
        with wave.open(str(p), "rb") as wf:
            return _compute_stereo_rms_and_corr(wf, min_active_rms)
    except Exception as e:
        logger.debug("Stereo activity inspection failed for %s: %s", p, e)
        return {"is_dual_active_stereo": False, "channels": 1, "rms_ch0": 0.0, "rms_ch1": 0.0}


def _standardize_word_candidates(w: Dict[str, Any], spk: str) -> Dict[str, Any]:
    w_copy = {**w, "speaker": spk}
    cands = w_copy.get("candidate_speakers")
    if isinstance(cands, list):
        remap = {"SPEAKER_00": "SPEAKER_01", "SPEAKER_01": "SPEAKER_00"}
        w_copy["candidate_speakers"] = [
            {**c, "speaker": remap.get(str(c.get("speaker")), c.get("speaker"))}
            if isinstance(c, dict) else c
            for c in cands
        ]
    return w_copy


def _standardize_stereo_segment(seg: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    s_copy = dict(seg)
    is_local = s_copy.get("channel") == 1 or s_copy.get("speaker") == "SPEAKER_01"
    spk = "SPEAKER_00" if is_local else "SPEAKER_01"
    s_copy["speaker"] = spk
    s_copy["raw_speaker"] = spk
    s_copy["channel"] = 1 if is_local else 0
    if "words" in s_copy and isinstance(s_copy["words"], list):
        s_copy["words"] = [_standardize_word_candidates(w, spk) for w in s_copy["words"]]
    return s_copy, (not is_local)


def align_and_diarize_dual_channel_stereo(
    segments: List[Dict[str, Any]],
    stereo_audio_path: Union[str, Path],
    expected_roster_count: Optional[int] = None,
    diarization_engine: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Isolates local mic channel via energy and partitions remote channel into N_remote speakers."""
    activity = detect_stereo_channel_activity(stereo_audio_path)
    if not activity.get("is_dual_active_stereo"):
        return None

    from .stereo_alignment import align_segments_via_stereo_energy
    aligned = align_segments_via_stereo_energy(segments, stereo_audio_path)
    if not aligned:
        return None

    total_words = sum(len(str(s.get("text", "")).split()) for s in aligned)
    avg_words = total_words / max(1, len(aligned))
    if len(segments) >= 10 and len(aligned) > len(segments) * 1.8 and avg_words < 4.5:
        logger.info(
            "Stereo energy alignment shattered %d segments into %d micro-turns (avg %.1f words/turn); "
            "falling back to Pyannote neural diarization.",
            len(segments), len(aligned), avg_words,
        )
        return None

    remote_count = 0
    standardized: List[Dict[str, Any]] = []
    for seg in aligned:
        s_copy, is_remote = _standardize_stereo_segment(seg)
        if is_remote:
            remote_count += 1
        standardized.append(s_copy)

    n_remote = max(1, (expected_roster_count or 2) - 1)
    if n_remote >= 2 and remote_count >= 2:
        standardized, _ = split_merged_remote_cluster(
            standardized,
            audio_path=stereo_audio_path,
            metadata=metadata,
            diarization_engine=diarization_engine,
            starvation_ratio=0.05,
        )
    if metadata is not None:
        metadata["stereo_channel_activity"] = activity
        metadata["diarization_method"] = "stereo_channel_isolated"
    return standardized


def detect_starved_or_merged_clusters(
    segments: Sequence[Dict[str, Any]],
    expected_roster_count: int,
    starvation_ratio: float = STARVATION_TURN_RATIO_THRESHOLD,
) -> Dict[str, Any]:
    """Detects when a multi-speaker call has a starved cluster (< 5% turns) or missing clusters."""
    if expected_roster_count < 2 or not segments:
        return {"is_starved": False, "starved_clusters": [], "counts": {}}

    counts: Dict[str, int] = {}
    durations: Dict[str, float] = {}
    for s in segments:
        spk = str(s.get("raw_speaker") or s.get("speaker") or "Unknown")
        if spk.lower() in ("unknown", "speaker unknown"):
            continue
        counts[spk] = counts.get(spk, 0) + 1
        st = float(s.get("start", s.get("start_time_sec", 0.0)))
        en = float(s.get("end", s.get("end_time_sec", st)))
        durations[spk] = durations.get(spk, 0.0) + max(0.0, en - st)

    total_turns = sum(counts.values())
    if total_turns < 6:
        return {"is_starved": False, "starved_clusters": [], "counts": counts}

    starved = [spk for spk, cnt in counts.items() if (cnt / total_turns) < starvation_ratio]
    missing_clusters = len(counts) < expected_roster_count
    return {
        "is_starved": bool(starved or missing_clusters),
        "starved_clusters": starved,
        "missing_clusters": missing_clusters,
        "counts": counts,
        "durations": {k: round(v, 2) for k, v in durations.items()},
        "total_turns": total_turns,
    }


def _extract_wav_mono_channel(arr: np.ndarray, n_ch: int, seg: Dict[str, Any]) -> np.ndarray:
    if n_ch <= 1:
        return arr
    arr = arr.reshape(-1, n_ch)
    seg_ch = seg.get("channel")
    if seg_ch in (0, 1) and int(seg_ch) < n_ch:
        return arr[:, int(seg_ch)]
    rms0 = float(np.sqrt(np.mean(arr[:, 0] ** 2)))
    rms1 = float(np.sqrt(np.mean(arr[:, 1] ** 2)))
    return arr[:, 1] if rms1 >= rms0 else arr[:, 0]


def _extract_wav_window_vector(wf: wave.Wave_read, seg: Dict[str, Any]) -> Optional[np.ndarray]:
    sr, n_ch, total_frames = wf.getframerate(), wf.getnchannels(), wf.getnframes()
    st = float(seg.get("start", seg.get("start_time_sec", 0.0)))
    en = float(seg.get("end", seg.get("end_time_sec", st + 1.0)))
    s_f, e_f = max(0, int(st * sr)), min(total_frames, int(en * sr))
    if e_f - s_f < int(sr * 0.15):
        return None
    wf.setpos(s_f)
    arr = np.frombuffer(wf.readframes(e_f - s_f), dtype=np.int16).astype(np.float32) / 32768.0
    arr = _extract_wav_mono_channel(arr, n_ch, seg)
    if sr != 16000 and len(arr) > 0:
        target_len = max(1, int(round(len(arr) * 16000.0 / float(sr))))
        arr = np.interp(
            np.linspace(0.0, 1.0, num=target_len, endpoint=False),
            np.linspace(0.0, 1.0, num=len(arr), endpoint=False),
            arr,
        ).astype(np.float32)
    n_win = max(1, len(arr) // 512)
    framed = arr[: n_win * 512].reshape(n_win, 512) if len(arr) >= 512 else arr.reshape(1, -1)
    fft_mag = np.mean(np.abs(np.fft.rfft(framed, n=512, axis=1))[:, :256], axis=0).astype(np.float32)
    norm = float(np.linalg.norm(fft_mag))
    return (fft_mag / norm) if norm > 1e-6 else None


def _extract_embedded_vectors(
    segments: Sequence[Dict[str, Any]],
    target_indices: Sequence[int],
) -> Dict[int, np.ndarray]:
    vectors: Dict[int, np.ndarray] = {}
    for idx in target_indices:
        emb = segments[idx].get("embedding")
        if isinstance(emb, (list, np.ndarray)):
            vec = np.asarray(emb, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vec))
            if norm > 1e-6:
                vectors[idx] = vec / norm
    return vectors


def _populate_wav_vectors(
    p: Path,
    segments: Sequence[Dict[str, Any]],
    target_indices: Sequence[int],
    vectors: Dict[int, np.ndarray],
) -> None:
    try:
        with wave.open(str(p), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnframes() <= 0:
                return
            for idx in target_indices:
                if idx not in vectors:
                    v_wav = _extract_wav_window_vector(wf, segments[idx])
                    if v_wav is not None:
                        vectors[idx] = v_wav
    except Exception as e:
        logger.debug("WAV feature extraction diagnostic: %s", e)


def _try_populate_neural_vectors(
    diarization_engine: Optional[Any],
    p: Optional[Path],
    segments: Sequence[Dict[str, Any]],
    target_indices: Sequence[int],
    vectors: Dict[int, np.ndarray],
) -> None:
    if diarization_engine is None or p is None or not p.is_file():
        return
    extract_fn = getattr(diarization_engine, "extract_turn_embedding", None)
    if not callable(extract_fn):
        return
    for idx in target_indices:
        if idx in vectors:
            continue
        try:
            st = float(segments[idx].get("start", segments[idx].get("start_time_sec", 0.0)))
            en = float(segments[idx].get("end", segments[idx].get("end_time_sec", st + 1.0)))
            emb = extract_fn(str(p), st, en, channel=segments[idx].get("channel"))
            if emb is not None:
                vec = np.asarray(emb, dtype=np.float32).reshape(-1)
                norm = float(np.linalg.norm(vec))
                if norm > 1e-6:
                    vectors[idx] = vec / norm
        except Exception:
            continue


def _extract_per_turn_acoustic_vectors(
    audio_path: Optional[Union[str, Path]],
    segments: Sequence[Dict[str, Any]],
    target_indices: Sequence[int],
    diarization_engine: Optional[Any] = None,
) -> Dict[int, np.ndarray]:
    """Extracts a unit-norm feature vector for each target segment index."""
    vectors = _extract_embedded_vectors(segments, target_indices)
    p = Path(audio_path) if audio_path else None
    if len(vectors) < len(target_indices):
        _try_populate_neural_vectors(diarization_engine, p, segments, target_indices, vectors)
    if len(vectors) < len(target_indices) and p and p.is_file() and p.suffix.lower() in (".wav", ".wave"):
        _populate_wav_vectors(p, segments, target_indices, vectors)

    from .voiceprint_store import generate_deterministic_embedding
    for idx in target_indices:
        if idx not in vectors:
            txt = str(segments[idx].get("text", ""))
            v = np.asarray(generate_deterministic_embedding(f"turn:{idx}:{txt[:32]}", dim=256), dtype=np.float32)
            vectors[idx] = v / max(1e-6, float(np.linalg.norm(v)))
    return vectors


def _check_third_person_mention(text: str, first_tok: str) -> bool:
    if not first_tok or not text:
        return False
    tok = re.escape(first_tok)
    tp_pat = re.compile(
        rf"\b(?:know\s+{tok}\s+is|that\s+{tok}\s+(?:may|might|has|had|was|is|said|asked|did|will|would|can)|"
        rf"(?:with|ask|to|about|for|from|let|tell|reach\s+out\s+to|transfer\s+to|bring\s+in)\s+{tok}\b|"
        rf"{tok}['’]s\s+team|{tok}\s*[\.\s]*once\s+(?:he|she|they)|"
        rf"{tok}\s+(?:can|could|will|would|might|has|had|is|was|said|told|agreed)\b|"
        rf"(?:^|[\.\?!\[\]\(\),]\s*){tok},\s*(?:can|could|do|did|would|will|what|are|is|please)\b)",
        re.IGNORECASE,
    )
    return bool(tp_pat.search(text))


def _match_hint_to_candidate(matched: Optional[Dict[str, Any]], remote_candidates: Sequence[str]) -> Optional[str]:
    if not matched:
        return None
    m_spk = matched["speaker"].lower()
    return next((c for c in remote_candidates if m_spk in c.lower() or c.lower() in m_spk), None)


def _apply_exclusion_to_anchor(
    idx: int,
    text: str,
    cand_first: Dict[str, str],
    remote_candidates: Sequence[str],
    positive_anchors: Dict[int, str],
    third_person_exclusions: Dict[int, str],
    total_remote_candidates: Optional[Sequence[str]] = None,
) -> None:
    pool_size = len(total_remote_candidates) if total_remote_candidates else len(remote_candidates)
    for first_tok, full_cand in cand_first.items():
        if _check_third_person_mention(text, first_tok):
            third_person_exclusions[idx] = full_cand
            # Inversion is ONLY valid if the true total remote candidate pool is strictly 2.
            # In calls with >= 3 remote attendees, third-person mention cannot default to the other peer.
            if pool_size == 2 and len(remote_candidates) == 2 and idx not in positive_anchors:
                positive_anchors[idx] = remote_candidates[1] if remote_candidates[0] == full_cand else remote_candidates[0]


def _identify_anchor_turns(
    segments: Sequence[Dict[str, Any]],
    target_indices: Sequence[int],
    remote_candidates: Sequence[str],
    hints: Sequence[Dict[str, Any]],
    total_remote_candidates: Optional[Sequence[str]] = None,
) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Finds positive and negative (third-person self-mention) speaker anchors on target turns."""
    from dual_pipeline.speaker_attribution_core.candidate_scorer import normalize_search_text
    from dual_pipeline.speaker_attribution_core.quote_hint_turn_mapper import (
        _find_matching_hint,
        _prepare_valid_hints,
    )

    valid_hints = _prepare_valid_hints(list(hints or []))
    positive_anchors: Dict[int, str] = {}
    third_person_exclusions: Dict[int, str] = {}
    cand_first = {c.strip().split()[0].lower(): c for c in remote_candidates if len(c.strip().split()[0]) >= 3}

    for idx in target_indices:
        text = str(segments[idx].get("text", "")).strip()
        if not text:
            continue
        norm_text = normalize_search_text(text)
        matched = _find_matching_hint(valid_hints, norm_text, set(norm_text.split())) if valid_hints else None
        cand_match = _match_hint_to_candidate(matched, remote_candidates)
        if cand_match:
            positive_anchors[idx] = cand_match
        _apply_exclusion_to_anchor(
            idx,
            text,
            cand_first,
            remote_candidates,
            positive_anchors,
            third_person_exclusions,
            total_remote_candidates=total_remote_candidates,
        )

    return positive_anchors, third_person_exclusions


def _compute_initial_centroids(
    mat: np.ndarray,
    a_anchors: List[np.ndarray],
    b_anchors: List[np.ndarray],
) -> Tuple[np.ndarray, np.ndarray]:
    if a_anchors and b_anchors:
        ca, cb = np.mean(a_anchors, axis=0), np.mean(b_anchors, axis=0)
    elif a_anchors:
        ca = np.mean(a_anchors, axis=0)
        ca = ca / max(1e-6, float(np.linalg.norm(ca)))
        cb = mat[int(np.argmin(np.dot(mat, ca)))]
    elif b_anchors:
        cb = np.mean(b_anchors, axis=0)
        cb = cb / max(1e-6, float(np.linalg.norm(cb)))
        ca = mat[int(np.argmin(np.dot(mat, cb)))]
    else:
        ca = mat[0]
        cb = mat[int(np.argmin(np.dot(mat, ca)))]
    return ca / max(1e-6, float(np.linalg.norm(ca))), cb / max(1e-6, float(np.linalg.norm(cb)))


def _step_kmeans_iteration(
    target_indices: Sequence[int],
    vectors: Dict[int, np.ndarray],
    pos_anchors: Dict[int, str],
    cand_a: str,
    cluster_a: str,
    cluster_b: str,
    ca: np.ndarray,
    cb: np.ndarray,
    a_anchors: List[np.ndarray],
    b_anchors: List[np.ndarray],
) -> Tuple[Dict[int, str], np.ndarray, np.ndarray]:
    assignments: Dict[int, str] = {}
    grp_a, grp_b = list(a_anchors), list(b_anchors)
    for idx in target_indices:
        if idx in pos_anchors:
            assignments[idx] = cluster_a if pos_anchors[idx] == cand_a else cluster_b
        elif float(np.dot(vectors[idx], ca)) >= float(np.dot(vectors[idx], cb)):
            assignments[idx] = cluster_a
            grp_a.append(vectors[idx])
        else:
            assignments[idx] = cluster_b
            grp_b.append(vectors[idx])
    if grp_a:
        avg_a = np.mean(grp_a, axis=0)
        ca = avg_a / max(1e-6, float(np.linalg.norm(avg_a)))
    if grp_b:
        avg_b = np.mean(grp_b, axis=0)
        cb = avg_b / max(1e-6, float(np.linalg.norm(avg_b)))
    return assignments, ca, cb


def _run_constrained_kmeans(
    target_indices: Sequence[int],
    vectors: Dict[int, np.ndarray],
    pos_anchors: Dict[int, str],
    cand_a: str,
    cand_b: str,
    cluster_a: str,
    cluster_b: str,
    enrolled_centroids: Optional[Dict[str, Sequence[float]]] = None,
) -> Dict[int, str]:
    a_anchors = [vectors[i] for i, c in pos_anchors.items() if c == cand_a and i in vectors]
    b_anchors = [vectors[i] for i, c in pos_anchors.items() if c == cand_b and i in vectors]
    if enrolled_centroids:
        ec_low = {k.strip().lower(): np.asarray(v, dtype=np.float32).ravel() for k, v in enrolled_centroids.items() if len(v) == 256}
        if cand_a.strip().lower() in ec_low:
            a_anchors.insert(0, ec_low[cand_a.strip().lower()])
        if cand_b.strip().lower() in ec_low:
            b_anchors.insert(0, ec_low[cand_b.strip().lower()])
    mat = np.stack([vectors[i] for i in target_indices], axis=0)
    ca, cb = _compute_initial_centroids(mat, a_anchors, b_anchors)

    assignments: Dict[int, str] = {}
    for _ in range(4):
        assignments, ca, cb = _step_kmeans_iteration(
            target_indices, vectors, pos_anchors, cand_a, cluster_a, cluster_b, ca, cb, a_anchors, b_anchors
        )
    return assignments


def _resolve_roster_names(meta: Dict[str, Any]) -> List[str]:
    from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import extract_roster_with_roles
    roster_entries = extract_roster_with_roles(meta)
    names = [str(r["name"]) for r in roster_entries if isinstance(r, dict) and r.get("name")]
    if not names and isinstance(meta.get("attendees"), list):
        names = [a.get("name") if isinstance(a, dict) else str(a) for a in meta["attendees"]]
    return names


def _is_local_ch1_cluster(segments: Sequence[Dict[str, Any]], spk_cand: str) -> bool:
    """Returns True if the cluster belongs to the isolated local microphone channel (Ch1)."""
    cand_segs = [s for s in segments if (s.get("raw_speaker") or s.get("speaker")) == spk_cand]
    if not cand_segs:
        return False
    ch1_count = sum(1 for s in cand_segs if s.get("channel") == 1)
    if ch1_count > len(cand_segs) / 2:
        return True
    has_stereo = any(s.get("channel") in (0, 1) for s in segments)
    if has_stereo and spk_cand == "SPEAKER_00":
        return True
    return False


def _select_merged_remote_cluster(
    segments: Sequence[Dict[str, Any]],
    non_starved: List[Tuple[str, int]],
    roster_names: List[str],
    hints: Sequence[Dict[str, Any]],
    missing_clusters: bool = False,
) -> Optional[str]:
    remote_candidates = [
        (spk_cand, cnt) for spk_cand, cnt in non_starved
        if not _is_local_ch1_cluster(segments, spk_cand)
    ]
    for spk_cand, _ in remote_candidates:
        cand_idxs = [
            i for i, s in enumerate(segments)
            if (s.get("raw_speaker") or s.get("speaker")) == spk_cand and s.get("channel") != 1
        ]
        pos, excl = _identify_anchor_turns(segments, cand_idxs, roster_names, hints)
        if pos and excl:
            return spk_cand
    if missing_clusters and remote_candidates:
        return remote_candidates[0][0]
    return None


def _apply_turn_assignments(
    segments: Sequence[Dict[str, Any]],
    assignments: Dict[int, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    updated: List[Dict[str, Any]] = []
    new_counts: Dict[str, int] = {}
    for idx, seg in enumerate(segments):
        s_copy = dict(seg)
        if idx in assignments:
            new_spk = assignments[idx]
            s_copy["speaker"] = new_spk
            s_copy["raw_speaker"] = new_spk
            if isinstance(s_copy.get("words"), list):
                s_copy["words"] = [{**w, "speaker": new_spk} for w in s_copy["words"]]
        spk_final = str(s_copy.get("raw_speaker") or s_copy.get("speaker") or "Unknown")
        new_counts[spk_final] = new_counts.get(spk_final, 0) + 1
        updated.append(s_copy)
    return updated, new_counts


def _cascade_additional_splits(
    updated_segments: List[Dict[str, Any]],
    new_counts: Dict[str, int],
    roster_names: List[str],
    all_remote_cands: List[str],
    hints: Sequence[Dict[str, Any]],
    audio_path: Optional[Union[str, Path]],
    diarization_engine: Optional[Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    while len(new_counts) < len(roster_names):
        remote_clusters = sorted(
            [(k, v) for k, v in new_counts.items() if k != "SPEAKER_00" and v >= 2],
            key=lambda x: x[1],
            reverse=True,
        )
        if not remote_clusters:
            break
        split_src = remote_clusters[0][0]
        next_label = f"SPEAKER_{len(new_counts):02d}"
        sub_indices = [i for i, s in enumerate(updated_segments) if (s.get("raw_speaker") or s.get("speaker")) == split_src]
        if len(sub_indices) < 2:
            break
        cand_offset = max(0, len(new_counts) - 2)
        pair = (all_remote_cands[cand_offset : cand_offset + 2] + all_remote_cands)[:2]
        sub_pos, _ = _identify_anchor_turns(
            updated_segments, sub_indices, pair, hints, total_remote_candidates=all_remote_cands
        )
        sub_vecs = _extract_per_turn_acoustic_vectors(audio_path, updated_segments, sub_indices, diarization_engine=diarization_engine)
        sub_assign = _run_constrained_kmeans(sub_indices, sub_vecs, sub_pos, pair[0], pair[1], split_src, next_label)
        updated_segments, new_counts = _apply_turn_assignments(updated_segments, sub_assign)
        if next_label not in new_counts:
            break
    return updated_segments, new_counts


def _resolve_remote_candidates(roster_names: List[str], meta: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    local_hint = str(meta.get("local_speaker") or meta.get("operator_name") or "").strip().lower()
    local_person = next((r for r in roster_names if (local_hint and local_hint in r.lower()) or "amit" in r.lower()), roster_names[0])
    all_remote = [r for r in roster_names if r != local_person] or list(roster_names)
    return all_remote, all_remote[:2]


def split_merged_remote_cluster(
    segments: List[Dict[str, Any]],
    audio_path: Optional[Union[str, Path]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    diarization_engine: Optional[Any] = None,
    starvation_ratio: float = STARVATION_TURN_RATIO_THRESHOLD,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Splits a collapsed remote cluster into balanced sub-clusters using acoustic vectors and anchors."""
    meta = metadata or {}
    roster_names = _resolve_roster_names(meta)
    diag = detect_starved_or_merged_clusters(segments, len(roster_names), starvation_ratio=starvation_ratio)
    if not diag["is_starved"] or len(roster_names) < 3:
        return segments, {"split_performed": False, **diag}

    counts: Dict[str, int] = diag["counts"]
    starved_set: Set[str] = set(diag["starved_clusters"])
    non_starved = sorted([(k, v) for k, v in counts.items() if k not in starved_set], key=lambda x: x[1], reverse=True)
    if not non_starved:
        return segments, {"split_performed": False, **diag}

    from dual_pipeline.speaker_attribution_core.candidate_scorer import load_speaker_hints
    hints = meta.get("speaker_hints") or load_speaker_hints(meta)
    target_cluster = _select_merged_remote_cluster(
        segments, non_starved, roster_names, hints, missing_clusters=bool(diag.get("missing_clusters"))
    )
    if not target_cluster:
        return segments, {"split_performed": False, "reason": "no_merged_cluster_detected", **diag}
    reassigned_label = next(
        (s_lbl for s_lbl in sorted(starved_set) if not _is_local_ch1_cluster(segments, s_lbl)),
        f"SPEAKER_{len(counts):02d}",
    )
    target_indices = [i for i, s in enumerate(segments) if (s.get("raw_speaker") or s.get("speaker")) in (target_cluster, reassigned_label) and s.get("channel") != 1]
    if len(target_indices) < 2:
        return segments, {"split_performed": False, **diag}

    all_remote_cands, remote_cands = _resolve_remote_candidates(roster_names, meta)
    pos_anchors, tp_excl = _identify_anchor_turns(segments, target_indices, remote_cands, hints, total_remote_candidates=all_remote_cands)
    vectors = _extract_per_turn_acoustic_vectors(audio_path, segments, target_indices, diarization_engine=diarization_engine)
    enrolled_c = meta.get("enrolled_centroids") if isinstance(meta.get("enrolled_centroids"), dict) else None
    assignments = _run_constrained_kmeans(target_indices, vectors, pos_anchors, remote_cands[0], remote_cands[1], target_cluster, reassigned_label, enrolled_centroids=enrolled_c)
    updated_segments, new_counts = _apply_turn_assignments(segments, assignments)
    updated_segments, new_counts = _cascade_additional_splits(updated_segments, new_counts, roster_names, all_remote_cands, hints, audio_path, diarization_engine)
    split_meta = {
        "split_performed": True, "merged_cluster": target_cluster, "reassigned_cluster": reassigned_label,
        "candidate_pair": remote_cands, "anchor_turn_count": len(pos_anchors),
        "third_person_exclusions": {str(k): v for k, v in tp_excl.items()}, "counts_before": counts, "counts_after": new_counts,
    }
    if metadata is not None:
        metadata["subcluster_split_receipt"] = split_meta
    return updated_segments, split_meta


def bridge_live_voice_clusters_to_batch(
    batch_segments: List[Dict[str, Any]],
    live_turns: Sequence[Dict[str, Any]],
    speaker_map: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Bridges live recording Voice A..Z labels onto batch SPEAKER_00..NN clusters via temporal overlap."""
    from .stereo_alignment import compute_live_to_batch_cluster_bridge
    return compute_live_to_batch_cluster_bridge(batch_segments, live_turns, speaker_map)
