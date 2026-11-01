"""
dual_pipeline/live_meeting_recorder_core/speaker_hint_matcher.py

Token overlap and fuzzy quote matching for speaker hint reconciliation.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Optional, Set, Tuple


_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what", "which",
    "this", "that", "these", "those", "then", "just", "so", "than", "such",
    "both", "through", "about", "for", "is", "of", "while", "during", "to",
    "he", "she", "it", "they", "we", "you", "i", "his", "her", "its", "their",
    "our", "your", "my", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "in", "on", "at", "by", "with", "from", "up",
    "down", "into", "over", "after", "are", "am", "will", "would", "can",
    "could", "should", "may", "might", "must", "shall", "get", "got", "going",
    "there", "here", "when", "where", "why", "how", "all", "any", "each",
    "few", "more", "most", "other", "some", "no", "nor", "not", "only", "own",
    "same", "too", "very", "also", "even", "still", "well", "kind", "lot",
    "thing", "things",
})


_CONTRACTION_MAP = (
    (re.compile(r"\bwon['’]t\b"), "will not"),
    (re.compile(r"\bcan['’]t\b"), "cannot"),
    (re.compile(r"\bshan['’]t\b"), "shall not"),
    (re.compile(r"\bain['’]t\b"), "is not"),
    (re.compile(r"n['’]t\b"), " not"),
    (re.compile(r"\blet['’]s\b"), "let us"),
    (re.compile(r"\bi['’]m\b"), "i am"),
    (re.compile(r"['’]re\b"), " are"),
    (re.compile(r"\b(he|she|it|that|what|there|here|who|where|when|why|how)['’]s\b"), r"\1 is"),
    (re.compile(r"['’]ve\b"), " have"),
    (re.compile(r"['’]ll\b"), " will"),
    (re.compile(r"['’]d\b"), " would"),
)


def normalize_search_text(text: str) -> str:
    """Lowercases, expands English contractions, removes punctuation, and collapses whitespace."""
    if not text:
        return ""
    lower = text.lower()
    lower = re.sub(r"\buiux\b", "ui ux", lower)
    for pat, repl in _CONTRACTION_MAP:
        lower = pat.sub(repl, lower)
    clean = re.sub(r"[^\w\s]", " ", lower)
    return " ".join(clean.split())


def segment_contains_quote(s: Dict[str, Any], clean_quote: str) -> bool:
    """Checks if any textual representation of segment contains the quote."""
    s_text = normalize_search_text(str(s.get("text", "")))
    s_verb = normalize_search_text(str(s.get("verbatim", "")))
    s_norm = normalize_search_text(str(s.get("normalized", "")))
    return (clean_quote in s_text) or (clean_quote in s_verb) or (clean_quote in s_norm)


def _seg_sec(s: Dict[str, Any]) -> float:
    return float(s.get("start_sec", s.get("turn_sec", s.get("seconds", 0.0))) or 0.0)


def extract_phonetic_drift_candidates(quote: str, matched_segment_text: str) -> List[Tuple[str, str]]:
    """Extracts (asr_token, canonical_quote_token) phonetic drift candidates from matched hint segments."""
    q_words = [w for w in normalize_search_text(quote).split() if len(w) >= 4 and w not in _STOPWORDS]
    s_words = [w for w in normalize_search_text(matched_segment_text).split() if len(w) >= 4 and w not in _STOPWORDS]
    drifts: List[Tuple[str, str]] = []
    for qw in q_words:
        if qw in s_words:
            continue
        for sw in s_words:
            if sw[0] == qw[0] and 0.75 <= SequenceMatcher(None, qw, sw).ratio() < 1.0:
                if (sw, qw) not in drifts:
                    drifts.append((sw, qw))
    return drifts


def _phonetic_single_token_match(segments: List[Dict[str, Any]], target: str, elapsed_seconds: Optional[float]) -> List[Dict[str, Any]]:
    out = []
    for s in segments:
        if elapsed_seconds is not None and abs(_seg_sec(s) - elapsed_seconds) > 60.0:
            continue
        words = normalize_search_text(str(s.get("text", "")) or str(s.get("verbatim", ""))).split()
        if any(len(w) >= 4 and w[0] == target[0] and SequenceMatcher(None, target, w).ratio() >= 0.75 for w in words):
            out.append(s)
    return out


def _match_short_tokens(
    segments: List[Dict[str, Any]], tokens: List[str], content_tokens: Optional[List[str]] = None, elapsed_seconds: Optional[float] = None
) -> List[Dict[str, Any]]:
    matched, clean_phrase = [], " ".join(tokens)
    c_toks = content_tokens if content_tokens is not None else [t for t in tokens if t not in _STOPWORDS]
    for s in segments:
        s_text = normalize_search_text(str(s.get("text", "")) or str(s.get("verbatim", "")))
        s_words = s_text.split()
        if clean_phrase in s_text or (len(c_toks) >= 2 and all(t in s_words for t in c_toks)) or (len(tokens) == 1 and c_toks and c_toks[0] in s_words):
            matched.append(s)
    if not matched and len(tokens) == 1 and len(c_toks) == 1 and len(c_toks[0]) >= 4:
        return _phonetic_single_token_match(segments, c_toks[0], elapsed_seconds)
    return matched


def _score_single_segment_overlap(
    segments: List[Dict[str, Any]], quote_set: Set[str], min_ratio: float
) -> List[Tuple[float, Dict[str, Any]]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    min_common = 2 if len(quote_set) >= 2 else 1
    for s in segments:
        s_text = normalize_search_text(str(s.get("text", "")) or str(s.get("verbatim", "")))
        s_tokens = set(s_text.split())
        if not s_tokens:
            continue
        common = quote_set.intersection(s_tokens)
        ratio = len(common) / len(quote_set)
        if len(common) >= min_common and ratio >= min_ratio:
            scored.append((ratio, s))
    return scored


def _score_adjacent_segment_overlap(
    segments: List[Dict[str, Any]], quote_set: Set[str], min_ratio: float
) -> List[Tuple[float, Dict[str, Any]]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    min_common = 2 if len(quote_set) >= 2 else 1
    for i in range(len(segments) - 1):
        s1, s2 = segments[i], segments[i + 1]
        t1 = normalize_search_text(str(s1.get("text", "")) or str(s1.get("verbatim", "")))
        t2 = normalize_search_text(str(s2.get("text", "")) or str(s2.get("verbatim", "")))
        combined = set(f"{t1} {t2}".split())
        common = quote_set.intersection(combined)
        ratio = len(common) / len(quote_set)
        if len(common) >= min_common and ratio >= min_ratio:
            scored.append((ratio, s1))
            scored.append((ratio, s2))
    return scored


def token_overlap_match(
    segments: List[Dict[str, Any]],
    clean_quote: str,
    min_ratio: float = 0.65,
    elapsed_seconds: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Scores segment token overlap against content quote tokens (excluding stopwords)."""
    tokens = clean_quote.split()
    if not tokens:
        return []
    content_tokens = [t for t in tokens if t not in _STOPWORDS]
    if not content_tokens:
        return []
    if len(tokens) < 3 or len(content_tokens) < 2:
        return _match_short_tokens(segments, tokens, content_tokens=content_tokens, elapsed_seconds=elapsed_seconds)

    quote_set = set(content_tokens)
    scored = _score_single_segment_overlap(segments, quote_set, min_ratio)
    if not scored:
        scored = _score_adjacent_segment_overlap(segments, quote_set, min_ratio)

    if not scored:
        return []
    max_score = max(score for score, _ in scored)
    top = [s for score, s in scored if score >= max_score - 0.1]
    if len(top) > 1 and elapsed_seconds is not None:
        near = [s for s in top if abs(_seg_sec(s) - elapsed_seconds) <= 90.0]
        if near:
            return [min(near, key=lambda s: abs(_seg_sec(s) - elapsed_seconds))]
    return top


