"""
dual_pipeline/live_meeting_recorder_core/copilot_turn_signals.py

Wave 3: Conversation understanding engine for Live Executive Copilot.
Provides:
1. Deterministic ask detection without '?' (inversion, Wh-starters, imperative prompts, indirect asks).
2. Dialogue act classification and backchannel identification.
3. Roster fuzzy matching replacing unsafe substring search (prevents 'Al' matching 'Alan').
4. Addressee identification (vocative syntax and roster alignment).
5. AskLedger tracking open dialogue asks and closing answered asks upon speaker response.
6. Rolling topic and digest extraction across confirmed dialogue turns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple


class AskKind(str, Enum):
    CLARIFICATION = "clarification"
    DECISION = "decision"
    CONFIRMATION = "confirmation"
    ACTION = "action"
    OPEN = "open"


BACKCHANNEL_PATTERNS = {
    "yeah", "yep", "yes", "sure", "okay", "ok", "got it", "right", "mhm",
    "uh-huh", "makes sense", "cool", "understood", "totally", "no problem",
    "sounds good", "alright", "all right", "aye", "definitely", "exactly",
}

IMPERATIVE_ASK_PREFIXES = (
    "tell me if", "tell us if", "tell me about", "tell us about", "explain how", "explain why", "explain what",
    "walk me through", "walk us through", "let me know if", "let us know if",
    "can someone clarify", "could someone clarify", "wondering if", "wondering how",
    "curious about", "curious if", "curious whether", "need to know if",
    "need to know whether", "i'd like to understand", "we'd like to understand",
    "let's check whether", "let's check if", "can we check", "could we check",
)

INVERSION_AUX_STARTERS = (
    "can we", "could we", "should we", "would we", "shall we", "will we",
    "can you", "could you", "would you", "should you", "will you",
    "can someone", "could someone", "would someone", "should someone",
    "do we", "did we", "does it", "do they", "is there", "are there",
    "is it", "are we", "was there", "were there", "have we", "has anyone",
)

WH_STARTERS = ("what", "where", "when", "why", "who", "which", "how")


def is_backchannel(text: str) -> bool:
    """Detects brief acknowledgment or backchannel utterances."""
    cleaned = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
    if not cleaned:
        return False
    words = cleaned.split()
    if len(words) <= 3 and cleaned in BACKCHANNEL_PATTERNS:
        return True
    return False


def _classify_ask_kind(cleaned_low: str) -> AskKind:
    if any(k in cleaned_low for k in ("clarify", "explain", "walk through", "elaborate", "what do you mean", "mean by", "verify", "check", "tell me", "tell us")):
        return AskKind.CLARIFICATION
    if any(k in cleaned_low for k in ("decide", "decision", "go with", "which option", "approve", "vote", "pick", "choose")):
        return AskKind.DECISION
    if any(k in cleaned_low for k in ("confirm", "is that right", "is it true", "correct?", "do we agree", "sound right")):
        return AskKind.CONFIRMATION
    if any(k in cleaned_low for k in ("who will", "can you take", "who owns", "assign", "next step", "action item", "take the lead")):
        return AskKind.ACTION
    return AskKind.OPEN


def detect_ask_signals(text: str) -> Tuple[bool, Optional[str], bool]:
    """
    Detects if an utterance is an ask (even without '?') or a backchannel.
    Returns: (is_ask, ask_kind_str, is_backchannel).
    """
    if is_backchannel(text):
        return False, None, True

    raw = (text or "").strip()
    if not raw:
        return False, None, False

    low = raw.lower()
    has_qmark = "?" in raw

    is_imperative = any(low.startswith(p) or f", {p}" in low for p in IMPERATIVE_ASK_PREFIXES)
    is_inversion = any(low.startswith(s) for s in INVERSION_AUX_STARTERS)
    is_wh = any(low.startswith(w + " ") for w in WH_STARTERS)

    matched_text = low
    if not (is_imperative or is_inversion or is_wh):
        sub_low = re.sub(r"^(?:hey\s+|hi\s+)?[a-z]+[\s,:\-]+", "", low).strip()
        if sub_low and (
            any(sub_low.startswith(p) for p in IMPERATIVE_ASK_PREFIXES)
            or any(sub_low.startswith(s) for s in INVERSION_AUX_STARTERS)
            or any(sub_low.startswith(w + " ") for w in WH_STARTERS)
        ):
            matched_text = sub_low
            is_imperative = True

    if has_qmark or is_imperative or is_inversion or is_wh:
        kind = _classify_ask_kind(matched_text)
        return True, kind.value, False

    return False, None, False


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculates edit distance between two normalized tokens."""
    if s1 == s2:
        return 0
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


