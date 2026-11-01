"""
dual_pipeline/live_meeting_recorder_core/copilot_clause_verifier.py

Deep factual clause verifier for Live Executive Copilot (Wave 4).
Enforces:
1. Clause-level semantic and lexical grounding against retained assertions.
2. Anti-hallucination of invented durations and timelines (even with valid citation IDs).
3. Anti-hallucination of unsupported reuse and cross-engagement dependency claims.
4. Attribution verification for named systems and entities.
5. Equal sentence-level verification across supporting bullets, suggested script, and summaries.
6. Explicit labeling of suggestions vs verified facts.
7. Fail-closed handling for empty evidence and timeout fallbacks.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier")

COMMON_STOPWORDS: Set[str] = {
    "the", "and", "for", "with", "this", "that", "from", "project", "meeting",
    "status", "could", "would", "should", "what", "which", "there", "their",
    "have", "been", "were", "will", "about", "into", "over", "some", "also",
    "than", "then", "just", "more", "most", "only", "can", "our", "all",
    "are", "was", "has", "had", "not", "but", "out", "who", "whom", "how",
    "when", "where", "why", "must", "any", "each", "both", "such", "said",
    "does", "did", "doing", "done", "these", "those", "under", "between",
}

CAP_IGNORE: Set[str] = {
    "We", "The", "Our", "All", "Each", "Can", "Could", "Should", "Would",
    "What", "Which", "Who", "How", "When", "Where", "Why", "There", "This",
    "That", "These", "Those", "Team", "Phase", "Project", "Let", "Here", "Yes", "No", "Note",
    "Given", "However", "Because", "Although", "While", "Since", "During", "Following",
    "Across", "Within", "Before", "After", "Under", "Without", "Despite", "Based",
    "According", "First", "Second", "Third", "Meanwhile", "Additionally", "Specifically", "Currently",
    "Prior", "Current", "Date", "Meeting", "Status", "Workstream", "Stakeholder",
}

NUM_WORDS: Dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20",
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}

DURATION_PATTERN = re.compile(
    r"\b(?:"
    r"(?:\d+(?:\.\d+)?|\d+-\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|ninety)[-\s]+"
    r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|sprints?|quarters?|years?)"
    r"|sprint\s+\d+|q[1-4]\b|\b202[4-9]\b|\btomorrow\b|\byesterday\b|\bnext\s+week\b"
    r")\b",
    re.IGNORECASE,
)

REUSE_DEPENDENCY_PATTERN = re.compile(
    r"\b(?:reus(?:e|ing|ed|able)|re-us(?:e|ing|ed|able)|depend(?:s|ed|ing)?\s+on|dependency"
    r"|derived\s+from|shared\s+(?:component|library|service|module|code|architecture))\b",
    re.IGNORECASE,
)

REUSE_PREDICATE_KEYWORDS: Set[str] = {
    "reuse", "reused", "reusing", "reusable", "depend", "depends", "depends_on",
    "dependency", "derived", "derived_from", "share", "shared", "shared_component",
    "constrains", "blocks", "implements", "relies_on",
}

GROUNDING_QUESTION_FALLBACK = "Can we verify the technical ground truth for this topic?"


def normalize_duration_str(d: str) -> str:
    s = d.lower()
    s = re.sub(r"(?<=[a-z])-(?=[a-z])|(?<=[a-z])-(?=\d)|(?<=\d)-(?=[a-z])", " ", s)
    for w, dig in NUM_WORDS.items():
        s = re.sub(rf"\b{w}\b", dig, s)
    s = re.sub(r"\b(day|week|month|hour|year|minute|second)s?\b", r"\1s", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_durations(text: str) -> Set[str]:
    return {normalize_duration_str(m.group(0)) for m in DURATION_PATTERN.finditer(text)}


def check_reuse_support(clause: str, a: Dict[str, Any]) -> bool:
    if not REUSE_DEPENDENCY_PATTERN.search(clause):
        return True
    a_txt = f"{a.get('claim_text', '')} {a.get('predicate', '')} {a.get('object_literal', '')}".lower()
    pred = str(a.get("predicate", "")).lower()
    return any(k in pred for k in REUSE_PREDICATE_KEYWORDS) or bool(REUSE_DEPENDENCY_PATTERN.search(a_txt))


def _utterance_support_text(selected_utterance: Optional[Any]) -> str:
    if selected_utterance is None:
        return ""
    getter = selected_utterance.get if hasattr(selected_utterance, "get") else lambda k: getattr(selected_utterance, k, "")
    return f"{getter('speaker') or ''} {getter('text') or ''} {getter('speaker_summary') or ''}".strip()


def strip_vocative_target(clause: str, target: Optional[str] = None) -> str:
    """Strips leading workstream brackets, vocative addressee prefix, and speaker-point framing."""
    if not clause:
        return ""
    text = re.sub(r"^\[[^\]]+\]\s*", "", clause.strip()).strip()
    if target:
        t_clean = target.strip()
        parts = t_clean.split()
        names = [re.escape(t_clean)] + ([re.escape(parts[0])] if len(parts) > 1 else [])
        pattern = rf"^(?:(?:hi|hey|hello)\s+)?(?:@)?(?:{'|'.join(names)})\s*[,:\-–—]\s*"
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    else:
        text = re.sub(r"^(?:(?:hi|hey|hello)\s+)?(?:@)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*[,:\-–—]\s*", "", text).strip()
    text = re.sub(
        r"^(?:(?:to|regarding)\s+[A-Za-z0-9_.\- ]+'s\s+point|on\s+your\s+[a-z0-9_\- ]+\s+point)\s*[,:\-–—]\s*",
        "", text, flags=re.IGNORECASE,
    ).strip()
    text = re.sub(
        r"^(?:(?:in\s+parallel|specifically|additionally),\s+)?"
        r"(?:let's\s+(?:also\s+)?align\s+on|let\s+us\s+(?:also\s+)?align\s+on|we\s+can\s+(?:also\s+)?align\s+on"
        r"|what\s+concrete\s+ownership\s+boundaries\s+and\s+deliverables\s+should\s+we\s+lock\s+in\s+regarding"
        r"|what\s+specific\s+guardrails\s+or\s+next\s+steps\s+should\s+we\s+lock\s+in\s+(?:on|given\s+that)"
        r"|could\s+we\s+clarify(?:\s+the\s+next\s+step\s+on)?|what\s+is\s+the\s+next\s+step\s+for"
        r"|we\s+should\s+confirm\s+whether|(?:we're|we\s+are)\s+(?:also\s+)?tracking\s+that"
        r"|follow\s+up\s+on\s*:|note\s+that|how\s+does|our\s+agreed\s+baseline\s+is\s+that|our\s+records\s+show\s+that)\s+",
        "", text, flags=re.IGNORECASE,
    ).strip()
    return re.sub(r"(?:\.\s*what\s+is\s+the\s+gap\s+here\?|\s+connect\s+to\s+this\?)\s*$", "", text, flags=re.IGNORECASE).strip()


_FRAMING_VERBS: Set[str] = {
    "confirm", "verify", "review", "align", "ensure", "establish", "propose",
    "recommend", "focus", "prioritize", "address", "resolve", "clarify",
    "anchor", "follow", "track", "bound", "schedule", "validate",
}


def check_entity_support(
    clause: str, a_txt: str, target: Optional[str] = None, utterance_txt: str = ""
) -> bool:
    clean_clause = strip_vocative_target(clause, target)
    words = re.findall(r"\b[A-Z][a-zA-Z0-9_-]+\b", clean_clause)
    first_word = clean_clause.split()[0].rstrip(",:;.?!") if clean_clause.split() else ""
    entities = {
        w for w in words
        if w not in CAP_IGNORE
        and not w.startswith(("Asrt_", "Assertion-", "ASRT_"))
        and not (w == first_word and len(w) > 1 and w[1:].islower() and (w.lower() in COMMON_STOPWORDS or w.lower() in _FRAMING_VERBS))
    }
    if not entities:
        return True
    target_tokens = {w.lower() for w in re.findall(r"\b[a-zA-Z0-9_-]+\b", target)} if target else set()
    combined_lower = f"{a_txt} {a_txt.replace('_', ' ')} {utterance_txt}".lower()
    return all((target and ent.lower() in target_tokens) or ent.lower() in combined_lower for ent in entities)


def _assertion_support_text(a: Dict[str, Any]) -> str:
    return (
        f"{a.get('claim_text', '')} {a.get('predicate', '')} {a.get('subject', '')} "
        f"{a.get('object', '')} {a.get('_resolved_subject', '')} {a.get('_resolved_object', '')} "
        f"{a.get('subject_node_id', '')} {a.get('object_node_id', '')} {a.get('object_literal', '')} "
        f"{a.get('observed_at', '')} {a.get('source_date', '')}"
    )


def extract_content_tokens(text: str) -> Set[str]:
    clean = re.sub(r"\b(?:asrt_[a-zA-Z0-9_-]+|assertion-[a-zA-Z0-9_-]+)\b", "", text)
    clean = re.sub(r"\bwe're\b", "we are", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bit's\b", "it is", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bthat's\b", "that is", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\blet's\b", "let us", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bcan't\b", "cannot", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bn't\b", " not", clean, flags=re.IGNORECASE)
    tokens = {w.lower() for w in re.findall(r"\b[A-Za-z0-9_]{3,}\b", clean)}
    tokens |= {w.lower() for w in re.findall(r"\b[A-Za-z0-9]{3,}\b", clean.replace("_", " "))}
    return {w for w in tokens if w not in COMMON_STOPWORDS}


def _verify_explicit_citations(
    clause: str,
    by_id: Dict[str, Dict[str, Any]],
    c_durs: Set[str],
    target: Optional[str] = None,
    utterance_txt: str = "",
) -> Optional[str]:
    for m in re.findall(r"\b(asrt_[a-zA-Z0-9_-]+|assertion-[a-zA-Z0-9_-]+)\b", clause):
        if m in by_id:
            cand = by_id[m]
            cand_txt = _assertion_support_text(cand)
            cand_durs = extract_durations(cand_txt)
            if c_durs and not c_durs.issubset(cand_durs):
                continue
            if not check_reuse_support(clause, cand):
                continue
            if not check_entity_support(clause, cand_txt, target=target, utterance_txt=utterance_txt):
                continue
            return m
    return None


def _is_assertion_match(
    c_tokens: Set[str],
    c_durs: Set[str],
    clause: str,
    a: Dict[str, Any],
    target: Optional[str] = None,
    utterance_txt: str = "",
) -> bool:
    a_txt = _assertion_support_text(a)
    a_tokens = extract_content_tokens(a_txt)
    overlap = c_tokens & a_tokens
    min_ov = 1 if (a.get("is_live_turn") and len(a_tokens) <= 2) else 2
    if len(overlap) < min_ov or (not a.get("is_live_turn") and len(overlap) < 3 and (len(overlap) / max(1, len(c_tokens))) < 0.35):
        return False
    a_durs = extract_durations(a_txt)
    if c_durs and not c_durs.issubset(a_durs):
        return False
    if not check_reuse_support(clause, a) or not check_entity_support(clause, a_txt, target=target, utterance_txt=utterance_txt):
        return False
    return True


def _match_assertion_candidates(
    c_tokens: Set[str],
    c_durs: Set[str],
    clause: str,
    valid_assertions: List[Dict[str, Any]],
    target: Optional[str] = None,
    utterance_txt: str = "",
) -> Optional[str]:
    for a in valid_assertions:
        aid = a.get("assertion_id")
        if aid and _is_assertion_match(c_tokens, c_durs, clause, a, target=target, utterance_txt=utterance_txt):
            return str(aid)
    return None


def _match_pairwise_union(
    c_tokens: Set[str],
    c_durs: Set[str],
    clause: str,
    valid_assertions: List[Dict[str, Any]],
    target: Optional[str] = None,
    utterance_txt: str = "",
) -> Optional[str]:
    if len(valid_assertions) < 2:
        return None
    top_pool = valid_assertions[:4]
    for i in range(len(top_pool)):
        for j in range(i + 1, len(top_pool)):
            a1, a2 = top_pool[i], top_pool[j]
            t1 = extract_content_tokens(f"{a1.get('claim_text', '')} {a1.get('object_literal', '')}")
            t2 = extract_content_tokens(f"{a2.get('claim_text', '')} {a2.get('object_literal', '')}")
            if not (c_tokens & t1) or not (c_tokens & t2):
                continue
            union_ast = {
                "assertion_id": a1.get("assertion_id") or a2.get("assertion_id"),
                "claim_text": f"{_assertion_support_text(a1)} {_assertion_support_text(a2)}",
                "predicate": f"{a1.get('predicate', '')} {a2.get('predicate', '')}",
            }
            if _is_assertion_match(c_tokens, c_durs, clause, union_ast, target=target, utterance_txt=utterance_txt):
                return str(union_ast["assertion_id"])
    return None


_UNSUPPORTED_HEADER_RE = re.compile(r"\b(?:client\s+production\s+milestone|production\s+milestone)\s*:", re.IGNORECASE)
_UNSUPPORTED_ANCHOR_RE = re.compile(
    r"\bon\s+your\s+(?:how|what|why|when|where|who|which|overall|work|better|together|most|best|latest|novel|intelligent|intellectual|copilot)\s+point\b",
    re.IGNORECASE,
)
_REGARDING_VOCATIVE_RE = re.compile(r"^\s*(?:[-*•]\s*)?(?:\[[^\]]+\]\s*)?([A-Za-z][A-Za-z\s]+?),\s+regarding\s+([A-Za-z][A-Za-z\s]+?)'s\s+point\b", re.IGNORECASE)


def _has_unsupported_clause_framing(clause: str) -> bool:
    if not clause:
        return True
    if _UNSUPPORTED_HEADER_RE.search(clause) or _UNSUPPORTED_ANCHOR_RE.search(clause):
        return True
    m = _REGARDING_VOCATIVE_RE.match(clause)
    if m:
        from .copilot_roster_bridge import are_same_speaker_identity
        if are_same_speaker_identity(m.group(1), m.group(2)):
            return True
    return False


def check_clause_grounding(
    clause: str,
    valid_assertions: List[Dict[str, Any]],
    by_id: Dict[str, Dict[str, Any]],
    target: Optional[str] = None,
    selected_utterance: Optional[Any] = None,
) -> Optional[str]:
    """Checks whether a single clause/sentence is strictly grounded in retained assertions and live transcript context."""
    if _has_unsupported_clause_framing(clause):
        return None
    utt_txt = _utterance_support_text(selected_utterance)
    c_durs = extract_durations(clause)
    hit = _verify_explicit_citations(clause, by_id, c_durs, target=target, utterance_txt=utt_txt)
    if hit:
        return hit
    clean_clause = strip_vocative_target(clause, target)
    c_tokens = extract_content_tokens(clean_clause)
    if not c_tokens:
        return None
    matched = _match_assertion_candidates(c_tokens, c_durs, clause, valid_assertions, target=target, utterance_txt=utt_txt)
    if matched or not valid_assertions:
        return matched
    pair_hit = _match_pairwise_union(c_tokens, c_durs, clause, valid_assertions, target=target, utterance_txt=utt_txt)
    if pair_hit:
        return pair_hit
    low_clause = clean_clause.lower()
    if "was described as" in low_clause and "which status is current" in low_clause:
        union_ast = {
            "assertion_id": valid_assertions[0].get("assertion_id") or "conflict_pair",
            "claim_text": " ".join(_assertion_support_text(a) for a in valid_assertions) + " prior date current date described",
            "predicate": " ".join(str(a.get("predicate") or "") for a in valid_assertions),
        }
        if _is_assertion_match(c_tokens, set(), clause, union_ast, target=target, utterance_txt=utt_txt):
            return str(union_ast["assertion_id"])
    return None


def verify_and_filter_sentences(
    text: str, valid_assertions: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]],
    cited_ids: Set[str], target: Optional[str] = None, selected_utterance: Optional[Any] = None,
) -> List[str]:
    retained: List[str] = []
    for s in (s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()):
        if hit := check_clause_grounding(s, valid_assertions, by_id, target=target, selected_utterance=selected_utterance):
            retained.append(s)
            cited_ids.add(hit)
    return retained


def verify_and_filter_bullets(
    raw_bullets: List[Any], valid_assertions: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]],
    cited_ids: Set[str], target: Optional[str] = None, selected_utterance: Optional[Any] = None,
) -> List[str]:
    filtered: List[str] = []
    for b in raw_bullets:
        if hit := check_clause_grounding(str(b), valid_assertions, by_id, target=target, selected_utterance=selected_utterance):
            filtered.append(str(b))
            cited_ids.add(hit)
    return filtered


def verify_and_filter_script(
    script: str, valid_assertions: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]],
    cited_ids: Set[str], target: Optional[str] = None, selected_utterance: Optional[Any] = None,
) -> str:
    retained = verify_and_filter_sentences(script, valid_assertions, by_id, cited_ids, target=target, selected_utterance=selected_utterance)
    return " ".join(retained) if retained else GROUNDING_QUESTION_FALLBACK


def verify_and_filter_summary(
    summary_text: str, valid_assertions: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]],
    cited_ids: Set[str], target: Optional[str] = None, selected_utterance: Optional[Any] = None,
) -> str:
    if not summary_text:
        return ""
    return " ".join(verify_and_filter_sentences(summary_text, valid_assertions, by_id, cited_ids, target=target, selected_utterance=selected_utterance))


def apply_suggestion_labeling(parsed: Dict[str, Any], cited_ids: Set[str]) -> None:
    script = str(parsed.get("suggested_script", ""))
    is_question = "?" in script or script.startswith("Can we verify")
    if is_question:
        parsed["label"] = "Clarification" if script.startswith("Can we verify") else "Suggestion"
        parsed["is_suggestion"] = True
    elif cited_ids:
        parsed["label"] = "Verified Answer"
        parsed["is_suggestion"] = False
    else:
        parsed["label"] = "Suggestion"
        parsed["is_suggestion"] = True


def _handle_empty_assertions(parsed: Dict[str, Any]) -> Dict[str, Any]:
    parsed["cited_assertion_ids"] = []
    parsed["supporting_details_bullets"] = []
    parsed["suggested_script"] = GROUNDING_QUESTION_FALLBACK
    parsed["snap_cue"] = "Verify ground truth"
    parsed["category"] = "ALIGNMENT"
    if "speaker_summary" in parsed:
        parsed["speaker_summary"] = ""
    if "summary" in parsed:
        parsed["summary"] = ""
    apply_suggestion_labeling(parsed, set())
    parsed["rigor_tier"] = "UNVERIFIED"
    return parsed


def enforce_deep_clause_verification(
    parsed: Dict[str, Any],
    verified_assertions: Optional[List[Dict[str, Any]]],
    rigor_tier: str = "VERIFIED",
    target: Optional[str] = None,
    selected_utterance: Optional[Any] = None,
) -> Dict[str, Any]:
    """Applies deep clause verification, duration, reuse, attribution, and summary checks."""
    parsed["rigor_tier"] = rigor_tier
    assertions = verified_assertions or []
    if not assertions:
        return _handle_empty_assertions(parsed)

    effective_target = target or parsed.get("target")
    eff_utt = selected_utterance if selected_utterance is not None else parsed.get("selected_utterance")

    by_id: Dict[str, Dict[str, Any]] = {
        str(a.get("assertion_id")): a for a in assertions if a.get("assertion_id")
    }
    cited_ids: Set[str] = set()

    # 1. Supporting details bullets
    raw_bullets = parsed.get("supporting_details_bullets")
    if isinstance(raw_bullets, list):
        parsed["supporting_details_bullets"] = verify_and_filter_bullets(
            raw_bullets, assertions, by_id, cited_ids, target=effective_target, selected_utterance=eff_utt
        )

    # 2. Suggested script
    script = str(parsed.get("suggested_script", "")).strip()
    if script:
        new_script = verify_and_filter_script(
            script, assertions, by_id, cited_ids, target=effective_target, selected_utterance=eff_utt
        )
        if new_script.startswith("Can we verify"):
            parsed["snap_cue"] = "Verify ground truth"
            parsed["category"] = "ALIGNMENT"
        parsed["suggested_script"] = new_script

    # 3. Summaries receive equal checks
    for sum_key in ("speaker_summary", "summary", "headline"):
        val = parsed.get(sum_key)
        if isinstance(val, str) and val.strip():
            parsed[sum_key] = verify_and_filter_summary(
                val, assertions, by_id, cited_ids, target=effective_target, selected_utterance=eff_utt
            )

    parsed["cited_assertion_ids"] = sorted(cited_ids)
    apply_suggestion_labeling(parsed, cited_ids)
    if not cited_ids:
        parsed["rigor_tier"] = "UNVERIFIED"
    return parsed