def relabel_markdown_for_hint(
    md_text: str, target_voice_id: str, speaker_name: str, quote: str, is_generic_host: bool
) -> str:
    """Relabels speaker headers in live markdown transcript (targeted line match when shared/generic)."""
    if is_generic_host and quote:
        norm_q = normalize_search_text(quote)
        out_lines = []
        for line in md_text.splitlines():
            norm_line = normalize_search_text(line)
            if norm_q and (norm_q in norm_line or bool(token_overlap_match([{"text": line}], norm_q, min_ratio=0.60))):
                line = re.sub(
                    r"((?:\*\*|\#\#\s*)?\[\d{2}:\d{2}(?::\d{2})?\](?:\*\*)?\s+)[^:]+:",
                    rf"\1{speaker_name}:",
                    line,
                    count=1,
                )
            out_lines.append(line)
        return "\n".join(out_lines) + ("\n" if md_text.endswith("\n") else "")
    pattern = re.compile(
        rf"((?:\*\*|\#\#\s*)?\[\d{{2}}:\d{{2}}(?::\d{{2}})?\](?:\*\*)?\s+){re.escape(target_voice_id)}:"
    )
    return pattern.sub(rf"\1{speaker_name}:", md_text)


def _expand_candidate_dirs(candidate_dirs: List[Any]) -> List[Any]:
    from pathlib import Path
    expanded: List[Path] = []
    for d in candidate_dirs:
        if not d:
            continue
        p = Path(d)
        expanded.extend([p, p / ".cache"])
        if p.name.startswith(".staging_"):
            expanded.extend([p.parent, p.parent / ".cache"])
    return expanded