COMMON_NAME_ALIASES: Dict[str, Set[str]] = {
    "mitch": {"mitchell", "mitch"}, "mitchell": {"mitch", "mitchell"},
    "jen": {"jennifer", "jen"}, "jennifer": {"jen", "jennifer"},
    "dan": {"daniel", "dan"}, "daniel": {"dan", "daniel"},
    "dave": {"david", "dave"}, "david": {"dave", "david"},
    "alex": {"alexander", "alexandra", "alex"}, "alexander": {"alex", "alexander"},
    "mike": {"michael", "mike"}, "michael": {"mike", "michael"},
    "chris": {"christopher", "chris"}, "christopher": {"chris", "christopher"},
    "matt": {"matthew", "matt"}, "matthew": {"matt", "matthew"},
    "rob": {"robert", "rob", "bob"}, "bob": {"robert", "bob", "rob"}, "robert": {"rob", "bob", "robert"},
    "bill": {"william", "bill", "will"}, "william": {"bill", "will", "william"},
    "tom": {"thomas", "tom"}, "thomas": {"tom", "thomas"},
    "steph": {"stephan", "stephane", "stephen", "steph"},
    "stephan": {"steph", "stephan", "stephane"}, "stephen": {"steph", "stephen", "stephan"},
    "jason": {"jason", "jay"}, "jay": {"jason", "jay"},
    "ed": {"eduardo", "edward", "ed"}, "eduardo": {"ed", "eduardo", "edward"}, "edward": {"ed", "eduardo", "edward"},
    "jeff": {"jeff", "jeffrey", "jeffery"}, "jeffrey": {"jeff", "jeffrey", "jeffery"}, "jeffery": {"jeff", "jeffrey", "jeffery"},
    "kate": {"kate", "katie", "catherine", "cathryn", "katherine", "kathryn"},
    "cathryn": {"kate", "katie", "catherine", "cathryn", "katherine", "kathryn"},
    "sklyer": {"skyler", "sklyer"}, "skyler": {"skyler", "sklyer"},
}


def _extract_roster_names(c: Dict[str, Any]) -> Tuple[str, str, str]:
    c_full = str(c.get("canonical_name") or c.get("name") or c.get("display_name") or "").strip()
    c_first = c_full.split()[0].lower() if c_full else ""
    return c_full, c_full.lower(), c_first


def _eval_candidate_matches(matches: List[Dict[str, Any]]) -> Optional[Tuple[Optional[Dict[str, Any]], str]]:
    if len(matches) == 1:
        return matches[0], "RESOLVED"
    if len(matches) > 1:
        from .copilot_roster_bridge import are_same_speaker_identity
        first_name = _extract_roster_names(matches[0])[0]
        if first_name and all(are_same_speaker_identity(first_name, _extract_roster_names(m)[0]) for m in matches[1:]):
            return matches[0], "RESOLVED"
        return None, "AMBIGUOUS"
    return None


def _is_fuzzy_typo(s1: str, s2: str) -> bool:
    if abs(len(s1) - len(s2)) > 1:
        return False
    if _levenshtein_distance(s1, s2) <= 1:
        return True
    if len(s1) == len(s2) and len(s1) >= 4:
        diffs = [i for i, (c1, c2) in enumerate(zip(s1, s2)) if c1 != c2]
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            if s1[diffs[0]] == s2[diffs[1]] and s1[diffs[1]] == s2[diffs[0]]:
                return True
    return False


