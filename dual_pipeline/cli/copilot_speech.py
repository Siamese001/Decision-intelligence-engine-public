"""Small, speakable Copilot responses built only from verified graph assertions."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.live_meeting_recorder_core.copilot_context import STOPWORDS
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import are_same_speaker_identity


@dataclass
class SpeechRenderingResult:
    """Detailed result of speech synthesis across candidate assertions."""
    body: str
    rendered_count: int = 0
    malformed_count: int = 0
    budget_exhausted_count: int = 0
    failure_reason: Optional[str] = None  # None, "MALFORMED_REPRESENTATION", "WORD_BUDGET_EXHAUSTED"
    assertion_id: Optional[str] = None


_EXTRACTION_METADATA_PATTERNS = (
    re.compile(r"\bis\s+an\s+identified\b", re.IGNORECASE),
    re.compile(r"\bis\s+a\s+reported\s+speaker\s+in\b", re.IGNORECASE),
    re.compile(r"\bstatus/posture\s*:", re.IGNORECASE),
    re.compile(r"\bcandidate\s+(?:ai[-_\s]*sdlc|enterprise[-_\s]*retrieval|gemini/enterprise[-_\s]*agent|co-hydrated\s+operating-model)\b", re.IGNORECASE),
    re.compile(r"^\s*[-*•]?\s*\*\*(?:apex_global|partner_advisory|attendees|participants|roster)\b", re.IGNORECASE),
    re.compile(r"^\s*[-*•]?\s*\**(?:sender|from|to|cc|bcc|sent|subject)\**\s*:", re.IGNORECASE),
    re.compile(r"\[[^\]]+@[^\]]+\]\(mailto:", re.IGNORECASE),
    re.compile(r"\b(?:technical_anchor|strategic_anchor|domain_anchor)\b", re.IGNORECASE),
    re.compile(r"\b(?:is\s+not\s+the\s+[^;]+;\s*he\s+works\s+strictly\s+in|superseded\s+per\s+operator\s+ratification|departed\s+partner_advisory\s+effective)\b", re.IGNORECASE),
    re.compile(r"\braci,\s+enabled\s+capabilities,\s+tool\s+access\b", re.IGNORECASE),
    re.compile(r"^workstream\s+[A-Z][^.!?:]+-\s+[A-Z][^.!?:]+\.?$", re.IGNORECASE),
    re.compile(r"^(?:[A-Z0-9_/.-]{2,6}\s+){3,}[A-Z0-9_/.-]{2,6}\.?$"),
    re.compile(r"\bin\s+your\s+cloud\s+in\s+our\s+cloud\b", re.IGNORECASE),
    re.compile(r"\b(?:um|uh)\b", re.IGNORECASE),
)
_CLAIM_PREFIX_PATTERNS = (
    re.compile(r"^\[[^\]]+\]\s*"),
    re.compile(r"^(?:client\s+production\s+milestone|production\s+milestone|milestone|deliverable|action\s+item|key\s+decision|status\s+update)\s*:\s*", re.IGNORECASE),
    re.compile(r"^(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?(?:\s+and\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)?)\s+(?:reported|stated|noted)\s+that\s+", re.IGNORECASE),
    re.compile(r"^([A-Za-z0-9_.\- /]+?)\s*:\s*\1\b[,:\s-]*", re.IGNORECASE),
    re.compile(r"^(?:next\s*era\s+energy\s*/\s*nee|nee\s*/\s*next\s*era\s+energy)\s*:\s*", re.IGNORECASE),
)
_INTERNAL_CAVEAT_PATTERNS = (
    re.compile(r"\bperson-specific\s+attribution\s+from\s+the\s+spelling\b", re.IGNORECASE),
    re.compile(r"\bremains\s+blocked\s+pending\s+identity\s+confirmation\b", re.IGNORECASE),
    re.compile(r"\btreat\s+this\s+as\s+source-attributed\s+internal\s+feedback\b", re.IGNORECASE),
    re.compile(r"\bthis\s+is\s+internal\s+synthesis,\s+not\s+formal\s+delivery\s+status\b", re.IGNORECASE),
    re.compile(r"\bthis\s+supports\s+proposal-level\s+discussion,\s+not\s+approved\s+delivery\s+authority\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+establish\s+.*\bdecision\s+rights\b", re.IGNORECASE),
    re.compile(r"\bcomplete\s+replacement\s+scope\s+and\s+retirement\s+timing\s+are\s+not\s+established\b", re.IGNORECASE),
    re.compile(r"^(?:scope|risk/watch|evidence\s+basis)\s*:", re.IGNORECASE),
)

_PROPER_NOUN_STARTERS = frozenset({
    "I", "Sherpa", "Conduit", "Nexus", "Jarvis", "Speaker_Alpha", "Speaker_Beta", "Marcus",
    "Amit", "Andy", "Mitch", "Mitchell", "Jason", "Sharon", "Daniel", "Mandy", "Miles", "Skyler",
    "Ed", "Eduardo", "Michael", "Chaffee", "Rob", "Apex Global", "PartnerAdvisory", "LENS", "SOW", "Maximo",
    "Brett", "Jeffrey", "Jeff", "Tyler", "John", "Creech", "Traci", "Tracy", "MaxCore", "NEA", "Vanguard",
    "Gavin", "Kinnick", "Leavitt", "Josh", "Shreyak", "Chakra", "Gemini", "Google", "Decommissioned", "Production",
})

_ACTION_VERBS = frozenset({
    "challenge", "respond", "react", "address", "question", "ask", "clarify",
    "counter", "push", "back", "increase", "increased", "point", "comment",
})
_NON_ANCHOR_MODIFIERS = frozenset({
    "most", "more", "less", "best", "latest", "last", "first", "next", "new", "old",
    "good", "great", "better", "same", "other", "main", "key", "big", "real", "full",
    "whole", "entire", "prior", "current", "recent", "novel", "smart", "human",
    "intelligent", "intellectual", "strategic", "skeptical", "overall", "general", "copilot",
})


def _trim_trailing_conjunctions(text: str) -> str:
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
        _LEADING_SCAFFOLD_RE, _has_substantive_content, _is_dangling_word,
    )
    curr = _LEADING_SCAFFOLD_RE.sub("", text.strip(" .")).strip(" ,;.")
    words = curr.split()
    while words and _is_dangling_word(words):
        words.pop()
    res = " ".join(words).strip(" ,;:-.")
    return res if _has_substantive_content(res) else ""


_ARROW_TRIPLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,}\s*->\s*\S+$")
_OPAQUE_ID_RE = re.compile(r"\b(?:entity|node|source|src)[-_][0-9a-f]{6,}\b", re.IGNORECASE)
_NODE_PREFIX_RE = re.compile(r"^(?:person|org|project|workstream|anchor|system|metric|role|policy)[_:\s]+", re.IGNORECASE)


def _strip_markdown_table_syntax(text: str) -> str:
    if "|" not in text:
        return text
    cells: List[str] = []
    for raw_c in text.split("|"):
        c = raw_c.strip()
        low = c.lower()
        if not c or c.isdigit() or set(c) <= {"-", ":", " "} or "unscored" in low or low in ("[blank]", "blank", "tbd", "n/a"):
            continue
        cells.append(c)
    return ": ".join(cells[:2]) if len(cells) >= 2 else (cells[0] if cells else "")


_PARENTHETICAL_ORG_NOISE_RE = re.compile(
    r"\s*\((?:[^()]|\([^()]*\))*(?:bus\s+ops|in\s+progress|status\s*:|headcount|staff|technical_anchor|strategic_anchor|domain_anchor)(?:[^()]|\([^()]*\))*\)",
    re.IGNORECASE,
)
_CHARTER_TRACKER_TAIL_RE = re.compile(
    r"(?:,\s*\$[\d,]+[kKmM]?\s+nav,\s*\d+%\s+assignment\s+progress|\s*\|\s*(?:resource\s+note|open\s+q[1-4]\s+roles).*$"
    r"|,\s*assigned\s+[\d.]+\s*\([^)]+\),\s*and\s+gap\s+[\d.]+\s*\([^)]+\)\.\s*assigned\s+team:.*$"
    r"|\s*\(pm:\s*[\d.]+,\s*sw:[^)]+\))",
    re.IGNORECASE,
)
_ROLE_PAREN_CARD_RE = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s*\(([^)]{3,70})\)\.?$")


def _coalesce_distilled_clauses(clauses: List[str]) -> str:
    if len(clauses) >= 2 and clauses[1].lower().startswith("no-code is preferred because "):
        tail_reason = re.sub(r"^no-code\s+is\s+preferred\s+because\s+it\s+", "as no-code ", clauses[1].rstrip(".!?"), flags=re.IGNORECASE)
        clauses = [f"{clauses[0].rstrip('.!?')}, {tail_reason}"]
    joined = " ".join(clauses)
    return clauses[0] if (len(clauses) > 1 and len(joined.split()) > 26 and len(clauses[0].split()) >= 6) else joined


def _distill_claim_text(raw: str) -> str:
    text = " ".join(str(raw).split()).replace("Â·", "·").replace("Â", "").strip()
    if not text or _ARROW_TRIPLE_RE.match(text) or any(p.search(text) for p in _EXTRACTION_METADATA_PATTERNS):
        return ""
    text = _strip_markdown_table_syntax(text)
    text = _ROLE_PAREN_CARD_RE.sub(r"\1, \2", text)
    text = _CHARTER_TRACKER_TAIL_RE.sub("", text).strip()
    text = _PARENTHETICAL_ORG_NOISE_RE.sub("", text)
    text = re.sub(r"\b([A-Z])\.\s*([A-Z])\b", r"\1\2", text)
    text = re.sub(r"^\s*[-*•]\s+", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text).strip()
    for pat in _CLAIM_PREFIX_PATTERNS:
        text = pat.sub("", text).strip()
    clauses = [
        c.strip() for c in re.split(r"(?<=[.!?])\s+|;\s+", text)
        if c.strip() and not any(p.search(c) for p in _INTERNAL_CAVEAT_PATTERNS)
    ]
    if not clauses:
        return ""
    joined = _coalesce_distilled_clauses(clauses)
    for pat in _CLAIM_PREFIX_PATTERNS:
        joined = pat.sub("", joined).strip()
    return _trim_trailing_conjunctions(joined)


def _resolve_node_display_name(node_id: Any, nodes: Dict[str, Any]) -> str:
    node = nodes.get(node_id, {}) if isinstance(nodes, dict) else {}
    cand = str(node.get("display_name") or node.get("canonical_name") or node.get("name") or node.get("label") or "").strip()
    if not cand:
        nid_str = str(node_id or "").strip()
        if not nid_str or nid_str.startswith(("entity-", "node-")) or _OPAQUE_ID_RE.search(nid_str):
            return ""
        cand = _NODE_PREFIX_RE.sub("", nid_str)
        if nid_str.lower().startswith("person_"):
            cand = cand.replace("_", " ").title()
    if _OPAQUE_ID_RE.search(cand):
        return ""
    return cand.replace("_", " ").strip()


def spoken_claim(assertion: Dict[str, Any], retriever: CopilotGraphRetriever) -> str:
    if not isinstance(assertion, dict) or assertion.get("_has_opaque_endpoint"):
        return ""
    raw_val = str(assertion.get("object_literal") or assertion.get("claim_text") or assertion.get("claim") or "").strip()
    if raw_val and not _ARROW_TRIPLE_RE.match(raw_val):
        distilled = _distill_claim_text(raw_val)
        if distilled and not _OPAQUE_ID_RE.search(distilled) and " -> " not in distilled:
            return distilled
    topology = getattr(retriever, "_topology", None)
    nodes = getattr(topology, "nodes", {}) if topology is not None else {}
    subject = _resolve_node_display_name(assertion.get("subject_node_id") or assertion.get("subject"), nodes)
    obj = _resolve_node_display_name(assertion.get("object_node_id") or assertion.get("object"), nodes)
    if not subject or not obj:
        return ""
    assertion["_resolved_subject"], assertion["_resolved_object"] = subject, obj
    relation = str(assertion.get("predicate") or "relates to").lower().replace("_", " ")
    return f"{subject} {relation} {obj}"


def _clean_conflict_date(raw_date: Any, fallback: str) -> str:
    s = str(raw_date or "").split("T")[0].strip()
    return fallback if (not s or _OPAQUE_ID_RE.search(s)) else s


def _format_single_conflict(conflict: Dict[str, Any], verified_ids: set[Any], max_words: Optional[int], retriever: CopilotGraphRetriever) -> str:
    earlier, later = conflict.get("assertion_a", {}), conflict.get("assertion_b", {})
    if not ({earlier.get("assertion_id"), later.get("assertion_id")} <= verified_ids):
        return ""
    subject_id = earlier.get("subject_node_id") or earlier.get("subject")
    topology = getattr(retriever, "_topology", None)
    nodes = getattr(topology, "nodes", {}) if topology else {}
    subject = _resolve_node_display_name(subject_id, nodes)
    if not subject:
        return ""
    earlier["_resolved_subject"] = later["_resolved_subject"] = subject
    c_earlier = _sanitize_speakable_claim(spoken_claim(earlier, retriever))
    c_later = _sanitize_speakable_claim(spoken_claim(later, retriever))
    if not c_earlier or not c_later:
        return ""
    date_a, date_b = _clean_conflict_date(conflict.get("date_a"), "Prior Date"), _clean_conflict_date(conflict.get("date_b"), "Current Date")
    comment = f"{subject} was described as {c_earlier} on {date_a} and {c_later} on {date_b}. Which status is current?"
    return comment if ("```" not in comment and (max_words is None or len(comment.split()) <= max_words)) else ""


def _conflict_comment(assertions: List[Dict[str, Any]], max_words: Optional[int], retriever: CopilotGraphRetriever) -> str:
    verified_ids = {a.get("assertion_id") for a in assertions}
    for conflict in getattr(retriever, "_last_gap_conflicts", []):
        comment = _format_single_conflict(conflict, verified_ids, max_words, retriever)
        if comment:
            return comment
    return ""


_MARKUP_TAG_RE = re.compile(r"(\[.*?\]|```|<.*?>|\{.*?\})")
_FILE_EXT_PAREN_RE = re.compile(r"\s*\([^)]*\.(?:json|yaml|yml|md|txt|csv|sql)\)", re.IGNORECASE)
_META_PAREN_RE = re.compile(r"\s*\([^)]*(?:source:|id:|asrt_|ast_|node:|score|manage|operate|govern)[^)]*\)", re.IGNORECASE)
_ISO_TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
_MATH_SYMBOLS = (
    (re.compile(r">="), "at least "), (re.compile(r"<="), "at most "),
    (re.compile(r"!="), "does not equal "), (re.compile(r"\s*(?:—|--)\s*"), " and "),
)
_CONTRACTIONS = (
    (re.compile(r"\bwe are\b", re.IGNORECASE), "we're"), (re.compile(r"\bit is\b", re.IGNORECASE), "it's"),
    (re.compile(r"\bdo not\b", re.IGNORECASE), "don't"), (re.compile(r"\bcannot\b", re.IGNORECASE), "can't"),
    (re.compile(r"\bthat is\b", re.IGNORECASE), "that's"), (re.compile(r"\blet us\b", re.IGNORECASE), "let's"),
)


def _normalize_spoken_ear(text: str) -> str:
    cleaned = _FILE_EXT_PAREN_RE.sub("", text)
    cleaned = _META_PAREN_RE.sub("", cleaned)
    cleaned = _ISO_TS_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\b([A-Z][a-z]+)_([A-Z][a-z]+)\b", r"\1 \2", cleaned)
    for pat, rep in _MATH_SYMBOLS:
        cleaned = pat.sub(rep, cleaned)
    for pat, rep in _CONTRACTIONS:
        cleaned = pat.sub(rep, cleaned)
    return " ".join(cleaned.split())


_LEADING_VERB_CLAUSE_RE = re.compile(
    r"^(?:i\s+may\s+have\s+it[,;\s]+but\s+(?:can|do)\s+you\s+mind\s+just\s+|(?:can|do)\s+you\s+mind\s+just\s+|(?:can|could)\s+you\s+just\s+|i['’]?m\s+going\s+to\s+go\s+ahead\s+and\s+"
    r"|(?:working|doing)\s+on\s+(?:stuff|things)\s+(?:here\s+)?(?:in\s+the\s+|on\s+the\s+|at\s+the\s+)?"
    r"|(?:platform|architecture|solution|executive)\s+overview\s*:\s*|as\s+discussed,?\s*(?:and\s+reflected\s+in\s+our\s+sow,?\s*)?)",
    re.IGNORECASE,
)
_ASR_SPLIT_SOW_RE = re.compile(r"\b(?:the,\s+)?(?:in\s+NEA,\s+the\s+)?S-\s*So,\s*W\b", re.IGNORECASE)
_LIVE_LEADIN_STRIP_RE = re.compile(
    r"^(?:(?:i['’]?m|we['’]?re|i\s+am|we\s+are)\s+(?:just\s+|currently\s+|really\s+)?(?:talking|speaking|chatting|working)\s+"
    r"|(?:i|we)\s+(?:really\s+|just\s+)?(?:need|want|have)\s+to\s+(?:align\s+on\s+|clarify\s+|lock\s+in\s+|figure\s+out\s+)?"
    r"|tell\s+(?:them|him|her)\s+(?:that\s+)?"
    r"|let['’]?s\s+(?:align\s+on\s+|clarify\s+|lock\s+in\s+))",
    re.IGNORECASE,
)
_TRAILING_PLEASANTRY_RE = re.compile(
    r"(?:[.,;]\s*|\s+)(?:which\s+(?:was|is)\s+(?:nice|great|good|cool|awesome)|(?:i['’]?m\s+)?excited\s+to\s+get\s+(?:q[1-4]\s+)?kick\s*off)\b.*$",
    re.IGNORECASE,
)
_OWNERSHIP_ALIGNMENT_RE = re.compile(
    r"\b(?:lead|owner|ownership|boundary|boundaries|expectation|expectations|role|onboarding|alignment|align|deliverable|deliverables|guardrail|guardrails|churn)\b",
    re.IGNORECASE,
)


_SUBORDINATE_STARTERS_RE = re.compile(
    r"^(?:instead\s+of|rather\s+than|while|although|whereas|because|since|when|whenever|if|unless|before|after)\b[^,]+,\s*",
    re.IGNORECASE,
)


def _distill_live_turn_for_speech(clean_claim: str) -> str:
    s = _TRAILING_PLEASANTRY_RE.sub("", clean_claim).strip(" .,;:-")
    s = _LIVE_LEADIN_STRIP_RE.sub("", s).strip(" .,;:-")
    s_core = _SUBORDINATE_STARTERS_RE.sub("", s).strip(" .,;:-")
    if len(s_core.split()) >= 4:
        s = s_core
    if not s:
        return clean_claim
    words = s.split()
    first = words[0].rstrip(",:;")
    if first not in _PROPER_NOUN_STARTERS and not any(c.isupper() for c in first[1:]) and len(s) > 1 and s[0].isupper() and not s[1].isupper():
        s = s[0].lower() + s[1:]
    return s


def _preserve_initial_caps(words: List[str]) -> bool:
    first_word = words[0].rstrip(",:;")
    if first_word.lower() in ("a", "an", "the", "some", "many", "all", "most"):
        return False
    if first_word in _PROPER_NOUN_STARTERS or any(c.isupper() for c in first_word[1:]):
        return True
    if len(words) < 2:
        return False
    second_word = words[1].rstrip(",:;")
    return bool(first_word[:1].isupper() and second_word[:1].isupper() and second_word in _PROPER_NOUN_STARTERS)


def _sanitize_speakable_claim(text: str) -> str:
    cleaned = _normalize_spoken_ear(text.replace("Â·", "·").replace("Â", ""))
    cleaned = re.sub(r"^\s*[-*•]\s+", "", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = _LEADING_VERB_CLAUSE_RE.sub("", cleaned)
    cleaned = _TRAILING_PLEASANTRY_RE.sub("", cleaned)
    cleaned = _ASR_SPLIT_SOW_RE.sub(lambda m: "the NEA SOW" if "nea" in m.group(0).lower() else "SOW", cleaned)
    cleaned = _trim_trailing_conjunctions(_MARKUP_TAG_RE.sub("", cleaned).replace("?", "").strip())
    if not cleaned:
        return ""
    if _preserve_initial_caps(cleaned.split()):
        return cleaned
    return (cleaned[0].lower() + cleaned[1:]) if (len(cleaned) > 1 and cleaned[0].isupper() and not cleaned[1].isupper()) else cleaned


def _lower_first(text: str) -> str:
    return _sanitize_speakable_claim(text)


_PLACEHOLDER_SPEAKERS = frozenset({
    "unknown", "none", "operator", "participant", "speaker", "host", "voice host", "system", "user", "attendee", "guest", "caller", "webinar operator", "ai trends",
})


def _extract_safe_speaker(selected_utterance: Optional[Any]) -> str:
    if selected_utterance is None:
        return ""
    raw_val = selected_utterance.get("speaker") if hasattr(selected_utterance, "get") else getattr(selected_utterance, "speaker", "")
    raw_spk = _MARKUP_TAG_RE.sub("", str(raw_val or "").strip()).strip()
    if not raw_spk or raw_spk.lower() in _PLACEHOLDER_SPEAKERS or "operator" in raw_spk.lower() or "webinar" in raw_spk.lower():
        return ""
    return raw_spk


def _extract_topic_anchor(selected_utterance: Optional[Any]) -> str:
    if selected_utterance is None:
        return ""
    getter = selected_utterance.get if hasattr(selected_utterance, "get") else lambda k: getattr(selected_utterance, k, None)
    kws = getter("matched_keywords")
    if isinstance(kws, list) and kws:
        for raw_kw in kws:
            clean_kw = _MARKUP_TAG_RE.sub("", str(raw_kw).strip()).strip()
            kw_low = clean_kw.lower()
            if clean_kw and len(clean_kw) >= 3 and kw_low not in _ACTION_VERBS and kw_low not in STOPWORDS and kw_low not in _NON_ANCHOR_MODIFIERS:
                return f"on your {clean_kw} point, "
    return ""


def _is_different_speaker(target: str, spk: str) -> bool:
    if not target or not spk:
        return False
    if are_same_speaker_identity(target, spk):
        return False
    tl, sl = target.lower(), spk.lower()
    return tl != sl and not tl.startswith(sl) and not sl.startswith(tl)


def _frame_inquiry(verb_phrase: str, target: str, spk: str, clean_claim: str, anchor: str = "") -> str:
    if _is_different_speaker(target, spk):
        return f"{target}, regarding {spk}'s point, {verb_phrase} {clean_claim}?"
    if target:
        return f"{target}, {anchor}{verb_phrase} {clean_claim}?"
    if spk:
        return f"To {spk}'s point, {verb_phrase} {clean_claim}?"
    return f"{verb_phrase[0].upper() + verb_phrase[1:]} {clean_claim}?"


def _frame_statement(prefix_str: str, suffix_str: str, target: str, spk: str, clean_claim: str, anchor: str = "") -> str:
    if _is_different_speaker(target, spk) and prefix_str != "we should confirm whether":
        return f"{target}, regarding {spk}'s point, {prefix_str} {clean_claim}{suffix_str}"
    if target:
        return f"{target}, {anchor}{prefix_str} {clean_claim}{suffix_str}"
    if spk:
        return f"To {spk}'s point, {prefix_str} {clean_claim}{suffix_str}"
    return f"{prefix_str[0].upper() + prefix_str[1:]} {clean_claim}{suffix_str}"


_INTENT_STATEMENT_MAP: Dict[str, Tuple[str, str]] = {
    "gaps": ("we should confirm whether", "."),
    "connect": ("how does", " connect to this?"),
    "review": ("our agreed baseline is that", "."),
}


_FINITE_CLAUSE_VERB_RE = re.compile(
    r"^(?:[A-Za-z0-9_.\-]+\s+){1,7}(?:are|is|was|were|will|can|could|should|would|may|might|must|use|uses|has|have|begin|beginning|become|becoming|replace|replacing|change|changing)\b",
    re.IGNORECASE,
)


_LIVE_CLAUSE_LEADS = ("we're tracking that", "in parallel, we're also tracking that", "additionally, we're also tracking that")
_LIVE_GRAPH_CTX_LEADS = ("as we're hearing in this session,", "building on that,", "additionally,")
_LIVE_PHRASE_LEADS = ("let's align on", "in parallel, let's also align on", "additionally, let's also align on")


def _select_live_inquiry_lead(q_claim: str, is_clause: bool) -> str:
    if _OWNERSHIP_ALIGNMENT_RE.search(q_claim):
        return "what concrete ownership boundaries and deliverables should we lock in regarding"
    if is_clause:
        return "what specific guardrails or next steps should we lock in given that"
    return "what specific guardrails or next steps should we lock in on"


def _select_live_statement_lead(intent: str, effective_target: str, spk: str, is_clause: bool, idx: int) -> str:
    if not is_clause:
        return _LIVE_PHRASE_LEADS[idx]
    is_untargeted_ctx = intent == "graph-context" and not (effective_target or spk)
    return _LIVE_GRAPH_CTX_LEADS[idx] if is_untargeted_ctx else _LIVE_CLAUSE_LEADS[idx]


def _live_turn_comment(intent: str, effective_target: str, spk: str, clean_claim: str, anchor: str, line_index: int = 0) -> Optional[str]:
    starts_prep = bool(re.match(r"^(?:in|on|at|for|with|from|by|under|across|during)\b", clean_claim, re.IGNORECASE))
    is_clause = not starts_prep and bool(
        re.match(r"^(?:you|we|they|i|he|she|it|there|some|all|many|most)\b", clean_claim, re.IGNORECASE)
        or _FINITE_CLAUSE_VERB_RE.search(clean_claim)
    )
    if intent in ("respond", "review", "graph-context"):
        lead_stmt = _select_live_statement_lead(intent, effective_target, spk, is_clause, min(max(line_index, 0), 2))
        return _frame_statement(lead_stmt, ".", effective_target, spk, clean_claim, anchor=anchor)
    if intent in ("next-questions", "nbq", "challenge"):
        q_claim = clean_claim.split(". ")[0].strip(" .,;:-") if ". " in clean_claim else clean_claim
        return _frame_inquiry(_select_live_inquiry_lead(q_claim, is_clause), effective_target, spk, q_claim, anchor)
    return None


def _select_statement_lead_pair(intent: str, line_index: int, is_untargeted_ctx: bool = False) -> Tuple[str, str]:
    if intent in ("gaps", "connect"):
        return _INTENT_STATEMENT_MAP[intent]
    if is_untargeted_ctx:
        return ("to execute that shift,", ".") if line_index >= 2 else ("in practice,", ".")
    if line_index == 1:
        return ("in parallel, we're also tracking that", ".") if intent == "review" else ("in parallel, our agreed baseline is that", ".")
    if line_index >= 2:
        return ("specifically, we're also tracking that", ".")
    return _INTENT_STATEMENT_MAP.get(intent, ("we're tracking that", "."))


def _format_graph_statement_comment(
    intent: str, effective_target: str, spk: str, clean_claim: str, line_index: int
) -> str:
    untargeted_ctx = intent == "graph-context" and not (effective_target or spk)
    if untargeted_ctx and re.match(r"^(?:as|at|across|for|while|when)\s+\w+", clean_claim, re.IGNORECASE):
        return f"{clean_claim[0].upper() + clean_claim[1:]}."
    lead_pair = _select_statement_lead_pair(intent, line_index, is_untargeted_ctx=untargeted_ctx)
    return _frame_statement(lead_pair[0], lead_pair[1], effective_target, spk, clean_claim)


def _comment_for_claim(
    intent: str, target: str, claim: str, selected_utterance: Optional[Any] = None, include_anchor: bool = True, is_live_turn: bool = False, line_index: int = 0,
) -> str:
    clean_claim = _sanitize_speakable_claim(claim)
    if is_live_turn:
        clean_claim = _distill_live_turn_for_speech(clean_claim)
    spk, effective_target, anchor = (_extract_safe_speaker(selected_utterance), target, _extract_topic_anchor(selected_utterance)) if include_anchor else ("", "", "")
    if is_live_turn and (live_res := _live_turn_comment(intent, effective_target, spk, clean_claim, anchor, line_index=line_index)):
        return live_res
    if intent in ("respond", "next-questions", "nbq", "challenge") or (intent == "connect" and not effective_target):
        is_conn = intent == "connect"
        return _frame_inquiry("could we clarify", effective_target, "" if is_conn else spk, clean_claim, "" if is_conn else anchor)
    if intent == "next-actions":
        return _frame_inquiry("what is the next step for", effective_target, spk, clean_claim, anchor)
    return _format_graph_statement_comment(intent, effective_target, spk, clean_claim, line_index)


def _workstream_from_nodes(assertion: Dict[str, Any], nodes: Dict[str, Any]) -> List[str]:
    if not isinstance(nodes, dict):
        return []
    names: List[str] = []
    for nid_key in ("subject_node_id", "object_node_id"):
        nd = nodes.get(assertion.get(nid_key))
        if isinstance(nd, dict) and nd.get("node_type") == "workstream":
            names = _workstream_from_text(str(nd.get("canonical_name") or nd.get("display_name") or ""), names)
    return names


def _workstream_from_text(claim: str, existing: List[str]) -> List[str]:
    added, cl = list(existing), claim.lower()
    if "conduit" in cl and "PD Conduit" not in added:
        added.append("PD Conduit")
    if "lens" in cl and "LENS" not in added:
        added.append("LENS")
    return added


def _extract_workstream_prefix(assertion: Dict[str, Any], retriever: CopilotGraphRetriever) -> str:
    """Extracts explicit organizational provenance prefix (e.g. '[PD Conduit]') for cross-project claims."""
    if not isinstance(assertion, dict) or assertion.get("is_primary") or assertion.get("provenance_scope") == "PRIMARY":
        return ""
    ret_eng = getattr(retriever, "engagement_id", None)
    ast_eng = assertion.get("engagement_id") or assertion.get("project_scope")
    if ret_eng and ast_eng and str(ret_eng) == str(ast_eng):
        return ""
    topology = getattr(retriever, "_topology", None)
    nodes = getattr(topology, "nodes", {}) if topology else {}
    workstreams = _workstream_from_nodes(assertion, nodes)
    if not workstreams:
        return ""
    claim = str(assertion.get("claim_text") or assertion.get("object_literal") or "")
    workstreams = _workstream_from_text(claim, workstreams)
    workstreams.sort(key=lambda w: (0 if "conduit" in w.lower() else (1 if "lens" in w.lower() else 2)))
    return f"[{workstreams[0]}] "


def _build_prefixed_comment(
    intent: str, safe_target: str, claim: str, prefix: str, selected_utterance: Optional[Any], include_anchor: bool, is_live_turn: bool = False, line_index: int = 0,
) -> Tuple[str, int]:
    comment = _comment_for_claim(intent, safe_target, claim, selected_utterance=selected_utterance, include_anchor=include_anchor, is_live_turn=is_live_turn, line_index=line_index)
    if prefix:
        comment = f"{prefix}{comment}"
    return comment, len(comment.split())


def _process_assertion_candidate(
    assertion: Dict[str, Any], intent: str, safe_target: str, max_words: Optional[int],
    used: int, retriever: CopilotGraphRetriever, selected_utterance: Optional[Any] = None, line_index: int = 0,
) -> Tuple[Optional[str], int, bool, bool]:
    """Returns (comment, word_count, is_malformed, is_budget_exhausted)."""
    claim = spoken_claim(assertion, retriever)
    if not claim or "```" in claim:
        return None, 0, True, False
    prefix = _extract_workstream_prefix(assertion, retriever)
    is_live = bool(assertion.get("is_live_turn"))
    comment, count = _build_prefixed_comment(intent, safe_target, claim, prefix, selected_utterance, include_anchor=(used == 0), is_live_turn=is_live, line_index=line_index)
    if max_words is None or used + count <= max_words:
        return comment, count, False, False
    plain_comment, plain_count = _build_prefixed_comment(intent, safe_target, claim, prefix, selected_utterance, include_anchor=False, is_live_turn=is_live, line_index=line_index)
    if used + plain_count <= max_words:
        return plain_comment, plain_count, False, False
    return None, 0, False, True


def _determine_failure_reason(
    has_body: bool, has_assertions: bool, malformed_count: int, budget_exhausted_count: int,
) -> Optional[str]:
    if has_body or not has_assertions:
        return None
    if malformed_count > 0 and budget_exhausted_count == 0:
        return "MALFORMED_REPRESENTATION"
    return "WORD_BUDGET_EXHAUSTED" if budget_exhausted_count > 0 else None


def _seed_initial_gap_comments(
    intent: str, assertions: List[Dict[str, Any]], max_words: Optional[int], retriever: CopilotGraphRetriever
) -> Optional[Tuple[List[str], int]]:
    if intent != "gaps":
        return [], 0
    conflict = _conflict_comment(assertions, max_words, retriever)
    if not conflict:
        return [], 0
    if len(assertions) <= 2:
        return None
    return [conflict], len(conflict.split())


def _try_compress_budget_fallback(
    assertions: List[Dict[str, Any]], intent: str, safe_target: str, max_words: int,
    retriever: CopilotGraphRetriever, selected_utterance: Optional[Any],
) -> Optional[str]:
    from dual_pipeline.live_meeting_recorder_core.proactive_inflection_agent import compress_for_speak_gate
    cap_words = min(max_words, 28 if max_words <= 35 else 45)
    for assertion in assertions:
        claim = spoken_claim(assertion, retriever)
        if not claim or "```" in claim:
            continue
        prefix = _extract_workstream_prefix(assertion, retriever)
        raw_comment, _ = _build_prefixed_comment(intent, safe_target, claim, prefix, selected_utterance, include_anchor=False)
        compressed = compress_for_speak_gate(raw_comment, max_words=cap_words, max_steps=3)
        if compressed and len(compressed.split()) <= cap_words:
            return compressed
    return None


def render_speaker_comments(
    intent: str, target: Optional[str], assertions: List[Dict[str, Any]],
    max_words: Optional[int], retriever: CopilotGraphRetriever,
    selected_utterance: Optional[Any] = None, max_items: int = 3,
) -> SpeechRenderingResult:
    """Turn verified assertions into at most three short comments, distinguishing malformed vs budget exhaustion."""
    seeded = _seed_initial_gap_comments(intent, assertions, max_words, retriever)
    if seeded is None:
        return SpeechRenderingResult(body=_conflict_comment(assertions, max_words, retriever), rendered_count=1)
    comments, used = seeded
    malformed_count, budget_exhausted_count = 0, 0
    safe_target = " ".join((target or "").split()).replace("```", "")
    cap = max(1, int(max_items))
    for assertion in assertions:
        if len(comments) >= cap:
            break
        comment, count, is_malformed, is_exhausted = _process_assertion_candidate(
            assertion, intent, safe_target, max_words, used, retriever, selected_utterance=selected_utterance, line_index=len(comments)
        )
        malformed_count += int(is_malformed)
        budget_exhausted_count += int(is_exhausted)
        if comment and not is_malformed and not is_exhausted and comment not in comments:
            comments.append(comment)
            used += count
    if not comments and budget_exhausted_count > 0 and max_words is not None and max_words >= 30:
        if fb := _try_compress_budget_fallback(assertions, intent, safe_target, max_words, retriever, selected_utterance):
            comments.append(fb)
    body = "\n".join(comments)
    failure_reason = _determine_failure_reason(bool(body), bool(assertions), malformed_count, budget_exhausted_count)
    return SpeechRenderingResult(
        body=body, rendered_count=len(comments),
        malformed_count=malformed_count, budget_exhausted_count=budget_exhausted_count,
        failure_reason=failure_reason,
    )


def speaker_comments(
    intent: str, target: Optional[str], assertions: List[Dict[str, Any]],
    max_words: Optional[int], retriever: CopilotGraphRetriever,
    selected_utterance: Optional[Any] = None,
) -> str:
    """Turn verified assertions into at most three short comments."""
    return render_speaker_comments(intent, target, assertions, max_words, retriever, selected_utterance=selected_utterance).body


def synthesize_typed_insight_comment(
    assertions: List[Dict[str, Any]], dialogue_context: Any = None,
    target: Optional[str] = None, selected_utterance: Optional[Any] = None,
    max_words: Optional[int] = None, recent_history: Optional[List[str]] = None,
) -> SpeechRenderingResult:
    """Synthesizes the single highest-scoring typed insight (contradiction, risk, owner_gap, prior_decision)."""
    from dual_pipeline.live_meeting_recorder_core.copilot_insight import select_best_insight
    from dual_pipeline.parameters import get_copilot_speech_limits
    eff_max = get_copilot_speech_limits()["insight_max_words"] if max_words is None else max_words
    safe_target = _MARKUP_TAG_RE.sub("", " ".join((target or "").split())).strip()
    spk = _extract_safe_speaker(selected_utterance)
    insight = select_best_insight(
        assertions, dialogue_context=dialogue_context, target=safe_target or None,
        speaker=spk or None, recent_history=recent_history, max_words=eff_max,
    )
    if not insight:
        return SpeechRenderingResult(body="", rendered_count=0, failure_reason="REDUNDANT_OR_LOW_NOVELTY")
    spoken = insight.spoken_comment
    if eff_max is not None and len(spoken.split()) > eff_max:
        return SpeechRenderingResult(body="", rendered_count=0, budget_exhausted_count=1, failure_reason="WORD_BUDGET_EXHAUSTED")
    return SpeechRenderingResult(body=spoken, rendered_count=1, assertion_id=insight.assertion.get("assertion_id"))

