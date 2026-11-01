"""
dual_pipeline/audio_ingestion_core/asr_deduplicator.py

Cross-segment ASR deduplication and echo suppression.
Suppresses consecutive Whisper repetition loops and delayed acoustic cross-channel echoes.
"""
from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
except ImportError:  # pragma: no cover
    _rapidfuzz_fuzz = None

logger = logging.getLogger(__name__)


def _compute_similarity_ratio(a: str, b: str) -> float:
    """Computes normalized [0.0, 1.0] string similarity using local RapidFuzz with stdlib fallback."""
    if _rapidfuzz_fuzz is not None:
        return float(_rapidfuzz_fuzz.ratio(a, b)) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _normalize_text(txt: str) -> str:
    """Strips punctuation, lowercases, and trims whitespace."""
    return re.sub(r"[^\w\s]", "", txt).lower().strip()


def _is_consecutive_loop(
    norm_text: str,
    prev_norm: str,
    words_len: int,
    similarity_threshold: float,
) -> Tuple[bool, str]:
    """Detects consecutive identical or near-duplicate segment loops."""
    if norm_text == prev_norm:
        return True, "CONSECUTIVE_SEGMENT_LOOP"
    if words_len >= 4 and len(prev_norm.split()) >= 4:
        ratio = _compute_similarity_ratio(norm_text, prev_norm)
        if ratio >= similarity_threshold:
            return True, f"CONSECUTIVE_NEAR_DUPLICATE_LOOP (ratio={ratio:.2f})"
    return False, ""


def _are_distinct_known_speakers(spk_a: str, spk_b: str) -> bool:
    generic = {"", "unknown", "speaker unknown", "speaker"}
    a_clean = spk_a.strip().lower()
    b_clean = spk_b.strip().lower()
    if a_clean in generic or b_clean in generic:
        return False
    return a_clean != b_clean


def _is_delayed_echo(
    seg_start: float,
    norm_text: str,
    words_len: int,
    recent_history: List[Tuple[float, float, str, str]],
    echo_window_seconds: float,
    similarity_threshold: float,
    speaker: str = "Unknown",
    cross_speaker_echo_window_seconds: float = 1.5,
) -> bool:
    """Detects delayed cross-channel acoustic echoes within a time window.

    Same-speaker loops are checked across `echo_window_seconds` (15.0s), whereas
    distinct speakers are only treated as acoustic mic bleed when occurring within
    `cross_speaker_echo_window_seconds` (1.5s), preserving genuine conversational agreements.
    """
    if words_len < 3:
        return False
    for hist_start, hist_end, hist_norm, hist_spk in reversed(recent_history):
        gap = seg_start - hist_end
        if gap > echo_window_seconds:
            break
        if _are_distinct_known_speakers(speaker, hist_spk) and gap > cross_speaker_echo_window_seconds:
            continue
        if norm_text == hist_norm:
            return True
        if words_len >= 4 and len(hist_norm.split()) >= 4:
            ratio = _compute_similarity_ratio(norm_text, hist_norm)
            if ratio >= similarity_threshold:
                return True
    return False


def trim_chunk_boundary_overlap(
    prev_text: str,
    curr_text: str,
    min_overlap_words: int = 2,
    max_check_words: int = 15,
) -> str:
    """Detects and trims overlapping suffix from previous segment that appears as prefix in current segment."""
    prev_norm = _normalize_text(prev_text)
    curr_norm = _normalize_text(curr_text)
    prev_words = prev_norm.split()
    curr_words = curr_norm.split()
    if not prev_words or not curr_words:
        return curr_text

    max_n = min(len(prev_words), len(curr_words), max_check_words)
    best_overlap = 0
    for n in range(max_n, min_overlap_words - 1, -1):
        if prev_words[-n:] == curr_words[:n]:
            best_overlap = n
            break

    if best_overlap > 0:
        raw_tokens = curr_text.split()
        if len(raw_tokens) <= best_overlap:
            return ""
        return " ".join(raw_tokens[best_overlap:]).strip()

    return curr_text


def _apply_boundary_overlap_trim(
    prev_raw: str,
    raw_text: str,
    seg: Dict[str, Any],
    filter_log_path: Optional[Path | str],
    log_asr_drop: Any,
) -> Tuple[Optional[Dict[str, Any]], str, str, List[str]]:
    trimmed_text = trim_chunk_boundary_overlap(prev_raw, raw_text)
    if trimmed_text == raw_text:
        norm = _normalize_text(raw_text)
        return seg, raw_text, norm, norm.split()
    if not trimmed_text:
        logger.debug("Filtered boundary overlap duplicate segment: '%s'", raw_text[:60])
        log_asr_drop(filter_log_path, raw_text, "CHUNK_BOUNDARY_OVERLAP_DUPLICATE")
        return None, "", "", []
    seg_out = dict(seg)
    seg_out["text"] = trimmed_text
    norm_out = _normalize_text(trimmed_text)
    if "words" in seg_out and isinstance(seg_out["words"], list):
        seg_out["words"] = seg_out["words"][-len(trimmed_text.split()):]
    return seg_out, trimmed_text, norm_out, norm_out.split()


def deduplicate_consecutive_asr_segments(
    segments: List[Dict[str, Any]],
    similarity_threshold: float = 0.88,
    echo_window_seconds: float = 15.0,
    filter_log_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Deduplicates consecutive ASR repetition loops and delayed acoustic cross-channel echoes."""
    if not segments:
        return []

    from dual_pipeline.audio_postprocess import log_asr_drop

    deduped: List[Dict[str, Any]] = []
    recent_history: List[Tuple[float, float, str, str]] = []

    for seg in segments:
        raw_text = seg.get("text", "").strip()
        norm_text = _normalize_text(raw_text) if raw_text else ""
        if not norm_text:
            continue

        words = norm_text.split()
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        speaker = str(seg.get("speaker", "Unknown"))

        if deduped:
            prev_seg = deduped[-1]
            prev_raw = prev_seg.get("text", "").strip()
            prev_norm = _normalize_text(prev_raw)
            prev_spk = str(prev_seg.get("speaker", "Unknown"))
            prev_end = float(prev_seg.get("end", seg_start))
            distinct = _are_distinct_known_speakers(speaker, prev_spk)
            if not distinct or (seg_start - prev_end) <= 1.5:
                is_loop, reason = _is_consecutive_loop(norm_text, prev_norm, len(words), similarity_threshold)
                if is_loop:
                    log_asr_drop(filter_log_path, raw_text, reason, pattern=norm_text[:40])
                    continue
            if not distinct:
                seg_opt, raw_text, norm_text, words = _apply_boundary_overlap_trim(
                    prev_raw, raw_text, seg, filter_log_path, log_asr_drop
                )
                if seg_opt is None:
                    continue
                seg = seg_opt

        if _is_delayed_echo(
            seg_start, norm_text, len(words), recent_history, echo_window_seconds, similarity_threshold, speaker=speaker
        ):
            log_asr_drop(filter_log_path, raw_text, "CROSS_CHANNEL_DELAYED_ECHO", pattern=norm_text[:40])
            continue

        deduped.append(seg)
        recent_history.append((seg_start, seg_end, norm_text, speaker))

    return deduped


__all__ = ["deduplicate_consecutive_asr_segments", "trim_chunk_boundary_overlap"]