def _find_fuzzy_matches(q_first: str, roster_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(q_first) < 4:
        return []
    return [
        c for c in roster_candidates
        if len(_extract_roster_names(c)[2]) >= 4
        and _is_fuzzy_typo(q_first, _extract_roster_names(c)[2])
    ]


def _candidate_matches_multi_token(q_tokens: List[str], c: Dict[str, Any]) -> bool:
    from .copilot_roster_bridge import are_same_speaker_identity

    c_full, c_low, c_first = _extract_roster_names(c)
    c_tokens = c_low.split()
    if len(c_tokens) < 2:
        return False
    first_ok = (
        c_first == q_tokens[0]
        or are_same_speaker_identity(q_tokens[0], c_first)
        or (len(q_tokens[0]) >= 4 and len(c_first) >= 4 and _is_fuzzy_typo(q_tokens[0], c_first))
    )
    last_ok = c_tokens[-1] == q_tokens[-1] or (len(q_tokens[-1]) >= 4 and len(c_tokens[-1]) >= 4 and _is_fuzzy_typo(q_tokens[-1], c_tokens[-1]))
    return first_ok and last_ok


def roster_fuzzy_match(
    query: str,
    roster_candidates: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Resolves query to a roster candidate without partial substring leakage.
    Strictly forbids 'Al' matching 'Alan' or arbitrary substring containment.
    Returns: (matched_candidate_dict, status_code).
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return None, "EMPTY_QUERY"
    q_low = clean_q.lower()
    q_tokens = q_low.split()
    q_first = q_tokens[0] if q_tokens else ""

    # 1. Exact full name match
    res = _eval_candidate_matches([c for c in roster_candidates if _extract_roster_names(c)[1] == q_low])
    if res is not None:
        return res

    # 1a. Multi-token query: disambiguate by first + last name (with alias and typo tolerance)
    if len(q_tokens) >= 2:
        res = _eval_candidate_matches([c for c in roster_candidates if _candidate_matches_multi_token(q_tokens, c)])
        if res is not None:
            return res
        return None, "NOT_FOUND"

    # 1b. Canonical nickname + middle-name identity equivalence (e.g. "Ed More" <-> "Eduardo Juan More")
    from .copilot_roster_bridge import are_same_speaker_identity
    res = _eval_candidate_matches([c for c in roster_candidates if are_same_speaker_identity(clean_q, _extract_roster_names(c)[0])])
    if res is not None:
        return res

    # 2. Exact first name match (only if unambiguous)
    res = _eval_candidate_matches([c for c in roster_candidates if _extract_roster_names(c)[2] == q_first])
    if res is not None:
        return res

    # 3. Known nickname/alias match (must be exact token match in alias pool)
    alias_pool = COMMON_NAME_ALIASES.get(q_first, set())
    if alias_pool:
        res = _eval_candidate_matches([c for c in roster_candidates if _extract_roster_names(c)[2] in alias_pool])
        if res is not None:
            return res

    # 4. High-confidence typo tolerance (Levenshtein distance = 1, len >= 4 only)
    res = _eval_candidate_matches(_find_fuzzy_matches(q_first, roster_candidates))
    if res is not None:
        return res

    return None, "NOT_FOUND"


def detect_addressee(text: str, roster: List[Dict[str, Any]]) -> Optional[str]:
    """Identifies the addressee of an utterance if addressed directly to a roster participant."""
    cleaned = (text or "").strip()
    if not cleaned or not roster:
        return None

    # Check opening vocative: "Alan, ...", "Hey Alan, ...", "Sarah - ..." or unpunctuated "Alan tell us..."
    opening = re.match(r"^(?:hey\s+|hi\s+)?([A-Za-z]+)(?:,\s*|\s*-\s*|\s*:\s*|\s+)", cleaned, re.IGNORECASE)
    if opening:
        token = opening.group(1)
        matched, status = roster_fuzzy_match(token, roster)
        if status == "RESOLVED" and matched:
            return str(matched.get("canonical_name") or matched.get("name") or token)

    # Check closing vocative: "..., what do you think, Alan?"
    closing = re.search(r",\s*([A-Za-z]+)[\?!.]*$", cleaned, re.IGNORECASE)
    if closing:
        token = closing.group(1)
        matched, status = roster_fuzzy_match(token, roster)
        if status == "RESOLVED" and matched:
            return str(matched.get("canonical_name") or matched.get("name") or token)

    return None


@dataclass
class AskRecord:
    """Durable record of an identified question or request in the dialogue."""
    ask_id: str
    turn_seq: int
    speaker: str
    addressee: Optional[str]
    text: str
    ask_kind: str
    created_sec: float
    status: str = "OPEN"  # "OPEN", "ANSWERED", "DROPPED"
    answered_by: Optional[str] = None
    answer_turn_seq: Optional[int] = None
    answer_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_ASK_OVERLAP_STOPWORDS: frozenset[str] = frozenset({
    "that", "this", "with", "from", "have", "what", "when", "where", "which", "while",
    "would", "could", "should", "about", "there", "their", "they", "them", "then",
    "than", "some", "more", "most", "other", "into", "over", "also", "only", "just",
    "like", "think", "know", "make", "need", "want", "sure", "well", "were", "been",
})


def _check_turn_answers_ask(
    ask: AskRecord,
    speaker: str,
    text: str,
    is_bc: bool,
) -> bool:
    if is_bc:
        return False
    if speaker.lower() == ask.speaker.lower():
        return False

    low = text.lower()
    # 1. If direct addressee replies with substantial response
    if ask.addressee and ask.addressee.lower() in speaker.lower():
        if len(text.split()) >= 3:
            return True

    # 2. Explicit question-answering markers
    explicit_cues = ("to answer your question", "regarding your question", "as for that question", "answering your")
    if any(cue in low for cue in explicit_cues):
        return True

    # 3. Entity / keyword intersection between ask and response (excluding generic stopwords)
    ask_tokens = {w for w in re.findall(r"\b\w{4,}\b", ask.text.lower()) if w not in _ASK_OVERLAP_STOPWORDS}
    resp_tokens = {w for w in re.findall(r"\b\w{4,}\b", low) if w not in _ASK_OVERLAP_STOPWORDS}
    overlap = ask_tokens & resp_tokens
    if len(overlap) >= 2:
        return True

    # 4. Strong conversational answering prefix with at least 1 topical overlap
    cues = ("yes, ", "no, ", "that is correct", "we already ", "i confirmed", "the answer is")
    if any(low.startswith(cue) for cue in cues) and len(overlap) >= 1:
        return True

    return False


class AskLedger:
    """Tracks active conversational asks and marks them answered upon resolution."""

    def __init__(self) -> None:
        self._asks: List[AskRecord] = []
        self._counter: int = 0

    def register_ask(
        self,
        turn_seq: int,
        speaker: str,
        text: str,
        addressee: Optional[str],
        ask_kind: str,
        created_sec: float,
    ) -> AskRecord:
        self._counter += 1
        record = AskRecord(
            ask_id=f"ask_{turn_seq}_{self._counter}",
            turn_seq=turn_seq,
            speaker=speaker,
            addressee=addressee,
            text=text,
            ask_kind=ask_kind,
            created_sec=created_sec,
            status="OPEN",
        )
        self._asks.append(record)
        return record

    def process_turn(
        self,
        turn_seq: int,
        speaker: str,
        text: str,
        is_bc: bool = False,
    ) -> List[AskRecord]:
        """Evaluates whether this dialogue turn answers any open ask."""
        resolved: List[AskRecord] = []
        for ask in self._asks:
            if ask.status == "OPEN" and _check_turn_answers_ask(ask, speaker, text, is_bc):
                ask.status = "ANSWERED"
                ask.answered_by = speaker
                ask.answer_turn_seq = turn_seq
                ask.answer_text = text
                resolved.append(ask)
        return resolved

    def open_asks(self) -> List[AskRecord]:
        return [a for a in self._asks if a.status == "OPEN"]

    def all_asks(self) -> List[AskRecord]:
        return list(self._asks)


def generate_rolling_digest(
    active_topic: str,
    open_asks: List[AskRecord],
    salient_entities: List[str],
    last_act: str,
) -> str:
    """Synthesizes a rolling topic digest for situational awareness."""
    parts = []
    if active_topic:
        parts.append(f"Active Topic: {active_topic}")
    if open_asks:
        parts.append(f"Open Asks ({len(open_asks)}): {open_asks[-1].speaker}: {open_asks[-1].text}")
    if salient_entities:
        parts.append(f"Salient Entities: {', '.join(salient_entities[:5])}")
    parts.append(f"Current Act: {last_act}")
    return " | ".join(parts)
