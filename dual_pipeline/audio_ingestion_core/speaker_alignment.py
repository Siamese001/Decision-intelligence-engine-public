"""Interval-supported speaker alignment with continuity guards and overlap detection.

Aligns ASR word and segment timings against acoustic diarization intervals.
Enforces:
- Exact piecewise interval support and candidate speaker scoring.
- Continuity guards to avoid artificial flapping across small pauses.
- Explicit overlap tracking (active_speakers and is_overlap).
- Preservation of genuine acoustic uncertainty ('Unknown' speakers).
- Zero paid remote LLM/diarization APIs (deterministic L1 logic).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .continuity_smoother import group_words_into_subsegments, smooth_intra_segment_continuity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcousticInterval:
    """Bounded acoustic speech interval from local diarization engine."""
    interval_id: str
    speaker_cluster: str
    start_time_sec: float
    end_time_sec: float
    confidence: float = 1.0
    channel: Union[int, str] = 0

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_time_sec - self.start_time_sec)


@dataclass
class WordEvidence:
    """Word-level acoustic evidence conforming to speaker_utterance.schema.json."""
    word_id: str
    text: str
    start_time_sec: float
    end_time_sec: float
    channel: Union[int, str] = 0
    start_sample: Optional[int] = None
    end_sample: Optional[int] = None
    candidate_speakers: List[Dict[str, Any]] = field(default_factory=list)
    is_overlap: bool = False
    alignment_confidence: Optional[float] = None
    exclusion_reason: Optional[str] = None
    speaker: str = "Unknown"


def extract_acoustic_intervals(diarization: Any) -> List[AcousticInterval]:
    """Normalizes pyannote Annotation or iterable tracks into sorted AcousticInterval list."""
    if diarization is None:
        return []

    intervals: List[AcousticInterval] = []
    idx = 0

    if hasattr(diarization, "itertracks"):
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            st = float(getattr(turn, "start", 0.0))
            en = float(getattr(turn, "end", st))
            spk = str(speaker)
            intervals.append(
                AcousticInterval(
                    interval_id=f"int_{idx:04d}",
                    speaker_cluster=spk,
                    start_time_sec=st,
                    end_time_sec=en,
                )
            )
            idx += 1
    elif isinstance(diarization, (list, tuple)):
        for item in diarization:
            if isinstance(item, AcousticInterval):
                intervals.append(item)
                idx += 1
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                st = float(item[0])
                en = float(item[1])
                spk = str(item[2])
                intervals.append(
                    AcousticInterval(
                        interval_id=f"int_{idx:04d}",
                        speaker_cluster=spk,
                        start_time_sec=st,
                        end_time_sec=en,
                    )
                )
                idx += 1
            elif isinstance(item, dict):
                st = float(item.get("start", 0.0))
                en = float(item.get("end", st))
                spk = str(item.get("speaker", "Unknown"))
                intervals.append(
                    AcousticInterval(
                        interval_id=f"int_{idx:04d}",
                        speaker_cluster=spk,
                        start_time_sec=st,
                        end_time_sec=en,
                        channel=item.get("channel", 0),
                    )
                )
                idx += 1

    intervals.sort(key=lambda x: (x.start_time_sec, x.end_time_sec))
    return intervals


def _align_segment_fallback(seg: Dict[str, Any], intervals: List[AcousticInterval]) -> Dict[str, Any]:
    seg_start = float(seg.get("start", 0.0))
    seg_end = float(seg.get("end", seg_start))
    best_speaker = "Unknown"
    max_ov = 0.0
    overlapping_spks: List[str] = []

    for inv in intervals:
        ov = max(0.0, min(seg_end, inv.end_time_sec) - max(seg_start, inv.start_time_sec))
        if ov > 0.0:
            overlapping_spks.append(inv.speaker_cluster)
        if ov > max_ov:
            max_ov = ov
            best_speaker = inv.speaker_cluster

    seg_copy = dict(seg)
    seg_copy["speaker"] = best_speaker
    seg_copy["overlap"] = len(set(overlapping_spks)) > 1
    seg_copy["active_speakers"] = sorted(set(overlapping_spks)) if overlapping_spks else [best_speaker]
    return seg_copy


def _build_subsegment(seg: Dict[str, Any], words: List[Dict[str, Any]], speaker: Optional[str]) -> Dict[str, Any]:
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


def _is_valid_continuity_bridge(w_ch: Any, inv_ch: Any, cos_dist: float) -> bool:
    if cos_dist > 0.35:
        return False
    return not (w_ch is not None and inv_ch is not None and str(w_ch) != str(inv_ch))


def _append_continuity_cand(
    spk: str, gap: float, max_gap: float, covering_spks: List[str], candidates: List[Dict[str, Any]]
) -> None:
    covering_spks.append(spk)
    candidates.append({
        "speaker": spk,
        "support_score": round(max(0.1, 1.0 - (gap / max_gap)), 4),
        "continuity_supported": True,
        "gap_sec": round(gap, 4),
    })


def _can_bridge_backward(
    w_start: float, w_end: float, prev_inv: Optional[AcousticInterval], next_inv: Optional[AcousticInterval],
    current_speaker: Optional[str], w_ch: Any, cos_dist: float,
) -> bool:
    if not (current_speaker and current_speaker != "Unknown" and prev_inv is not None):
        return False
    gap = w_start - prev_inv.end_time_sec
    if not (0.0 <= gap <= 0.25 and prev_inv.speaker_cluster == current_speaker):
        return False
    if not _is_valid_continuity_bridge(w_ch, getattr(prev_inv, "channel", None), cos_dist):
        return False
    return not (next_inv is not None and next_inv.start_time_sec < w_end and next_inv.speaker_cluster != current_speaker)


def _can_bridge_forward(
    w_start: float, w_end: float, prev_inv: Optional[AcousticInterval], next_inv: Optional[AcousticInterval],
    w_ch: Any, cos_dist: float,
) -> bool:
    if next_inv is None:
        return False
    gap = next_inv.start_time_sec - w_end
    if not (0.0 <= gap <= 0.25 and _is_valid_continuity_bridge(w_ch, getattr(next_inv, "channel", None), cos_dist)):
        return False
    return not (prev_inv is not None and (w_start - prev_inv.end_time_sec) < gap and prev_inv.speaker_cluster != next_inv.speaker_cluster)


def _try_continuity_candidates(
    w: Dict[str, Any],
    w_start: float,
    w_end: float,
    prev_inv: Optional[AcousticInterval],
    next_inv: Optional[AcousticInterval],
    current_speaker: Optional[str],
    covering_spks: List[str],
    candidates: List[Dict[str, Any]],
) -> None:
    w_ch = w.get("channel")
    cos_dist = float(w.get("cosine_distance_to_adjacent", 0.0))

    if prev_inv is not None and next_inv is not None and prev_inv.speaker_cluster == next_inv.speaker_cluster != "Unknown":
        gap_prev = max(0.0, w_start - prev_inv.end_time_sec)
        gap_next = max(0.0, next_inv.start_time_sec - w_end)
        if gap_prev <= 0.8 and gap_next <= 0.8 and _is_valid_continuity_bridge(w_ch, getattr(prev_inv, "channel", None), cos_dist):
            _append_continuity_cand(prev_inv.speaker_cluster, min(gap_prev, gap_next), 0.8, covering_spks, candidates)
            return

    if _can_bridge_backward(w_start, w_end, prev_inv, next_inv, current_speaker, w_ch, cos_dist) and prev_inv and current_speaker:
        _append_continuity_cand(current_speaker, w_start - prev_inv.end_time_sec, 0.25, covering_spks, candidates)
        return

    if _can_bridge_forward(w_start, w_end, prev_inv, next_inv, w_ch, cos_dist) and next_inv:
        _append_continuity_cand(next_inv.speaker_cluster, next_inv.start_time_sec - w_end, 0.25, covering_spks, candidates)


def _candidate_rank_score(cand: Dict[str, Any], w: Dict[str, Any]) -> float:
    base = float(cand.get("support_score", 0.0))
    tp = w.get("transition_priors")
    bonus = 0.12 * float(tp.get(str(cand.get("speaker")), 0.0)) if isinstance(tp, dict) else 0.0
    return round(base + bonus, 4)


def _align_single_word(
    w: Dict[str, Any],
    intervals: List[AcousticInterval],
    num_intervals: int,
    interval_idx: int,
    current_speaker: Optional[str],
) -> Tuple[int, str]:
    w_start = float(w.get("start", 0.0))
    w_end = float(w.get("end", w_start))
    w_dur = max(1e-5, w_end - w_start)

    while interval_idx < num_intervals and intervals[interval_idx].end_time_sec < w_start:
        interval_idx += 1

    covering_spks: List[str] = []
    candidates: List[Dict[str, Any]] = []
    temp_idx = interval_idx

    while temp_idx < num_intervals and intervals[temp_idx].start_time_sec <= w_end:
        inv = intervals[temp_idx]
        ov = max(0.0, min(w_end, inv.end_time_sec) - max(w_start, inv.start_time_sec))
        if ov > 0.0:
            covering_spks.append(inv.speaker_cluster)
            candidates.append({
                "speaker": inv.speaker_cluster,
                "support_score": round(min(1.0, ov / w_dur), 4),
                "interval_id": inv.interval_id,
                "overlap_sec": round(ov, 4),
                "interval_duration": round(inv.duration_sec, 4),
            })
        temp_idx += 1

    prev_inv = intervals[interval_idx - 1] if interval_idx > 0 else None
    next_inv = intervals[interval_idx] if interval_idx < num_intervals else None
    if not covering_spks:
        _try_continuity_candidates(w, w_start, w_end, prev_inv, next_inv, current_speaker, covering_spks, candidates)

    if candidates:
        has_multi_overlap = len(set(covering_spks)) > 1
        candidates.sort(
            key=lambda x: (
                _candidate_rank_score(x, w),
                -float(x.get("interval_duration", 9999.0)) if has_multi_overlap else 0.0,
                x.get("speaker") == current_speaker,
            ),
            reverse=True,
        )
        w_speaker = candidates[0]["speaker"]
    else:
        wx_spk = w.get("speaker")
        min_dist = min(
            abs(w_start - prev_inv.end_time_sec) if prev_inv is not None else 999.0,
            abs(next_inv.start_time_sec - w_end) if next_inv is not None else 999.0,
        )
        if wx_spk and wx_spk != "Unknown" and min_dist <= 0.30:
            w_speaker = str(wx_spk)
            covering_spks.append(w_speaker)
            candidates.append({
                "speaker": w_speaker,
                "support_score": round(max(0.1, 1.0 - min_dist), 4),
                "whisperx_assigned": True,
            })
        else:
            w_speaker = "Unknown"

    w["overlapping_speakers"] = list(dict.fromkeys(covering_spks))
    w["candidate_speakers"] = candidates
    w["speaker"] = w_speaker
    return interval_idx, w_speaker


def _check_fail_fast_unknown(aligned_segments: List[Dict[str, Any]], fail_fast_unknown_pct: float) -> None:
    if aligned_segments and fail_fast_unknown_pct > 0.0:
        unknown_count = sum(1 for s in aligned_segments if s.get("speaker") in ("Unknown", None, ""))
        pct = unknown_count / len(aligned_segments)
        if pct > fail_fast_unknown_pct:
            pct_int = int(fail_fast_unknown_pct * 100)
            raise RuntimeError(
                f"Fail-Fast: {unknown_count}/{len(aligned_segments)} segments (>{pct_int}%) have 'Unknown' speaker "
                f"after acoustic diarization. Refusing to emit unattributed transcript."
            )


def _align_words_in_segment(
    seg: Dict[str, Any],
    intervals: List[AcousticInterval],
    num_intervals: int,
    interval_idx: int,
) -> Tuple[List[Dict[str, Any]], int]:
    words = seg.get("words", [])
    if not words:
        return [_align_segment_fallback(seg, intervals)], interval_idx

    aligned_words: List[Dict[str, Any]] = []
    current_speaker: Optional[str] = None

    for w in words:
        interval_idx, w_speaker = _align_single_word(
            w, intervals, num_intervals, interval_idx, current_speaker
        )
        if w_speaker != "Unknown":
            current_speaker = w_speaker
        aligned_words.append(w)

    smooth_intra_segment_continuity(aligned_words, seg, intervals, _align_segment_fallback)
    subsegments = group_words_into_subsegments(seg, aligned_words, _build_subsegment)
    return subsegments, interval_idx


def align_segments_with_diarization(
    sanitized_segments: List[Dict[str, Any]],
    diarization: Any,
    fail_fast_unknown_pct: float = 0.50,
) -> List[Dict[str, Any]]:
    """
    Aligns sanitized ASR segments and embedded words with acoustic diarization intervals
    using WhisperX assign_word_speakers (IntervalTree) plus subsegment boundary grouping.
    """
    if diarization is None:
        return sanitized_segments

    intervals = extract_acoustic_intervals(diarization)
    num_intervals = len(intervals)
    if num_intervals == 0:
        aligned = [_align_segment_fallback(seg, []) for seg in sanitized_segments]
        _check_fail_fast_unknown(aligned, fail_fast_unknown_pct)
        return aligned

    from .whisperx_aligner import assign_speakers_with_whisperx
    wx_segments = assign_speakers_with_whisperx(sanitized_segments, intervals, fill_nearest=True)

    aligned_segments: List[Dict[str, Any]] = []
    interval_idx = 0

    for seg in wx_segments:
        subs, interval_idx = _align_words_in_segment(seg, intervals, num_intervals, interval_idx)
        aligned_segments.extend(subs)

    _check_fail_fast_unknown(aligned_segments, fail_fast_unknown_pct)
    return aligned_segments


# Re-export stereo alignment capabilities from modular subpackage
from .stereo_alignment import (  # noqa: E402
    align_segments_via_stereo_energy,
    load_stereo_audio_channels,
)

__all__ = [
    "AcousticInterval",
    "WordEvidence",
    "extract_acoustic_intervals",
    "align_segments_with_diarization",
    "align_segments_via_stereo_energy",
    "load_stereo_audio_channels",
]


