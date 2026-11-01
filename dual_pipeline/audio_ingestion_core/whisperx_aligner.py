"""WhisperX Forced Phoneme Alignment and Word-Speaker Assignment Engine.

Restores the canonical WhisperX + Pyannote alignment pipeline:
1. `whisperx.load_align_model` + `whisperx.align` (WAV2VEC2 forced phoneme alignment + Punkt sentence segmentation).
2. `whisperx.assign_word_speakers` (IntervalTree dominant-speaker assignment per aligned sentence).
3. Sentence-preserving utterance assembly (never splits inside a WhisperX sentence).
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ffmpeg import ensure_ffmpeg

logger = logging.getLogger(__name__)

_ALIGN_MODEL_CACHE: Dict[Tuple[str, str], Tuple[Any, Dict[str, Any]]] = {}


def _get_whisperx_align_model(language_code: str, device: str) -> Tuple[Any, Dict[str, Any]]:
    """Loads and caches the WhisperX wav2vec2 phoneme alignment model under strict socket airgap."""
    from .acoustic_airgap import AcousticAirgapContext
    key = (language_code, device)
    if key not in _ALIGN_MODEL_CACHE:
        with AcousticAirgapContext():
            import whisperx
            model_a, align_meta = whisperx.load_align_model(language_code=language_code, device=device)
            _ALIGN_MODEL_CACHE[key] = (model_a, align_meta)
    return _ALIGN_MODEL_CACHE[key]


def align_with_whisperx(
    segments: List[Dict[str, Any]],
    audio_path: Optional[str],
    language: str = "en",
) -> Tuple[List[Dict[str, Any]], bool]:
    """Runs WhisperX wav2vec2 forced phoneme alignment on ASR segments when audio is present.

    Returns (aligned_segments, was_whisperx_aligned).
    """
    if not segments or not audio_path:
        return segments, False

    path_obj = Path(audio_path)
    if not path_obj.is_file() or path_obj.stat().st_size <= 4096:
        return segments, False

    ensure_ffmpeg()
    try:
        import torch
        import whisperx

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        clean_input: List[Dict[str, Any]] = []
        for s in segments:
            text = str(s.get("text", "")).strip()
            st = float(s.get("start", 0.0))
            en = float(s.get("end", st))
            if not text:
                continue
            if en <= st:
                en = st + 0.05
            clean_input.append({"start": st, "end": en, "text": text})

        if not clean_input:
            return segments, False

        try:
            model_a, align_meta = _get_whisperx_align_model(language or "en", device)
        except Exception:
            device = "cpu"
            model_a, align_meta = _get_whisperx_align_model(language or "en", device)

        audio = whisperx.load_audio(str(path_obj))
        aligned_res = whisperx.align(
            clean_input,
            model_a,
            align_meta,
            audio,
            device,
            return_char_alignments=False,
        )
        aligned_segs = aligned_res.get("segments", [])
        if aligned_segs:
            for seg in aligned_segs:
                seg["_whisperx_aligned"] = True
            logger.info(
                "WhisperX phoneme forced alignment completed on %s (%d sentence segments)",
                device,
                len(aligned_segs),
            )
            return aligned_segs, True
    except Exception as e:
        logger.warning("WhisperX forced alignment fallback to raw ASR timestamps: %s", e)

    return segments, False


def _min_interval_gap(st: float, en: float, intervals: List[Any], channel: Optional[Any] = None) -> float:
    best_gap = 999.0
    for inv in intervals:
        inv_ch = getattr(inv, "channel", None)
        if channel is not None and inv_ch is not None and str(channel) != str(inv_ch):
            continue
        inv_st = float(inv.start_time_sec)
        inv_en = float(inv.end_time_sec)
        if min(en, inv_en) - max(st, inv_st) > 0.0:
            return 0.0
        gap = min(abs(st - inv_en), abs(inv_st - en))
        if gap < best_gap:
            best_gap = gap
    return best_gap


def _gate_fill_nearest_gaps(
    out_segments: List[Dict[str, Any]], intervals: List[Any], max_fill_gap_sec: float
) -> None:
    for seg in out_segments:
        seg_ch = seg.get("channel")
        s_st = float(seg.get("start", 0.0))
        s_en = float(seg.get("end", s_st))
        if _min_interval_gap(s_st, s_en, intervals, channel=seg_ch) > max_fill_gap_sec:
            seg["speaker"] = "Unknown"
        for w in seg.get("words", []) if isinstance(seg.get("words"), list) else []:
            w_st = float(w.get("start", s_st))
            w_en = float(w.get("end", w_st))
            if _min_interval_gap(w_st, w_en, intervals, channel=w.get("channel", seg_ch)) > max_fill_gap_sec:
                w["speaker"] = "Unknown"


def _group_word_speaker_runs(words: List[Dict[str, Any]], seg_spk: str) -> List[Tuple[str, List[Dict[str, Any]]]]:
    runs: List[Tuple[str, List[Dict[str, Any]]]] = []
    for w in words:
        w_spk = str(w.get("speaker") or seg_spk)
        if w_spk == "Unknown" and runs:
            w_spk = runs[-1][0]
        w_copy = dict(w, speaker=w_spk)
        if runs and runs[-1][0] == w_spk:
            runs[-1][1].append(w_copy)
        else:
            runs.append((w_spk, [w_copy]))
    return runs


def _expand_multi_speaker_segments(out_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    for seg in out_segments:
        seg["_whisperx_aligned"] = True
        words = seg.get("words")
        if not isinstance(words, list) or len(words) <= 1:
            expanded.append(seg)
            continue
        seg_spk = str(seg.get("speaker") or "Unknown")
        distinct = {str(w.get("speaker") or seg_spk) for w in words if w.get("speaker") != "Unknown"}
        if len(distinct) <= 1:
            expanded.append(seg)
            continue
        for r_spk, r_words in _group_word_speaker_runs(words, seg_spk):
            st = float(r_words[0].get("start", seg.get("start", 0.0)))
            en = float(r_words[-1].get("end", seg.get("end", st)))
            txt = " ".join(str(x.get("word", "")).strip() for x in r_words).strip()
            expanded.append({
                **seg, "speaker": r_spk, "words": r_words, "start": st, "end": en,
                "text": txt, "active_speakers": sorted(distinct), "overlap": True, "_whisperx_aligned": True,
            })
    return expanded


def assign_speakers_with_whisperx(
    segments: List[Dict[str, Any]],
    intervals: List[Any],
    fill_nearest: bool = True,
    lock_sentence_speaker: bool = False,
    max_fill_gap_sec: float = 0.30,
) -> List[Dict[str, Any]]:
    """Delegates word and segment speaker assignment to whisperx.assign_word_speakers with <0.3s fill_nearest gating."""
    if not segments or not intervals:
        return copy.deepcopy(segments)

    import pandas as pd
    import whisperx

    diarize_df = pd.DataFrame([
        {"start": float(inv.start_time_sec), "end": float(inv.end_time_sec), "speaker": str(inv.speaker_cluster)}
        for inv in intervals
    ])
    result = whisperx.assign_word_speakers(diarize_df, {"segments": copy.deepcopy(segments)}, fill_nearest=fill_nearest)
    out_segments = result.get("segments", [])

    if fill_nearest:
        _gate_fill_nearest_gaps(out_segments, intervals, max_fill_gap_sec)

    if lock_sentence_speaker:
        for seg in out_segments:
            dom_spk = seg.get("speaker")
            if dom_spk and "words" in seg:
                for w in seg["words"]:
                    w["speaker"] = dom_spk
            seg["active_speakers"] = [dom_spk] if dom_spk else ["Unknown"]
            seg["overlap"] = False
            seg["_whisperx_aligned"] = True
        return out_segments

    return _expand_multi_speaker_segments(out_segments)


def whisperx_segments_to_utterances(
    segments: List[Dict[str, Any]],
    max_merge_gap_sec: float = 1.5,
    max_words: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Builds complete-thought utterances from WhisperX sentence segments without mid-sentence splitting."""
    if max_words is None:
        from dual_pipeline.parameters import get_audio_max_words_per_utterance
        max_words = get_audio_max_words_per_utterance()
    from .utterance_assembler import _is_short_acknowledgment

    utterances: List[Dict[str, Any]] = []
    word_counter = 0

    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        spk = str(seg.get("speaker") or "Unknown")
        st = float(seg.get("start", 0.0))
        en = float(seg.get("end", st))
        raw_words = seg.get("words", [])
        norm_words: List[Dict[str, Any]] = []
        word_ids: List[str] = []
        for w in raw_words:
            wid = w.get("word_id") or f"w_{word_counter:05d}"
            word_counter += 1
            w_copy = dict(w)
            w_copy["word_id"] = wid
            w_copy["speaker"] = str(w.get("speaker") or spk)
            w_copy.setdefault("overlap", False)
            norm_words.append(w_copy)
            word_ids.append(wid)

        if (
            utterances
            and utterances[-1]["speaker"] == spk
            and (st - utterances[-1]["end_time_sec"]) <= max_merge_gap_sec
            and (len(utterances[-1]["words"]) + len(norm_words)) <= max_words
        ):
            prev = utterances[-1]
            prev["end_time_sec"] = round(max(prev["end_time_sec"], en), 4)
            prev["text"] = f"{prev['text']} {text}".strip()
            prev["words"].extend(norm_words)
            prev["word_ids"].extend(word_ids)
            wc = len(prev["text"].split())
            prev["is_short_acknowledgment"] = _is_short_acknowledgment(prev["text"], wc)
        else:
            wc = len(text.split())
            utterances.append({
                "utterance_id": f"utt_{len(utterances):04d}",
                "speaker": spk,
                "start_time_sec": round(st, 4),
                "end_time_sec": round(en, 4),
                "text": text,
                "word_ids": word_ids,
                "words": norm_words,
                "boundary_reasons": ["whisperx_sentence"],
                "is_short_acknowledgment": _is_short_acknowledgment(text, wc),
                "overlap_context": False,
                "timing_provenance": "sample_clock_exact",
            })

    return utterances
