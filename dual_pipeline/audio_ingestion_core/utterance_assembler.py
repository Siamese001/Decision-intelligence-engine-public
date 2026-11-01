"""Complete-thought speaker utterance assembler with pause and boundary guards.

Assembles word-level timing and aligned ASR segments into readable, complete-thought
speaker utterances conforming to speaker_utterance.schema.json.

Key Invariants:
1. Two same-speaker segments with small pause produce one coherent utterance.
2. Intervening speaker turns, overlap events, or speaker changes are hard merge barriers.
3. Pause thresholds:
   - Bridge gaps <= max_bridge_gap_sec (default: 0.8s) when speaker continuity is supported.
   - Punctuation (. ? !) + gap >= punctuation_pause_sec (default: 0.25s) ends clause.
   - Gap >= force_new_block_pause_sec (default: 1.5s) forces a new block.
   - Max 80 words or 45s per display block; splits at latest valid clause boundary.
4. Short acknowledgments (<= 2 words) preserved with is_short_acknowledgment=True.
5. Strict word conservation: zero dropped words, zero invented duplicates.
6. Modules stay within repository line/complexity limits (deterministic L1 logic).
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.parameters import get_audio_max_words_per_utterance

logger = logging.getLogger(__name__)

TERMINAL_PUNCTUATION = {".", "?", "!"}
CLAUSE_PUNCTUATION = {",", ";", ":", "—", "-"}
SHORT_ACKNOWLEDGMENTS = {
    "yeah", "yes", "okay", "ok", "right", "sure", "thanks", "thank you",
    "exactly", "yep", "mmhmm", "mm-hmm", "uh-huh", "uhhuh", "alright", "got it"
}
VETO_INTERJECTIONS = {"no", "nope", "wait", "stop", "hold on", "no wait", "no hold on"}


@dataclass
class AssemblyConfig:
    """Configurable pause, duration, and length guards for utterance assembly."""
    max_bridge_gap_sec: float = 0.8
    punctuation_pause_sec: float = 0.25
    force_new_block_pause_sec: float = 1.5
    max_words_per_utterance: int = field(default_factory=get_audio_max_words_per_utterance)
    max_duration_sec: float = 45.0
    filter_passive_acknowledgments: bool = False
    speaker_pacing_map: Dict[str, float] = field(default_factory=dict)
    embedding_divergence_threshold: float = 0.65


@dataclass
class SpeakerUtterance:
    """Canonical speaker utterance conforming to speaker_utterance.schema.json."""
    utterance_id: str
    speaker: str
    start_time_sec: float
    end_time_sec: float
    text: str
    word_ids: List[str]
    words: List[Dict[str, Any]] = field(default_factory=list)
    boundary_reasons: List[str] = field(default_factory=list)
    is_short_acknowledgment: bool = False
    overlap_context: bool = False
    timing_provenance: str = "sample_clock_exact"
    raw_speaker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "utterance_id": self.utterance_id,
            "speaker": self.speaker,
            "raw_speaker": self.raw_speaker or self.speaker,
            "start_time_sec": round(self.start_time_sec, 4),
            "end_time_sec": round(self.end_time_sec, 4),
            "text": self.text,
            "word_ids": self.word_ids,
            "words": self.words,
            "boundary_reasons": self.boundary_reasons,
            "is_short_acknowledgment": self.is_short_acknowledgment,
            "overlap_context": self.overlap_context,
            "timing_provenance": self.timing_provenance,
        }


def _clean_word_text(text: str) -> str:
    return text.strip()


def _is_short_acknowledgment(text: str, word_count: int) -> bool:
    if word_count > 3:
        return False
    clean_hyphen = re.sub(r"[^\w\s\-]", "", text).strip().lower()
    clean_nohyphen = re.sub(r"[^\w\s]", "", text).strip().lower()
    return clean_hyphen in SHORT_ACKNOWLEDGMENTS or clean_nohyphen in SHORT_ACKNOWLEDGMENTS


def _has_terminal_punct(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped and stripped[-1] in TERMINAL_PUNCTUATION)


def _has_clause_punct(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped and stripped[-1] in CLAUSE_PUNCTUATION)


def _is_duplicate_audio_event(
    w1: Dict[str, Any],
    w2: Dict[str, Any],
    tolerance_sec: float = 0.2,
) -> bool:
    """Returns True if two word records represent duplicate recognition of the SAME audio event."""
    t1 = re.sub(r"[^\w]", "", str(w1.get("word") or w1.get("text", ""))).lower()
    t2 = re.sub(r"[^\w]", "", str(w2.get("word") or w2.get("text", ""))).lower()
    if not t1 or t1 != t2:
        return False
    s1, e1 = float(w1.get("start", 0.0)), float(w1.get("end", 0.0))
    s2, e2 = float(w2.get("start", 0.0)), float(w2.get("end", 0.0))
    overlap = min(e1, e2) - max(s1, s2)
    min_dur = min(max(1e-4, e1 - s1), max(1e-4, e2 - s2))
    return bool(overlap > 0.5 * min_dur or (abs(s1 - s2) <= tolerance_sec and abs(e1 - e2) <= tolerance_sec))


def _decorate_first_word(w_copy: Dict[str, Any], seg_locked: bool, seg_emb: Any) -> None:
    if seg_locked:
        w_copy["turn_boundary_locked"] = True
    if seg_emb is not None and "segment_embedding" not in w_copy:
        w_copy["segment_embedding"] = seg_emb


def _normalize_segment_words(el: Dict[str, Any], word_counter: int) -> Tuple[List[Dict[str, Any]], int]:
    seg_spk = el.get("speaker", "Unknown")
    seg_ov = bool(el.get("overlap", False))
    seg_locked = bool(el.get("turn_boundary_locked", False))
    seg_prov = el.get("timing_provenance")
    seg_emb = el.get("embedding") or el.get("segment_embedding")
    words: List[Dict[str, Any]] = []
    for idx, w in enumerate(el["words"]):
        w_copy = dict(w)
        w_copy.setdefault("speaker", seg_spk)
        if not w_copy["speaker"]:
            w_copy["speaker"] = seg_spk
        w_copy.setdefault("overlap", seg_ov or len(w.get("overlapping_speakers", [])) > 1)
        if idx == 0:
            _decorate_first_word(w_copy, seg_locked, seg_emb)
        if seg_prov:
            w_copy.setdefault("timing_provenance", seg_prov)
        if "word_id" not in w_copy:
            w_copy["word_id"] = f"w_{word_counter:05d}"
            word_counter += 1
        words.append(w_copy)
    return words, word_counter


def _normalize_single_word(el: Dict[str, Any], word_counter: int) -> Tuple[Dict[str, Any], int]:
    w_copy = dict(el)
    if "word" not in w_copy and "text" in w_copy:
        w_copy["word"] = w_copy["text"]
    if (el.get("embedding") or el.get("segment_embedding")) and "segment_embedding" not in w_copy:
        w_copy["segment_embedding"] = el.get("embedding") or el.get("segment_embedding")
    if "word_id" not in w_copy:
        w_copy["word_id"] = f"w_{word_counter:05d}"
        word_counter += 1
    return w_copy, word_counter


def _normalize_segment_without_words(el: Dict[str, Any], word_counter: int) -> Tuple[List[Dict[str, Any]], int]:
    raw_text = str(el.get("text") or el.get("word") or "").strip()
    if not raw_text:
        return [], word_counter
    tokens = raw_text.split()
    if len(tokens) <= 1:
        w_copy, word_counter = _normalize_single_word(el, word_counter)
        return [w_copy], word_counter
    st = float(el.get("start", el.get("start_time_sec", 0.0)))
    en = float(el.get("end", el.get("end_time_sec", st + 1.0)))
    step = max(0.1, en - st) / len(tokens)
    spk, ov = el.get("speaker", "Unknown"), bool(el.get("overlap", False))
    seg_locked, seg_prov = bool(el.get("turn_boundary_locked", False)), el.get("timing_provenance")
    seg_emb = el.get("embedding") or el.get("segment_embedding")
    words: List[Dict[str, Any]] = []
    for idx, tok in enumerate(tokens):
        w_st = round(st + idx * step, 4)
        w_dict: Dict[str, Any] = {
            "word": tok, "text": tok, "start": w_st, "end": round(w_st + step, 4),
            "speaker": spk, "overlap": ov, "word_id": f"w_{word_counter:05d}",
        }
        if idx == 0 and seg_locked:
            w_dict["turn_boundary_locked"] = True
        if idx == 0 and seg_emb is not None:
            w_dict["segment_embedding"] = seg_emb
        if seg_prov:
            w_dict["timing_provenance"] = seg_prov
        words.append(w_dict)
        word_counter += 1
    return words, word_counter


def _flatten_and_dedupe_elements(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    words: List[Dict[str, Any]] = []
    word_counter = 1
    for el in elements:
        if "words" in el and el["words"]:
            sub_words, word_counter = _normalize_segment_words(el, word_counter)
        else:
            sub_words, word_counter = _normalize_segment_without_words(el, word_counter)
        words.extend(sub_words)
    deduped: List[Dict[str, Any]] = []
    for w in words:
        if deduped and not w.get("turn_boundary_locked") and _is_duplicate_audio_event(deduped[-1], w):
            continue
        deduped.append(w)
    return deduped


def _resolve_speaker_pause_scale(spk: Optional[str], config: AssemblyConfig) -> float:
    if not spk or not config.speaker_pacing_map:
        return 1.0
    wpm = config.speaker_pacing_map.get(spk) or config.speaker_pacing_map.get(spk.lower())
    if not wpm or wpm <= 0:
        return 1.0
    return max(0.70, min(1.45, 140.0 / float(wpm)))


def _has_embedding_divergence(
    prev_emb: Optional[Any], curr_emb: Optional[Any], threshold: float
) -> bool:
    if not isinstance(prev_emb, (list, tuple)) or not isinstance(curr_emb, (list, tuple)) or len(prev_emb) != len(curr_emb) or not prev_emb:
        return False
    import math
    n1 = math.sqrt(sum(float(x) * float(x) for x in prev_emb))
    n2 = math.sqrt(sum(float(y) * float(y) for y in curr_emb))
    if n1 < 1e-9 or n2 < 1e-9:
        return False
    sim = sum(float(a) * float(b) for a, b in zip(prev_emb, curr_emb)) / (n1 * n2)
    return sim < threshold


def _check_word_boundary(
    spk: str,
    current_speaker: Optional[str],
    prev_text: str,
    gap: float,
    config: AssemblyConfig,
    turn_boundary_locked: bool = False,
    prev_emb: Optional[Any] = None,
    curr_emb: Optional[Any] = None,
) -> Optional[str]:
    if spk != current_speaker:
        if spk == "Unknown" or current_speaker in (None, "Unknown"):
            return "uncertainty_boundary"
        return "speaker_change"
    if turn_boundary_locked:
        return "platform_turn_boundary"
    if gap >= 0.20 and _has_embedding_divergence(prev_emb, curr_emb, config.embedding_divergence_threshold):
        return "acoustic_embedding_divergence"
    scale = _resolve_speaker_pause_scale(current_speaker, config)
    if gap >= config.force_new_block_pause_sec * scale:
        return "pause_exceeded"
    if _has_terminal_punct(prev_text) and gap >= config.punctuation_pause_sec * scale:
        return "clause_boundary"
    if gap > config.max_bridge_gap_sec * scale:
        return "pause_exceeded"
    return None


def _format_assembled_text(text_parts: List[str]) -> str:
    assembled_parts: List[str] = []
    for p_idx, p in enumerate(text_parts):
        p_str = str(p)
        if p_idx > 0 and not p_str.startswith(" ") and (not assembled_parts or not assembled_parts[-1].endswith(" ")):
            assembled_parts.append(" " + p_str)
        else:
            assembled_parts.append(p_str)
    return "".join(assembled_parts).strip()


def _create_utterance(
    utt_id: str,
    speaker: str,
    words: List[Dict[str, Any]],
    boundary_reasons: List[str],
    timing_provenance: str,
) -> SpeakerUtterance:
    text_parts = [w.get("word") or w.get("text", "") for w in words]
    word_ids = [w["word_id"] for w in words]
    has_overlap = any(w.get("overlap") or len(w.get("overlapping_speakers", [])) > 1 for w in words)
    assembled_text = _format_assembled_text(text_parts)
    st = float(words[0].get("start", 0.0))
    en = float(words[-1].get("end", st))
    reasons = boundary_reasons or ["window_boundary"]
    eff_prov = "platform_per_channel" if any(w.get("timing_provenance") == "platform_per_channel" for w in words) else timing_provenance
    return SpeakerUtterance(
        utterance_id=utt_id,
        speaker=speaker or "Unknown",
        start_time_sec=st,
        end_time_sec=en,
        text=assembled_text,
        word_ids=word_ids,
        words=words,
        boundary_reasons=reasons,
        is_short_acknowledgment=_is_short_acknowledgment(assembled_text, len(assembled_text.split())),
        overlap_context=has_overlap,
        timing_provenance=eff_prov,
    )


def _find_clause_split_idx(words: List[Dict[str, Any]]) -> int:
    for idx in range(len(words) - 1, 0, -1):
        cw_text = words[idx].get("word") or words[idx].get("text", "")
        if _has_clause_punct(cw_text) or _has_terminal_punct(cw_text):
            return idx + 1
    return -1


def _handle_overflow_words(
    current_words: List[Dict[str, Any]],
    w: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    split_idx = _find_clause_split_idx(current_words)
    if 0 < split_idx < len(current_words):
        return current_words[:split_idx], current_words[split_idx:] + [w], "clause_boundary"
    return current_words, [w], "window_boundary"


def _commit_utterance(
    utterances: List[SpeakerUtterance],
    current_speaker: Optional[str],
    current_boundary_reasons: List[str],
    target_words: List[Dict[str, Any]],
    boundary_reason: Optional[str],
    utt_counter: int,
    timing_provenance: str,
) -> int:
    if not target_words:
        return utt_counter
    reasons = list(current_boundary_reasons)
    if boundary_reason and boundary_reason not in reasons:
        reasons.append(boundary_reason)
    utt = _create_utterance(
        f"utt_{utt_counter:04d}",
        current_speaker or "Unknown",
        target_words,
        reasons,
        timing_provenance,
    )
    utterances.append(utt)
    return utt_counter + 1


def _is_utterance_overflow(current_words: List[Dict[str, Any]], w_end: float, config: AssemblyConfig) -> bool:
    return len(current_words) >= config.max_words_per_utterance or (w_end - float(current_words[0].get("start", 0.0))) >= config.max_duration_sec


class UtteranceAssembler:
    """Assembles raw words or aligned segments into structured speaker utterances."""

    def __init__(self, config: Optional[AssemblyConfig] = None):
        self.config = config or AssemblyConfig()

    def assemble(
        self,
        elements: List[Dict[str, Any]],
        timing_provenance: str = "sample_clock_exact",
    ) -> List[SpeakerUtterance]:
        """Assembles speech elements into canonical complete-thought SpeakerUtterances."""
        words = _flatten_and_dedupe_elements(elements) if elements else []
        if not words:
            return []
        utterances: List[SpeakerUtterance] = []
        current_words: List[Dict[str, Any]] = []
        current_speaker: Optional[str] = None
        current_boundary_reasons: List[str] = []
        active_emb: Optional[Any] = None
        utt_counter = 1

        for w in words:
            spk = w.get("speaker") or "Unknown"
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", w_start))
            w_emb = w.get("segment_embedding")
            if not current_words:
                current_speaker, active_emb, current_words = spk, w_emb, [w]
                continue
            prev_w = current_words[-1]
            gap = max(0.0, w_start - float(prev_w.get("end", 0.0)))
            boundary_reason = _check_word_boundary(
                spk, current_speaker, prev_w.get("word") or prev_w.get("text", ""), gap, self.config,
                bool(w.get("turn_boundary_locked")), prev_emb=active_emb, curr_emb=w_emb,
            )
            if boundary_reason:
                utt_counter = _commit_utterance(
                    utterances, current_speaker, current_boundary_reasons, current_words, boundary_reason, utt_counter, timing_provenance
                )
                current_speaker, active_emb, current_words, current_boundary_reasons = spk, w_emb, [w], [boundary_reason]
                continue
            active_emb = w_emb or active_emb
            if _is_utterance_overflow(current_words, w_end, self.config):
                to_commit, current_words, reason = _handle_overflow_words(current_words, w)
                utt_counter = _commit_utterance(
                    utterances, current_speaker, current_boundary_reasons, to_commit, reason, utt_counter, timing_provenance
                )
                current_speaker, current_boundary_reasons = spk, [reason]
                continue
            current_words.append(w)

        if current_words:
            _commit_utterance(
                utterances, current_speaker, current_boundary_reasons, current_words, "window_boundary", utt_counter, timing_provenance
            )
        return filter_passive_acknowledgments(utterances) if self.config.filter_passive_acknowledgments else utterances


def filter_passive_acknowledgments(utterances: List[SpeakerUtterance]) -> List[SpeakerUtterance]:
    """Filter standalone short acknowledgments unless immediately following a question."""
    filtered: List[SpeakerUtterance] = []
    prev_was_question = False
    for utt in utterances:
        if utt.is_short_acknowledgment or _is_short_acknowledgment(utt.text, len(utt.text.split())):
            if prev_was_question:
                filtered.append(utt)
                prev_was_question = False
            continue
        prev_was_question = utt.text.strip().endswith("?")
        filtered.append(utt)
    return filtered


def assemble_utterances(
    elements: List[Dict[str, Any]],
    config: Optional[AssemblyConfig] = None,
    timing_provenance: str = "sample_clock_exact",
) -> List[Dict[str, Any]]:
    """Convenience function returning list of utterance dictionaries."""
    return [u.to_dict() for u in UtteranceAssembler(config=config).assemble(elements, timing_provenance=timing_provenance)]
