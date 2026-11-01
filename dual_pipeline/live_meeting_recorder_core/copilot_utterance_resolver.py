"""Roster target resolution and topic-driven utterance selection for copilot."""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    RosterResolver, SelectedUtterance, UtteranceSelector, _pick_latest_substantive_turn, _safe_float_sec, _score_turns_by_topic,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import are_same_speaker_identity
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import coalesce_contiguous_turns


def _safe_int_seq(val: Any, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _roster_from_graph(in_mem: Any) -> List[Dict[str, Any]]:
    nodes = getattr(in_mem, "nodes", {}) if in_mem else {}
    return [
        {"name": nd.get("canonical_name") or nd.get("display_name") or nid, "canonical_name": nd.get("canonical_name") or nd.get("display_name") or nid, "display_name": nd.get("canonical_name") or nd.get("display_name") or nid, "node_id": nid}
        for nid, nd in nodes.items()
        if str(nd.get("node_type", "")).lower() == "person" or nid.startswith("person:")
    ]


def _roster_from_turns(all_turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for t in all_turns:
        spk = t.get("speaker")
        if spk and spk not in seen:
            seen.add(spk)
            candidates.append({"name": spk, "canonical_name": spk, "display_name": spk})
    return candidates


def _normalize_candidate(c: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if isinstance(c, str):
        return c.strip().lower(), {"name": c, "canonical_name": c, "display_name": c}
    if isinstance(c, dict):
        return str(c.get("canonical_name") or c.get("name") or "").strip().lower(), c
    return None, None


def _dedup_roster_pool(pool: List[Any], existing: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = list(existing or [])
    seen_names = {str(d.get("canonical_name") or d.get("name") or "").strip().lower() for d in deduped}
    for c in pool:
        name_key, c_dict = _normalize_candidate(c)
        if name_key and name_key not in seen_names and c_dict:
            if not any(are_same_speaker_identity(name_key, str(d.get("canonical_name") or d.get("name") or "")) for d in deduped):
                seen_names.add(name_key)
                deduped.append(c_dict)
    return deduped


def _build_roster_candidates(
    context_model: ConversationContextModel, in_mem: Any, all_turns: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    session_pool = list(context_model.attendees or []) + _roster_from_turns(all_turns)
    session_deduped = _dedup_roster_pool(session_pool)
    return _dedup_roster_pool(_roster_from_graph(in_mem), existing=session_deduped)


def _match_roster_target(
    target: str, roster_candidates: List[Dict[str, Any]]
) -> Tuple[Optional[str], Optional[Tuple[str, str]]]:
    roster_res = RosterResolver.resolve(target, roster_candidates)
    if roster_res.status == "AMBIGUOUS":
        return None, ("AMBIGUOUS_SPEAKER_IDENTITY", roster_res.details or f"Ambiguous attendee match for '{target}'.")
    if roster_res.status == "NOT_FOUND" and roster_candidates:
        return None, ("SPEAKER_NOT_IN_ROSTER", roster_res.details or f"Target speaker '{target}' not found in meeting roster.")
    if roster_res.status == "RESOLVED" and roster_res.canonical_name:
        return roster_res.canonical_name, None
    return target, None


def _select_speaker_summary_turns(
    all_turns: List[Dict[str, Any]], spk_low: str, topic_keywords: List[str]
) -> List[Dict[str, Any]]:
    coalesced = coalesce_contiguous_turns(all_turns)
    spk_turns = [
        (idx, t) for idx, t in enumerate(coalesced)
        if (str(t.get("speaker") or "").strip().lower() == spk_low or are_same_speaker_identity(spk_low, str(t.get("speaker") or "")))
        and str(t.get("text") or "").strip()
    ]
    if topic_keywords and spk_turns:
        scored = _score_turns_by_topic(spk_turns, topic_keywords)
        if scored:
            chosen = [t for _, _, _, t in scored[:3]]
            chosen.sort(key=lambda x: _safe_int_seq(x.get("seq"), 0))
            return chosen
    ranked = sorted(spk_turns, key=lambda item: (score_utterance_domain_substance(str(item[1].get("text") or "")), item[0]), reverse=True)
    chosen = [t for _, t in ranked[:3]]
    chosen.sort(key=lambda x: _safe_int_seq(x.get("seq"), 0))
    return chosen


def _format_window_summary(window_turns: List[Dict[str, Any]]) -> str:
    return " | ".join(
        f"{t.get('speaker', 'Speaker')}: {str(t.get('text') or '').strip()}"
        for t in (window_turns or [])[-4:] if str(t.get("text") or "").strip()
    )


def _populate_utterance_summaries(
    sel: Optional[SelectedUtterance], all_turns: List[Dict[str, Any]], window_turns: List[Dict[str, Any]], topic_keywords: List[str],
) -> Optional[SelectedUtterance]:
    if not sel:
        return None
    chosen = _select_speaker_summary_turns(all_turns, (sel.speaker or "").strip().lower(), topic_keywords)
    texts = list(dict.fromkeys(str(t.get("text") or "").strip() for t in chosen if str(t.get("text") or "").strip()))
    sel.speaker_summary = " ".join(texts) if texts else (sel.text or "").strip()
    sel.window_summary = _format_window_summary(window_turns)
    return sel


def _select_speaker_utterance(
    all_turns: List[Dict[str, Any]],
    target: str,
    topic_keywords: List[str],
    window_sec: float = 45.0,
    strict_target: bool = False,
) -> Tuple[Optional[Any], List[Dict[str, Any]], Optional[Tuple[str, str]]]:
    sel_res = UtteranceSelector.select(
        all_turns, target_speaker=target, topic_keywords=topic_keywords, window_sec=window_sec, strict_target=strict_target
    )
    if sel_res.status == "NO_SPEAKER_TURNS":
        err_code = sel_res.error_code or ("ZERO_ATTRIBUTED_TURNS_FOR_TARGET" if strict_target else "SPEAKER_HAS_NO_TURNS")
        return None, [], (err_code, sel_res.details or f"Speaker '{target}' has no spoken turns in active transcript.")
    if sel_res.status == "NO_RELEVANT_UTTERANCE":
        return None, [], ("ZERO_RELEVANT_UTTERANCES", sel_res.details or f"Speaker '{target}' has turns, but none address topic keywords {topic_keywords}.")
    return _populate_utterance_summaries(sel_res.selected_utterance, all_turns, sel_res.window_turns, topic_keywords), sel_res.window_turns, None


def _prefer_primary_turns(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [t for t in turns if not t.get("is_sibling_session")] or turns


def _select_topic_utterance(
    all_turns: List[Dict[str, Any]], topic_keywords: List[str], window_sec: float = 45.0,
) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
    """Selects the most topic-relevant utterance, or latest substantive turn, across all speakers."""
    if not all_turns:
        return None, []
    pool = _prefer_primary_turns(all_turns)
    indexed = list(enumerate(pool))
    eff_kws = list(set(k.lower() for k in (topic_keywords or [])) - _GENERIC_ORG_AND_PROMPT_TOKENS)
    scored = _score_turns_by_topic(indexed, eff_kws) if eff_kws else []
    best_score, matched_kws, idx, turn = scored[0] if scored else (0.5, [], *_pick_latest_substantive_turn(indexed))
    sel = SelectedUtterance(
        seq=_safe_int_seq(turn.get("seq"), idx + 1), speaker=str(turn.get("speaker") or "Unknown"),
        text=str(turn.get("text") or ""), timestamp=str(turn.get("timestamp") or "00:00:00"),
        turn_sec=_safe_float_sec(turn.get("turn_sec"), idx * 3.0), relevance_score=best_score, matched_keywords=matched_kws,
    )
    window = UtteranceSelector._extract_window(pool, idx, sel.turn_sec, window_sec)
    return _populate_utterance_summaries(sel, pool, window, topic_keywords), window


def _infer_target_from_keywords(topic_keywords: List[str], roster: List[Dict[str, Any]]) -> Tuple[Optional[str], List[str]]:
    if not topic_keywords or not roster:
        return None, topic_keywords
    for kw in list(topic_keywords):
        res = RosterResolver.resolve(kw, roster)
        if res.status == "RESOLVED" and res.canonical_name:
            return res.canonical_name, [k for k in topic_keywords if k.lower() != kw.lower()]
    return None, topic_keywords


_ROLE_SPEAKER_RE = re.compile(r"\b(?:teacher|instructor|counselor|facilitator|moderator|doctor|therapist|coach)\b|['’]s\s+\w+", re.IGNORECASE)
_PLACEHOLDER_SPEAKER_RE = re.compile(r"^(?:speaker|unknown|voice\s+\w+|guest|caller|participant)$", re.IGNORECASE)


def _find_fallback_turn_speaker(all_turns: List[Dict[str, Any]], allow_placeholder: bool) -> Optional[str]:
    non_host = [str(t.get("speaker") or "").strip() for t in all_turns if str(t.get("speaker") or "").strip().lower() not in {"amit", "amit ayer", "host", "voice host", "system", ""}]
    unique = list(dict.fromkeys(non_host))
    role_matches = [s for s in unique if _ROLE_SPEAKER_RE.search(s)]
    if len(role_matches) == 1:
        return role_matches[0]
    if allow_placeholder:
        ph_matches = [s for s in unique if _PLACEHOLDER_SPEAKER_RE.search(s) or _ROLE_SPEAKER_RE.search(s)]
        if ph_matches and len(ph_matches) == len(unique):
            return ph_matches[-1]
    return None


def _select_with_role_fallback(
    all_turns: List[Dict[str, Any]], effective_target: str, resolved_target: Optional[str],
    roster_err: Optional[Tuple[str, str]], effective_keywords: List[str], win_sec: float, strict_target: bool = False,
) -> Tuple[Optional[str], Optional[Any], List[Dict[str, Any]], Optional[Tuple[str, str]]]:
    if roster_err:
        if strict_target:
            return None, None, [], ("ZERO_ATTRIBUTED_TURNS_FOR_TARGET", f"ZERO_ATTRIBUTED_TURNS_FOR_TARGET: {roster_err[1]}")
        fb_spk = _find_fallback_turn_speaker(all_turns, allow_placeholder=False)
        return (effective_target, *_select_speaker_utterance(all_turns, fb_spk, effective_keywords, window_sec=win_sec)) if fb_spk else (None, None, [], roster_err)
    utt, win, sel_err = _select_speaker_utterance(all_turns, resolved_target or effective_target, effective_keywords, window_sec=win_sec, strict_target=strict_target)
    if sel_err and sel_err[0] in ("SPEAKER_HAS_NO_TURNS", "ZERO_ATTRIBUTED_TURNS_FOR_TARGET") and not strict_target:
        fb_spk = _find_fallback_turn_speaker(all_turns, allow_placeholder=True)
        if fb_spk:
            utt, win, sel_err = _select_speaker_utterance(all_turns, fb_spk, effective_keywords, window_sec=win_sec)
    return resolved_target, utt, win, sel_err


def _turn_matches_target_speaker(target: str, turn: Dict[str, Any]) -> bool:
    spk = str(turn.get("speaker") or "")
    return target.lower() in spk.lower() or are_same_speaker_identity(target, spk)


def _extract_dialogue_turns(context_model: ConversationContextModel) -> List[Dict[str, Any]]:
    raw = [{"seq": t.seq, "speaker": t.speaker, "text": t.text, "timestamp": t.timestamp, "turn_sec": t.turn_sec, "act": t.act, "is_sibling_session": bool(getattr(t, "is_sibling_session", False))} for t in context_model._turns if not t.is_quarantined]
    return coalesce_contiguous_turns(raw)


def _resolve_speaker_and_utterance(
    target: Optional[str], topic_keywords: List[str], context_model: ConversationContextModel, in_mem: Any,
) -> Tuple[Optional[str], Optional[Any], List[Dict[str, Any]], Optional[Tuple[str, str]]]:
    """Resolves target against roster and selects the relevant utterance and window."""
    all_turns = _extract_dialogue_turns(context_model)
    roster = _build_roster_candidates(context_model, in_mem, all_turns)
    session_roster = _dedup_roster_pool(list(context_model.attendees or []) + _roster_from_turns(all_turns))
    effective_target, effective_keywords = target, list(topic_keywords)
    if not effective_target and roster:
        inferred_spk, stripped_kws = _infer_target_from_keywords(effective_keywords, session_roster or roster)
        if inferred_spk and any(_turn_matches_target_speaker(inferred_spk, t) for t in all_turns):
            effective_target, effective_keywords, topic_keywords[:] = inferred_spk, stripped_kws, stripped_kws
    win_sec = max(45.0, float(getattr(context_model, "focus_seconds", 45.0) or 45.0))
    if not effective_target:
        return (None, *_select_topic_utterance(all_turns, effective_keywords, window_sec=win_sec), None) if all_turns else (None, None, [], None)
    strict_target = bool(getattr(context_model, "strict_target", False))
    res_target, r_err = _match_roster_target(effective_target, roster)
    res_target, utterance, window, sel_err = _select_with_role_fallback(
        all_turns, effective_target, res_target, r_err, effective_keywords, win_sec, strict_target=strict_target
    )
    if sel_err:
        return res_target, None, [], sel_err
    if utterance is not None and res_target and are_same_speaker_identity(utterance.speaker, res_target):
        utterance.speaker = res_target
    return res_target, utterance, window, None


_LIVE_DIRECTIVE_RE = re.compile(
    r"\b(?:live|liver|whole|entire|full)\s+transcripts?\b|\b(?:on|from|in)\s+transcripts?\b"
    r"|\bbased\s+on\b.*\b(?:feedback|discussion|call|meeting|conversation)\b"
    r"|\blatest\s+(?:discussion|conversation|points?|turns?)\b|\bright\s+now\b"
    r"|\breview\s+(?:the\s+)?(?:last|past|recent|whole|entire)\b|\bsynthesize\b|\bredo\s+prompt\b",
    re.IGNORECASE,
)


def has_live_transcript_directive(raw_request: Optional[str]) -> bool:
    """Returns True if the operator prompt explicitly directs copilot to use or review the live transcript."""
    return bool(raw_request and _LIVE_DIRECTIVE_RE.search(str(raw_request)))


_ACT_TO_PREDICATE: Dict[str, str] = {
    "objection": "RAISES_RISK", "commitment": "COMMITS_TO", "decision_point": "DECIDES", "question_to_operator": "ASKS_ABOUT",
}
_DANGLING_TAIL_WORDS = frozenset({
    "and", "or", "but", "so", "because", "to", "for", "with", "the", "a", "an", "of", "in", "on", "at", "by", "from",
    "into", "about", "as", "is", "are", "was", "were", "be", "been", "being", "that", "which", "who", "whom", "whose",
    "this", "these", "those", "if", "when", "while", "where", "how", "what", "why", "whether", "potentially", "maybe",
    "perhaps", "really", "just", "very", "quite", "rather", "could", "would", "should", "can", "will", "might",
})
_NON_SUBSTANTIVE_WORDS = _DANGLING_TAIL_WORDS | frozenset({
    "i", "you", "we", "they", "he", "she", "it", "me", "us", "them", "my", "your", "our", "their", "his", "her", "its",
    "think", "thought", "know", "mean", "like", "just", "really", "very", "quite", "potentially", "probably", "maybe",
    "perhaps", "could", "would", "should", "can", "will", "might", "may", "must", "shall", "do", "does", "did", "done",
    "have", "has", "had", "having", "get", "got", "getting", "going", "go", "went", "make", "makes", "made", "say",
    "says", "said", "see", "look", "okay", "ok", "yeah", "yep", "yes", "no", "not", "right", "now", "then", "here",
    "there", "where", "when", "what", "how", "why", "some", "any", "all", "more", "most", "other", "another", "such",
    "own", "same", "than", "too", "way", "thing", "things", "stuff", "lot", "want", "wanted", "need", "needed", "well", "uh", "um",
})
_LEADING_SCAFFOLD_RE = re.compile(
    r"^(?:(?:yeah|yep|yes|no|okay|ok|so|well|uh|um|like|right|now|and|but|i\s+mean|you\s+know|i\s+think(?:\s+that)?|i\s+thought(?:\s+that)?|i\s+guess(?:\s+that)?|i\s+feel\s+like)\b[,:\s]*)+",
    re.IGNORECASE,
)
_LABEL_PREFIX_NOUNS = frozenset({"option", "plan", "tier", "class", "type", "phase", "group", "part", "section", "model", "wave", "voice", "speaker"})


def _is_dangling_word(words: List[str]) -> bool:
    tok = words[-1].lower().rstrip(",;:-.!?")
    alpha = re.sub(r"[^a-z0-9]", "", tok)
    if not alpha or tok in _DANGLING_TAIL_WORDS:
        return True
    if len(alpha) == 1 and alpha.isalpha():
        prev = words[-2].lower().rstrip(",;:-.") if len(words) >= 2 else ""
        return prev not in _LABEL_PREFIX_NOUNS
    return False


_LEADING_VERB_CLAUSE_RE = re.compile(
    r"^(?:i\s+may\s+have\s+it[,;\s]+but\s+(?:can|do)\s+you\s+mind\s+just\s+|(?:can|do)\s+you\s+mind\s+just\s+|(?:can|could)\s+you\s+just\s+|i['’]?m\s+going\s+to\s+go\s+ahead\s+and\s+"
    r"|(?:working|doing)\s+on\s+(?:stuff|things)\s+(?:here\s+)?(?:in\s+the\s+|on\s+the\s+|at\s+the\s+)?)",
    re.IGNORECASE,
)
_ASR_SPLIT_SOW_RE = re.compile(r"\b(?:the,\s+)?(?:in\s+NEA,\s+the\s+)?S-\s*So,\s*W\b", re.IGNORECASE)
_WHISPER_HALLUCINATION_RE = re.compile(
    r"(?:thank(?:s|\s+you)\s+(?:so\s+much\s+|very\s+much\s+)?for\s+(?:watching|listening|tuning\s+in|joining(?:\s+us)?|your\s+(?:time|attention)|this\s+(?:excellent\s+|great\s+|wonderful\s+)?opportunity)[^.!?]*"
    r"|(?:i['’]?ll\s+|we['’]?ll\s+)?see\s+you\s+(?:in\s+the\s+next\s+(?:video|episode|one)|next\s+time)[^.!?]*"
    r"|\btune\s+in\s+(?:on|to|for)\b[^.!?]*"
    r"|\bclick\s+the\s+join\s+button\s+below[^.!?]*"
    r"|(?:please\s+)?(?:like\s+and\s+)?subscribe(?:\s+to\s+[^.!?]*)?|subtitles?\s+by\b[^.!?]*)",
    re.IGNORECASE,
)
_ASR_PROFANITY_RE = re.compile(r"\b(?:shit(?:\s*show|ty|s)?|fuck\w*|f\*+\w*|bitch\w*|asshole\w*|bastard\w*)\b", re.IGNORECASE)
_BROKEN_STUTTER_PREFIX_RE = re.compile(r"^.{0,65}?\b\w+(?:['’]\w+)?-\s+(?=[A-Z])")
_HEADLESS_FRAGMENT_RE = re.compile(r"^(?:(?:was|were|am|been|being|had|did|does)\s+|why\s+am\s+i\b)", re.IGNORECASE)
_FINITE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|am|be|been|have|has|had|do|does|did|will|would|should|could|can|may|might|must|"
    r"need|needs|want|wants|align|build|sign|use|run|ship|own|deliver|block|fix|test|deploy|launch|review|"
    r"track|check|move|start|keep|make|take|get|give|see|know|think|agree|work|partner|integrate|[a-z]{3,}(?:ed|ing))\b",
    re.IGNORECASE,
)
_FILLER_SENTENCE_RE = re.compile(
    r"^(?:which\s+(?:was|is)\s+(?:nice|great|good|cool|awesome|fine|helpful)|that\s+(?:was|is)\s+(?:nice|great|good|cool|awesome)"
    r"|(?:i['’]?m\s+|we['’]?re\s+)?(?:so\s+|really\s+)?excited\s+to\s+get\s+(?:q[1-4]\s+)?kick\s*off|(?:working|doing)\s+on\s+(?:stuff|things)\s+here(?:\s+in\s+the)?"
    r"|this\s+is\s+a\s+presentation\s+of\b.*|hello,?\s+i['’]?m\s+[^.!?]+\bhere\s+to\s+talk\s+to\s+you\b.*|my\s+name\s+is\b.*|good\s+morning,\s+good\s+afternoon\b.*)$",
    re.IGNORECASE,
)


def _is_glossary_echo_or_headless(sent: str) -> bool:
    s = sent.strip()
    if not s or _HEADLESS_FRAGMENT_RE.search(s):
        return True
    commas = s.count(",")
    if commas >= 3 and len(s.split()) <= commas * 3 + 2:
        return True
    return len(s.split()) <= 7 and not _FINITE_VERB_RE.search(s) and not _DOMAIN_SIGNAL_RE.search(s)


def _try_combine_next_sentence(chosen: str, valid: List[str], best_idx: int) -> str:
    if best_idx + 1 >= len(valid):
        return chosen
    nxt = valid[best_idx + 1]
    if not _DOMAIN_SIGNAL_RE.search(nxt) or score_utterance_domain_substance(nxt) < 2.5:
        return chosen
    nxt_lc = (nxt[0].lower() + nxt[1:]) if (len(nxt) > 1 and nxt[0].isupper() and nxt[1].islower()) else nxt
    comb = f"{chosen}, and {nxt_lc}"
    return comb if len(comb.split()) <= 30 else chosen


def _select_best_turn_sentences(sents: List[str]) -> str:
    valid = [s for s in sents if not _FILLER_SENTENCE_RE.match(s) and not _ASR_PROFANITY_RE.search(s) and not _is_glossary_echo_or_headless(s)]
    if not valid:
        return ""
    valid = [s for s in valid if not re.search(r"\b\w+(?:['’]\w+)?-\s+", s)] or valid
    if len(valid) == 1:
        return valid[0]
    n_valid = max(1, len(valid) - 1)
    best_idx = max(
        range(len(valid)),
        key=lambda i: (
            score_utterance_domain_substance(valid[i]) + 1.5 * (i / n_valid)
            - (3.5 if i > 0 and re.match(r"^(?:this|that|these|those|it)\b", valid[i], re.IGNORECASE) else 0.0),
            i,
        ),
    )
    return _try_combine_next_sentence(valid[best_idx], valid, best_idx)


def _clean_live_turn_claim(text: str) -> str:
    clean = _WHISPER_HALLUCINATION_RE.sub(" ", text.strip()).strip()
    clean = _LEADING_SCAFFOLD_RE.sub("", _LEADING_VERB_CLAUSE_RE.sub("", _LEADING_SCAFFOLD_RE.sub("", clean).strip()).strip()).strip()
    clean = _ASR_SPLIT_SOW_RE.sub(lambda m: "the NEA SOW" if "nea" in m.group(0).lower() else "SOW", clean)
    clean = re.sub(r"(?<=[.!?])\s+[^.!?]+$", "", re.sub(r"\bthe[,;\s]+the\b", "the", clean, flags=re.IGNORECASE))
    sents = [_LEADING_SCAFFOLD_RE.sub("", _BROKEN_STUTTER_PREFIX_RE.sub("", _LEADING_VERB_CLAUSE_RE.sub("", s.strip().rstrip(".!?")).strip()).strip()).strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    best_sent = _select_best_turn_sentences(sents) if sents else clean.rstrip(".!?")
    if not best_sent:
        return ""
    words = best_sent.split()[:30]
    while words and _is_dangling_word(words):
        words.pop()
    return " ".join(words).strip(",;:-. ")


_SIGNOFF_AND_FILLER_RE = re.compile(
    r"\b(?:have\s+to\s+run|got\s+to\s+run|gotta\s+run|another\s+meeting|jump\s+to\s+another|drop\s+off|hop\s+off|"
    r"talk\s+to\s+you\s+later|see\s+you\s+guys|have\s+a\s+good\s+one|make\s+it\s+right|yeah[,.\s]+for\s+sure|"
    r"seems\s+pretty\s+straightforward|that\s+makes\s+sense|sounds\s+good\s+to\s+me|nothing\s+else\s+from\s+me|"
    r"excited\s+to\s+get\s+(?:q[1-4]\s+)?kick\s*off|working\s+on\s+(?:stuff|things)\s+here|which\s+was\s+nice)\b",
    re.IGNORECASE,
)
_DOMAIN_SIGNAL_RE = re.compile(
    r"(?:\$\d+|\b(?:rule|rules|nameplate|nameplates|ge|igbt|turbine|turbines|solar|wind|transformer|transformers|"
    r"notification|notifications|engineer|engineers|metric|metrics|accuracy|project|equipment|manufacturer|"
    r"extraction|model|pipeline|database|architecture|deployment|production|ui|ux|workflow|workflows|alert|alerts|fleet|"
    r"daniel|schaap|number|numbers|percent|hardcoded|threshold|validation|config|configurable|"
    r"tech\s+lead|lead|leads|owner|owners|ownership|boundary|boundaries|expectation|expectations|"
    r"stakeholder|stakeholders|alignment|integration|integrations|churn|deliverable|deliverables|"
    r"guardrail|guardrails|onboarding|onboard|roadmap|milestone|milestones|sow|scope|workstream|workstreams|"
    r"conduit|maxcore|nea|apex_trans|vanguard|jarvis|lens|sherpa|nexus|sap|vector|blocker|blockers|risk|risks|"
    r"dependency|dependencies|handoff|design|governance|priority|priorities|timeline|deadline|velocity|throughput|"
    r"agreement|agreements|contract|contracts|budget|pricing|cost|costs|margin|revenue|investment|million|billion|"
    r"enterprise|platform|platforms|connector|connectors|catalog|cataloging|agent|agents|sprint|sprints|backlog|jira|"
    r"story|stories|release|security|compliance|privacy|pii|hardened|audit|sla|latency|adoption|rollout|pilot|poc|mvp|"
    r"capability|capabilities|system|systems|service|services|app|apps|data|cloud|api|apis|schema|schemas|"
    r"approval|decisions?|delegat\w+|research\w*|organizes?|compares?|coordinat\w+|memory|persistent|multimodal|"
    r"\d+(?:\.\d+)?%|(?:phase|wave|tier|option|sprint)\s*\d+)\b)",
    re.IGNORECASE,
)
_CONVERSATIONAL_NOISE = frozenset({
    "minute", "minutes", "meeting", "meetings", "straightforward", "pretty", "seems", "cool",
    "sure", "right", "run", "running", "guys", "everybody", "everyone", "today", "tomorrow",
    "awesome", "great", "nice", "excited", "kick", "kickoff", "thanks", "thank", "appreciate", "bye", "hello", "hey",
})
_MULTIWORD_NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")
_CAMEL_OR_ACRONYM_RE = re.compile(r"\b(?:[A-Z]{2,}[a-z0-9]*|[A-Z][a-z]+[A-Z][A-Za-z0-9]*)\b")
_IGNORED_ACRONYMS = frozenset({"OK", "AM", "PM", "Q1", "Q2", "Q3", "Q4", "II", "III"})
_BROKEN_CHUNK_DEFECT_RE = re.compile(r"\b\w+(?:['’]\w+)?-\s+|\b([a-z]{3,})\s+\1\b|\b(?:are\s+run\s+multi-step|and\s+to\s+use|continues\s+to|will\s+not|companies\s+basically)\b\.?\s*$|\b(?:my\s+name\s+is|doing\s+the\s+ceremony|pleasure\s+to\s+be\s+here|across\s+llt|wild\s+scale)\b|^(?:this|that|these|those|their|it|which|even)\b", re.IGNORECASE)


def _count_named_entity_signals(raw: str) -> int:
    names = {m.group(0) for m in _MULTIWORD_NAME_RE.finditer(raw)}
    name_words = {w.strip(",.;:!?\"'()") for n in names for w in n.split()}
    acronyms = {m.group(0) for m in _CAMEL_OR_ACRONYM_RE.finditer(raw) if m.group(0) not in _IGNORED_ACRONYMS}
    stripped = [w.strip(",.;:!?\"'()") for w in raw.split()[1:]]
    mid_proper = {
        t for t in stripped
        if len(t) >= 4 and t not in name_words and t[0].isupper() and t[1:].islower()
        and t.lower() not in _NON_SUBSTANTIVE_WORDS and t.lower() not in _CONVERSATIONAL_NOISE
    }
    return len(names) + len(acronyms) + len(mid_proper)


def score_utterance_domain_substance(text: str) -> float:
    """Scores a coalesced speaker utterance by domain substance while penalizing sign-offs and fillers."""
    raw = _WHISPER_HALLUCINATION_RE.sub(" ", (text or "").strip()).strip()
    if not raw or not _has_substantive_content(raw):
        return -10.0
    if _ASR_PROFANITY_RE.search(raw) or _is_glossary_echo_or_headless(raw):
        return -8.0
    domain_hits, entity_hits = len(_DOMAIN_SIGNAL_RE.findall(raw)), _count_named_entity_signals(raw)
    if _SIGNOFF_AND_FILLER_RE.search(raw) and domain_hits == 0 and entity_hits == 0:
        return -5.0
    tokens = [re.sub(r"[^a-z0-9]+", "", w.lower()) for w in raw.split()]
    substantive = [t for t in tokens if len(t) >= 3 and t not in _NON_SUBSTANTIVE_WORDS and t not in _CONVERSATIONAL_NOISE]
    if not substantive:
        return -5.0
    ent_bonus = float(entity_hits) * 1.8 if domain_hits > 0 else float(min(entity_hits, 1)) * 0.6
    score = float(len(substantive)) * 0.4 + float(domain_hits) * 2.5 + ent_bonus
    if raw.endswith("...") or (tokens and _is_dangling_word(raw.split())):
        score -= 1.5
    if _BROKEN_CHUNK_DEFECT_RE.search(raw):
        score -= 8.5
    return round(score - (3.0 if _SIGNOFF_AND_FILLER_RE.search(raw) else 0.0), 3)


def _has_substantive_content(claim: str) -> bool:
    if _SIGNOFF_AND_FILLER_RE.search(claim) and not _DOMAIN_SIGNAL_RE.search(claim) and _count_named_entity_signals(claim) == 0:
        return False
    tokens = [re.sub(r"[^a-z0-9]+", "", w.lower()) for w in claim.split()]
    return any(len(t) >= 3 and t not in _NON_SUBSTANTIVE_WORDS and t not in _CONVERSATIONAL_NOISE for t in tokens)


def _resolve_selected_utterance_act(selected_utterance: Any, dialogue: Any, sel_seq: int) -> str:
    sel_act = getattr(selected_utterance, "act", None)
    if not sel_act and hasattr(dialogue, "_turns"):
        sel_act = next((getattr(r, "act", None) for r in getattr(dialogue, "_turns", []) if getattr(r, "seq", None) == sel_seq), None)
    return sel_act or "status_update"


def _turn_to_dict(t: Any, default_seq: int = 1) -> Dict[str, Any]:
    if isinstance(t, dict):
        return dict(t)
    return {"seq": getattr(t, "seq", default_seq), "speaker": getattr(t, "speaker", "Speaker"), "text": getattr(t, "text", ""), "timestamp": getattr(t, "timestamp", "00:00:00"), "turn_sec": getattr(t, "turn_sec", 0.0), "act": getattr(t, "act", "status_update"), "is_sibling_session": bool(getattr(t, "is_sibling_session", False))}


def _coalesce_fragment_turns(turns: List[Any]) -> List[Dict[str, Any]]:
    return coalesce_contiguous_turns(_prefer_primary_turns([_turn_to_dict(t, idx + 1) for idx, t in enumerate(turns) if t is not None]))


def _expand_focus_with_full_turns(focus_coalesced: List[Dict[str, Any]], focus_raw: List[Any], raw_model: Any) -> List[Dict[str, Any]]:
    all_turns_attr = getattr(raw_model, "_turns", None)
    if not all_turns_attr or len(all_turns_attr) <= len(focus_raw):
        return focus_coalesced
    full_turns = _prefer_primary_turns(_extract_dialogue_turns(raw_model))
    if sum(1 for t in focus_coalesced if score_utterance_domain_substance(str(t.get("text") or "")) >= 4.5) < 2:
        return full_turns
    seen_seqs = {t.get("seq") for t in focus_coalesced}
    return [t for t in full_turns if t.get("seq") not in seen_seqs and score_utterance_domain_substance(str(t.get("text") or "")) >= 7.0] + focus_coalesced


def _rank_focus_turns_by_substance(dialogue: Any) -> List[Dict[str, Any]]:
    if not hasattr(dialogue, "focus_turns"):
        return []
    focus_raw = list(dialogue.focus_turns())
    focus_coalesced = _expand_focus_with_full_turns(_coalesce_fragment_turns(focus_raw), focus_raw, getattr(dialogue, "dialogue", dialogue))
    base_scores = [score_utterance_domain_substance(_clean_live_turn_claim(str(t.get("text") or ""))) for t in focus_coalesced]
    sub_secs = [_safe_float_sec(t.get("turn_sec"), idx * 3.0) for idx, t in enumerate(focus_coalesced) if base_scores[idx] > 0.0]
    latest_sub_sec, n_pool = (max(sub_secs) if sub_secs else 0.0), max(1, len(focus_coalesced) - 1)

    def _effective_rank_key(item: Tuple[int, Dict[str, Any]]) -> Tuple[float, int]:
        idx, t = item
        base = base_scores[idx]
        if base <= 0.0:
            return (base, idx)
        recency = 1.5 * math.exp(-max(0.0, latest_sub_sec - _safe_float_sec(t.get("turn_sec"), idx * 3.0)) / 360.0) + 0.5 * (idx / n_pool)
        return (round(base + recency, 3), idx)

    ordered = [t for _, t in sorted(enumerate(focus_coalesced), key=_effective_rank_key, reverse=True)]
    return [t for idx, t in enumerate(ordered) if score_utterance_domain_substance(_clean_live_turn_claim(str(t.get("text") or ""))) > 0.0] or ordered


def _insert_selected_utterance(raw_turns: List[Dict[str, Any]], selected_utterance: Any, dialogue: Any, target: Optional[str]) -> None:
    sel_txt = str(getattr(selected_utterance, "text", "") or "") if (target or getattr(selected_utterance, "matched_keywords", None)) else ""
    if not sel_txt:
        return
    sel_seq = getattr(selected_utterance, "seq", 1)
    sel_dict = {"seq": sel_seq, "speaker": getattr(selected_utterance, "speaker", target or "Speaker"), "text": sel_txt, "act": _resolve_selected_utterance_act(selected_utterance, dialogue, sel_seq)}
    top_score = score_utterance_domain_substance(str(raw_turns[0].get("text") or "")) if raw_turns else -1.0
    raw_turns.insert(0, sel_dict) if score_utterance_domain_substance(sel_txt) >= top_score else raw_turns.append(sel_dict)


def _gather_candidate_live_turns(
    dialogue: Any, target: Optional[str], selected_utterance: Optional[Any], topic_keywords: Optional[List[str]],
) -> List[Dict[str, Any]]:
    from .copilot_context import _turn_matches_keyword
    raw_turns = _rank_focus_turns_by_substance(dialogue)
    if selected_utterance is not None:
        _insert_selected_utterance(raw_turns, selected_utterance, dialogue, target)
    kws = list({k.lower() for k in (topic_keywords or ()) if len(k) >= 2} - _GENERIC_ORG_AND_PROMPT_TOKENS)
    if kws:
        kw_matched = [t for t in raw_turns if any(_turn_matches_keyword(str(t.get("text", "")).lower(), k) for k in kws)]
        return (kw_matched + [t for t in raw_turns if t not in kw_matched]) if kw_matched else raw_turns
    if target:
        t_low = target.strip().lower()
        t_turns = [t for t in raw_turns if t_low in str(t.get("speaker") or "").lower() or are_same_speaker_identity(target, str(t.get("speaker") or ""))]
        return t_turns + [t for t in raw_turns if t not in t_turns]
    return raw_turns


def _build_live_turn_assertion_dict(claim: str, spk: str, seq: int, act: str = "status_update") -> Dict[str, Any]:
    spk_slug = re.sub(r"[^a-z0-9_]+", "_", spk.lower()).strip("_") or "participant"
    return {
        "assertion_id": f"live_turn_{seq}", "claim_text": claim, "object_literal": claim, "verbatim_span": claim[:80],
        "predicate": _ACT_TO_PREDICATE.get(act, "DISCUSSES"), "subject_node_id": f"attendee:{spk_slug}",
        "source_type": "ACOUSTIC_TRANSCRIPT", "primary_source_id": f"live_transcript:turn_{seq}",
        "evidence_ids": [f"live_turn_{seq}", f"acoustic:{seq}", f"session:{seq}"], "source_ids": [f"transcript:{seq}", f"acoustic:{seq}"],
        "confidence_score": 0.92, "canonical_status": "ACTIVE", "validation_state": "verified",
        "is_live_turn": True, "is_primary": True, "is_priority": False, "provenance_scope": "PRIMARY",
    }


def _extract_valid_live_claim(txt: str, seen_claims: set[str]) -> Optional[str]:
    from .c19_firewall import has_c19_marker
    from .copilot_context import PROMPT_INJECTION_DISALLOWED_PATTERNS, _LOW_SIGNAL_UTTERANCE_RE
    if len(txt) < 8 or has_c19_marker(txt) or _LOW_SIGNAL_UTTERANCE_RE.search(txt) or any(p.search(txt) for p in PROMPT_INJECTION_DISALLOWED_PATTERNS):
        return None
    claim = _clean_live_turn_claim(txt)
    if len(claim.split()) < 3 or _LOW_SIGNAL_UTTERANCE_RE.search(claim) or _ASR_PROFANITY_RE.search(claim) or not _has_substantive_content(claim) or claim.lower() in seen_claims:
        return None
    seen_claims.add(claim.lower())
    return claim


_NOVEL_GRAPH_DIRECTIVE_RE = re.compile(r"\b(?:no\s+repeating|not\s+repeating|don['’]?t\s+repeat|without\s+repeating|novel\s+(?:points?|insights?)|new\s+insights?)\b", re.IGNORECASE)
_META_CLAIM_STUB_RE = re.compile(r"^\s*[-*•]?\s*\**(?:sender|from|to|cc|bcc|sent|subject|apex_global|partner_advisory|attendees|participants|roster)\**\s*:|\bis\s+an\s+identified\b|\bis\s+a\s+reported\s+speaker\b|mailto:|\b(?:um|uh)\b|\b(?:has\s+q[1-4]\s+202\d\s+charter\s+mission|delivers\s+q[1-4]\s+202\d\s+workstream|resource\s+note|nea\s+planner\s+workstream|belongs\s+to\s+program|submitted\s+workbook\s+source\s+row|confirmed\s+horizontal\s+layer|confirmed\s+(?:nee\s+)?routing\s+backbone|pod-charter\s+row\s+list|reports\s+to|has\s+role\s+in|referenced\s+in\s+source\s+document|affiliated\s+with|estimated\s+cost|related\s+to\s+maximo|weakly\s+reflected\s+in\s+v\d+|linkedin\s+screenshot|is\s+distinct\s+from|the\s+parent\s+company)\b|\brole:\s+[a-z]|:\s+organization\s+apex_global\b|apex_global\s+energy\s+\(nea\s*/\s*(?:apex|pgd)\)|\bi['’]?ve\s+been\s+at\s+apex_global\b|\(technical_anchor\)\s+in\b|\s->\s|^enterprise\s+ai\s+enablement\s+delivers\b|^\s*\**[a-z]+(?:\s+[a-z]+)?:\**\s+(?:let['’]s\s+see|i\s+think)|(?:…|\.\.\.)\.?\s*$", re.IGNORECASE)
_GENERIC_ORG_AND_PROMPT_TOKENS = frozenset({"nee", "apex_global", "partner_advisory", "nea", "apex", "energy", "leader", "leaders", "audience", "webinar", "point", "points", "show", "make", "best", "next", "explain", "applies", "apply"})
_NON_TECH_MODEL_COMPOUND_RE = re.compile(r"\b(?:(?:delivery|operating|commercial|engagement|governance)[-\s]+models?|this\s+model\s+(?:aligns|is\s+built)|that\s+model)\b", re.IGNORECASE)
_CONCRETE_EXECUTION_RE = re.compile(r"\b(?:sap|servicenow|successfactors|systems?\s+of\s+record|write\s+back|no-code|connectors?|gemini\s+enterprise|traverse|agent\s+fleet|agent\s+designer|ge\s+301|reusable\s+patterns)\b", re.IGNORECASE)
_WORKFLOW_SHIFT_RE = re.compile(r"\b(moving\s+from\s+ai\s+that\s+generates\s+information\s+to\s+ai\s+that\s+performs\s+workflows|researches\s+options,\s+compares\s+information,\s+organizes\s+the\s+plan,\s+and\s+asks\s+for\s+approval\b[^.!?]*)", re.IGNORECASE)


_AGENTIC_BRIDGE_TRIGGER = frozenset({"agent", "agents", "tool", "tools", "integration", "integrations", "process", "processes", "workflow", "workflows"})
_AGENTIC_BRIDGE_ADD = frozenset({"agentic", "connector", "connectors", "action", "orchestration", "applications"})


def _expand_substantive_tokens(text: str, exclude_orgs: bool = False) -> set[str]:
    stop = _NON_SUBSTANTIVE_WORDS | _CONVERSATIONAL_NOISE | (_GENERIC_ORG_AND_PROMPT_TOKENS if exclude_orgs else frozenset())
    raw = set(re.findall(r"\b[a-z0-9_]{3,}\b", _NON_TECH_MODEL_COMPOUND_RE.sub(" ", text.lower()))) - stop
    expanded = set(raw) | {re.sub(r"([^s])s$", r"\1", w) for w in raw} | {f"{w}s" for w in raw}
    return (expanded | _AGENTIC_BRIDGE_ADD) if (exclude_orgs and (expanded & _AGENTIC_BRIDGE_TRIGGER)) else expanded


def _score_corroborated_graph_assertion(a: Dict[str, Any], idx: int, live_domain_tokens: set[str], req_specific: set[str]) -> Optional[Tuple[int, int, int, int, int, int, Dict[str, Any]]]:
    claim_str = str(a.get("claim_text") or a.get("object_literal") or "")
    toks = _expand_substantive_tokens(claim_str, exclude_orgs=False)
    dom_hits = len(toks & live_domain_tokens)
    if dom_hits == 0:
        return None
    exec_hits = len(_CONCRETE_EXECUTION_RE.findall(claim_str))
    is_prose = int(not bool(re.match(r"^[A-Za-z0-9\s/&-]{2,40}:\s+", claim_str)))
    return (is_prose, int(bool(req_specific & toks)), dom_hits + min(2, exec_hits), exec_hits, int(len(claim_str.split()) <= 36), -idx, a)


def _filter_corroborated_graph_assertions(non_stub: List[Dict[str, Any]], dedup_live: List[Dict[str, Any]], raw_req: Optional[str]) -> List[Dict[str, Any]]:
    live_domain_tokens = {t for a in dedup_live for t in _expand_substantive_tokens(str(a.get("claim_text") or ""), exclude_orgs=True)}
    req_tokens = _expand_substantive_tokens(str(raw_req or ""), exclude_orgs=False)
    all_tokens = live_domain_tokens | req_tokens
    if not all_tokens:
        return non_stub
    req_specific = req_tokens & {"nea", "lens", "sherpa", "conduit", "jarvis", "rewire", "apex_trans"}
    scored_domain = [s for idx, a in enumerate(non_stub) if (s := _score_corroborated_graph_assertion(a, idx, live_domain_tokens, req_specific))]
    if scored_domain:
        return [a for *_, a in sorted(scored_domain, reverse=True)]
    fallback = [a for a in non_stub if not (t := _expand_substantive_tokens(str(a.get("claim_text") or a.get("object_literal") or ""))) or bool(t & all_tokens)]
    return fallback or non_stub


def merge_live_and_graph_assertions(live_asrts: List[Dict[str, Any]], assertions: List[Dict[str, Any]], raw_req: Optional[str], explicit_live: bool) -> List[Dict[str, Any]]:
    non_stub = [a for a in assertions if not str(a.get("assertion_id", "")).startswith("asrt_attendee_") and not a.get("is_live_turn") and not _META_CLAIM_STUB_RE.search(str(a.get("claim_text") or a.get("object_literal") or "").strip())]
    seen = {str(a.get("claim_text") or "").strip().lower() for a in non_stub}
    dedup_live = [a for a in live_asrts if str(a.get("claim_text") or "").strip().lower() not in seen]
    if not dedup_live or (raw_req and _NOVEL_GRAPH_DIRECTIVE_RE.search(str(raw_req)) and non_stub):
        return non_stub or assertions
    for a in dedup_live:
        a["is_priority"] = a["live_directive_lock"] = True
    corroborated = _filter_corroborated_graph_assertions(non_stub, dedup_live, raw_req)
    return [dedup_live[0], *corroborated[:2], *dedup_live[1:], *corroborated[2:]]


def _enrich_primary_live_assertion_with_workflow_shift(assertions: List[Dict[str, Any]], raw_turns: List[Dict[str, Any]], target: Optional[str]) -> None:
    if not assertions or target is not None or len(assertions[0]["claim_text"].split()) > 16:
        return
    base = assertions[0]["claim_text"]
    for t in raw_turns:
        m = _WORKFLOW_SHIFT_RE.search(str(t.get("text") or ""))
        sub = m.group(1).strip().rstrip(".!?") if m else ""
        if sub and sub.lower() not in base.lower() and len(f"{base}, {sub}".split()) <= 32:
            assertions[0]["claim_text"] = assertions[0]["object_literal"] = f"{base}, {sub[0].lower() + sub[1:]}"
            return


def synthesize_live_transcript_assertions(dialogue: Any, target: Optional[str], selected_utterance: Optional[Any], topic_keywords: Optional[List[str]] = None, max_items: int = 3) -> List[Dict[str, Any]]:
    raw_turns = _gather_candidate_live_turns(dialogue, target, selected_utterance, topic_keywords)
    assertions, seen_claims, used_seqs = [], set(), set()
    for idx, t in enumerate(raw_turns):
        if not (claim := _extract_valid_live_claim(str(t.get("text") or "").strip(), seen_claims)):
            continue
        raw_seq = int(t.get("seq") or (idx + 1))
        seq = raw_seq if raw_seq not in used_seqs else (max(used_seqs) + 1)
        used_seqs.add(seq)
        assertions.append(_build_live_turn_assertion_dict(claim, str(t.get("speaker") or target or "Participant").strip(), seq, act=str(t.get("act") or "status_update")))
        if len(assertions) >= max_items:
            break
    _enrich_primary_live_assertion_with_workflow_shift(assertions, raw_turns, target)
    return assertions

