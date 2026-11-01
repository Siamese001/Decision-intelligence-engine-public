"""Deterministic healer for evidence quote pointers and verbatim text spans (L4)."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _is_non_transcript_evidence(ev: Dict[str, Any]) -> bool:
    spk = str(ev.get("speaker") or "").strip().lower()
    src = str(ev.get("source_type") or ev.get("source") or "").strip().upper()
    loc = ev.get("locator")
    loc_kind = str(loc.get("kind") or "").strip().lower() if isinstance(loc, dict) else str(loc or "").strip().lower()
    loc_val = str(loc.get("value") or "").strip().lower() if isinstance(loc, dict) else str(loc or "").strip().lower()
    return (
        spk.startswith("attachment:")
        or src in ("TRANSCRIPT_HITL", "ATTACHMENT", "COLLATERAL", "C0_RECEIPT")
        or loc_kind in ("attachment", "hitl_decision_ledger", "c0_receipt", "collateral", "region")
        or loc_kind.startswith("attachment:")
        or loc_val.startswith("attachment:")
        or ev.get("attribution_mode") == "COLLATERAL_REVIEWED"
    )



def _parse_transcript_turns(transcript: str) -> List[Dict[str, Any]]:
    if not transcript:
        return []
    header_pattern = re.compile(
        r"(?:^|\n)(?:###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n\n)?"
        r"(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?"
        r"\*\*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?([^*]+?):\*\*"
        r"\s*(?:\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?",
        re.MULTILINE,
    )
    matches = list(header_pattern.finditer(transcript))
    if not matches:
        return [{
            "timestamp": "",
            "speaker": "",
            "dialogue": transcript,
            "start": 0,
            "end": len(transcript),
        }]
    turns: List[Dict[str, Any]] = []
    for i, m in enumerate(matches):
        ts = m.group(1) or m.group(2) or m.group(3) or m.group(5) or ""
        spk = m.group(4).strip()
        d_start = m.end()
        d_end = matches[i + 1].start() if i + 1 < len(matches) else len(transcript)
        dialogue = transcript[d_start:d_end].strip()
        turns.append({
            "timestamp": ts,
            "speaker": spk,
            "dialogue": dialogue,
            "start": d_start,
            "end": d_end,
        })
    return turns


def _find_markdown_tolerant_slice(haystack: str, needle: str) -> Optional[str]:
    """Find raw slice in haystack when needle has stripped markdown (* or **)."""
    clean_needle = needle.replace("*", "").strip()
    if len(clean_needle) < 10:
        return None
    tokens = [re.escape(tok) for tok in re.split(r"\s+", clean_needle) if tok]
    if not tokens:
        return None
    pat = r"\*{0,2}" + r"\*{0,2}\s+\*{0,2}".join(tokens) + r"\*{0,2}"
    try:
        m = re.search(pat, haystack)
        if m:
            return m.group(0)
    except re.error:
        pass
    return None


def _score_matching_sentences(
    turn_dialogue: str,
    tw_set: Set[str],
    min_chars: int = 15,
) -> Optional[str]:
    raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", turn_dialogue)
    min_overlap = 2 if len(turn_dialogue.strip()) < 80 or len(tw_set) <= 3 else 3
    scored = []
    for raw in raw_sentences:
        s = re.sub(r"^(?:###\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?\*\*[^*]+?:\*\*\s*", "", raw.strip()).strip()
        if len(s) < min_chars:
            continue
        s_words = set(re.findall(r"\b[a-zA-Z0-9]+\b", s.lower()))
        overlap = len(tw_set & s_words)
        if overlap >= min_overlap:
            scored.append((overlap, s))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    return None


def _find_best_verbatim_span(
    turn_dialogue: str,
    target_text: str,
    min_chars: int = 15,
) -> Optional[str]:
    if not turn_dialogue or not target_text:
        return None
    if target_text in turn_dialogue and len(target_text) >= min_chars:
        return target_text

    md_slice = _find_markdown_tolerant_slice(turn_dialogue, target_text)
    if md_slice and len(md_slice) >= min_chars:
        return md_slice

    raw_clauses = [c.strip() for c in re.split(r"[,;.\n!?-]+\s*", target_text) if len(c.strip()) >= min_chars]
    for c in sorted(raw_clauses, key=len, reverse=True):
        if c in turn_dialogue:
            return c
        c_md = _find_markdown_tolerant_slice(turn_dialogue, c)
        if c_md and len(c_md) >= min_chars:
            return c_md

    tw = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9]+\b", target_text)]
    if not tw:
        return None

    return _score_matching_sentences(turn_dialogue, set(tw), min_chars=min_chars)


def _score_candidate_turns(
    ev_item: Dict[str, Any],
    exact_text: str,
    turns: List[Dict[str, Any]],
) -> List[Tuple[int, Dict[str, Any]]]:
    loc = ev_item.get("locator")
    loc_val = str(loc.get("value") if isinstance(loc, dict) else (loc or "")).strip()
    spk = str(ev_item.get("speaker") or "").lower()
    spk_tokens = {p for p in re.findall(r"[a-z0-9]+", spk) if p not in ("person", "speaker", "dr", "mr", "ms", "true", "false")}
    tw = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", exact_text.lower()))

    scored_turns: List[Tuple[int, Dict[str, Any]]] = []
    for t in turns:
        score = 0
        if loc_val and t.get("timestamp"):
            t_ts = t["timestamp"].lstrip("0:")
            l_ts = loc_val.lstrip("0:")
            if t_ts == l_ts or (t_ts and l_ts and (t_ts in l_ts or l_ts in t_ts)):
                score += 50
        if spk_tokens:
            turn_spk_tokens = set(re.findall(r"[a-z0-9]+", t.get("speaker", "").lower()))
            if spk_tokens & turn_spk_tokens:
                score += 25
        if tw:
            turn_tw = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", t.get("dialogue", "").lower()))
            score += len(tw & turn_tw) * 3
        if score > 0:
            scored_turns.append((score, t))

    scored_turns.sort(key=lambda x: x[0], reverse=True)
    return scored_turns


def _sync_evidence_turn_metadata(ev_item: Dict[str, Any], cand_turn: Dict[str, Any]) -> None:
    if cand_turn.get("speaker") and ev_item.get("speaker"):
        ev_item["speaker"] = cand_turn["speaker"]
    if cand_turn.get("timestamp") and "locator" in ev_item:
        loc = ev_item.get("locator")
        if isinstance(loc, dict):
            loc["value"] = cand_turn["timestamp"]
        elif isinstance(loc, str):
            ev_item["locator"] = cand_turn["timestamp"]


def _reanchor_span_with_offset(
    ev_item: Dict[str, Any],
    exact_text: str,
    turns: List[Dict[str, Any]],
    transcript: str,
) -> Tuple[Optional[str], int]:
    scored_turns = _score_candidate_turns(ev_item, exact_text, turns)
    for _, cand_turn in scored_turns:
        span = _find_best_verbatim_span(cand_turn["dialogue"], exact_text, min_chars=15)
        if span and span in transcript:
            t_pos = transcript.find(span, cand_turn["start"], cand_turn["end"])
            if t_pos == -1:
                t_pos = transcript.find(span)
            _sync_evidence_turn_metadata(ev_item, cand_turn)
            return span, t_pos

    for cand_turn in turns:
        span = _find_best_verbatim_span(cand_turn["dialogue"], exact_text, min_chars=15)
        if span and span in transcript:
            t_pos = transcript.find(span, cand_turn["start"], cand_turn["end"])
            if t_pos == -1:
                t_pos = transcript.find(span)
            _sync_evidence_turn_metadata(ev_item, cand_turn)
            return span, t_pos
    return None, -1


def _reanchor_span(
    ev_item: Dict[str, Any],
    exact_text: str,
    turns: List[Dict[str, Any]],
    transcript: str,
) -> Optional[str]:
    span, _ = _reanchor_span_with_offset(ev_item, exact_text, turns, transcript)
    return span


def heal_quote_pointers(data: Dict[str, Any], transcript: str = "") -> None:
    """Re-anchors quote pointers in graph projection evidence index against transcript."""
    evidence_index = data.get("evidence_index", {})
    if not isinstance(evidence_index, dict) or not transcript:
        return

    turns = _parse_transcript_turns(transcript)

    for ev_id, ev_item in evidence_index.items():
        if not isinstance(ev_item, dict):
            continue
        if _is_non_transcript_evidence(ev_item):
            exact_att = str(ev_item.get("exact_text") or ev_item.get("text") or "").strip()
            if exact_att and (ev_item.get("start_offset") is None or ev_item.get("end_offset") is None):
                ev_item.setdefault("start_offset", 0)
                ev_item.setdefault("end_offset", len(exact_att))
                ev_item.setdefault("quote_pointer", [ev_item["start_offset"], ev_item["end_offset"]])
            continue

        qp = ev_item.get("quote_pointer")
        exact_text = ev_item.get("exact_text") or ev_item.get("text") or ""

        target_span: Optional[str] = None
        t_pos = -1
        if exact_text and exact_text in transcript:
            target_span = exact_text
            for _, cand_turn in _score_candidate_turns(ev_item, exact_text, turns):
                cand_pos = transcript.find(exact_text, cand_turn["start"], cand_turn["end"])
                if cand_pos != -1:
                    t_pos = cand_pos
                    break
            if t_pos == -1:
                t_pos = transcript.find(exact_text)
        else:
            target_span, t_pos = _reanchor_span_with_offset(ev_item, exact_text, turns, transcript)

        if target_span and t_pos != -1:
            end_pos = t_pos + len(target_span)
            ev_item["exact_text"] = target_span
            if "text" in ev_item:
                ev_item["text"] = target_span
            if isinstance(qp, list) and len(qp) >= 2:
                qp[0] = t_pos
                qp[1] = end_pos
            elif isinstance(qp, dict):
                qp["start_offset"] = t_pos
                qp["end_offset"] = end_pos
                if "exact_text" in qp:
                    qp["exact_text"] = target_span
            elif isinstance(qp, list) and qp and isinstance(qp[0], dict):
                qp[0]["start_offset"] = t_pos
                qp[0]["end_offset"] = end_pos
                if "exact_text" in qp[0]:
                    qp[0]["exact_text"] = target_span
            else:
                ev_item["quote_pointer"] = [t_pos, end_pos]
            if "start_offset" in ev_item:
                ev_item["start_offset"] = t_pos
            if "end_offset" in ev_item:
                ev_item["end_offset"] = end_pos
