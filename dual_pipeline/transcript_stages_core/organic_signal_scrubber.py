"""Context-aware intelligent verbatim scrubber for Output 01 transcripts.

Strips non-lexical vocalized fillers (um, uh, ugh, mhm), collapses stutters
and duplicated words, repairs dangling fragments, and prunes passive backchannels
unless answering a direct question.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Non-lexical vocal pauses and hesitations
VOCAL_FILLERS: Set[str] = {
    "ugh", "um", "umm", "ummm", "uh", "uhh", "uhhh", "er", "err",
    "ah", "ahh", "hmm", "hmmm", "mhm", "mm-hmm", "mmhmm", "uh-huh", "uhhuh",
}

# Standalone backchannels and acknowledgments (normalized lowercase, no punct)
BACKCHANNEL_WORDS: Set[str] = {
    "ok", "okay", "yeah", "yep", "yes", "right", "sure", "thanks",
    "thank you", "thank", "cool", "nice", "all right", "got it", "mhm", "mm-hmm", "uh-huh",
}

# Meeting opening and closing fluff (normalized lowercase, no punct)
OPENING_CLOSING_PHATIC: Set[str] = {
    "good afternoon everybody", "good morning everybody", "good morning",
    "good afternoon", "thanks for joining", "thank you for joining",
    "hey everyone", "bye everybody", "have a good one",
}

# 1-2 word orphan fragments from false diarization switches
ORPHAN_FRAGMENTS: Set[str] = {
    "the", "a", "an", "and", "or", "so", "it", "that", "in", "to", "of", "is",
    "for", "with", "overall", "again", "months", "not a",
}

RE_BOLD_BRACKET_HEADER = re.compile(
    r"^\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^:*]+):\*\*\s*(.*)$",
    re.DOTALL,
)
RE_SPLIT_BOLD_HEADER = re.compile(
    r"^\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*\s+\*\*([^*:\n]+):\*\*\s*(.*)$",
    re.DOTALL,
)
RE_BOLD_SPEAKER_OUTSIDE = re.compile(
    r"^\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^*]+)\*\*:\s*(.*)$",
    re.DOTALL,
)
RE_BRACKET_BOLD_SPEAKER = re.compile(
    r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+\*\*([^*:\n]+)(?:\*\*:\s*|:\*\*|\s*:\s*\*+)\s*(.*)$",
    re.DOTALL,
)
RE_INLINE_BOLD_HEADER = re.compile(
    r"^\*\*([^*:]+):?\*\*:?\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]:?\s*(.*)$",
    re.DOTALL,
)
RE_TURN_HEADER = re.compile(
    r"^(?:#{1,3}\s+)?\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^:\n]+):\s*(.*)$",
    re.DOTALL,
)


def _strip_leading_fillers(text: str) -> str:
    """Strip leading hesitations while preserving capitalization of following text."""
    had_comma = bool(re.match(r"^(?:um|umm|uh|uhh|ugh|er|ah|hmm|mhm|mm-hmm|uh-huh),\s*(?:no|yes|yeah)\b", text, re.I))
    pattern = r"^(?:(?:um|umm|uh|uhh|ugh|er|ah|hmm|mhm|mm-hmm|uh-huh)\b[\s,.\-—…]*)+"
    cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    if had_comma and re.match(r"^(?:No|Yes|Yeah)\b(?!\s*,)", cleaned):
        cleaned = re.sub(r"^(No|Yes|Yeah)\b", r"\1,", cleaned)
    return cleaned


def _strip_inline_fillers(text: str) -> str:
    """Remove inline non-lexical fillers and heal surrounding punctuation."""
    filler_tokens = "|".join(re.escape(f) for f in sorted(VOCAL_FILLERS, key=len, reverse=True))

    # Pattern 1: Hesitations bounded by ellipsis (I... uh... it's)
    text = re.sub(
        rf"\.{3,}\s*(?:{filler_tokens})\s*\.{3,}",
        "...",
        text,
        flags=re.IGNORECASE,
    )

    # Pattern 2: Hesitations surrounded by commas or spaces (, um, -> ,)
    text = re.sub(
        rf",\s*(?:{filler_tokens})\s*,",
        ",",
        text,
        flags=re.IGNORECASE,
    )

    # Pattern 3: Standalone word boundary filler with trailing punctuation (um, -> " ")
    text = re.sub(
        rf"\b(?:{filler_tokens})\b[\s,]*",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    # Clean double spaces and stray comma spacing
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.:;?!])", r"\1", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r",\s*(\.{3,})", r"\1", text)
    text = re.sub(r"\.{4,}", "...", text)
    return text.strip()


def strip_vocalized_fillers(text: str) -> str:
    """Strip non-lexical vocal pauses both at start of turn and inline."""
    if not text:
        return ""
    cleaned = _strip_leading_fillers(text)
    cleaned = _strip_inline_fillers(cleaned)
    return _strip_leading_fillers(cleaned)


def _collapse_hyphen_stutters(text: str) -> str:
    """Collapse hyphenated stutter sequences (e.g. 'It -it -it -that' -> 'that')."""
    pattern = r"\b([A-Za-z]{2,})\s*-\s*(?:(?:\1|[A-Za-z]{2,})\s*-\s*)+([A-Za-z]{2,})\b"

    def _replace_stutter(m: re.Match[str]) -> str:
        matched = m.group(0).lower()
        matched_norm = re.sub(r"\s*-\s*", "-", matched)
        if re.search(r"-(?:to|of|by|in|on|for|and|or)-", matched_norm):
            return m.group(0)
        for compound in ("out-of-the-box", "state-of-the-art", "end-to-end", "up-to-date", "day-to-day", "ready-to-use"):
            if compound in matched_norm:
                return compound
        final_word = m.group(2)
        first_word = m.group(1)
        if first_word[0].isupper():
            return final_word.capitalize()
        return final_word

    text = re.sub(pattern, _replace_stutter, text, flags=re.IGNORECASE)
    # Simple single hyphen stutter: 'is -is' -> 'is'
    text = re.sub(r"\b([A-Za-z]+)\s*-\s*\1\b", r"\1", text, flags=re.IGNORECASE)
    return text


def _collapse_word_repetitions(text: str) -> str:
    """Collapse immediate duplicated words while preserving intentional emphasis."""
    exceptions = {"very", "had", "that", "five", "one", "two", "three", "four", "six", "seven", "eight", "nine", "ten"}

    def _dedupe_match(m: re.Match[str]) -> str:
        word = m.group(1)
        if word.lower() in exceptions:
            return m.group(0)
        return word

    # Adjacent identical words across whitespace
    text = re.sub(r"\b([A-Za-z]{2,})\s+\1\b", _dedupe_match, text, flags=re.IGNORECASE)
    # Consecutive identical punctuated words: 'different. different.' -> 'different.'
    text = re.sub(r"\b([A-Za-z]{2,})\.\s+\1\.", r"\1.", text, flags=re.IGNORECASE)
    return text


def collapse_stutters_and_repetitions(text: str) -> str:
    """Deduplicate stutters and immediate word repetitions."""
    if not text:
        return ""
    text = _collapse_hyphen_stutters(text)
    return _collapse_word_repetitions(text)


def repair_dangling_connectors(text: str) -> str:
    """Repair trailing broken connectors and orphaned punctuation."""
    if not text:
        return ""
    # If clause already ends in period followed by dangling connector (e.g. 'thing. And so .')
    text = re.sub(r"(?<=\.)\s*(?:and\s+so|or|but\s+also)\s*\.?\s*$", "", text, flags=re.IGNORECASE)
    # Remove trailing broken connectors like 'And so .' or 'but also .'
    text = re.sub(r"\b(?:and\s+so|but\s+also)\s*\.\s*$", ".", text, flags=re.IGNORECASE)
    # Strip dangling connector after period: '. Or' -> '.'
    text = re.sub(r"\.\s*(?:or|and|so|but|also)\s*$", ".", text, flags=re.IGNORECASE)
    # Collapse 'performance or .' -> 'performance.'
    text = re.sub(r"\b\s+(?:or|and)\s+\.\s*$", ".", text, flags=re.IGNORECASE)
    # Clean double dots that are NOT part of an ellipsis
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"\s+\.", ".", text)
    return text.strip()


def is_question_turn(text: str) -> bool:
    """Return True if turn ends in a question mark or poses an active question."""
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    lower = stripped.lower()
    question_cues = (
        "any questions", "does that make sense", "what do you think",
        "is that what you had in mind", "would that work",
    )
    return any(cue in lower for cue in question_cues)


def is_passive_backchannel(text: str) -> bool:
    """Return True if turn consists strictly of short backchannel acknowledgments."""
    clean = re.sub(r"[^\w\s]", "", text).strip().lower()
    if not clean:
        return True
    tokens = clean.split()
    if len(tokens) <= 3 and all(t in BACKCHANNEL_WORDS for t in tokens):
        return True
    return False


def is_opening_closing_phatic(text: str) -> bool:
    """Return True if turn consists solely of meeting opening/closing fluff."""
    clean = re.sub(r"[^\w\s]", "", text).strip().lower()
    return clean in OPENING_CLOSING_PHATIC


def is_orphan_fragment(text: str) -> bool:
    """Return True if turn is a 1-2 word orphan fragment without substantive content."""
    clean = re.sub(r"[^\w\s]", "", text).strip().lower()
    if not clean:
        return True
    tokens = clean.split()
    if len(tokens) == 1 and tokens[0] in ORPHAN_FRAGMENTS:
        return True
    if len(tokens) <= 2 and clean in ORPHAN_FRAGMENTS:
        return True
    return False


class ParsedTurn(Tuple[str, str, str]):
    """Parsed turn preserving (ts, spk, content) and markdown format style."""

    def __new__(cls, ts: str, spk: str, content: str, format_style: str = "standard"):
        instance = super().__new__(cls, (ts, spk, content))
        instance.ts = ts
        instance.spk = spk
        instance.content = content
        instance.format_style = format_style
        return instance

    def format(self, cleaned_content: str) -> str:
        if self.format_style == "bold_bracket":
            return f"**[{self.ts}] {self.spk}:** {cleaned_content}"
        if self.format_style == "split_bold":
            return f"**[{self.ts}]** **{self.spk}:** {cleaned_content}"
        if self.format_style == "bold_speaker_outside":
            return f"**[{self.ts}] {self.spk}**: {cleaned_content}"
        if self.format_style == "multiline_heading":
            return f"### [{self.ts}]\n\n**{self.spk}:** {cleaned_content}"
        if self.format_style == "bracket_bold_speaker":
            return f"[{self.ts}] **{self.spk}:** {cleaned_content}"
        if self.format_style == "inline_bold":
            return f"**{self.spk}:** [{self.ts}] {cleaned_content}"
        if self.format_style == "heading_turn":
            return f"### [{self.ts}] {self.spk}: {cleaned_content}"
        return f"[{self.ts}] {self.spk}: {cleaned_content}"


def _parse_turn(line: str) -> Optional[ParsedTurn]:
    """Parse a transcript line into (timestamp, speaker, content) with format preservation."""
    m = RE_BOLD_BRACKET_HEADER.match(line)
    if m:
        return ParsedTurn(m.group(1), m.group(2).strip(), m.group(3).strip(), "bold_bracket")
    m = RE_SPLIT_BOLD_HEADER.match(line)
    if m:
        return ParsedTurn(m.group(1), m.group(2).strip(), m.group(3).strip(), "split_bold")
    m = RE_BOLD_SPEAKER_OUTSIDE.match(line)
    if m:
        return ParsedTurn(m.group(1), m.group(2).strip(), m.group(3).strip(), "bold_speaker_outside")
    m = RE_BRACKET_BOLD_SPEAKER.match(line)
    if m:
        spk = re.sub(r"^\*\*|\*\*:\s*$|:\*\*\s*$|:\s*\*+\s*$", "", m.group(2)).strip()
        return ParsedTurn(m.group(1), spk, m.group(3).strip(), "bracket_bold_speaker")
    m = RE_INLINE_BOLD_HEADER.match(line)
    if m:
        return ParsedTurn(m.group(2), m.group(1).strip(), m.group(3).strip(), "inline_bold")
    m = RE_TURN_HEADER.match(line)
    if m:
        is_heading = line.lstrip().startswith("###")
        fmt = "heading_turn" if is_heading else "standard"
        return ParsedTurn(m.group(1), m.group(2).strip(), m.group(3).strip(), fmt)
    return None


def clean_turn_text(text: str) -> str:
    """Apply lexical disfluency, stutter, and connector cleanup to turn text."""
    filler_tokens = "|".join(re.escape(f) for f in sorted(VOCAL_FILLERS, key=len, reverse=True))
    t = re.sub(
        rf"(?<=[.!?]\s)(?:(?:{filler_tokens})\b[\s,.\-—…]*)+([a-z])",
        lambda m: m.group(1).upper(),
        text,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        rf"(?<=[.!?]\s)(?:(?:{filler_tokens})\b[\s,.\-—…]*)+",
        "",
        t,
        flags=re.IGNORECASE,
    )
    cleaned = strip_vocalized_fillers(t)
    cleaned = collapse_stutters_and_repetitions(cleaned)
    cleaned = repair_dangling_connectors(cleaned)
    return cleaned.strip()


class _TurnAccumulator:
    """Stateful accumulator for multiline and single-line transcript turns."""

    def __init__(self, preserve_verbatim_turns: bool = False) -> None:
        self.elements: List[str] = []
        self.prev_was_question = False
        self.in_postamble = False
        self.in_dialogue = False
        self.curr_ts: Optional[str] = None
        self.curr_spk: Optional[str] = None
        self.curr_lines: List[str] = []
        self.curr_fmt: str = "multiline_heading"
        self.pending_ts: Optional[str] = None
        self.pending_is_heading = True
        self.awaiting_speaker = False
        self.preserve_verbatim_turns = preserve_verbatim_turns

    def flush(self) -> None:
        """Process and emit buffered turn if valid."""
        if self.curr_spk is None:
            return
        raw = " ".join(l.strip() for l in self.curr_lines if l.strip())
        cleaned = clean_turn_text(raw)
        ts, spk = self.curr_ts or "00:00:00", self.curr_spk
        fmt = self.curr_fmt
        self.curr_ts, self.curr_spk, self.curr_lines = None, None, []

        # Standalone passive backchannels that do not answer a question must always be pruned
        if is_passive_backchannel(cleaned):
            if not self.prev_was_question:
                logger.debug("Pruned passive backchannel turn: [%s] %s: %s", ts, spk, cleaned)
                return
            self.prev_was_question = False
        else:
            self.prev_was_question = is_question_turn(cleaned)

        if not self.preserve_verbatim_turns:
            if not cleaned or is_opening_closing_phatic(cleaned) or is_orphan_fragment(cleaned):
                return
        elif not cleaned:
            return

        turn = ParsedTurn(ts, spk, cleaned, fmt)
        self.elements.append(turn.format(cleaned))

    def _try_consume_speaker(self, stripped: str) -> bool:
        if not stripped:
            return True
        m = re.match(r"^(?:\*\*([^*:\n]+):?\*\*|([A-Za-z0-9\s\.\,\'’\-]+?)):?\s*(.*)$", stripped)
        if not m:
            return False
        self.curr_ts = self.pending_ts
        self.curr_spk = (m.group(1) or m.group(2)).strip()
        body = m.group(3).strip()
        self.curr_lines = [body] if body else []
        self.curr_fmt = "multiline_heading" if self.pending_is_heading else "standard"
        self.awaiting_speaker = False
        return True

    def handle_line(self, line: str) -> None:
        """Handle individual line within state machine."""
        stripped = line.strip()
        if re.match(r"^##\s+(?:Ambiguity|Evidence|Attribution)\b", stripped, re.IGNORECASE):
            self.flush()
            self.in_postamble = True
            self.elements.append(line)
            return
        if self.in_postamble:
            self.elements.append(line)
            return

        m_ts = re.match(r"^(?:#{1,3}\s+)?\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)\]$", stripped)
        if m_ts:
            self.flush()
            self.in_dialogue, self.pending_ts = True, m_ts.group(1)
            self.pending_is_heading, self.awaiting_speaker = stripped.startswith("#"), True
            return

        if self.awaiting_speaker and self._try_consume_speaker(stripped):
            return

        parsed = _parse_turn(stripped)
        if parsed:
            self.flush()
            self.in_dialogue, self.curr_ts, self.curr_spk = True, parsed.ts, parsed.spk
            self.curr_lines, self.curr_fmt, self.awaiting_speaker = [parsed.content], parsed.format_style, False
            return

        if re.match(r"^(?:\[(?:Recording gap|Natural pause|Session pause):[^\]]+\]|---)$", stripped, re.IGNORECASE):
            self.flush()
            self.elements.append(line)
            return

        if self.in_dialogue and self.curr_spk is not None:
            if stripped:
                self.curr_lines.append(stripped)
            return

        self.elements.append(line)


def scrub_organic_signal(transcript: str, preserve_verbatim_turns: bool = False) -> str:
    """Process full transcript, filtering passive turns and enriching organic signal.

    Preserves backchannels when they immediately follow an interrogative question.
    Handles both single-line turn syntax and multiline heading turn blocks.
    """
    if not transcript or not transcript.strip():
        return ""
    acc = _TurnAccumulator(preserve_verbatim_turns=preserve_verbatim_turns)
    for line in transcript.splitlines():
        acc.handle_line(line)
    acc.flush()
    res = "\n\n".join(e for e in acc.elements if e is not None)
    return re.sub(r"\n{3,}", "\n\n", res)
