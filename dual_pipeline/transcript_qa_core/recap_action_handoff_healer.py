"""Deterministic multi-turn speaker handoff and technical-owner reconciliation for Output 02 Action Ledgers."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .recap_healer_helpers import _audit

_TURN_RE = re.compile(
    r"###\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n+\*\*([^*:]+):\*\*\s*([^\n]+)",
    re.MULTILINE,
)
_LOC_SPAN_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)(?:[–—-](\d{1,2}:\d{2}(?::\d{2})?))?\]")
_CHAT_SHARE_RE = re.compile(
    r"\b(?:sent\s+it\s+in\s+the\s+chat|posted\s+in\s+the\s+chat|dropped\s+in\s+the\s+chat|takes\s+the\s+commercial\s+terms\s+out)\b",
    re.IGNORECASE,
)
_TECH_DISCLAIMER_RE = re.compile(
    r"\b(?:not\s+technical|isn't\s+technical|is\s+tech\s+strategy|it's\s+tech\s+strategy)\b",
    re.IGNORECASE,
)
_NAMED_ENGINEER_RE = re.compile(
    r"\b(?:engineer|architect)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    re.IGNORECASE,
)


def _ts_to_seconds(ts: str) -> int:
    parts = [int(p) for p in ts.strip().split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def parse_transcript_turns(f1_text: str) -> List[Dict[str, Any]]:
    """Parse timestamped speaker turns from Output 01 markdown."""
    turns: List[Dict[str, Any]] = []
    for idx, match in enumerate(_TURN_RE.finditer(f1_text or "")):
        ts = match.group(1).strip()
        turns.append(
            {
                "index": idx,
                "timestamp": ts,
                "seconds": _ts_to_seconds(ts),
                "speaker": match.group(2).strip(),
                "text": match.group(3).strip(),
            }
        )
    return turns


def _resolve_roster_speaker(name_hint: str, turns: List[Dict[str, Any]]) -> Optional[str]:
    hint_low = name_hint.strip().lower()
    if not hint_low:
        return None
    for t in turns:
        spk = str(t["speaker"])
        if spk.lower() == hint_low or spk.lower().startswith(hint_low + " "):
            return spk
    return None


def _select_extended_turns(
    turns: List[Dict[str, Any]], start_sec: int, end_sec: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    in_window = [t for t in turns if start_sec <= int(t["seconds"]) <= end_sec]
    if not in_window:
        return [], []
    last_idx = int(in_window[-1]["index"])
    follow_turns = [
        t
        for t in turns
        if last_idx < int(t["index"]) <= last_idx + 12 and int(t["seconds"]) <= end_sec + 55
    ]
    return in_window, follow_turns


def _find_chat_share_speaker(in_window: List[Dict[str, Any]], owner_low: str) -> Optional[str]:
    for t in in_window:
        if _CHAT_SHARE_RE.search(str(t["text"])) and str(t["speaker"]).lower() != owner_low:
            return str(t["speaker"])
    return None


def _scan_turn_cues(
    turns_slice: List[Dict[str, Any]],
    all_turns: List[Dict[str, Any]],
    owner_low: str,
    desc_low: str,
) -> Tuple[Optional[str], bool, Optional[str]]:
    named_engineer: Optional[str] = None
    has_tech_disclaimer = False
    last_relevant_ts: Optional[str] = None
    for t in turns_slice:
        txt = str(t["text"])
        m_eng = _NAMED_ENGINEER_RE.search(txt)
        if m_eng:
            named_engineer = _resolve_roster_speaker(m_eng.group(1), all_turns) or named_engineer
            last_relevant_ts = str(t["timestamp"])
        if "technical details" in txt.lower() and "technical details" in desc_low:
            last_relevant_ts = str(t["timestamp"])
        if _TECH_DISCLAIMER_RE.search(txt) and str(t["speaker"]).lower() == owner_low:
            has_tech_disclaimer = True
            last_relevant_ts = str(t["timestamp"])
    return named_engineer, has_tech_disclaimer, last_relevant_ts


def _detect_handoff_override(
    desc: str,
    current_owner: str,
    in_window: List[Dict[str, Any]],
    follow_turns: List[Dict[str, Any]],
    all_turns: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    owner_low = current_owner.strip().lower()
    desc_low = desc.lower()
    if "technical" not in desc_low:
        return None, None
    chat_speaker = _find_chat_share_speaker(in_window, owner_low)
    named_eng, has_disclaimer, last_ts = _scan_turn_cues(
        in_window + follow_turns, all_turns, owner_low, desc_low
    )
    if not (has_disclaimer or named_eng or chat_speaker):
        return None, None
    target_owner = named_eng or chat_speaker
    if target_owner and target_owner.lower() != owner_low:
        return target_owner, last_ts
    return None, None


def _reconcile_single_ledger_row(
    line: str, turns: List[Dict[str, Any]]
) -> Tuple[str, Optional[str]]:
    if not line.startswith("|") or not re.match(r"^\|\s*\d+\s*\|", line):
        return line, None
    raw_parts = line.split("|")
    has_trailing = line.rstrip().endswith("|")
    cells = [c.strip() for c in (raw_parts[1:-1] if has_trailing else raw_parts[1:])]
    if len(cells) < 6:
        return line, None

    desc, current_owner, loc_cell = cells[2], cells[3], cells[5]
    m_loc = _LOC_SPAN_RE.search(loc_cell)
    if not m_loc:
        return line, None

    start_ts = m_loc.group(1)
    end_ts = m_loc.group(2) or start_ts
    in_window, follow_turns = _select_extended_turns(
        turns, _ts_to_seconds(start_ts), _ts_to_seconds(end_ts)
    )
    if not in_window:
        return line, None

    new_owner, new_end_ts = _detect_handoff_override(
        desc, current_owner, in_window, follow_turns, turns
    )
    if not new_owner:
        return line, None

    final_end_ts = new_end_ts if (new_end_ts and _ts_to_seconds(new_end_ts) > _ts_to_seconds(end_ts)) else end_ts
    new_loc = f"[{start_ts}–{final_end_ts}]" if final_end_ts != start_ts else f"[{start_ts}]"
    raw_parts[4] = f" {new_owner} "
    raw_parts[6] = f" {new_loc} "
    updated_line = "|".join(raw_parts)
    audit_msg = (
        f"Reconciled Action Ledger handoff owner '{current_owner}' -> '{new_owner}' "
        f"and locator '{loc_cell}' -> '{new_loc}'"
    )
    return updated_line, audit_msg


def reconcile_action_ledger_handoff_owners(
    text: str,
    output_1: Optional[str] = None,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Reconciles multi-turn handoff owners and truncated locators in Output 02 Action Ledgers."""
    if not text or not output_1 or "technical" not in text.lower():
        return text
    turns = parse_transcript_turns(output_1)
    if not turns:
        return text

    new_lines: List[str] = []
    modified = False
    for line in text.splitlines():
        updated_line, audit_msg = _reconcile_single_ledger_row(line, turns)
        if audit_msg:
            modified = True
            _audit(changes, audit_log, audit_msg, "action_handoff_owner_reconciled", "RULE-H6-TECHNICAL-HANDOFF")
        new_lines.append(updated_line)

    return "\n".join(new_lines) if modified else text
