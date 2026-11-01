"""Stereo energy channel separation and acoustic speaker attribution."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .continuity_smoother import group_words_into_subsegments

logger = logging.getLogger(__name__)


def _build_stereo_subsegment(seg: Dict[str, Any], words: List[Dict[str, Any]], speaker: Optional[str]) -> Dict[str, Any]:
    """Constructs a subsegment preserving word-level timings and stereo channel metadata."""
    new_seg = dict(seg)
    new_seg["words"] = words
    new_seg["speaker"] = speaker
    if words and "channel" in words[0]:
        new_seg["channel"] = words[0]["channel"]
    new_seg["start"] = words[0].get("start", seg.get("start", 0.0))
    new_seg["end"] = words[-1].get("end", seg.get("end", 0.0))
    raw_joined = "".join(x.get("word", "") for x in words).strip()
    if " " not in raw_joined and len(words) > 1:
        raw_joined = " ".join(x.get("word", "").strip() for x in words).strip()
    new_seg["text"] = raw_joined
    new_seg["overlap"] = bool(any(len(x.get("overlapping_speakers", [])) > 1 for x in words))
    new_seg["active_speakers"] = sorted(
        set(s for x in words for s in x.get("overlapping_speakers", [speaker or "Unknown"]))
    )
    return new_seg


def load_stereo_audio_channels(audio_path: Union[str, Path]) -> Tuple[Any, Any, int]:
    """Loads Ch0 (remote) and Ch1 (local) audio arrays from WAV or media container."""
    import numpy as np
    import wave
    from pipeline_a_intake.media_extractor import extract_channel_wav

    p = Path(audio_path).resolve()
    if p.suffix.lower() in (".wav", ".wave"):
        try:
            with wave.open(str(p), "rb") as w:
                channels = w.getnchannels()
                sr = w.getframerate()
                sampwidth = w.getsampwidth()
                n_frames = w.getnframes()
                if channels == 2 and sampwidth == 2:
                    raw = w.readframes(n_frames)
                    data = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
                    return data[:, 0].astype(np.float32), data[:, 1].astype(np.float32), sr
        except Exception as e:
            logger.debug("Direct stereo wave read failed (%s), falling back to channel extractor", e)

    ch0_file = extract_channel_wav(p, channel_idx=0)
    ch1_file = extract_channel_wav(p, channel_idx=1)
    with wave.open(str(ch0_file), "rb") as w0:
        ch0 = np.frombuffer(w0.readframes(w0.getnframes()), dtype=np.int16).astype(np.float32)
        sr = w0.getframerate()
    with wave.open(str(ch1_file), "rb") as w1:
        ch1 = np.frombuffer(w1.readframes(w1.getnframes()), dtype=np.int16).astype(np.float32)
    return ch0, ch1, sr


def align_segments_via_stereo_energy(
    segments: List[Dict[str, Any]],
    stereo_audio_path: Union[str, Path],
    bleed_margin: float = 1.15,
    transcribe_window_fn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Attributes ASR segments and words to speakers via physical channel energy (Ch0 remote vs Ch1 local).

    Differentiates between direct participant speech and acoustic microphone bleed:
    - Ch0 direct (remote) > Ch1 bleed: SPEAKER_00 (Remote)
    - Ch1 direct (local) > Ch0 bleed: SPEAKER_01 (Local)
    Guarantees balanced, non-collapsed attribution without pyannote mono confusion.
    """
    import numpy as np

    if not segments:
        return []

    try:
        ch0, ch1, sr = load_stereo_audio_channels(stereo_audio_path)
    except Exception as e:
        logger.error("Failed to load stereo audio channels for attribution: %s", e)
        return segments

    def _rms(ch: np.ndarray, st: float, en: float) -> float:
        s_idx = max(0, int(st * sr))
        e_idx = min(len(ch), int(en * sr))
        if e_idx <= s_idx:
            return 0.0
        return float(np.sqrt(np.mean(ch[s_idx:e_idx] ** 2)))

    aligned_segments: List[Dict[str, Any]] = []

    for seg in segments:
        words = seg.get("words")
        seg_st = float(seg.get("start", 0.0))
        seg_en = float(seg.get("end", seg_st))

        if words and isinstance(words, list) and len(words) > 0:
            aligned_words: List[Dict[str, Any]] = []
            for w in words:
                w_st = float(w.get("start", seg_st))
                w_en = float(w.get("end", seg_en))
                rms0 = _rms(ch0, w_st, w_en)
                rms1 = _rms(ch1, w_st, w_en)

                if rms0 > rms1 * bleed_margin:
                    spk, ch_idx = "SPEAKER_00", 0
                elif rms1 > rms0 * bleed_margin:
                    spk, ch_idx = "SPEAKER_01", 1
                else:
                    spk, ch_idx = ("SPEAKER_00", 0) if rms0 >= rms1 else ("SPEAKER_01", 1)

                diff = abs(rms0 - rms1)
                total = max(1e-6, rms0 + rms1)
                conf = round(min(1.0, 0.5 + 0.5 * (diff / total)), 4)

                w_copy = dict(w)
                w_copy["speaker"] = spk
                w_copy["channel"] = ch_idx
                w_copy["alignment_confidence"] = conf
                w_copy["candidate_speakers"] = [
                    {"speaker": "SPEAKER_00", "support_score": round(rms0 / total, 4), "rms": round(rms0, 2)},
                    {"speaker": "SPEAKER_01", "support_score": round(rms1 / total, 4), "rms": round(rms1, 2)},
                ]
                aligned_words.append(w_copy)

            subs = group_words_into_subsegments(seg, aligned_words, _build_stereo_subsegment)
            aligned_segments.extend(subs)
        else:
            rms0 = _rms(ch0, seg_st, seg_en)
            rms1 = _rms(ch1, seg_st, seg_en)
            if rms0 > rms1 * bleed_margin:
                spk, ch_idx = "SPEAKER_00", 0
            elif rms1 > rms0 * bleed_margin:
                spk, ch_idx = "SPEAKER_01", 1
            else:
                spk, ch_idx = ("SPEAKER_00", 0) if rms0 >= rms1 else ("SPEAKER_01", 1)

            diff, total = abs(rms0 - rms1), max(1e-6, rms0 + rms1)
            conf = round(min(1.0, 0.5 + 0.5 * (diff / total)), 4)
            is_overlap = bool(rms0 > 100 and rms1 > 100 and min(rms0, rms1) / max(rms0, rms1) > 0.6)
            aligned_segments.append({
                **seg, "speaker": spk, "channel": ch_idx, "alignment_confidence": conf,
                "overlap": is_overlap, "overlap_redecode_required": is_overlap,
                "active_speakers": ["SPEAKER_00", "SPEAKER_01"] if is_overlap else [spk],
            })

    return redecode_overlapping_stereo_windows(aligned_segments, stereo_audio_path, transcribe_window_fn)