def _read_json_dict_list(fpath: Any) -> List[Dict[str, Any]]:
    import json
    try:
        raw = json.loads(fpath.read_text(encoding="utf-8"))
        return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
    except Exception:
        return []


def _extract_registry_bindings(raw: Dict[str, Any], eff_attendees: Any) -> Dict[str, str]:
    from .attendees import resolve_roster_name
    bindings: Dict[str, str] = {}
    for vid, spk in (raw.get("speaker_bindings") or {}).items():
        if vid and spk and str(vid).lower() not in ("voice_host", "host", "voice host"):
            bindings[str(vid)] = resolve_roster_name(str(spk), eff_attendees)
    for vid, c in (raw.get("clusters") or {}).items():
        spk = c.get("resolved_speaker") if isinstance(c, dict) else None
        if vid and spk and str(vid).lower() not in ("voice_host", "host", "voice host"):
            bindings[str(vid)] = resolve_roster_name(str(spk), eff_attendees)
    return bindings


def _load_voice_bindings_from_dirs(candidate_dirs: List[Any], eff_attendees: Any) -> Dict[str, str]:
    import json
    bindings: Dict[str, str] = {}
    for d in _expand_candidate_dirs(candidate_dirs):
        fpath = d / "voice_registry.json"
        if fpath.is_file():
            try:
                raw = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    bindings.update(_extract_registry_bindings(raw, eff_attendees))
            except Exception:
                continue
    return bindings


