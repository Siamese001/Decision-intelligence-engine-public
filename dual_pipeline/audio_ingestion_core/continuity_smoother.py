"""
dual_pipeline/audio_ingestion_core/continuity_smoother.py

Intra-segment continuity smoothing and subsegment grouping for acoustic speaker alignment.
Prevents unvoiced onsets, intra-utterance breaths, and micro-pauses within decoded
Whisper segments from fragmenting single-speaker utterances into isolated 'Unknown' turns.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence
import numpy as np


def _embedding_cosine_distance(emb_w: Any, emb_n: Any) -> float:
    if not (isinstance(emb_w, (list, tuple, np.ndarray)) and isinstance(emb_n, (list, tuple, np.ndarray))):
        return 0.0
    v1 = np.asarray(emb_w, dtype=np.float32).ravel()
    v2 = np.asarray(emb_n, dtype=np.float32).ravel()
    if v1.size == 0 or v1.size != v2.size:
        return 0.0
    n1, n2 = float(np.linalg.norm(v1)), float(np.linalg.norm(v2))
    if n1 <= 1e-6 or n2 <= 1e-6:
        return 0.0
    return 1.0 - float(np.dot(v1, v2) / (n1 * n2))


_BACKCHANNEL_TOKENS = frozenset({
    "yeah", "yes", "yep", "okay", "ok", "right", "sure", "no", "nope", "wait", "stop", "uh-huh", "mm-hmm",
})


def _can_smooth_into_neighbor(
    w: Dict[str, Any],
    neighbor_w: Optional[Dict[str, Any]],
    max_cosine_dist: float = 0.35,
) -> bool:
    """Blocks continuity smoothing across channel changes, backchannel interjections, or variance-calibrated cosine distance."""
    var_adj = min(0.10, float(w.get("speaker_variance") or (neighbor_w.get("speaker_variance") if neighbor_w else 0.0) or 0.0) * 0.5)
    eff_max_dist = max(0.20, max_cosine_dist - var_adj)
    if float(w.get("cosine_distance_to_adjacent", 0.0)) > eff_max_dist:
        return False
    if neighbor_w is None:
        return True
    w_ch, n_ch = w.get("channel"), neighbor_w.get("channel")
    if w_ch is not None and n_ch is not None and str(w_ch) != str(n_ch):
        return False
    raw_tok = str(w.get("word") or w.get("text") or "").strip().lower().strip(".,!?")
    if raw_tok in _BACKCHANNEL_TOKENS and w.get("is_interjection"):
        return False
    return _embedding_cosine_distance(w.get("embedding"), neighbor_w.get("embedding")) <= eff_max_dist


def _smooth_multi_speaker_word(w: Dict[str, Any], prev_w: Optional[Dict[str, Any]], next_w: Optional[Dict[str, Any]]) -> None:
    prev_spk = prev_w.get("speaker") if prev_w else None
    next_spk = next_w.get("speaker") if next_w else None
    if prev_spk and next_spk and prev_spk == next_spk and prev_spk != "Unknown" and _can_smooth_into_neighbor(w, prev_w):
        w["speaker"] = prev_spk
        w.setdefault("candidate_speakers", []).append({"speaker": prev_spk, "support_score": 0.5, "continuity_supported": True})
    elif prev_spk and prev_spk != "Unknown" and prev_w and _can_smooth_into_neighbor(w, prev_w):
        if (float(w.get("start", 0.0)) - float(prev_w.get("end", 0.0))) <= 0.25:
            w["speaker"] = prev_spk
            w.setdefault("candidate_speakers", []).append({"speaker": prev_spk, "support_score": 0.4, "continuity_supported": True})
    elif next_spk and next_spk != "Unknown" and next_w and _can_smooth_into_neighbor(w, next_w):
        if (float(next_w.get("start", 0.0)) - float(w.get("end", 0.0))) <= 0.25:
            w["speaker"] = next_spk
            w.setdefault("candidate_speakers", []).append({"speaker": next_spk, "support_score": 0.4, "continuity_supported": True})


def smooth_intra_segment_continuity(
    aligned_words: List[Dict[str, Any]],
    seg: Dict[str, Any],
    intervals: Sequence[Any],
    fallback_aligner: Callable[[Dict[str, Any], Sequence[Any]], Dict[str, Any]],
) -> None:
    """Applies intra-segment continuity smoothing across words in a decoded Whisper segment."""
    if not aligned_words:
        return

    known_words = [w for w in aligned_words if w.get("speaker") not in ("Unknown", None, "")]
    unique_known = {w["speaker"] for w in known_words}

    if len(unique_known) == 1:
        seg_spk = next(iter(unique_known))
        ref_w = known_words[0]
        for w in aligned_words:
            if w.get("speaker") in ("Unknown", None, "") and _can_smooth_into_neighbor(w, ref_w):
                w["speaker"] = seg_spk
                w.setdefault("candidate_speakers", []).append({
                    "speaker": seg_spk,
                    "support_score": 0.8,
                    "segment_continuity": True,
                })
    elif len(unique_known) > 1:
        for i, w in enumerate(aligned_words):
            if w.get("speaker") in ("Unknown", None, ""):
                prev_w = aligned_words[i - 1] if i > 0 else None
                next_w = aligned_words[i + 1] if i < len(aligned_words) - 1 else None
                _smooth_multi_speaker_word(w, prev_w, next_w)
    else:
        fallback_seg = fallback_aligner(seg, intervals)
        fb_spk = fallback_seg.get("speaker", "Unknown")
        if fb_spk != "Unknown":
            for w in aligned_words:
                w["speaker"] = fb_spk
                w.setdefault("candidate_speakers", []).append({
                    "speaker": fb_spk,
                    "support_score": 0.5,
                    "fallback_overlap": True,
                })


def group_words_into_subsegments(
    seg: Dict[str, Any],
    aligned_words: List[Dict[str, Any]],
    build_subsegment_fn: Callable[[Dict[str, Any], List[Dict[str, Any]], Optional[str]], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Groups consecutive words assigned to the same speaker into coherent subsegments."""
    if not aligned_words:
        return []

    subsegments: List[Dict[str, Any]] = []
    curr_speaker: Optional[str] = None
    current_words: List[Dict[str, Any]] = []

    for w in aligned_words:
        w_speaker = w.get("speaker", "Unknown")
        if curr_speaker is None:
            curr_speaker = w_speaker

        if w_speaker != curr_speaker and current_words:
            subsegments.append(build_subsegment_fn(seg, current_words, curr_speaker))
            current_words = [w]
            curr_speaker = w_speaker
        else:
            current_words.append(w)

    if current_words:
        subsegments.append(build_subsegment_fn(seg, current_words, curr_speaker))

    return subsegments
