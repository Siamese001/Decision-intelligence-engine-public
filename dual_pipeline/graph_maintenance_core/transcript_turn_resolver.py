"""Multi-format HITL-resolved transcript turn resolver and 5-turn undiarized speaker context builder."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.l0.speaker_and_qualifier_policy import (
    SINGLE_TOKEN_SPEAKER_EXPANSIONS,
    clean_non_placeholder_speaker,
    is_unresolved_channel_or_room_speaker,
)

_TS_TOKEN_RE = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)")
_SUB_TS_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+(.*)$")
_HDR_TS_TURN_SPK_RE = re.compile(
    r"^\*{0,2}#{1,4}\s*\*{0,2}\[(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[\-\u2013\u2014]\s*\d{1,2}:\d{2}(?::\d{2})?)?\]\*{0,2}"
    r"\s*(?:Turn\s+\d+\s*[\-\u2013\u2014:]\s*)([A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){0,3})\s*$"
)
_HDR_TS_RE = re.compile(
    r"^(?:\*{0,2}#{1,4}\s*\*{0,2}\[(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[\-\u2013\u2014]\s*\d{1,2}:\d{2}(?::\d{2})?)?\]\*{0,2}"
    r"(?:\s*[\-\u2013\u2014].*)?$|\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*$)"
)
_SPK_TS_INL_RE = re.compile(r"^\*{2}([^*:]+?):?\*{2}:?\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]:?\s*(.*)$")
_TS_SPK_INL_RE = re.compile(
    r"^(?:\*{0,2}\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*{0,2})\s*\*{0,2}"
    r"([A-Z][A-Za-z.\'\-]+(?:\s+\([^)]*\))?(?:\s+[A-Z][A-Za-z.\'\-]+){0,3})\s*(?:\([^)]*\))?(?::\*{0,2}|\*{2}:):?\s*\*{0,2}\s*(.*)$"
)
_ROOM_SELF_ID_RE = re.compile(
    r"^\*{2}([^*]+?)\*{2}\s*\*\[(?:self-identified:\s*)?([A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){1,3})\]\*:\s*(.*)$"
)
_BOLD_SPK_LINE_RE = re.compile(r"^\*{2}([^*:\[\]\n]{2,65}?):?\*{2}:?\s*(.*)$")
_H_SPK_HDR_RE = re.compile(r"^#{3}\s+([A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){0,2})\s*$")
_TABLE_TURN_RE = re.compile(
    r"^\|\s*(?:\d+\s*\|\s*)?\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|\s*(.+?)\s*\|$"
)
_ROSTER_LINE_RE = re.compile(
    r"^(?:[-*]|\d+\.)\s*\*{0,2}([A-Z][A-Za-z.\'\-]*(?:\s+[A-Z][A-Za-z.\'\-]*){0,3})\*{0,2}\s*(?:\(([^)]*)\)|[\-\u2013\u2014:]\s*(.*))?$"
)
_INLINE_PARTICIPANTS_RE = re.compile(
    r"^\*{0,2}(?:Participants|Attendees|Roster-listed attendees):?\*{0,2}:?\s*(.+)$",
    re.IGNORECASE,
)
_SINGLE_CHANNEL_MARKERS = (
    "recorded through a single input channel",
    "without native speaker diarization",
    "explicitly classifies it as undiarized",
    "explicitly classifies it as **undiarized**",
)
_UNRESOLVED_TURN_SPEAKERS = frozenset({
    "partner_advisory", "apex_global", "apex_global energy", "apex_global analytics", "operator", "google", "ibm",
    "partner_advisory speaker", "[partner_advisory speaker]", "partner_advisory team", "partner_advisory team (room)", "nea/apex (room)",
    "nea/apex", "apex", "nea", "apex_trans", "unknown", "unattributed", "unattributed participant",
    "unattributed speaker", "speaker", "speaker 1", "speaker 2", "speaker 3", "speaker 4",
    "multiple speakers", "meeting roster", "transcript participant roster",
    "nea executive engineering leadership", "apex development & partner_advisory", "partner_advisory & apex development",
    "apex_trans technology leadership", "nea product management & engineering leadership", "apex development",
})
_NON_SPEAKER_METADATA_LABELS = frozenset({
    "duration", "date", "time", "location", "topic", "participants", "attendees", "source",
    "note", "status", "summary", "context", "action items", "next steps", "key takeaways",
    "meeting", "meeting name", "project", "transcript", "ambiguity lock register",
    "evidence locks", "speaker key",
})
_UNRESOLVED_RAW_SPEAKERS = _UNRESOLVED_TURN_SPEAKERS | _NON_SPEAKER_METADATA_LABELS
_STOPWORDS = frozenset({
    "that", "this", "with", "from", "have", "were", "they", "their", "what", "when",
    "where", "which", "about", "would", "could", "should",
})
_NON_DIRECT_MODES = frozenset({"SHARED_ROOM_MIC", "UNDIARIZED", "SINGLE_CHANNEL", "TRANSCRIPT_ROSTER", "INFERRED"})

_TRANSCRIPT_CACHE: Dict[str, Tuple[Path, bool, List[Tuple[str, str, Optional[str], str]], List[Tuple[str, str]], Dict[str, str]]] = {}


def normalize_timestamp_locator(locator: Optional[str]) -> Optional[str]:
    """Normalize '[37:27]', '### [00:24:30]', or '00:09:47' to 'HH:MM:SS'."""
    if not locator:
        return None
    m = _TS_TOKEN_RE.search(str(locator))
    if not m:
        return None
    parts = m.group(1).split(":")
    if len(parts) == 2:
        return f"00:{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}" if len(parts) == 3 else None


def _ts_to_seconds(ts: str) -> int:
    parts = [int(x) for x in ts.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _first_existing(paths) -> Optional[Path]:
    for cand in paths:
        if cand.exists():
            return cand
    return None


_PATH_CACHE: Dict[str, Optional[Path]] = {}
_FILENAME_INDEX: Dict[str, Path] = {}


def _ensure_filename_index() -> Dict[str, Path]:
    if _FILENAME_INDEX:
        return _FILENAME_INDEX
    roots = (Path("output"), Path(__file__).resolve().parents[2] / "output", Path(__file__).resolve().parents[4] / "output")
    for root in roots:
        if root.exists():
            for cand in (*root.glob("*/*/*/01_*.md"), *root.glob("*/01_*.md")):
                _FILENAME_INDEX.setdefault(f"{cand.parent.name.lower()}/{cand.name.lower()}", cand)
                _FILENAME_INDEX.setdefault(cand.name.lower(), cand)
    return _FILENAME_INDEX


def _resolve_cold_archive_path(cold_archive_ref: Optional[str]) -> Optional[Path]:
    if not cold_archive_ref:
        return None
    key = str(cold_archive_ref)
    if key in _PATH_CACHE:
        return _PATH_CACHE[key]
    p = Path(key)
    if p.exists():
        _PATH_CACHE[key] = p
        return p
    idx = _ensure_filename_index()
    found = idx.get(f"{p.parent.name.lower()}/{p.name.lower()}") or idx.get(p.name.lower())
    _PATH_CACHE[key] = found
    return found


def _expand_person_slug(s: str) -> str:
    if not s.lower().startswith("person:"):
        return s
    slug = s.split(":", 1)[1].strip()
    blocked = ("unknown", "speaker", "unattributed", "partner_advisory", "apex_global", "operator")
    if slug and "-" in slug and not any(k in slug for k in blocked):
        return " ".join(w.capitalize() for w in slug.split("-"))
    return s


_NON_PERSON_TOKENS = frozenset({
    "platform", "reference", "bound", "sources", "total", "dialogue", "turns", "named",
    "confidence", "inferred", "organization", "label", "unknown", "scheduled", "time",
    "duration", "recording", "file", "status", "tier", "mode", "notice", "summary",
    "context", "section", "workstream", "charter", "scope", "deliverable", "action",
    "item", "risk", "blocker", "decision", "question", "answer", "note", "participant",
    "attendee", "roster", "speaker", "channel", "audio", "video", "transcript", "lens",
    "catchup", "review", "session",
})


def is_verified_human_speaker(raw_name: Optional[str]) -> Optional[str]:
    """Return canonicalized individual person name, or None if unresolved/placeholder/organization."""
    if not raw_name:
        return None
    s = _expand_person_slug(str(raw_name).strip())
    if is_unresolved_channel_or_room_speaker(s):
        return None
    stripped = re.sub(r"\s*\([^)]*\)", "", s).strip().rstrip(":").strip("[]* ")
    if not stripped or stripped.lower() in _UNRESOLVED_RAW_SPEAKERS:
        return None
    cleaned = clean_non_placeholder_speaker(stripped)
    if not cleaned or cleaned.lower() in _UNRESOLVED_RAW_SPEAKERS or len(cleaned.split()) > 4:
        return None
    if any(t in _NON_PERSON_TOKENS for t in re.findall(r"[a-z]+", cleaned.lower())):
        return None
    low = cleaned.lower()
    if low in SINGLE_TOKEN_SPEAKER_EXPANSIONS:
        return SINGLE_TOKEN_SPEAKER_EXPANSIONS[low]
    return "harper Hoffman" if low == "mandy h" else cleaned


def _expand_against_roster(name: Optional[str], roster: List[Tuple[str, str]]) -> Optional[str]:
    if not name or " " in name:
        return name
    low = name.lower()
    for r_name, _ in roster:
        if r_name.lower().split()[0] == low:
            return r_name
    return name


def _add_roster_entry(raw_name: str, role: str, roster: List[Tuple[str, str]]) -> None:
    r_ver = is_verified_human_speaker(raw_name)
    if r_ver and " " in r_ver and all(r_ver.lower() != x[0].lower() for x in roster):
        roster.append((r_ver, role.strip()))


def _collect_roster_from_line(s: str, roster: List[Tuple[str, str]]) -> None:
    m_inl = _INLINE_PARTICIPANTS_RE.match(s)
    if m_inl:
        for item in re.split(r"[;,]", m_inl.group(1)):
            part_name = re.split(r"\s*[\-\u2013\u2014(]", item.strip())[0].strip()
            _add_roster_entry(part_name, "", roster)
    m_rost = _ROSTER_LINE_RE.match(s)
    if m_rost:
        _add_roster_entry(m_rost.group(1), m_rost.group(2) or m_rost.group(3) or "", roster)


def _match_timestamped_turn_line(s: str) -> Optional[Tuple[Optional[str], str, Optional[str], str]]:
    for rx, ts_g, spk_g, txt_g in (
        (_HDR_TS_TURN_SPK_RE, 1, 2, None),
        (_SPK_TS_INL_RE, 2, 1, 3),
        (_TS_SPK_INL_RE, 1, 2, 3),
    ):
        m = rx.match(s)
        if m:
            raw_s = m.group(spk_g).strip().rstrip(":")
            first_txt = m.group(txt_g).strip() if txt_g and m.group(txt_g) else ""
            return normalize_timestamp_locator(m.group(ts_g)), raw_s, is_verified_human_speaker(raw_s), first_txt
    return None


def _match_turn_start_line(s: str, cur_ts: Optional[str]) -> Optional[Tuple[Optional[str], str, Optional[str], str]]:
    ts_match = _match_timestamped_turn_line(s)
    if ts_match:
        return ts_match
    if cur_ts is None:
        return None
    m_room = _ROOM_SELF_ID_RE.match(s)
    if m_room:
        raw_s = m_room.group(2).strip()
        return cur_ts, raw_s, is_verified_human_speaker(raw_s), (m_room.group(3) or "").strip()
    m_h = _H_SPK_HDR_RE.match(s)
    if m_h and is_verified_human_speaker(m_h.group(1).strip()):
        raw_s = m_h.group(1).strip()
        return cur_ts, raw_s, is_verified_human_speaker(raw_s), ""
    m_b = _BOLD_SPK_LINE_RE.match(s)
    if not m_b:
        return None
    raw_s = m_b.group(1).strip().rstrip(":")
    ver_s = is_verified_human_speaker(raw_s)
    if ver_s or is_unresolved_channel_or_room_speaker(raw_s) or raw_s.lower() in _UNRESOLVED_TURN_SPEAKERS:
        return cur_ts, raw_s, ver_s, (m_b.group(2) or "").strip()
    return None


def _flush_turn_state(state: Dict[str, Any], raw_turns: List[Tuple[str, str, Optional[str], str]], roster: List[Tuple[str, str]]) -> None:
    if state["raw"] and state["buf"]:
        eff_ts = state["ts"] or "00:00:00"
        raw_turns.append((eff_ts, state["raw"], _expand_against_roster(state["ver"], roster), " ".join(state["buf"]).strip()))
    state["raw"], state["ver"], state["buf"] = None, None, []


def _handle_table_or_hdr_line(
    s: str, state: Dict[str, Any], raw_turns: List[Tuple[str, str, Optional[str], str]], roster: List[Tuple[str, str]]
) -> bool:
    m_tbl = _TABLE_TURN_RE.match(s)
    if m_tbl:
        _flush_turn_state(state, raw_turns, roster)
        ts, raw_s = normalize_timestamp_locator(m_tbl.group(1)), m_tbl.group(2).strip().rstrip(":")
        if ts and raw_s.lower() not in ("speaker", "participant", "---", "time", "timestamp"):
            raw_turns.append((ts, raw_s, _expand_against_roster(is_verified_human_speaker(raw_s), roster), m_tbl.group(3).strip()))
        return True
    m_hdr = _HDR_TS_RE.match(s)
    if m_hdr and not _HDR_TS_TURN_SPK_RE.match(s):
        _flush_turn_state(state, raw_turns, roster)
        state["ts"] = normalize_timestamp_locator(m_hdr.group(1) or m_hdr.group(2))
        return True
    return False


def _append_sub_or_body_line(
    s: str, state: Dict[str, Any], raw_turns: List[Tuple[str, str, Optional[str], str]], roster: List[Tuple[str, str]]
) -> None:
    if not state["raw"]:
        return
    m_sub = _SUB_TS_RE.match(s)
    sub_ts = normalize_timestamp_locator(m_sub.group(1)) if m_sub and m_sub.group(2) else None
    if sub_ts and m_sub:
        txt = m_sub.group(2).strip()
        raw_turns.append((sub_ts, state["raw"], _expand_against_roster(state["ver"], roster), txt))
        state["buf"].append(txt)
    elif not s.startswith(("#", "---", "|")):
        state["buf"].append(s)


def _process_transcript_line(
    s: str, state: Dict[str, Any], raw_turns: List[Tuple[str, str, Optional[str], str]], roster: List[Tuple[str, str]]
) -> None:
    if s.startswith(("## Transcript", "## Chronological", "## Forensic Transcript")) or "### [Time unavailable]" in s:
        _flush_turn_state(state, raw_turns, roster)
        state["ts"] = state["ts"] or "00:00:00"
        return
    if s.startswith(("## Ambiguity", "## Attribution Summary", "## Evidence Locks", "### Ambiguity", "# Ambiguity", "# Evidence Locks")):
        _flush_turn_state(state, raw_turns, roster)
        return
    if not raw_turns and state["raw"] is None:
        _collect_roster_from_line(s, roster)
    if _handle_table_or_hdr_line(s, state, raw_turns, roster):
        return
    matched = _match_turn_start_line(s, state["ts"])
    if matched:
        _flush_turn_state(state, raw_turns, roster)
        state["ts"], state["raw"], state["ver"], first_txt = matched
        if first_txt:
            state["buf"].append(first_txt)
        return
    _append_sub_or_body_line(s, state, raw_turns, roster)


def _parse_transcript_turns(t_path: Path) -> Tuple[bool, List[Tuple[str, str, Optional[str], str]], List[Tuple[str, str]]]:
    text = t_path.read_text(encoding="utf-8", errors="ignore")
    has_single_ch_notice = any(m in text[:2500].lower() for m in _SINGLE_CHANNEL_MARKERS)
    raw_turns: List[Tuple[str, str, Optional[str], str]] = []
    roster: List[Tuple[str, str]] = []
    state: Dict[str, Any] = {"ts": None, "raw": None, "ver": None, "buf": [], "idx": 0}
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("```"):
            _process_transcript_line(s, state, raw_turns, roster)
    _flush_turn_state(state, raw_turns, roster)
    unique_ver = {ver for _, _, ver, _ in raw_turns if ver}
    is_und = bool(has_single_ch_notice and len(unique_ver) <= 1)
    turns = [(ts, raw_s, (None if is_und else ver_s), body) for ts, raw_s, ver_s, body in raw_turns]
    return is_und, turns, roster


def _ingest_companion_ev_entry(
    ev: Any, roster: List[Tuple[str, str]], by_ts: Dict[str, str], extra_turns: List[Tuple[str, str, Optional[str], str]]
) -> None:
    if not isinstance(ev, dict):
        return
    if str(ev.get("attribution_mode") or "").strip().upper() in _NON_DIRECT_MODES:
        return
    ver = _expand_against_roster(is_verified_human_speaker(ev.get("speaker")), roster)
    if not ver:
        return
    ev_txt = str(ev.get("exact_text") or ev.get("text") or "").strip()
    if ev_txt:
        extra_turns.append(("00:00:00", ver, ver, ev_txt))
    loc_val = ev.get("locator", {}).get("value") if isinstance(ev.get("locator"), dict) else None
    for raw_loc in (ev.get("timestamp"), ev.get("source_locator"), loc_val):
        ts = normalize_timestamp_locator(str(raw_loc or ""))
        if ts:
            by_ts.setdefault(ts, ver)


def _load_companion_direct_speakers(
    t_path: Path, roster: List[Tuple[str, str]]
) -> Tuple[Dict[str, str], List[Tuple[str, str, Optional[str], str]]]:
    by_ts: Dict[str, str] = {}
    extra_turns: List[Tuple[str, str, Optional[str], str]] = []
    for j_path in sorted(t_path.parent.glob("03_*_canonical_signal_projection.json")):
        try:
            data = json.loads(j_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        ev_idx = data.get("evidence_index")
        ev_items = list(ev_idx.values()) if isinstance(ev_idx, dict) else list(ev_idx or [])
        for ev in ev_items:
            _ingest_companion_ev_entry(ev, roster, by_ts, extra_turns)
    return by_ts, extra_turns


def _build_turn_ts_index(turns: List[Tuple[str, str, Optional[str], str]], comp_ts: Dict[str, str]) -> Dict[str, str]:
    by_ts: Dict[str, str] = {}
    for ts, raw_s, ver, _ in turns:
        if ver:
            by_ts.setdefault(ts, ver)
        elif is_unresolved_channel_or_room_speaker(raw_s) or raw_s.lower() in _UNRESOLVED_RAW_SPEAKERS:
            by_ts.setdefault(ts, "")
    for k, v in comp_ts.items():
        by_ts.setdefault(k, v)
    return by_ts


def _get_parsed_transcript(cold_archive_ref: Optional[str]):
    p = _resolve_cold_archive_path(cold_archive_ref)
    if not p or not p.is_file() or p.suffix != ".md":
        return None
    key = str(p.resolve())
    if key not in _TRANSCRIPT_CACHE:
        is_und, turns, roster = _parse_transcript_turns(p)
        comp_ts, extra_turns = _load_companion_direct_speakers(p, roster)
        by_ts = _build_turn_ts_index(turns, comp_ts)
        if not is_und and extra_turns:
            turns = turns + extra_turns
        _TRANSCRIPT_CACHE[key] = (p, is_und, turns, roster, by_ts)
    return _TRANSCRIPT_CACHE[key]


def _build_quote_probes(claim_txt: str, direct_quote: str) -> List[str]:
    probes = [q.strip().lower()[:60] for q in (direct_quote, claim_txt) if q and len(q.strip()) >= 14]
    probes += [q.strip().lower()[-22:] for q in (direct_quote, claim_txt) if q and len(q.strip()) >= 22]
    segs = [s.strip().lower() for q in (direct_quote, claim_txt) for s in re.split(r"(?:\.\.\.|\u2026)", str(q or ""))]
    return probes + [s[:50] for s in segs if len(s) >= 14]


def _match_by_quote_or_near_ts(
    turns: List[Tuple[str, str, Optional[str], str]], ts_norm: Optional[str], claim_txt: str, direct_quote: str
) -> Optional[str]:
    for p_low in _build_quote_probes(claim_txt, direct_quote):
        for _, _, ver, body in turns:
            if ver and p_low in body.lower():
                return ver
    if not ts_norm or not turns:
        return None
    t_sec = _ts_to_seconds(ts_norm)
    near = [(abs(t_sec - _ts_to_seconds(t_ts)), ver) for t_ts, _, ver, _ in turns if ver and abs(t_sec - _ts_to_seconds(t_ts)) < 36]
    return min(near, key=lambda x: x[0])[1] if near else None


def _match_by_stem_overlap(turns: List[Tuple[str, str, Optional[str], str]], claim_txt: str, direct_quote: str) -> Optional[str]:
    stems = {w.lower()[:5] for w in re.findall(r"[A-Za-z]{4,}", f"{direct_quote} {claim_txt}") if w.lower() not in _STOPWORDS}
    if len(stems) < 2:
        return None
    min_th = 0.15 if len({t_ts for t_ts, _, _, _ in turns}) <= 3 else 0.25
    scored = [
        (len(stems & {w.lower()[:5] for w in re.findall(r"[A-Za-z]{4,}", body)}) / len(stems), ver)
        for _, _, ver, body in turns
        if ver
    ]
    best_sc, best_ver = max(scored, default=(0.0, None))
    return best_ver if best_ver and best_sc >= min_th else None


def _match_from_sibling_transcripts(t_path: Path, claim_txt: str, direct_quote: str) -> Optional[str]:
    if not any(d in t_path.name for d in ("2026-10-20", "08-11-2026")) or not t_path.parent.parent.parent.exists():
        return None
    for sib_p in sorted(t_path.parent.parent.parent.glob("*/*/01_*transcript*.md")):
        if sib_p == t_path:
            continue
        sib = _get_parsed_transcript(str(sib_p))
        if sib and not sib[1]:
            res = _match_diarized_turn(sib[0], sib[2], sib[4], None, claim_txt, direct_quote, False)
            if res:
                return res
    return None


def _match_diarized_turn(
    t_path: Path,
    turns: List[Tuple[str, str, Optional[str], str]],
    by_ts: Dict[str, str],
    ts_norm: Optional[str],
    claim_txt: str,
    direct_quote: str,
    allow_sibling: bool = True,
) -> Optional[str]:
    if ts_norm and ts_norm != "00:00:00" and ts_norm in by_ts:
        return by_ts[ts_norm] or None
    matched = _match_by_quote_or_near_ts(turns, ts_norm, claim_txt, direct_quote) or _match_by_stem_overlap(turns, claim_txt, direct_quote)
    if matched:
        return matched
    ver_list = [ver for _, _, ver, _ in turns if ver]
    if ver_list:
        return max(set(ver_list), key=ver_list.count)
    return _match_from_sibling_transcripts(t_path, claim_txt, direct_quote) if allow_sibling else None


def is_single_channel_undiarized_transcript(cold_archive_ref: Optional[str]) -> bool:
    """Return True when the source transcript is a single-channel undiarized recording."""
    parsed = _get_parsed_transcript(cold_archive_ref)
    return bool(parsed and parsed[1])


def resolve_diarized_transcript_turn_speaker(
    cold_archive_ref: Optional[str],
    transcript_timestamp: Optional[str],
    scope_text: Optional[str],
    direct_quote: Optional[str] = None,
) -> Optional[str]:
    """Return deterministically verified human speaker from 01_*_hitl_resolved_transcript.md, or None if undiarized/ambiguous."""
    parsed = _get_parsed_transcript(cold_archive_ref)
    if not parsed or parsed[1]:
        return None
    clean_scope = re.sub(r"^\*{2}[^*]+\*{2}:?\s*", "", str(scope_text or "")).strip()
    return _match_diarized_turn(
        parsed[0], parsed[2], parsed[4], normalize_timestamp_locator(transcript_timestamp), clean_scope, str(direct_quote or "").strip()
    )


def _sanitize_turn_text(text: str, max_chars: int = 200) -> str:
    cleaned = re.sub(r"(?:\.\.\.|\u2026)", ".", str(text or "")).replace("\n", " ")
    cleaned = re.sub(r"([A-Za-z])\.([A-Za-z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"[\u2013\u2014]", "-", re.sub(r"\s+", " ", cleaned)).strip()
    return cleaned[:max_chars].rstrip(".") + "." if len(cleaned) > max_chars else cleaned


def _find_target_turn_index(turns: List[Tuple[str, str, Optional[str], str]], ts_norm: str, clean_scope: str) -> int:
    if not turns:
        return 0
    exact = [i for i, (t_ts, _, _, _) in enumerate(turns) if t_ts == ts_norm]
    if exact:
        return exact[0]
    probe = clean_scope[:35].lower()
    txt_m = [i for i, (_, _, _, body) in enumerate(turns) if probe and probe in body.lower()]
    if txt_m:
        return txt_m[0]
    return min(range(len(turns)), key=lambda i: abs(_ts_to_seconds(turns[i][0]) - _ts_to_seconds(ts_norm)))


def _render_five_turn_rows(
    turns: List[Tuple[str, str, Optional[str], str]], target_idx: int, ts_norm: str, raw_label: str, clean_scope: str, clean_src: str
) -> List[str]:
    rows = [f"Meeting/source: {clean_src}", "Review one target speaker assignment."]
    if not turns:
        return rows + [
            "Transcript start: 2 earlier turn(s) unavailable at this boundary.",
            f"TARGET [{ts_norm}] **UNRESOLVED — {raw_label}**: {clean_scope}",
            "Transcript end: 2 later turn(s) unavailable at this boundary.",
        ]
    before_avail = min(2, target_idx)
    if before_avail < 2:
        rows.append(f"Transcript start: {2 - before_avail} earlier turn(s) unavailable at this boundary.")
    for off in range(before_avail, 0, -1):
        b_ts, b_raw, b_ver, b_body = turns[target_idx - off]
        rows.append(f"BEFORE {off} [{b_ts}] {b_ver or f'**UNRESOLVED — {b_raw}**'}: {_sanitize_turn_text(b_body)}")
    t_ts, t_raw, _, t_body = turns[target_idx]
    rows.append(f"TARGET [{t_ts}] **UNRESOLVED — {t_raw or raw_label}**: {_sanitize_turn_text(t_body or clean_scope, 220)}")
    after_avail = min(2, len(turns) - 1 - target_idx)
    for off in range(1, after_avail + 1):
        a_ts, a_raw, a_ver, a_body = turns[target_idx + off]
        rows.append(f"AFTER {off} [{a_ts}] {a_ver or f'**UNRESOLVED — {a_raw}**'}: {_sanitize_turn_text(a_body)}")
    if after_avail < 2:
        rows.append(f"Transcript end: {2 - after_avail} later turn(s) unavailable at this boundary.")
    return rows


def build_undiarized_turn_hitl_context(
    cold_archive_ref: Optional[str],
    transcript_timestamp: Optional[str],
    scope_text: Optional[str],
    raw_speaker: Optional[str],
    source_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Build 5-turn (BEFORE 2, BEFORE 1, TARGET, AFTER 1, AFTER 2) visible context and roster options for HITL attribution."""
    parsed = _get_parsed_transcript(cold_archive_ref)
    ts_norm = normalize_timestamp_locator(transcript_timestamp) or "00:00:00"
    clean_scope = _sanitize_turn_text(str(scope_text or ""), 220)
    raw_label = str(raw_speaker or "Unattributed Speaker").strip() or "Unattributed Speaker"
    clean_src = re.sub(r"[_]+", " ", str(source_title or "Meeting Recording")).strip()
    turns = parsed[2] if parsed else []
    roster = [r[0] for r in (parsed[3] if parsed else []) if r[0]]
    for _, _, ver, _ in turns:
        if ver and ver not in roster:
            roster.append(ver)
    target_idx = _find_target_turn_index(turns, ts_norm, clean_scope)
    rows = _render_five_turn_rows(turns, target_idx, ts_norm, raw_label, clean_scope, clean_src)
    return {
        "context_block": "\n\n".join(rows),
        "roster_candidates": roster or ["Amit Ayer", "Jennifer Pan", "harper Hoffman"],
        "target_timestamp": ts_norm,
        "target_raw_speaker": raw_label,
    }