def _load_hint_entries_from_dirs(candidate_dirs: List[Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen_files: Set[str] = set()
    for d in _expand_candidate_dirs(candidate_dirs):
        for fname in ("speaker_hints_queue.json", "speaker_hints.json"):
            fpath = d / fname
            if fpath.is_file() and (resolved := str(fpath.resolve())) not in seen_files:
                seen_files.add(resolved)
                entries.extend(_read_json_dict_list(fpath))
    return entries


def _read_manifest_attendees(check_d: Any) -> List[str]:
    import json
    if not check_d.is_dir():
        return []
    for mf in sorted(check_d.glob("*manifest*.json")):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            if data.get("attendees"):
                return list(data["attendees"])
        except Exception:
            continue
    return []


_NON_NAME_SLUG_TOKENS = frozenset({
    "ai", "trends", "webinar", "operator", "meeting", "session", "call", "sync", "review",
    "weekly", "monthly", "daily", "standup", "kickoff", "debrief", "demo", "live", "recording",
    "discussion", "workshop", "panel", "presentation", "training", "onboarding", "governance",
    "architecture", "strategy", "planning", "project", "nexus", "lens", "sherpa", "conduit",
    "jarvis", "vanguard", "nee", "nea", "apex_trans", "apex", "partner_advisory", "google", "team", "enterprise",
})


def _infer_attendees_from_dirs(candidate_dirs: List[Any]) -> List[str]:
    for p in _expand_candidate_dirs(candidate_dirs):
        found = _read_manifest_attendees(p)
        if found:
            return found
        slug = re.sub(r"_\d{6}$", "", re.sub(r"^\.staging_", "", p.name))
        tokens = [t for t in slug.split("_") if t and t[0].isupper() and t.lower() not in _NON_NAME_SLUG_TOKENS]
        if len(tokens) >= 2 and len(tokens) % 2 == 0:
            return [f"{tokens[i]} {tokens[i + 1]}" for i in range(0, len(tokens), 2)]
    return []


def _is_role_or_expired_hint(h: Dict[str, Any], quote: str) -> bool:
    if h.get("status") == "EXPIRED" or quote.startswith("[ROLE_HINT:"):
        return True
    meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
    return h.get("attribution_type") == "role_hint" or meta.get("attribution_type") == "role_hint"


def _append_synthetic_hint_turn(
    h: Dict[str, Any], turns: List[Dict[str, Any]], canonical_spk: str, quote: str
) -> None:
    last_sec = float(turns[-1].get("turn_sec", 0.0)) + 1.0 if turns else 1.0
    raw_elapsed = float(h.get("session_elapsed_seconds") or last_sec)
    synth_sec = last_sec if (turns and raw_elapsed > last_sec + 30.0) else raw_elapsed
    fmt_ts = f"{int(synth_sec) // 3600:02d}:{(int(synth_sec) % 3600) // 60:02d}:{int(synth_sec) % 60:02d}"
    last_ts = next((str(t.get("timestamp")) for t in reversed(turns) if t.get("timestamp") not in (None, "", "00:00:00")), None)
    synth_ts = fmt_ts if synth_sec > 0 else (last_ts or "00:00:01")
    turns.append({"speaker": canonical_spk, "text": quote, "timestamp": synth_ts, "turn_sec": synth_sec, "source": "OPERATOR_HINT", "hint_applied": True})


def _record_voice_binding(vid: Any, canonical_spk: str, voice_bindings: Dict[str, str]) -> None:
    s_vid = str(vid or "").strip()
    if not s_vid or s_vid.lower() in ("voice_host", "host", "voice host"):
        return
    prior = voice_bindings.get(s_vid)
    if prior and prior != canonical_spk:
        voice_bindings[s_vid] = "__CONFLICT__"
    elif not prior:
        voice_bindings[s_vid] = canonical_spk


def _apply_matched_turns(
    matched: List[Dict[str, Any]], quote: str, canonical_spk: str, voice_bindings: Dict[str, str]
) -> None:
    for m_turn in matched:
        seg_txt = str(m_turn.get("text") or m_turn.get("verbatim") or "")
        drifts = extract_phonetic_drift_candidates(quote, seg_txt)
        m_turn.update({"speaker": canonical_spk, "source": "OPERATOR_HINT", "hint_applied": True})
        if drifts:
            m_turn["phonetic_drift_candidates"] = drifts
        _record_voice_binding(m_turn.get("voice_id"), canonical_spk, voice_bindings)


def _apply_single_hint_entry(
    h: Dict[str, Any], turns: List[Dict[str, Any]], eff_attendees: Any, seen_synthetic: Set[Tuple[str, str]], voice_bindings: Dict[str, str]
) -> None:
    from .attendees import resolve_roster_name
    quote, raw_spk = str(h.get("quote") or "").strip(), str(h.get("speaker_name") or "").strip()
    clean_q = normalize_search_text(quote) if (quote and raw_spk and not _is_role_or_expired_hint(h, quote)) else ""
    if not clean_q:
        return
    canonical_spk = resolve_roster_name(raw_spk, eff_attendees)
    _record_voice_binding(h.get("voice_id"), canonical_spk, voice_bindings)
    elapsed_sec = float(h["session_elapsed_seconds"]) if h.get("session_elapsed_seconds") is not None else None
    cand_turns = [t for t in turns if not (t.get("source") == "OPERATOR_HINT" and t.get("speaker") != canonical_spk)]
    matched = [t for t in cand_turns if segment_contains_quote(t, clean_q)] or token_overlap_match(cand_turns, clean_q, min_ratio=0.60, elapsed_seconds=elapsed_sec)
    if matched:
        _apply_matched_turns(matched, quote, canonical_spk, voice_bindings)
        return
    key, max_sec, raw_el = (canonical_spk.lower(), clean_q), max((float(t.get("turn_sec", 0.0)) for t in turns), default=0.0), float(h.get("session_elapsed_seconds") or 0.0)
    if (not turns or raw_el <= max_sec + 300.0) and key not in seen_synthetic:
        seen_synthetic.add(key)
        _append_synthetic_hint_turn(h, turns, canonical_spk, quote)


def _propagate_voice_bindings(turns: List[Dict[str, Any]], voice_bindings: Dict[str, str]) -> None:
    if not voice_bindings:
        return
    for t in turns:
        if t.get("hint_applied") or t.get("source") == "OPERATOR_HINT":
            continue
        vid = str(t.get("voice_id") or "").strip()
        bound_spk = voice_bindings.get(vid) if vid else None
        if not bound_spk or bound_spk == "__CONFLICT__":
            continue
        spk = str(t.get("speaker") or "").strip()
        if spk in ("", "Speaker", "Unknown", vid) or spk.lower().startswith("voice "):
            t["speaker"] = bound_spk


def _normalize_attendee_names(eff_attendees: Any) -> List[str]:
    names: List[str] = []
    for item in eff_attendees or []:
        n = str(item.get("name") or item.get("canonical_name") or "").strip() if isinstance(item, dict) else str(item or "").strip()
        if n and n not in names:
            names.append(n)
    return names


def _can_infer_two_party_turns(turns: List[Dict[str, Any]], names: List[str]) -> bool:
    if len(names) != 2 or not turns:
        return False
    from .copilot_roster_bridge import are_same_speaker_identity

    distinct_named = {
        str(t.get("speaker") or "").strip() for t in turns
        if str(t.get("speaker") or "").strip() not in ("", "Speaker", "Unknown", "Host", "Voice Host")
        and not str(t.get("speaker") or "").strip().lower().startswith("voice ")
    }
    return len(distinct_named) < 2 or all(
        any(are_same_speaker_identity(s, n) for n in names) for s in distinct_named
    )


def _infer_conversational_turn_speakers(turns: List[Dict[str, Any]], eff_attendees: Any) -> None:
    """Attributes back-to-back single-channel turns when a speaker addresses the other participant by name."""
    from .copilot_roster_bridge import are_same_speaker_identity
    from .copilot_turn_signals import detect_addressee

    names = _normalize_attendee_names(eff_attendees)
    if not _can_infer_two_party_turns(turns, names):
        return
    roster = [{"name": n, "canonical_name": n, "display_name": n} for n in names]
    for t in turns:
        if t.get("source") == "OPERATOR_HINT":
            continue
        addr = detect_addressee(str(t.get("text") or ""), roster)
        if addr:
            other = next((n for n in names if not are_same_speaker_identity(n, addr)), None)
            if other:
                t["speaker"] = other


def reconcile_turns_with_hint_queue(
    turns: List[Dict[str, Any]],
    candidate_dirs: List[Any],
    attendees: Any = None,
) -> List[Dict[str, Any]]:
    """Reconciles in-memory turns with queued/applied speaker hints and injects synthetic turns for unmatched hints."""
    entries = _load_hint_entries_from_dirs(candidate_dirs)
    eff_attendees = attendees or _infer_attendees_from_dirs(candidate_dirs)
    voice_bindings = _load_voice_bindings_from_dirs(candidate_dirs, eff_attendees)
    if entries or voice_bindings:
        seen_synthetic: Set[Tuple[str, str]] = set()
        for h in entries:
            _apply_single_hint_entry(h, turns, eff_attendees, seen_synthetic, voice_bindings)
        if seen_synthetic:
            turns.sort(key=_seg_sec)
        _propagate_voice_bindings(turns, voice_bindings)
    _infer_conversational_turn_speakers(turns, eff_attendees)
    return turns

