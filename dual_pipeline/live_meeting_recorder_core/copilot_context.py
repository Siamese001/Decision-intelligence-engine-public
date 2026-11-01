"""Windowed utterance context model, request/topic parsing, and roster identity resolution."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from .copilot_roster_bridge import are_same_speaker_identity, is_live_transcript_only_directive, parse_prompt_window_and_count, score_assertion_synonyms, strip_copilot_prompt_meta


class EvidenceSourceType(str, Enum):
    """Closed enumeration of distinguishable copilot evidence sources."""
    ACOUSTIC_TRANSCRIPT = "ACOUSTIC_TRANSCRIPT"
    OPERATOR_HINT = "OPERATOR_HINT"
    GRAPH_ASSERTION = "GRAPH_ASSERTION"


@dataclass
class DistinguishableEvidence:
    """Explicitly tagged evidence item preserving provenance across pipelines."""
    source_type: EvidenceSourceType
    content: Dict[str, Any]
    relevance_score: float = 1.0
    timestamp: Optional[str] = None
    speaker: Optional[str] = None
    assertion_id: Optional[str] = None
    provenance: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type.value if hasattr(self.source_type, "value") else str(self.source_type),
            "content": self.content, "relevance_score": round(self.relevance_score, 4),
            "timestamp": self.timestamp, "speaker": self.speaker, "assertion_id": self.assertion_id, "provenance": self.provenance,
        }


# Common English nickname to canonical first name mappings
COMMON_NAME_ALIASES: Dict[str, List[str]] = {
    "mitch": ["mitchell", "mitch"], "mitchell": ["mitch", "mitchell"], "jen": ["jennifer", "jen"], "jennifer": ["jen", "jennifer"],
    "dan": ["daniel", "dan"], "daniel": ["dan", "daniel"], "dave": ["david", "dave"], "david": ["dave", "david"],
    "alex": ["alexander", "alexandra", "alex"], "alexander": ["alex", "alexander"], "mike": ["michael", "mike"], "michael": ["mike", "michael"],
    "chris": ["christopher", "chris"], "christopher": ["chris", "christopher"], "matt": ["matthew", "matt"], "matthew": ["matt", "matthew"],
    "rob": ["robert", "rob", "bob"], "bob": ["robert", "bob", "rob"], "robert": ["rob", "bob", "robert"],
    "bill": ["william", "bill", "will"], "william": ["bill", "will", "william"], "tom": ["thomas", "tom"], "thomas": ["tom", "thomas"],
    "steph": ["stephan", "stephane", "stephen", "stepfan", "stefan", "steph"], "stephan": ["steph", "stephan", "stephane", "stephen", "stepfan", "stefan"], "stephen": ["steph", "stephen", "stephan", "stepfan", "stefan"], "stepfan": ["steph", "stephan", "stephen", "stepfan", "stefan"], "stefan": ["steph", "stephan", "stephen", "stepfan", "stefan"],
    "jason": ["jason", "jay"], "jay": ["jason", "jay"], "ed": ["eduardo", "edward", "ed"], "eduardo": ["ed", "eduardo"],
    "jeff": ["jeffrey", "jeffery", "jeff"], "jeffrey": ["jeff", "jeffrey", "jeffery"],
    "kate": ["catherine", "cathryn", "katherine", "kathryn", "katie", "kate"], "cathryn": ["kate", "cathryn"],
}

STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "amd", "or", "but", "if", "so", "as", "at", "by", "for", "from", "in", "into", "of", "off", "on", "onto", "out", "over", "to", "up", "with", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "can", "could", "should", "would", "will", "about", "regarding", "concerning", "comment", "comments", "say", "said", "saying", "think", "thinks", "thought", "tell", "tells", "react", "respond", "responding", "response", "reply", "question", "questions", "questiion", "questiions", "qustion", "action", "actions", "next", "best", "based", "live", "liver", "transcript", "transcripts", "whole", "entire", "full", "recent", "active", "current", "synthesize", "review", "need", "give", "get", "use", "using", "last", "min", "mins", "minute", "minutes", "sec", "secs", "second", "seconds", "detail", "details", "strategic", "speaker", "point", "points", "drive", "discussion", "forward", "one", "two", "three", "four", "five", "redo", "prompt", "above", "what", "when", "where", "which", "who", "whom", "why", "how", "overall", "work", "working", "better", "together", "general", "generally", "feedback", "thoughts", "takeaway", "takeaways", "this", "that", "these", "those", "we", "our", "us", "good", "call", "meeting", "session", "changed", "change", "changes", "back", "only", "not", "dont", "look", "anything", "contribute", "high", "signal", "talked", "talking", "talk", "mentioned", "discussed", "discussing", "just", "now", "right", "copilot", "latest", "most", "intelligent", "intelligence", "intellectual", "novel", "insight", "insights", "repeating", "repeat", "robotic", "simplify", "spoken", "refresh", "ingest", "words", "word", "iq", "skeptical", "i", "me", "my", "they", "them", "their", "very", "too", "make", "show", "audience", "webinar", "leader", "leaders", "explain", "explains", "apply", "applies", "applied",
}


PROMPT_INJECTION_DISALLOWED_PATTERNS = (
    re.compile(r"(?i)\b(?:system:|\[inst\]|<\s*system\s*>|<<sys>>|---begin|ignore previous instructions|disregard all prior|override prompt)\b"),
)


def sanitize_copilot_input(text: Optional[str], max_len: int = 256) -> str:
    """
    Sanitizes user input against prompt injection patterns and XML tag breakout.
    Enforces allowlist characters and strips adversarial override sequences.
    """
    if not text:
        return ""
    cleaned = str(text).strip()
    # Strip prompt injection / override patterns
    for pat in PROMPT_INJECTION_DISALLOWED_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # Neutralize XML tag delimiters to prevent breakout from boundary envelopes
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")
    # Filter non-printable control characters
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    # Allowlist characters: letters, digits, spaces, standard punctuation
    cleaned = re.sub(r"[^\w\s\.,?!'\":;\-_/@#&+=()]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def encode_xml_boundary(tag: str, content: str, max_len: int = 512) -> str:
    """
    Encapsulates sanitized content within a rigid XML boundary tag.
    Prevents prompt injection breakouts and clearly separates untrusted user tokens.
    """
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", tag).lower()
    clean_body = sanitize_copilot_input(content, max_len=max_len)
    return f"<{clean_tag}>{clean_body}</{clean_tag}>"


@dataclass
class ParsedCopilotRequest:
    """Structured extraction of user prompt, intent, target speaker, and topic."""
    raw_request: Optional[str]
    inferred_intent: str
    target_speaker: Optional[str]
    topic_query: Optional[str]
    topic_keywords: List[str]
    window_seconds: Optional[float] = None
    requested_count: Optional[int] = None
    has_explicit_intent: bool = False
    targets: List[str] = field(default_factory=list)
    live_transcript_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_xml_bounded_prompt(self) -> str:
        """Encapsulates parsed request fields in strict XML boundaries for prompt isolation."""
        parts = ["<copilot_request>"]
        parts.append(f"  {encode_xml_boundary('user_intent', self.inferred_intent)}")
        if self.target_speaker:
            parts.append(f"  {encode_xml_boundary('user_target', self.target_speaker)}")
        if self.topic_query:
            parts.append(f"  {encode_xml_boundary('user_topic', self.topic_query)}")
        parts.append("</copilot_request>")
        return "\n".join(parts)


TARGET_PREFIX_PATTERNS = (
    re.compile(r"\b(?:react(?:\s+back)?|respond(?:\s+back)?|reply(?:\s+back)?|follow up|push back)\s+(?:to|on)\s+([A-Za-z]+(?:'[sS]*)?)", re.IGNORECASE),
    re.compile(r"\b(?:question|questions|questiion|questiions|comment|comments|point|points|ask|probe|challenge|summarize|summary)\s+(?:for|to|with|from|of|by)\s+([A-Za-z]+(?:'[sS]*)?)", re.IGNORECASE),
    re.compile(r"\b(?:for|with)\s+([A-Za-z]+(?:'[sS]*)?)\s+(?:question|regarding|about)\b", re.IGNORECASE),
    re.compile(r"\b(?:what did|what does|what was|what is|what could be|what might be|address|tell me about)\s+([A-Za-z]+(?:'[sS]*)?)", re.IGNORECASE),
    re.compile(r"\b(?:from|by)\s+([A-Za-z]+(?:'[sS]*)?)(?:\s+(?:on|about|regarding|from|in)\b|\s*[\.!\?]?$)", re.IGNORECASE),
    re.compile(r"\b(?:challenge|summarize)\s+([A-Za-z]+(?:'[sS]*)?)(?:'[sS]?\b|\s+(?:on|about|regarding)\b|\s*[\.!\?]?$)", re.IGNORECASE),
    re.compile(r"\b(?:backfill|replace|succeed|cover\s+for|support|assist)\s+([A-Z][a-zA-Z]+(?:'[sS]*)?)", re.IGNORECASE),
)

THIRD_PARTY_INFORMATIONAL_PATTERNS = (
    re.compile(r"\b(?:what\s+did|what\s+does|what\s+was|what\s+is|who\s+is|tell\s+me\s+about|explain|background\s+on)\b", re.IGNORECASE),
    re.compile(r"\b(?:what\s+could\s+be|what\s+might\s+be|what\s+would\s+be)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+[a-z]+\s+(?:should|could|would|can|will|might|did|does)\b", re.IGNORECASE),
    re.compile(r"\b(?:issue|perspective|position|stance|view|opinion|thoughts|take)\s+(?:of|on|with|regarding)\b", re.IGNORECASE),
)


def _is_third_party_query(req: str) -> bool:
    clean = req.strip()
    return any(p.search(clean) for p in THIRD_PARTY_INFORMATIONAL_PATTERNS)


EXCLUDED_S_NAMES = {"chris", "james", "charles", "lucas", "thomas", "marcus", "miles"}
QUESTION_PREFIXES = ("what could be", "what might be", "what was", "what is")


def _strip_colloquial_possessive(target: str, clean: str) -> str:
    low = target.lower()
    if not low.endswith("s") or low in EXCLUDED_S_NAMES:
        return target
    has_issue_pattern = bool(re.search(r"\b" + re.escape(target) + r"\s+(?:issue|problem|concern|view|opinion|role|take|question|position|status|misalignment)\b", clean, re.IGNORECASE))
    is_question_prefix = any(clean.lower().startswith(p) for p in QUESTION_PREFIXES)
    return target[:-1] if (has_issue_pattern or is_question_prefix) else target


def _match_target_from_prefixes(clean: str) -> Optional[str]:
    for pat in TARGET_PREFIX_PATTERNS:
        m = pat.search(clean)
        if not m:
            continue
        cleaned = _clean_extracted_name(m.group(1), clean)
        if cleaned:
            return cleaned
    return None


_MULTI_TARGET_RE = re.compile(
    r"\b(?:from|between|by|with|for|to|of)\s+([A-Z][a-zA-Z]+(?:'[sS]*)?)\s+(?:and|&|vs\.?|versus)\s+([A-Z][a-zA-Z]+(?:'[sS]*)?)",
    re.IGNORECASE,
)
_COMPARATIVE_INTENT_RE = re.compile(
    r"\b(?:what\s+changed|changed\s+from|since\s+last|difference\s+between|baseline\s+vs\.?|compared\s+to|shift\s+from|delta\s+between)\b",
    re.IGNORECASE,
)


_ORG_OR_SYSTEM_ACRONYMS: Set[str] = {
    "nee", "nea", "apex_trans", "apex", "edo", "sow", "sap", "ai", "it", "hr", "qa", "ui", "ux", "api", "aws", "gcp", "llm", "rag", "pdlc", "grid_os", "maximo", "partner_advisory", "apex_global", "gemini", "vanguard", "jarvis", "lens", "sherpa", "nexus", "conduit", "maxcore",
}
_NON_NAME_TARGET_WORDS: Set[str] = {
    "show", "make", "share", "present", "demonstrate", "highlight", "illustrate", "prove", "deliver", "drive",
    "audience", "webinar", "leader", "leaders", "leadership", "executive", "executives", "client", "clients",
    "team", "teams", "group", "groups", "company", "organization", "enterprise", "everyone", "everybody",
    "someone", "anybody", "anyone", "people", "folks", "panel", "panelists", "speakers", "attendees", "participants",
    "call", "meeting", "session", "discussion", "conversation", "topic", "point", "points", "question", "questions",
    "apply", "build", "create", "develop", "use", "align", "connect", "bridge", "test", "evaluate", "assess",
}


def _clean_extracted_name(raw_name: str, clean_prompt: str) -> Optional[str]:
    stripped = re.sub(r"['’][sS]?$", "", raw_name.strip()).strip()
    low = stripped.lower()
    if not stripped or low in STOPWORDS or low in _ORG_OR_SYSTEM_ACRONYMS or low in _NON_NAME_TARGET_WORDS:
        return None
    if not stripped[0].isupper():
        return None
    if len(stripped) >= 2 and stripped.isupper():
        return None
    return _strip_colloquial_possessive(stripped, clean_prompt)


def _add_unique_target(cand: Optional[str], targets: List[str]) -> None:
    if cand and cand not in targets:
        targets.append(cand)


def _extract_fallback_alias_targets(clean: str, targets: List[str]) -> None:
    for word in re.findall(r"\b[A-Za-z]+\b", clean):
        low = word.lower()
        if low in COMMON_NAME_ALIASES and low not in ("will", "can", "may"):
            _add_unique_target(word.capitalize(), targets)


def _extract_targets_from_prompt(req: str) -> List[str]:
    clean = re.sub(r"^(?:[-/]{1,2}\s*)?(?:copilot|query|prompt)\b[:\s-]*", "", req, flags=re.IGNORECASE).strip()
    targets: List[str] = []
    for m in _MULTI_TARGET_RE.finditer(clean):
        _add_unique_target(_clean_extracted_name(m.group(1), clean), targets)
        _add_unique_target(_clean_extracted_name(m.group(2), clean), targets)
    prefix_target = _match_target_from_prefixes(clean)
    if prefix_target and prefix_target not in targets:
        targets.insert(0, prefix_target)
    m_single = re.search(r"\b(?:regarding|from|by|with|to|for)\s+([A-Z][a-zA-Z]+(?:'[sS]*)?)", clean)
    if m_single:
        _add_unique_target(_clean_extracted_name(m_single.group(1), clean), targets)
    if not targets:
        _extract_fallback_alias_targets(clean, targets)
    return targets


def _extract_target_from_prompt(req: str) -> Optional[str]:
    targets = _extract_targets_from_prompt(req)
    return targets[0] if targets else None


COMMAND_STRIP_RE = re.compile(
    r"\b(?:react|respond|reply|follow up|push back|question|questions|questiion|questiions|what is|what are|who is|tell me|"
    r"what did|what does|what was|what could be|what might be|say|said|saying|think|thinks|thought|"
    r"challenge|counter|summarize|summary|address|probe|ask|clarify|verify|check|"
    r"generate|explain|themes?|recordings?|applied|apply|show|make|audience|webinar)\b",
    re.IGNORECASE,
)


def _extract_topic_from_prompt(
    req: str, target: Optional[str] = None, targets: Optional[List[str]] = None
) -> Optional[str]:
    norm_req = re.sub(r"\bnext\s*era(?:\s+energy)?\b", "Apex Global", req, flags=re.IGNORECASE)
    clean = strip_copilot_prompt_meta(norm_req)
    all_targets = list(targets or ([target] if target else []))
    m = re.search(r"\b(?:about|regarding|on|commenting on|comment on|concern(?:s|ing)?|in terms of)\s+(.+?)(?:[\.!\?]|$)", clean, re.IGNORECASE)
    if m:
        extracted = strip_copilot_prompt_meta(m.group(1))
        for t_name in all_targets:
            extracted = re.sub(r"\s*\b(?:from|by|with|for|to)\s+" + re.escape(t_name) + r"(?:'s)?\b.*$", "", extracted, flags=re.IGNORECASE).strip()
        residual_m = [w for w in re.findall(r"\b[A-Za-z0-9_\-]+\b", extracted) if w.lower() not in STOPWORDS and not w.isdigit()]
        if residual_m:
            return extracted if not COMMAND_STRIP_RE.search(extracted) else " ".join(residual_m)

    residual = clean
    for t_name in all_targets:
        residual = re.sub(r"\b" + re.escape(t_name) + r"(?:\s+[A-Z][a-zA-Z]+)?(?:['’]s)?\b", "", residual)
        residual = re.sub(r"\b" + re.escape(t_name) + r"(?:['’]s)?\b", "", residual, flags=re.IGNORECASE)
    residual = COMMAND_STRIP_RE.sub("", residual)
    residual_words = [w for w in re.findall(r"\b[A-Za-z0-9_\-]+\b", residual) if w.lower() not in STOPWORDS and not w.isdigit()]
    return " ".join(residual_words) if residual_words else None


def _infer_intent_from_prompt(req: str, target: Optional[str]) -> str:
    low = req.lower()
    if _COMPARATIVE_INTENT_RE.search(low):
        return "gaps"
    if re.search(r"\b(?:react(?:\s+back)?\s+to|respond(?:\s+back)?\s+to|reply(?:\s+back)?\s+to|address|challenge|counter|push\s+back|summarize|summary|comments?\s+(?:on|to|for|from)|next\s+(?:best\s+)?(?:\d+\s+|two\s+|three\s+)?(?:comments?|points?))\b", low):
        return "respond" if target else ("respond" if re.match(r"^(?:please\s+)?(?:respond|react|reply)(?:\s+(?:now|back|live|to\s+(?:this\s+|the\s+)?(?:live\s+)?(?:transcript|call|discussion)))?[\.!\?]?$", low.strip()) else "graph-context")
    if re.match(r"^(?:please\s+)?(?:respond|react|reply)(?:\s+(?:now|back|live))?[\.!\?]?$", low.strip()):
        return "respond"
    if re.search(r"\b(?:next\s+(?:best\s+|two\s+|three\s+|\d+\s+)*questi+ons?|best\s+questi+ons?|nbq|what\s+(?:best\s+)?questi+ons?|questi+ons?\s+(?:for|to|based|on)|speaker\s+points?|drive\s+discussion\s+forward)\b", low):
        return "next-questions"
    if re.search(r"\b(?:next\s+(?:best\s+|two\s+|three\s+|\d+\s+)*actions?|commitments|what\s+actions?)\b", low):
        return "next-actions"
    if any(k in low for k in ("connect", "multi-hop", "chain")):
        return "connect"
    if any(k in low for k in ("gaps", "conflict", "contradiction")) or ("review" in low and not re.search(r"\b(?:transcripts?|mins?|minutes?|secs?|seconds?|comments?|questi+ons?)\b", low)):
        return "gaps"
    if _is_third_party_query(req):
        return "graph-context"
    return "respond" if target else "graph-context"


def _resolve_overrides_or_prompt(
    clean_req: str, target: Optional[str], topic: Optional[str], intent: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], List[str]]:
    if not clean_req:
        return target, topic, intent, None, ([target] if target else [])
    extracted_targets = _extract_targets_from_prompt(clean_req)
    res_target = target or (extracted_targets[0] if extracted_targets else None)
    if res_target and res_target not in extracted_targets:
        extracted_targets.insert(0, res_target)
    prompt_topic = _extract_topic_from_prompt(clean_req, target=res_target, targets=extracted_targets)
    return res_target, (topic or prompt_topic), (intent or _infer_intent_from_prompt(clean_req, res_target)), prompt_topic, extracted_targets


def _is_valid_topic_token(tok: str, target_low: str, keywords: List[str]) -> bool:
    return not tok.isdigit() and tok not in STOPWORDS and tok not in COMMAND_STRIP_RE.findall(tok) and tok != target_low and tok not in keywords


def _append_valid_tokens(src: Optional[str], target_low: str, keywords: List[str]) -> None:
    for tok in re.findall(r"\b[A-Za-z0-9_\-]{2,}\b", (src or "").lower()):
        if _is_valid_topic_token(tok, target_low, keywords):
            keywords.append(tok)


def _build_topic_keywords(
    topic: str, target: Optional[str], intent: str, prompt_topic: Optional[str] = None, targets: Optional[List[str]] = None,
) -> List[str]:
    keywords: List[str] = []
    target_low = target.lower().strip() if target else ""
    _append_valid_tokens(topic, target_low, keywords)
    _append_valid_tokens(prompt_topic, target_low, keywords)
    if intent in ("graph-context", "gaps", "review", "connect") and target_low and _is_valid_topic_token(target_low, "", keywords):
        keywords.append(target_low)
    for extra_t in (targets or []):
        et_low = extra_t.lower().strip()
        if et_low and _is_valid_topic_token(et_low, target_low, keywords):
            keywords.append(et_low)
    return keywords


def parse_copilot_request(
    raw_request: Optional[str] = None, intent_override: Optional[str] = None,
    target_override: Optional[str] = None, topic_override: Optional[str] = None,
) -> ParsedCopilotRequest:
    """Parses a natural-language request string or CLI arguments into structured intent/target/topic."""
    req = (raw_request or "").strip()
    clean_req = re.sub(r"^(?:[-/]{1,2}\s*)?(?:copilot|query|prompt)\b[:\s-]*", "", req, flags=re.IGNORECASE).strip()
    win_sec, req_count, has_explicit = parse_prompt_window_and_count(clean_req)
    target, topic, intent, prompt_topic, targets = _resolve_overrides_or_prompt(
        clean_req, (target_override or "").strip() or None, (topic_override or "").strip() or None, (intent_override or "").strip() or None
    )
    final_intent = intent or "graph-context"
    final_topic = topic or ""
    if final_intent in ("graph-context", "gaps") and target and not final_topic:
        final_topic = " ".join(targets) if targets else target
    keywords = _build_topic_keywords(final_topic, target, final_intent, prompt_topic=prompt_topic, targets=targets)
    return ParsedCopilotRequest(
        raw_request=raw_request, inferred_intent=final_intent, target_speaker=target,
        topic_query=final_topic or None, topic_keywords=keywords, window_seconds=win_sec,
        requested_count=req_count, has_explicit_intent=has_explicit, targets=targets,
        live_transcript_only=is_live_transcript_only_directive(clean_req),
    )


@dataclass
class RosterResolutionResult:
    """Result of speaker resolution against attendee roster."""
    status: str  # "RESOLVED", "AMBIGUOUS", "NOT_FOUND"
    canonical_name: Optional[str] = None
    display_name: Optional[str] = None
    matched_candidates: List[Dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    details: Optional[str] = None


def _normalize_roster(roster: List[Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in roster:
        if isinstance(item, dict):
            c_name = item.get("name") or item.get("canonical_name") or item.get("display_name") or ""
            org, role = item.get("org") or item.get("organization") or "Unknown", item.get("role") or item.get("node_type") or "Participant"
        elif isinstance(item, str):
            c_name, org, role = item, "Unknown", "Participant"
        else:
            c_name, org, role = str(getattr(item, "canonical_name", None) or getattr(item, "name", None) or ""), "Unknown", "Participant"
        if c_name:
            candidates.append({"canonical_name": c_name, "display_name": c_name, "org": org, "role": role})
    return candidates


TOPIC_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "productivity": ("velocity", "throughput", "efficiency", "commits", "lines of code", "loc", "output", "metrics", "kpi", "scorecard", "measures", "outcome", "cycle time", "productive"),
    "increase": ("increased", "increasing", "growth", "higher", "gain", "gains", "improvement", "boost", "doubled", "accelerated", "faster"),
    "velocity": ("productivity", "throughput", "commits", "lines of code", "sprint", "metrics", "kpi", "scorecard", "measures", "outcome", "cycle time", "workflow", "scoring", "monthly", "automate", "automation", "scale", "time", "hours"),
    "metrics": ("velocity", "productivity", "commits", "lines of code", "kpi", "scorecard", "measures", "outcome", "measurement", "telemetry", "throughput"),
}


class RosterResolver:
    """Resolves attendee queries to canonical meeting roster identities without partial ambiguity."""

    @classmethod
    def resolve(cls, query: str, roster: List[Any]) -> RosterResolutionResult:
        clean_q = (query or "").strip()
        if not clean_q:
            return RosterResolutionResult(status="NOT_FOUND", error_code="MISSING_TARGET", details="Target speaker query is empty.")
        candidates = _normalize_roster(roster)
        if not candidates:
            return RosterResolutionResult(status="RESOLVED", canonical_name=clean_q, display_name=clean_q)
        from .copilot_turn_signals import roster_fuzzy_match
        matched, status = roster_fuzzy_match(clean_q, candidates)
        if status == "RESOLVED" and matched:
            return RosterResolutionResult(status="RESOLVED", canonical_name=matched.get("canonical_name", clean_q), display_name=matched.get("display_name", clean_q), matched_candidates=[matched])
        if status == "AMBIGUOUS":
            return RosterResolutionResult(status="AMBIGUOUS", error_code="AMBIGUOUS_SPEAKER_IDENTITY", details=f"Multiple roster candidates match '{clean_q}'.")
        return RosterResolutionResult(status="NOT_FOUND", error_code="SPEAKER_NOT_IN_ROSTER", details=f"Target speaker '{clean_q}' not found in meeting roster.")


@dataclass
class SelectedUtterance:
    """The uniquely selected, relevance-scored speaker turn and aggregated live position."""
    seq: int
    speaker: str
    text: str
    timestamp: str
    turn_sec: float
    relevance_score: float
    matched_keywords: List[str]
    speaker_summary: str = ""
    window_summary: str = ""
    confidence: float = 1.0

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class UtteranceSelectionResult:
    """Result of windowed utterance selection."""
    status: str  # "SELECTED", "NO_SPEAKER_TURNS", "NO_RELEVANT_UTTERANCE"
    selected_utterance: Optional[SelectedUtterance] = None
    window_turns: List[Dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    details: Optional[str] = None


def _filter_speaker_turns(turns: List[Dict[str, Any]], target_speaker: str) -> List[Tuple[int, Dict[str, Any]]]:
    target_low = target_speaker.strip().lower()
    from .copilot_turn_signals import roster_fuzzy_match
    speaker_turns: List[Tuple[int, Dict[str, Any]]] = []
    for idx, t in enumerate(turns):
        spk = str(t.get("speaker") or "").strip()
        if not spk:
            continue
        if spk.lower() == target_low or are_same_speaker_identity(target_speaker, spk):
            speaker_turns.append((idx, t))
        else:
            matched, status = roster_fuzzy_match(target_speaker, [{"name": spk, "canonical_name": spk, "display_name": spk}])
            if status == "RESOLVED" and matched:
                speaker_turns.append((idx, t))
    return speaker_turns

def _turn_matches_keyword(txt: str, kw: str) -> bool:
    if re.search(r"\b" + re.escape(kw) + r"\b", txt) or (
        len(kw) >= 6 and re.search(r"\b" + re.escape(kw[: max(5, len(kw) - 2)]) + r"[a-z]{1,4}\b", txt)
    ):
        return True
    return any(re.search(r"\b" + re.escape(syn) + r"\b", txt) for syn in TOPIC_SYNONYMS.get(kw, ()))

_ACT_SALIENCE_WEIGHTS: Dict[str, float] = {
    "decision_point": 1.35, "objection": 1.30, "commitment": 1.25, "question_to_operator": 1.20, "topic_shift": 1.05, "status_update": 1.00,
}

def _score_turns_by_topic(speaker_turns: List[Tuple[int, Dict[str, Any]]], topic_keywords: List[str]) -> List[Tuple[float, List[str], int, Dict[str, Any]]]:
    scored: List[Tuple[float, List[str], int, Dict[str, Any]]] = []
    kw_lows = [k.lower() for k in topic_keywords if len(k) >= 2]
    latest_sec = max((_safe_float_sec(t.get("turn_sec"), idx * 3.0) for idx, t in speaker_turns), default=0.0)
    for idx, t in speaker_turns:
        matched = [k for k in kw_lows if _turn_matches_keyword(str(t.get("text") or "").lower(), k)]
        if matched:
            recency_mult = 0.70 + 0.30 * math.exp(-max(0.0, latest_sec - _safe_float_sec(t.get("turn_sec"), idx * 3.0)) / 300.0)
            act_mult = _ACT_SALIENCE_WEIGHTS.get(str(t.get("act") or "status_update"), 1.0)
            scored.append((round(float(len(matched)) * recency_mult * act_mult, 4), matched, idx, t))
    scored.sort(key=lambda x: (x[0], x[2]), reverse=True)
    return scored


ACOUSTIC_SPEAKER_PATTERN = re.compile(
    r"^(?:Voice(?:[\s_\-]+[A-Z\d]+|\s+Host)?|Host|Speaker(?:[\s_\-]+[A-Z\d]+)?|SPEAKER_\d+|Unknown|Unassigned|Unattributed)$", re.IGNORECASE,
)

def _is_acoustic_placeholder_speaker(speaker: str) -> bool:
    return True if not speaker.strip() else bool(ACOUSTIC_SPEAKER_PATTERN.match(speaker.strip()))

def _collect_acoustic_fallback_turns(turns: List[Dict[str, Any]]) -> List[Tuple[int, Dict[str, Any]]]:
    return [(idx, t) for idx, t in enumerate(turns) if _is_acoustic_placeholder_speaker(str(t.get("speaker") or "").strip())]

def _get_candidate_speaker_turns(
    turns: List[Dict[str, Any]], target_speaker: str, strict_target: bool = False
) -> Tuple[List[Tuple[int, Dict[str, Any]]], bool]:
    speaker_turns = _filter_speaker_turns(turns, target_speaker)
    if speaker_turns or strict_target:
        return speaker_turns, False
    acoustic_turns = _collect_acoustic_fallback_turns(turns)
    return (acoustic_turns, True) if acoustic_turns else ([], False)

def _safe_float_sec(val: Any, default: float) -> float:
    try:
        return default if val is None else float(val)
    except (ValueError, TypeError):
        return default


_LOW_SIGNAL_UTTERANCE_RE = re.compile(
    r"^(?:to\s+make\s+it\s+right|i['’]?m\s+totally\s+on\s+board|yeah\s+for\s+sure|sounds\s+good|makes\s+sense|hundred\s+percent|totally\s+agree|thanks?\s+(?:so\s+much\s+)?for\s+(?:taking\s+)?(?:your|the)\s+time|thank\s+you\s+so\s+much|appreciate\s+your\s+time|(?:all\s+right[,.\s]+)?talk\s+to\s+you\s+soon|have\s+a\s+(?:good|great)\s+(?:one|day|weekend)|see\s+you\s+(?:soon|later)|i\s+don['’]?t\s+know\s+if\s+i\s+have\s+anything|(?:cool[,.\s]+)?(?:i\s+mean[,.\s]+)?i['’]?m\s+so\s+excited|(?:working\s+on\s+(?:stuff|things)\s+here(?:\s+in\s+the)?\s*)?(?:i['’]?m\s+)?excited\s+to\s+get\s+(?:q[1-4]\s+)?kick\s*off|which\s+(?:was|is)\s+(?:nice|great|good|cool|awesome))\b|\b(?:no,\s*no,\s*no|okay,\s*forget\s+it|forget\s+it\.\s*just|and\s+then\s+proactively\s*$|and\s+like\s+we\s+have\s+all\s+this\s+intel|(?:cool[,.\s]+)?(?:i\s+mean[,.\s]+)?i['’]?m\s+so\s+excited|here['’]?s\s+my\s+understanding[,.\s]+right|excited\s+to\s+get\s+q[1-4]\s+kick\s*off)\b",
    re.IGNORECASE,
)
def _pick_latest_substantive_turn(candidate_turns: List[Tuple[int, Dict[str, Any]]]) -> Tuple[int, Dict[str, Any]]:
    from .copilot_utterance_resolver import _clean_live_turn_claim, _has_substantive_content, score_utterance_domain_substance
    valid = [
        (score_utterance_domain_substance(str(c_turn.get("text") or "")), c_idx, c_turn)
        for c_idx, c_turn in candidate_turns
        if len(_clean_live_turn_claim(str(c_turn.get("text") or "")).split()) >= 3
        and not _LOW_SIGNAL_UTTERANCE_RE.search(str(c_turn.get("text") or "").strip())
        and _has_substantive_content(_clean_live_turn_claim(str(c_turn.get("text") or "")))
    ]
    if valid:
        recent = [item for item in valid[-6:] if item[0] >= 4.5]
        if recent:
            recent.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return recent[0][1], recent[0][2]
        valid.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return valid[0][1], valid[0][2]
    return candidate_turns[-1]


class UtteranceSelector:
    """Selects the most relevant utterance for a speaker and topic within a time window."""

    @classmethod
    def select(
        cls, turns: List[Dict[str, Any]], target_speaker: str, topic_keywords: List[str],
        window_sec: float = 45.0, strict_target: bool = False,
    ) -> UtteranceSelectionResult:
        if not turns:
            return UtteranceSelectionResult(status="NO_SPEAKER_TURNS", error_code="NO_LIVE_CONTEXT", details="No meeting turns available for utterance selection.")
        candidate_turns, _ = _get_candidate_speaker_turns(turns, target_speaker, strict_target=strict_target)
        if not candidate_turns:
            err_code = "ZERO_ATTRIBUTED_TURNS_FOR_TARGET" if strict_target else "SPEAKER_HAS_NO_TURNS"
            return UtteranceSelectionResult(status="NO_SPEAKER_TURNS", error_code=err_code, details=f"ZERO_ATTRIBUTED_TURNS_FOR_TARGET: Speaker '{target_speaker}' has no spoken turns in active transcript.")
        if not topic_keywords:
            best_idx, best_turn = _pick_latest_substantive_turn(candidate_turns)
            best_score, matched_kws = 1.0, []
        else:
            scored = _score_turns_by_topic(candidate_turns, topic_keywords)
            if not scored:
                return UtteranceSelectionResult(status="NO_RELEVANT_UTTERANCE", error_code="ZERO_RELEVANT_UTTERANCES", details=f"Speaker '{target_speaker}' has {len(candidate_turns)} turn(s), but none address topic keywords {topic_keywords}.")
            best_score, matched_kws, best_idx, best_turn = scored[0]
        sel = SelectedUtterance(
            seq=int(best_turn.get("seq") or best_idx + 1), speaker=str(best_turn.get("speaker") or target_speaker),
            text=str(best_turn.get("text") or ""), timestamp=str(best_turn.get("timestamp") or "00:00:00"),
            turn_sec=_safe_float_sec(best_turn.get("turn_sec"), best_idx * 3.0), relevance_score=best_score, matched_keywords=matched_kws,
        )
        return UtteranceSelectionResult(status="SELECTED", selected_utterance=sel, window_turns=cls._extract_window(turns, best_idx, sel.turn_sec, window_sec))

    @staticmethod
    def _extract_window(turns: List[Dict[str, Any]], center_idx: int, center_sec: float, window_sec: float) -> List[Dict[str, Any]]:
        lo, hi = center_sec - window_sec, center_sec + window_sec
        res = [t for idx, t in enumerate(turns) if lo <= _safe_float_sec(t.get("turn_sec"), idx * 3.0) <= hi]
        return res if res else [turns[center_idx]]


def _score_keyword_hit(kw: str, comb: str) -> float:
    if re.search(r"\b" + re.escape(kw) + r"\b", comb):
        return 2.0
    if re.search(r"\b" + re.escape(kw), comb):
        return 1.0
    if any(re.search(r"\b" + re.escape(syn) + r"\b", comb) for syn in TOPIC_SYNONYMS.get(kw, ())):
        return 1.5
    return 0.0


_INTERNAL_META_NOTE_RE = re.compile(
    r"\b(?:superseded\s+per\s+operator\s+ratification|roster\s*&\s*role\s+correction|status\s+in\s+toolkit:|not\s+a\s+statement\s+of\s+current\s+nee\s+policy|after\s+merging\s+the\s+duplicate"
    r"|formally\s+left\s+partner_advisory\s+and\s+offboarded|decision\s+rights\s+remain\s+(?:open|unvalidated)|not\s+approved\s+delivery\s+authority|follow-up\s+dependency\s+for\s+shreyak"
    r"|organizing\s+our\s+work\s+around\s+individual\s+projects|this\s+model\s+aligns\s+our\s+teams|has\s+q[1-4]\s+202\d\s+charter\s+mission|delivers\s+q[1-4]\s+202\d\s+workstream|resource\s+note|nea\s+planner\s+workstream"
    r"|belongs\s+to\s+program|submitted\s+workbook\s+source\s+row|confirmed\s+horizontal\s+layer|confirmed\s+(?:nee\s+)?routing\s+backbone|pod-charter\s+row\s+list|reports\s+to|has\s+role\s+in|referenced\s+in\s+source\s+document|affiliated\s+with|estimated\s+cost|related\s+to\s+maximo|weakly\s+reflected\s+in\s+v\d+|linkedin\s+screenshot|is\s+distinct\s+from|the\s+parent\s+company)\b"
    r"|\brole:\s+[a-z]|:\s+organization\s+apex_global\b|apex_global\s+energy\s+\(nea\s*/\s*(?:apex|pgd)\)|\bi['’]?ve\s+been\s+at\s+apex_global\b|\(technical_anchor\)\s+in\b|\s->\s|^enterprise\s+ai\s+enablement\s+delivers\b|^\s*\**[a-z]+(?:\s+[a-z]+)?:\**\s+(?:let['’]s\s+see|i\s+think)|(?:…|\.\.\.)\.?\s*$",
    re.IGNORECASE,
)
_ORG_EXPANSION_KWS = frozenset({"nee", "apex_global", "apex_global energy", "nea", "apex_global analytics", "nee.ai", "org_nee", "audience_next_era_analytics_ge301", "workstream_nee_ai_to_gemini_transition", "pattern_gemini_no_code_first", "nee.ai to gemini transition", "apex_global analytics (nea) ge 301 track", "gemini no-code-first pattern"})


def _score_single_assertion(assertion: Dict[str, Any], kw_lows: List[str]) -> float:
    claim = str(assertion.get("claim_text") or assertion.get("object_literal") or "").lower()
    if re.search(r"\b(?:is an identified stakeholder|is a reported speaker in)\b", claim) or claim.startswith(("candidate ai-sdlc control", "candidate gemini/", "candidate enterprise-retrieval")):
        return 0.0
    if _INTERNAL_META_NOTE_RE.search(claim) or re.match(r"^(?:-\s*)?\*\*(?:apex_global\s+energy|partner_advisory|attendees|participants|roster)\b", claim.strip()):
        return 0.0
    comb = f"{str(assertion.get('subject_node_id') or '').lower()} {str(assertion.get('predicate') or '').lower()} {claim} {str(assertion.get('object_node_id') or '').lower()}"
    nee_kws = [k for k in kw_lows if k in _ORG_EXPANSION_KWS]
    other_kws = [k for k in kw_lows if k not in _ORG_EXPANSION_KWS]
    nee_score = max((_score_keyword_hit(k, comb) for k in nee_kws), default=0.0) + (0.8 if "nea" in nee_kws and _score_keyword_hit("nea", comb) > 0 else 0.0)
    score = nee_score + sum(_score_keyword_hit(kw, comb) for kw in other_kws) + score_assertion_synonyms(comb, kw_lows)
    if score <= 0.0:
        return 0.0
    if assertion.get("is_primary") or assertion.get("provenance_scope") == "PRIMARY":
        score += 1.5
    if assertion.get("is_priority"):
        score += 0.5
    return score * float(assertion.get("_temporal_decay_multiplier", 1.0))


def _prepare_topic_keywords(topic_keywords: List[str]) -> List[str]:
    kw_lows = list(dict.fromkeys(k.lower() for k in topic_keywords if len(k) >= 2))
    if "tech" in kw_lows and "owner" in kw_lows and "tech owner" not in kw_lows:
        kw_lows.append("tech owner")
    if "technical" in kw_lows and "owner" in kw_lows and "technical owner" not in kw_lows:
        kw_lows.append("technical owner")
    if any(k in kw_lows for k in ("apex_global", "nee", "nea")):
        kw_lows.extend(a for a in ("nee", "apex_global") if a not in kw_lows)
    return kw_lows


def _extract_utterance_domain_tokens(utterance_text: Optional[str]) -> List[str]:
    raw_words = [w for w in re.findall(r"\b[a-z]{4,}\b", (utterance_text or "").lower()) if w not in STOPWORDS]
    expanded: List[str] = []
    for w in raw_words:
        stem = w[:-1] if (w.endswith("s") and not w.endswith("ss") and len(w) >= 5) else w
        if stem not in expanded:
            expanded.append(stem)
    return expanded


def filter_assertions_for_utterance_topic(
    assertions: List[Dict[str, Any]], topic_keywords: List[str], utterance_text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filters and ranks graph assertions based on topic and live utterance relevance."""
    if not assertions or (not topic_keywords and not utterance_text):
        return assertions
    kw_lows = _prepare_topic_keywords(topic_keywords)
    utt_tokens = _extract_utterance_domain_tokens(utterance_text)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for a in assertions:
        base_terms = kw_lows if kw_lows else utt_tokens
        score = _score_single_assertion(a, base_terms)
        if score > 0.0 and utt_tokens and kw_lows:
            score += 0.85 * _score_single_assertion(a, utt_tokens)
        if score > 0.0:
            scored.append((score, a))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen: Set[str] = set()
    return [a for _, a in scored if not (k := str(a.get("claim_text") or a.get("object_literal") or a.get("assertion_id") or "").strip().lower()) or (k not in seen and not seen.add(k))]

