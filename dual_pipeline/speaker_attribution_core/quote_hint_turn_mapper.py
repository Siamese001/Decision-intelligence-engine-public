"""
dual_pipeline/speaker_attribution_core/quote_hint_turn_mapper.py

Applies operator quote hints directly to individual transcript turns before cluster mapping.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .candidate_scorer import normalize_search_text

_TURN_LINE_RE = re.compile(
    r"^(?P<prefix>(?:\*\*|\#\#\s*|\#\s*)?(?:\[[^\]]+\](?:\*\*)?\s*)?(?:\*\*)?\[?)"
    r"(?P<spk>[A-Za-z0-9_\s\.\-\(\)/]+?)"
    r"(?P<suffix>\]?(?:\*\*)?(?:\s*\[[^\]]+\])?(?::\*\*|\*\*:|:)\s*)"
    r"(?P<text>.+)$"
)


_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "does", "for",
    "from", "get", "got", "had", "has", "have", "he", "her", "here", "him", "his",
    "how", "i", "if", "in", "into", "is", "it", "its", "just", "like", "me", "my",
    "no", "not", "now", "of", "ok", "okay", "on", "or", "our", "out", "right",
    "so", "some", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "to", "up", "us", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "yeah", "yes", "you", "your",
})


def _prepare_valid_hints(hints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    for h in hints:
        if isinstance(h, dict):
            spk = str(h.get("speaker_name") or h.get("speaker") or "").strip()
            q = str(h.get("quote") or h.get("quote_text") or "").strip()
            if spk and q:
                norm_q = normalize_search_text(q)
                tokens = set(norm_q.split())
                content_tokens = {t for t in tokens if t not in _STOPWORDS and len(t) > 2}
                valid.append({
                    "speaker": spk,
                    "quote": q,
                    "norm_quote": norm_q,
                    "tokens": tokens,
                    "content_tokens": content_tokens,
                    "raw": h,
                })
    return valid


def _is_generic_cluster_id(label: str, hint_speakers: set[str]) -> bool:
    clean = label.strip()
    if not clean or clean in hint_speakers:
        return False
    low = clean.lower()
    if low.startswith(("speaker", "voice", "spk", "cluster", "unknown", "channel", "guest", "user")):
        return True
    return False


def _find_matching_hint(valid_hints: List[Dict[str, Any]], norm_text: str, text_tokens: set[str]) -> Any:
    multi_exact = [
        h for h in valid_hints
        if len(h["tokens"]) > 1 and h["norm_quote"] and (
            h["norm_quote"] in norm_text or (len(norm_text) >= 15 and norm_text in h["norm_quote"])
        )
    ]
    if multi_exact:
        return max(multi_exact, key=lambda x: len(x["norm_quote"]))
    for h in valid_hints:
        nq = h["norm_quote"]
        if nq and len(h["tokens"]) == 1 and nq in text_tokens:
            return h
    for h in valid_hints:
        q_tokens = h["tokens"]
        c_tokens = h["content_tokens"]
        if len(q_tokens) >= 3 and len(c_tokens) >= 2:
            overlap = len(q_tokens.intersection(text_tokens)) / len(q_tokens)
            c_overlap = len(c_tokens.intersection(text_tokens)) / len(c_tokens)
            if overlap >= 0.65 and c_overlap >= 0.65:
                return h
        elif len(q_tokens) >= 2 and len(c_tokens) >= 1 and q_tokens.issubset(text_tokens):
            return h
    return None


def apply_quote_hints_to_turns(
    transcript: str, hints: List[Dict[str, Any]]
) -> Tuple[str, Dict[str, str]]:
    """Applies operator quote hints per individual turn before cluster-level mapping."""
    if not hints or not transcript:
        return transcript, {}

    valid_hints = _prepare_valid_hints(hints)
    if not valid_hints:
        return transcript, {}

    lines = transcript.split("\n")
    updated_lines: List[str] = []
    reassignments: Dict[str, str] = {}

    for line in lines:
        line_s = line.strip()
        m = _TURN_LINE_RE.match(line_s)
        if not m:
            updated_lines.append(line)
            continue

        curr_spk = re.sub(r"[\*\[\]]+", "", m.group("spk")).strip()
        curr_text = m.group("text").strip()
        norm_text = normalize_search_text(curr_text)
        text_tokens = set(norm_text.split())

        matched_hint = _find_matching_hint(valid_hints, norm_text, text_tokens)
        if matched_hint:
            new_spk = matched_hint["speaker"]
            raw_h = matched_hint["raw"]
            raw_h.setdefault("bound_speaker_id", curr_spk)
            raw_h["status"] = "RECONCILED"
            raw_h["turn_matched"] = True
            if curr_spk.lower() != new_spk.lower():
                prefix = m.group("prefix")
                suffix = m.group("suffix")
                updated_lines.append(f"{prefix}{new_spk}{suffix}{curr_text}")
                reassignments[f"{curr_spk}:{curr_text[:30]}"] = new_spk
                continue

        updated_lines.append(line)

    return "\n".join(updated_lines), reassignments


def apply_quote_hints_to_utterances(
    utterances: List[Dict[str, Any]],
    hints: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Resolves speaker clusters in structured utterances to attendee names via quote hints.

    Matches valid hints against utterance text, maps corresponding cluster IDs
    (e.g. 'SPEAKER_01' -> 'Alex Sanchez'), and updates utterance speaker attributes.
    Returns (updated_utterances, cluster_to_speaker_map).
    """
    if not hints or not utterances:
        return utterances, {}

    valid_hints = _prepare_valid_hints(hints)
    if not valid_hints:
        return utterances, {}

    cluster_to_speaker: Dict[str, str] = {}
    cluster_hint_speakers: Dict[str, set[str]] = {}
    all_hint_speakers = {h["speaker"] for h in valid_hints if h.get("speaker")}

    for u in utterances:
        text = str(u.get("text", "")).strip()
        if not text:
            continue
        norm_text = normalize_search_text(text)
        tokens = set(norm_text.split())
        matched = _find_matching_hint(valid_hints, norm_text, tokens)
        if matched:
            spk_target = matched["speaker"]
            cluster = str(u.get("raw_speaker") or u.get("speaker") or "").strip()
            if u.get("speaker") and not u.get("raw_speaker"):
                u["raw_speaker"] = u["speaker"]
            if (
                cluster
                and cluster != spk_target
                and _is_generic_cluster_id(cluster, all_hint_speakers)
                and not matched["raw"].get("turn_only")
            ):
                cluster_hint_speakers.setdefault(cluster, set()).add(spk_target)
            u["speaker"] = spk_target
            u["hint_applied"] = True
            matched["raw"]["status"] = "RECONCILED"
            matched["raw"]["turn_matched"] = True

    for cluster, spk_set in cluster_hint_speakers.items():
        if len(spk_set) == 1:
            cluster_to_speaker[cluster] = next(iter(spk_set))

    if cluster_to_speaker:
        for u in utterances:
            if u.get("hint_applied"):
                continue
            curr_spk = str(u.get("raw_speaker") or u.get("speaker") or "").strip()
            if curr_spk in cluster_to_speaker:
                if u.get("speaker") and not u.get("raw_speaker"):
                    u["raw_speaker"] = u["speaker"]
                u["speaker"] = cluster_to_speaker[curr_spk]

    return utterances, cluster_to_speaker