def redecode_overlapping_stereo_windows(
    segments: List[Dict[str, Any]],
    stereo_audio_path: Union[str, Path],
    transcribe_window_fn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Implements DEC-INGEST-02: Hybrid Single-Pass + Overlap Re-Decode."""
    if not segments or transcribe_window_fn is None:
        return segments
    out: List[Dict[str, Any]] = []
    for seg in segments:
        if not (seg.get("overlap") or seg.get("overlap_redecode_required")):
            out.append(seg)
            continue
        st, en = float(seg.get("start", 0.0)), float(seg.get("end", float(seg.get("start", 0.0))))
        try:
            ch0_text = str(transcribe_window_fn(stereo_audio_path, 0, st, en) or "").strip()
            ch1_text = str(transcribe_window_fn(stereo_audio_path, 1, st, en) or "").strip()
            if ch0_text and ch1_text:
                out.append({**seg, "speaker": "SPEAKER_00", "channel": 0, "text": ch0_text, "overlap_redecoded": True})
                out.append({**seg, "speaker": "SPEAKER_01", "channel": 1, "text": ch1_text, "overlap_redecoded": True})
                continue
        except Exception as exc:
            logger.debug("Overlap window re-decode skipped at %.2f-%.2fs: %s", st, en, exc)
        out.append(seg)
    return out


def _normalize_channel_segments(
    raw_segs: List[Dict[str, Any]], ch_idx: int, spk_label: str, boundary_overlap_sec: float
) -> List[Dict[str, Any]]:
    from .asr_deduplicator import trim_chunk_boundary_overlap
    channel_out: List[Dict[str, Any]] = []
    prev_txt = ""
    for s in raw_segs:
        txt = str(s.get("text", "")).strip()
        if prev_txt and boundary_overlap_sec > 0 and txt:
            txt = trim_chunk_boundary_overlap(prev_txt, txt)
        if not txt:
            continue
        prev_txt = txt
        s_copy = {**s, "text": txt, "channel": ch_idx, "speaker": spk_label, "raw_speaker": spk_label}
        if isinstance(s_copy.get("words"), list):
            s_copy["words"] = [{**w, "channel": ch_idx, "speaker": spk_label} for w in s_copy["words"] if isinstance(w, dict)]
        channel_out.append(s_copy)
    return channel_out


def transcribe_stereo_channels_independently(
    stereo_audio_path: Union[str, Path],
    transcribe_channel_fn: Any,
    boundary_overlap_sec: float = 1.5,
) -> List[Dict[str, Any]]:
    """Transcribes Ch0 (Remote -> SPEAKER_00) and Ch1 (Host -> SPEAKER_01) independently (D-REC-01)."""
    import tempfile
    import wave
    import numpy as np

    ch0, ch1, sr = load_stereo_audio_channels(stereo_audio_path)
    merged: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="stereo_ch_asr_") as tmp_d:
        tmp_dir = Path(tmp_d)
        for ch_idx, ch_arr, spk_label in ((0, ch0, "SPEAKER_00"), (1, ch1, "SPEAKER_01")):
            rms = float(np.sqrt(np.mean(ch_arr ** 2))) if len(ch_arr) > 0 else 0.0
            if rms < 80.0:
                continue
            ch_wav = tmp_dir / f"ch{ch_idx}.wav"
            with wave.open(str(ch_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(np.clip(ch_arr, -32768, 32767).astype(np.int16).tobytes())
            raw_segs = transcribe_channel_fn(str(ch_wav), ch_idx) or []
            merged.extend(_normalize_channel_segments(raw_segs, ch_idx, spk_label, boundary_overlap_sec))

    merged.sort(key=lambda x: (float(x.get("start", 0.0)), int(x.get("channel", 0))))
    return merged


def _accumulate_segment_overlap(
    b_seg: Dict[str, Any],
    live_turns: Sequence[Dict[str, Any]],
    smap: Dict[str, str],
    overlap_scores: Dict[str, Dict[str, float]],
) -> None:
    b_spk = str(b_seg.get("raw_speaker") or b_seg.get("speaker") or "")
    if not b_spk:
        return
    b_st, b_en = float(b_seg.get("start", 0.0)), float(b_seg.get("end", float(b_seg.get("start", 0.0))))
    for l_turn in live_turns:
        l_spk = str(l_turn.get("raw_speaker") or l_turn.get("speaker") or "")
        if not l_spk:
            continue
        l_st = float(l_turn.get("start", l_turn.get("start_sec", 0.0)))
        l_en = float(l_turn.get("end", l_turn.get("end_sec", l_st)))
        inter = max(0.0, min(b_en, l_en) - max(b_st, l_st))
        if inter > 0.05:
            resolved = smap.get(l_spk, l_spk)
            overlap_scores.setdefault(b_spk, {})[resolved] = overlap_scores.get(b_spk, {}).get(resolved, 0.0) + inter


def _vec_cosine_sim(v1: Sequence[float], v2: Sequence[float]) -> float:
    import math
    if len(v1) != len(v2) or not v1:
        return 0.0
    n1 = math.sqrt(sum(float(x) * float(x) for x in v1))
    n2 = math.sqrt(sum(float(y) * float(y) for y in v2))
    return (sum(float(a) * float(b) for a, b in zip(v1, v2)) / (n1 * n2)) if (n1 > 1e-9 and n2 > 1e-9) else 0.0


def _accumulate_centroid_similarity(
    batch_centroids: Dict[str, Sequence[float]],
    live_centroids: Dict[str, Sequence[float]],
    smap: Dict[str, str],
    overlap_scores: Dict[str, Dict[str, float]],
) -> None:
    for b_spk, b_vec in batch_centroids.items():
        for l_spk, l_vec in live_centroids.items():
            sim = _vec_cosine_sim(b_vec, l_vec)
            if sim >= 0.72:
                resolved = smap.get(l_spk, l_spk)
                overlap_scores.setdefault(b_spk, {})[resolved] = overlap_scores.get(b_spk, {}).get(resolved, 0.0) + sim * 5.0


def compute_live_to_batch_cluster_bridge(
    batch_segments: List[Dict[str, Any]],
    live_turns: Sequence[Dict[str, Any]],
    speaker_map: Optional[Dict[str, str]] = None,
    batch_centroids: Optional[Dict[str, Sequence[float]]] = None,
    live_centroids: Optional[Dict[str, Sequence[float]]] = None,
) -> Dict[str, str]:
    """Bridges live voice clusters to batch clusters combining temporal overlap and 256-D cosine similarity."""
    overlap_scores: Dict[str, Dict[str, float]] = {}
    smap = speaker_map or {}
    for b_seg in batch_segments:
        _accumulate_segment_overlap(b_seg, live_turns, smap, overlap_scores)
    if batch_centroids and live_centroids:
        _accumulate_centroid_similarity(batch_centroids, live_centroids, smap, overlap_scores)
    return {
        b_spk: max(cand_weights.items(), key=lambda kv: kv[1])[0]
        for b_spk, cand_weights in overlap_scores.items()
        if cand_weights
    }
