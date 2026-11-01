"""
dual_pipeline/live_meeting_recorder_core/copilot_speak_gate.py

Governed Speak Gate for Live Copilot (Wave 2).
Enforces the core principle: "Silence beats a weak line."
Checks:
1. Grounding: claims carry assertion_id and matching verbatim_span.
2. Status & Staleness: superseded, deprecated, or stale assertions fail closed.
3. Number Invariant: numbers in claims must be attested in assertion ground truth.
4. Negation & Polarity: detects negation flips and polarity inversion defects.
5. Ownership & Quarantines: unknown or quarantined ownerships fail closed.
6. Tier Gating (T0-T3):
   - T3: High-confidence grounded claim (speaker_conf >= 0.85). Spoken.
   - T2: Grounded claim (speaker_conf >= 0.60). Spoken.
   - T1: Question without assertion (questions need no evidence) or downgraded claim.
   - T0: Unusable / barred / ungrounded / trap / defect. Strictly abstain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .c19_firewall import has_c19_marker, is_c19_quarantined_assertion

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_speak_gate")

NEGATION_TOKENS: frozenset[str] = frozenset({
    "not", "never", "no", "neither", "nor", "prohibit", "prohibits", "prohibited",
    "bar", "bars", "barred", "prevent", "prevents", "prevented", "cannot", "cant",
    "doesn't", "doesnt", "won't", "wont", "refuse", "refuses",
})

CANNED_SCRIPTS: frozenset[str] = frozenset({
    "can we verify the technical ground truth for this topic?",
    "can we verify ground truth?",
    "verify ground truth",
})

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")

OWNERSHIP_TRAP_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(?:approve|sign\s*off|authorize)\s+.*(?:on\s+behalf\s+of|without\s+review)\b", re.IGNORECASE),
    re.compile(r"\b(?:override\s+.*lock\s+without|reassign\s+.*unilaterally)\b", re.IGNORECASE),
    re.compile(r"\b(?:unilateral\s+ownership|bypass\s+.*review)\b", re.IGNORECASE),
    re.compile(r"\bassume\s+.*without\s+handoff\b", re.IGNORECASE),
]

COMMITMENT_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(?:promise|guarantee)\s+(?:the\s+customer|that\s+we\s+will|zero\s+downtime)\b", re.IGNORECASE),
    re.compile(r"\b(?:confirm\s+that\s+we\s+will\s+provide\s+.*(?:percent\s+uptime|sla))\b", re.IGNORECASE),
    re.compile(r"\b(?:commit\s+(?:our\s+team|to\s+finishing)|agree\s+to\s+waive)\b", re.IGNORECASE),
]

SPECULATION_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(?:off\s+the\s+record|hallway\s+chat)\b", re.IGNORECASE),
    re.compile(r"\b(?:stock\s+price|undisclosed\s+valuation)\b", re.IGNORECASE),
    re.compile(r"\b(?:secretly\s+planning\s+to\s+resign|competitor\s+launch)\b", re.IGNORECASE),
    re.compile(r"\b(?:speculate\s+on\s+why|unannounced\s+products)\b", re.IGNORECASE),
    re.compile(r"\b(?:unplanned\s+budget\s+extension)\b", re.IGNORECASE),
]


class SpeakTier(str, Enum):
    T3 = "T3"  # Fully grounded, high confidence, zero defects
    T2 = "T2"  # Grounded, standard confidence
    T1 = "T1"  # Question needing no evidence, or downgraded claim
    T0 = "T0"  # Must abstain / silence


class AbstainReasonCode(str, Enum):
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"
    STALE_ASSERTION = "STALE_ASSERTION"
    SUPERSEDED_STATUS = "SUPERSEDED_STATUS"
    NUMBER_MISMATCH = "NUMBER_MISMATCH"
    NEGATION_CONFLICT = "NEGATION_CONFLICT"
    CONTRADICTION = "CONTRADICTION"
    SCOPE_QUARANTINED = "SCOPE_QUARANTINED"
    C19_CAREER_LEVERAGE = "C19_CAREER_LEVERAGE"
    LOW_SPEAKER_CONFIDENCE = "LOW_SPEAKER_CONFIDENCE"
    OWNERSHIP_TRAP = "OWNERSHIP_TRAP"
    CROSS_PROJECT_VIOLATION = "CROSS_PROJECT_VIOLATION"
    CANNED_FALLBACK_REJECTED = "CANNED_FALLBACK_REJECTED"
    WORD_BUDGET_EXCEEDED = "WORD_BUDGET_EXCEEDED"
    EMPTY_CLAIM = "EMPTY_CLAIM"


@dataclass(frozen=True)
class SpeakGateDecision:
    """Deterministic verdict emitted by the Speak Gate."""
    should_speak: bool
    tier: SpeakTier
    retained_text: str
    abstain_reason: Optional[AbstainReasonCode] = None
    assertion_id: Optional[str] = None
    verbatim_span: Optional[str] = None
    speaker_confidence: float = 1.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_speak": self.should_speak,
            "tier": self.tier.value,
            "retained_text": self.retained_text,
            "abstain_reason": self.abstain_reason.value if self.abstain_reason else None,
            "assertion_id": self.assertion_id,
            "verbatim_span": self.verbatim_span,
            "speaker_confidence": self.speaker_confidence,
            "diagnostics": self.diagnostics,
        }


def is_question_intent(text: str, intent: Optional[str] = None) -> bool:
    """Identifies whether text or intent represents an investigative inquiry."""
    if not text:
        return False
    t = text.strip().lower()
    if t.endswith("?"):
        return True
    if intent in ("next-questions", "nbq", "gaps", "connect"):
        return True
    return any(t.startswith(q) for q in ("could we clarify", "can we clarify", "what is", "who is", "which is", "how do"))


def extract_numbers(text: str) -> Set[str]:
    """Extracts normalized numeric tokens from text."""
    if not text:
        return set()
    return set(_NUMBER_RE.findall(text.lower()))


_CONTRACTIONS_NORM = (
    (re.compile(r"\bwon['’]t\b", re.IGNORECASE), "will not"),
    (re.compile(r"\bcan['’]t\b", re.IGNORECASE), "cannot"),
    (re.compile(r"\bshan['’]t\b", re.IGNORECASE), "shall not"),
    (re.compile(r"\b(\w+)n['’]t\b", re.IGNORECASE), r"\1 not"),
)


def _normalize_for_polarity(text: str) -> str:
    """Normalizes contractions and contract/item number abbreviations before polarity checks."""
    cleaned = re.sub(r"\bno\.\s*(?=[a-z0-9#])", "number ", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"\bno-(?:go|op|code|show)\b", "nogo", cleaned, flags=re.IGNORECASE)
    for pat, rep in _CONTRACTIONS_NORM:
        cleaned = pat.sub(rep, cleaned)
    return " ".join(cleaned.lower().split())


def _clause_polarity_matches(claim_words: Set[str], claim_has_neg: bool, clauses: List[str]) -> Optional[bool]:
    best_clause: Optional[str] = None
    best_overlap = 0
    for c in clauses:
        norm_c = _normalize_for_polarity(c)
        c_words = set(re.findall(r"\b\w+\b", norm_c))
        overlap = len({w for w in c_words if len(w) >= 4} & claim_words)
        if overlap >= 2 and (bool(c_words & NEGATION_TOKENS) == claim_has_neg):
            return False
        if overlap > best_overlap:
            best_overlap = overlap
            best_clause = norm_c
    if best_clause is not None and best_overlap >= 2:
        best_words = set(re.findall(r"\b\w+\b", best_clause))
        return claim_has_neg != bool(best_words & NEGATION_TOKENS)
    return None


def has_negation_flip(claim_text: str, assertion_text: str) -> bool:
    """Detects polarity inversion between claim and ground truth assertion."""
    norm_claim = _normalize_for_polarity(claim_text)
    norm_ast = _normalize_for_polarity(assertion_text)
    if not norm_claim or not norm_ast or norm_claim in norm_ast or norm_ast in norm_claim:
        return False
    claim_words = set(re.findall(r"\b\w+\b", norm_claim))
    ast_words = set(re.findall(r"\b\w+\b", norm_ast))
    claim_has_neg = bool(claim_words & NEGATION_TOKENS)
    ast_has_neg = bool(ast_words & NEGATION_TOKENS)
    clauses = [c.strip() for c in re.split(r"(?<=[.!?])\s+|;\s+", assertion_text) if c.strip()]
    if len(clauses) > 1:
        verdict = _clause_polarity_matches(claim_words, claim_has_neg, clauses)
        if verdict is not None:
            return verdict
    return claim_has_neg != ast_has_neg


def _check_syntax_and_firewall(
    text: str, assertion: Optional[Dict[str, Any]], speaker_confidence: float
) -> Optional[SpeakGateDecision]:
    if not text:
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.EMPTY_CLAIM, speaker_confidence=speaker_confidence,
        )
    if assertion is not None and not isinstance(assertion, dict):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.UNGROUNDED_CLAIM, speaker_confidence=speaker_confidence,
        )
    clean_lower = text.lower().rstrip(". ")
    if clean_lower in CANNED_SCRIPTS or "verify ground truth" in clean_lower:
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.CANNED_FALLBACK_REJECTED, speaker_confidence=speaker_confidence,
        )
    if has_c19_marker(text) or (assertion and is_c19_quarantined_assertion(assertion)):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.C19_CAREER_LEVERAGE, speaker_confidence=speaker_confidence,
        )
    if assertion:
        eng_id = str(assertion.get("engagement_id") or assertion.get("project_scope") or "")
        if eng_id.startswith("quarantine:"):
            return SpeakGateDecision(
                should_speak=False, tier=SpeakTier.T0, retained_text="",
                abstain_reason=AbstainReasonCode.SCOPE_QUARANTINED,
                assertion_id=assertion.get("assertion_id"), speaker_confidence=speaker_confidence,
            )
    return None


# Patterns for written formatting and robotic phrasing that should never be spoken aloud on a live meeting call.
WRITTEN_ESSAY_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\s*[-*•]\s+", re.MULTILINE),  # Markdown bullet lists
    re.compile(r"\b(?:first|secondly|thirdly)\b.*,\s*(?:the\s+\d+|our\s+\d+|in\s+the\s+\d+)", re.IGNORECASE),  # Lecture enumeration
    re.compile(r"\b(?:pareto\s+curve|denominator|equal-weighting|bounding\s+box)\b", re.IGNORECASE),  # Academic essay jargon
    re.compile(r"[\u2014\u2013]{1,2}", re.UNICODE),  # Em-dash / en-dash clauses
    re.compile(r"\b(?:to|regarding)\s+(?:operator|participant|speaker|unknown|host|system)'s\s+point\b", re.IGNORECASE),  # Placeholder speaker vocative
    re.compile(r"\b(?:technical_anchor|strategic_anchor|domain_anchor)\b", re.IGNORECASE),  # Raw node type leaks
    re.compile(r"\b(?:\$\d+[kKmM]?\s+nav\b|\d+%\s+assignment\s+progress\b|future\s+hire\s+#\d+)", re.IGNORECASE),  # Raw tracker dump
    re.compile(r"\block\s+in\s+on\s+(?:instead\s+of|rather\s+than|because|although|whereas)\b", re.IGNORECASE),  # Broken subordinate splice
    re.compile(r"\b(?:[A-Z0-9_/.-]{2,6}\s+){3,}[A-Z0-9_/.-]{2,6}\b"),  # Diagram box acronym dump
    re.compile(r"\b(?:has\s+Q[1-4]\s+202\d\s+Charter\s+Mission|Resource\s+Note\b|NEA\s+Planner\s+Workstream)\b|(?:…|\.\.\.)\.?\s*$", re.IGNORECASE),
]

from dual_pipeline.parameters import get_copilot_speech_limits
_MAX_SENTENCE_WORDS = get_copilot_speech_limits()["max_sentence_words"]


def _check_word_and_sentence_budget(
    text: str, max_words: int, speaker_confidence: float
) -> Optional[SpeakGateDecision]:
    words = text.split()
    if len(words) > max_words:
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.WORD_BUDGET_EXCEEDED,
            speaker_confidence=speaker_confidence,
            diagnostics={"word_count": len(words), "max_words": max_words},
        )
    is_conflict = "was described as" in text.lower() and "which status is current" in text.lower()
    max_sentence_limit = max_words if is_conflict else (max(_MAX_SENTENCE_WORDS, min(48, max_words)) if max_words > 35 else _MAX_SENTENCE_WORDS)
    for s in (s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()):
        if len(s.split()) > max_sentence_limit:
            return SpeakGateDecision(
                should_speak=False, tier=SpeakTier.T0, retained_text="",
                abstain_reason=AbstainReasonCode.WORD_BUDGET_EXCEEDED,
                speaker_confidence=speaker_confidence,
                diagnostics={"sentence_words": len(s.split()), "max_sentence_words": max_sentence_limit},
            )
    return None


def _check_budget_and_traps(
    text: str, max_words: Optional[int], assertion_id: Optional[str], speaker_confidence: float
) -> Optional[SpeakGateDecision]:
    if max_words is not None:
        budget_dec = _check_word_and_sentence_budget(text, max_words, speaker_confidence)
        if budget_dec is not None:
            return budget_dec
    if any(p.search(text) for p in WRITTEN_ESSAY_PATTERNS):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.UNGROUNDED_CLAIM,
            assertion_id=assertion_id, speaker_confidence=speaker_confidence,
            diagnostics={"reason": "WRITTEN_ESSAY_FORMAT_REJECTED"},
        )
    if re.search(r"[,;\s]+(?:and|or|but|so|because)\s*[?.]?$", text, re.IGNORECASE):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.UNGROUNDED_CLAIM,
            assertion_id=assertion_id, speaker_confidence=speaker_confidence,
            diagnostics={"reason": "DANGLING_CONJUNCTION_REJECTED"},
        )
    if any(p.search(text) for p in OWNERSHIP_TRAP_PATTERNS):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.OWNERSHIP_TRAP,
            assertion_id=assertion_id, speaker_confidence=speaker_confidence,
        )
    if any(p.search(text) for p in COMMITMENT_PATTERNS) or any(p.search(text) for p in SPECULATION_PATTERNS):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.UNGROUNDED_CLAIM,
            assertion_id=assertion_id, speaker_confidence=speaker_confidence,
        )
    return None


def _check_assertion_staleness(
    assertion: Dict[str, Any], speaker_confidence: float
) -> Optional[SpeakGateDecision]:
    canonical_status = str(assertion.get("canonical_status") or assertion.get("status") or "ACTIVE").upper()
    if canonical_status in ("SUPERSEDED", "DEPRECATED", "REVOKED", "INVALID", "INACTIVE"):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.SUPERSEDED_STATUS,
            assertion_id=assertion.get("assertion_id"),
            speaker_confidence=speaker_confidence, diagnostics={"status": canonical_status},
        )
    if assertion.get("is_stale") is True:
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.STALE_ASSERTION,
            assertion_id=assertion.get("assertion_id"), speaker_confidence=speaker_confidence,
        )
    return None


def _check_grounding_content(
    text: str, assertion: Dict[str, Any], verbatim_span: Optional[str], speaker_confidence: float
) -> Optional[SpeakGateDecision]:
    ast_id = str(assertion.get("assertion_id") or "")
    ast_text = str(assertion.get("claim_text") or assertion.get("object_literal") or "").strip()
    span = str(verbatim_span if verbatim_span is not None else (assertion.get("verbatim_span") or "")).strip()

    if span and span.lower() not in ast_text.lower() and span.lower() not in text.lower():
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.UNGROUNDED_CLAIM,
            assertion_id=ast_id, verbatim_span=span, speaker_confidence=speaker_confidence,
        )

    claim_numbers = extract_numbers(text)
    if claim_numbers:
        ground_numbers = extract_numbers(ast_text) | extract_numbers(span)
        unattested = claim_numbers - ground_numbers
        if unattested:
            return SpeakGateDecision(
                should_speak=False, tier=SpeakTier.T0, retained_text="",
                abstain_reason=AbstainReasonCode.NUMBER_MISMATCH,
                assertion_id=ast_id, speaker_confidence=speaker_confidence,
                diagnostics={"unattested_numbers": sorted(unattested)},
            )

    if has_negation_flip(text, ast_text):
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.NEGATION_CONFLICT,
            assertion_id=ast_id, speaker_confidence=speaker_confidence,
        )
    return None


def _handle_evidence_absence(
    text: str,
    assertion: Optional[Dict[str, Any]],
    is_q: bool,
    speaker_confidence: float,
) -> Optional[SpeakGateDecision]:
    if assertion:
        return None
    if is_q:
        if speaker_confidence < 0.60:
            return SpeakGateDecision(
                should_speak=False, tier=SpeakTier.T0, retained_text="",
                abstain_reason=AbstainReasonCode.LOW_SPEAKER_CONFIDENCE,
                speaker_confidence=speaker_confidence,
            )
        return SpeakGateDecision(
            should_speak=True, tier=SpeakTier.T1, retained_text=text,
            speaker_confidence=speaker_confidence,
        )
    return SpeakGateDecision(
        should_speak=False, tier=SpeakTier.T0, retained_text="",
        abstain_reason=AbstainReasonCode.UNGROUNDED_CLAIM,
        speaker_confidence=speaker_confidence,
    )


def _resolve_tier_decision(
    text: str,
    assertion: Dict[str, Any],
    verbatim_span: Optional[str],
    speaker_confidence: float,
    is_q: bool,
) -> SpeakGateDecision:
    ast_id = str(assertion.get("assertion_id") or "")
    if speaker_confidence >= 0.85:
        tier = SpeakTier.T3
    elif speaker_confidence >= 0.60:
        tier = SpeakTier.T2
    else:
        tier = SpeakTier.T1

    if tier == SpeakTier.T1 and not is_q:
        return SpeakGateDecision(
            should_speak=False, tier=SpeakTier.T0, retained_text="",
            abstain_reason=AbstainReasonCode.LOW_SPEAKER_CONFIDENCE,
            assertion_id=ast_id, speaker_confidence=speaker_confidence,
        )

    ast_text = str(assertion.get("claim_text") or assertion.get("object_literal") or "").strip()
    span = str(verbatim_span if verbatim_span is not None else (assertion.get("verbatim_span") or "")).strip()
    return SpeakGateDecision(
        should_speak=True, tier=tier, retained_text=text, assertion_id=ast_id,
        verbatim_span=span or ast_text[:40], speaker_confidence=speaker_confidence,
    )


def evaluate_speak_gate(
    candidate_text: str,
    assertion: Optional[Dict[str, Any]] = None,
    verbatim_span: Optional[str] = None,
    speaker_confidence: float = 1.0,
    intent: Optional[str] = None,
    max_words: Optional[int] = 35,
    bound_engagement: Optional[str] = None,
) -> SpeakGateDecision:
    """Evaluates whether candidate text satisfies fail-closed speak gate invariants."""
    text = (candidate_text or "").strip()
    dec = _check_syntax_and_firewall(text, assertion, speaker_confidence)
    if dec is not None:
        return dec

    ast_id = str(assertion.get("assertion_id") or "") if assertion else None
    dec = _check_budget_and_traps(text, max_words, ast_id, speaker_confidence)
    if dec is not None:
        return dec

    if assertion:
        dec = _check_assertion_staleness(assertion, speaker_confidence)
        if dec is not None:
            return dec

    is_q = is_question_intent(text, intent=intent)
    dec = _handle_evidence_absence(text, assertion, is_q, speaker_confidence)
    if dec is not None:
        return dec

    dec = _check_grounding_content(text, assertion, verbatim_span, speaker_confidence)
    if dec is not None:
        return dec

    return _resolve_tier_decision(text, assertion, verbatim_span, speaker_confidence, is_q)


_COHERENCE_STOP_AND_FRAME_WORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "into", "over", "some", "also", "more", "most", "only",
    "can", "our", "all", "are", "was", "were", "has", "had", "have", "been", "will", "would", "could", "should",
    "may", "might", "must", "not", "but", "what", "which", "who", "when", "where", "why", "how", "there", "their",
    "they", "you", "your", "we", "we're", "let's", "tracking", "agreed", "baseline", "parallel", "specifically",
    "additionally", "align", "confirm", "whether", "clarify", "point", "regarding", "next", "step", "steps",
    "nee", "apex_global", "partner_advisory", "nea", "apex", "energy", "enterprise", "hearing", "session", "practice",
    "execute", "shift", "building",
})
_ADMIN_TRIVIA_NON_SEQUITUR_RE = re.compile(
    r"\b(?:pod\s+governance|active\s+nee\s+enterprise\s+charters|is\s+not\s+the\b.*\blead|superseded\s+per\s+operator"
    r"|not\s+approved\s+delivery\s+authority|status\s+in\s+toolkit|decision\s+rights\s+remain"
    r"|organizing\s+our\s+work\s+around\s+individual\s+projects|this\s+model\s+aligns\s+our\s+teams)\b",
    re.IGNORECASE,
)
_ROBOTIC_TRACKER_FOLLOWUP_RE = re.compile(
    r"\b(?:our\s+agreed\s+baseline\s+is\s+that|we're\s+also\s+tracking\s+that)\b",
    re.IGNORECASE,
)
_NON_TECH_MODEL_RE = re.compile(r"\b(?:delivery|operating|commercial|engagement|governance)[-\s]+models?\b", re.IGNORECASE)
_REPEATABLE_STATEMENT_OPENERS = frozenset({"we're tracking that", "our agreed baseline", "let's align on", "in practice,"})


def _extract_coherence_tokens(text: str) -> Set[str]:
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", text.strip())
    cleaned = _NON_TECH_MODEL_RE.sub(" ", cleaned.lower())
    raw = {
        w for w in re.findall(r"\b[a-z0-9_]{3,}\b", cleaned)
        if w not in _COHERENCE_STOP_AND_FRAME_WORDS
    }
    expanded = set(raw)
    for w in raw:
        if w.endswith("s") and not w.endswith("ss") and len(w) >= 5:
            expanded.add(w[:-1])
        elif len(w) >= 4 and not w.endswith("s"):
            expanded.add(w + "s")
    return expanded


def _extract_line_opener(line: str) -> str:
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", line.strip()).lower()
    words = cleaned.split()[:3]
    return " ".join(words)


def _is_incoherent_followup_line(
    ln: str,
    first_line: str,
    seen_openers: Set[str],
    anchor_tokens: Set[str],
    has_live: bool,
    check_topic: bool,
) -> bool:
    opener = _extract_line_opener(ln)
    if opener in _REPEATABLE_STATEMENT_OPENERS and opener in seen_openers:
        return True
    if has_live and _ROBOTIC_TRACKER_FOLLOWUP_RE.search(ln):
        return True
    if _ADMIN_TRIVIA_NON_SEQUITUR_RE.search(ln) and not _ADMIN_TRIVIA_NON_SEQUITUR_RE.search(first_line):
        return True
    if check_topic and has_live and anchor_tokens:
        ln_tokens = _extract_coherence_tokens(ln)
        if ln_tokens and not (ln_tokens & anchor_tokens):
            return True
    return False


def verify_whole_response_coherence(
    lines: List[str],
    assertions: Optional[List[Dict[str, Any]]] = None,
    intent: Optional[str] = None,
) -> Tuple[List[str], Optional[str]]:
    """Evaluates multi-sentence response coherence, pruning repetitive openers and cross-source topical non-sequiturs."""
    clean_lines = [ln.strip() for ln in lines if ln and ln.strip()]
    if len(clean_lines) <= 1:
        return clean_lines, None

    asrts = assertions or []
    has_live = any(a.get("is_live_turn") for a in asrts)
    has_graph = any(not a.get("is_live_turn") for a in asrts)
    anchor_tokens = _extract_coherence_tokens(clean_lines[0])
    for a in asrts:
        if a.get("is_live_turn"):
            anchor_tokens.update(_extract_coherence_tokens(str(a.get("claim_text") or "")))

    retained: List[str] = [clean_lines[0]]
    seen_openers: Set[str] = {_extract_line_opener(clean_lines[0])}
    pruned_any = False
    check_topic = (has_live and has_graph) or intent in ("graph-context", "review", None)

    for ln in clean_lines[1:]:
        if _is_incoherent_followup_line(ln, clean_lines[0], seen_openers, anchor_tokens, has_live, check_topic):
            pruned_any = True
            continue
        seen_openers.add(_extract_line_opener(ln))
        retained.append(ln)

    return retained, ("INCOHERENT_MULTI_SENTENCE_RESPONSE" if pruned_any else None)
