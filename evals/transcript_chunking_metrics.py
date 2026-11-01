"""Evaluation and verification metrics for speaker-turn and complete-thought chunking."""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple


def compute_boundary_reduction_ratio(raw_count: int, chunked_count: int) -> float:
    """Computes the percentage reduction in boundary count."""
    if raw_count <= 0:
        return 0.0
    return round((1.0 - (chunked_count / raw_count)) * 100.0, 2)


def compute_turn_coverage(mapped_count: int, total_count: int) -> float:
    """Computes the ratio of mapped/attributed turns: M / N."""
    if total_count <= 0:
        return 0.0
    return round(mapped_count / total_count, 4)


def compute_attribution_accuracy(correct_count: int, mapped_count: int) -> float:
    """Computes named assignment accuracy: C / M."""
    if mapped_count <= 0:
        return 0.0
    return round(correct_count / mapped_count, 4)


def compute_true_identified_share(correct_count: int, total_count: int) -> float:
    """Computes overall true identified share: C / N."""
    if total_count <= 0:
        return 0.0
    return round(correct_count / total_count, 4)


def verify_content_conservation(
    raw_turns: List[Dict[str, Any]],
    assembled_utterances: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    Verifies that complete-thought assembly conserves 100% of words without loss, duplication, or reordering.
    """
    raw_tokens: List[str] = []
    for t in raw_turns:
        txt = t.get("utterance_text") or t.get("text", "")
        raw_tokens.extend(txt.split())

    assembled_tokens: List[str] = []
    for u in assembled_utterances:
        txt = u.get("utterance_text") or u.get("text", "")
        assembled_tokens.extend(txt.split())

    if len(raw_tokens) != len(assembled_tokens):
        return (
            False,
            f"Token count mismatch: raw={len(raw_tokens)}, assembled={len(assembled_tokens)}",
        )

    for idx, (r_tok, a_tok) in enumerate(zip(raw_tokens, assembled_tokens)):
        if r_tok != a_tok:
            return (
                False,
                f"Token mismatch at index {idx}: expected '{r_tok}', got '{a_tok}'",
            )

    return True, f"100% word conservation verified ({len(raw_tokens)} words)"


def compute_time_level_metrics(
    reference_turns: List[Dict[str, Any]],
    hypothesis_turns: List[Dict[str, Any]],
    sample_rate: int = 16000,
    frame_ms: int = 100,
) -> Dict[str, Any]:
    """
    Computes time-level frame accuracy, missed speech, false alarm, and speaker confusion.
    """
    if not reference_turns and not hypothesis_turns:
        return {
            "total_speech_sec": 0.0,
            "der": 0.0,
            "confusion_sec": 0.0,
            "missed_sec": 0.0,
            "false_alarm_sec": 0.0,
        }

    max_ref = max((float(t.get("end_time_sec", t.get("end", 0.0))) for t in reference_turns), default=0.0)
    max_hyp = max((float(t.get("end_time_sec", t.get("end", 0.0))) for t in hypothesis_turns), default=0.0)
    total_sec = max(max_ref, max_hyp)

    frame_sec = frame_ms / 1000.0
    total_frames = math.ceil(total_sec / frame_sec) if total_sec > 0 else 1

    ref_frames: List[Optional[str]] = [None] * total_frames
    hyp_frames: List[Optional[str]] = [None] * total_frames

    for t in reference_turns:
        spk = t.get("reference_speaker") or t.get("speaker")
        st = float(t.get("start_time_sec", t.get("start", 0.0)))
        en = float(t.get("end_time_sec", t.get("end", st)))
        s_f = min(int(st / frame_sec), total_frames - 1)
        e_f = min(int(en / frame_sec), total_frames)
        for f in range(s_f, e_f):
            ref_frames[f] = spk

    for t in hypothesis_turns:
        spk = t.get("speaker")
        st = float(t.get("start_time_sec", t.get("start", 0.0)))
        en = float(t.get("end_time_sec", t.get("end", st)))
        s_f = min(int(st / frame_sec), total_frames - 1)
        e_f = min(int(en / frame_sec), total_frames)
        for f in range(s_f, e_f):
            hyp_frames[f] = spk

    confusion_frames = 0
    missed_frames = 0
    false_alarm_frames = 0
    total_speech_frames = 0

    for f in range(total_frames):
        r_spk = ref_frames[f]
        h_spk = hyp_frames[f]

        if r_spk is not None:
            total_speech_frames += 1
            if h_spk is None:
                missed_frames += 1
            elif h_spk != r_spk:
                confusion_frames += 1
        else:
            if h_spk is not None:
                false_alarm_frames += 1

    der = (
        (confusion_frames + missed_frames + false_alarm_frames) / total_speech_frames
        if total_speech_frames > 0
        else 0.0
    )

    return {
        "total_speech_sec": round(total_speech_frames * frame_sec, 2),
        "der": round(der, 4),
        "der_pct": round(der * 100.0, 2),
        "confusion_sec": round(confusion_frames * frame_sec, 2),
        "missed_sec": round(missed_frames * frame_sec, 2),
        "false_alarm_sec": round(false_alarm_frames * frame_sec, 2),
    }


def compute_chunking_efficiency(
    raw_turns: List[Dict[str, Any]],
    assembled_utterances: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Computes distribution metrics comparing fragmented turns vs assembled complete thoughts:
    - Single-word utterance ratio
    - At-most-three-word utterance ratio
    - Average words per utterance
    - Boundary reduction percentage
    """
    raw_n = len(raw_turns)
    asm_n = len(assembled_utterances)

    raw_words = [len((t.get("utterance_text") or t.get("text", "")).split()) for t in raw_turns]
    asm_words = [len((u.get("utterance_text") or u.get("text", "")).split()) for u in assembled_utterances]

    raw_1w = sum(1 for w in raw_words if w == 1)
    raw_le3w = sum(1 for w in raw_words if w <= 3)

    asm_1w = sum(1 for w in asm_words if w == 1)
    asm_le3w = sum(1 for w in asm_words if w <= 3)

    reduction = compute_boundary_reduction_ratio(raw_n, asm_n)

    return {
        "raw_turn_count": raw_n,
        "assembled_utterance_count": asm_n,
        "boundary_reduction_pct": reduction,
        "raw_single_word_pct": round(raw_1w / max(1, raw_n) * 100.0, 2),
        "assembled_single_word_pct": round(asm_1w / max(1, asm_n) * 100.0, 2),
        "raw_le3_word_pct": round(raw_le3w / max(1, raw_n) * 100.0, 2),
        "assembled_le3_word_pct": round(asm_le3w / max(1, asm_n) * 100.0, 2),
        "raw_avg_words_per_turn": round(sum(raw_words) / max(1, raw_n), 2),
        "assembled_avg_words_per_utterance": round(sum(asm_words) / max(1, asm_n), 2),
    }
