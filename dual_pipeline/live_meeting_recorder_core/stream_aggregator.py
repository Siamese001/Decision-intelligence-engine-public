"""Live Meeting Recorder Core: Real-Time Conversational Turn Aggregator & De-Clutterer.

Reduces clutter during live meeting recording:
  1. Aggregates fragmented 8-second Whisper chunks into coherent conversational turns.
  2. Aligns accurate start and end elapsed timestamps ([MM:SS] - [MM:SS]).
  3. Cleans speech disfluencies, filler words (um, uh, you know), and consecutive word stutters.
  4. Emits clean, readable turns to the live transcript feed and Copilot advisor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class AggregatedTurn:
    """Represents a coherent, aggregated conversational turn with aligned timestamps."""
    turn_id: str
    speaker: str
    text: str
    start_seconds: int
    end_seconds: int
    start_time_str: str
    end_time_str: str
    chunk_indices: List[int] = field(default_factory=list)
    is_final: bool = True

    @property
    def display_time(self) -> str:
        if self.start_time_str == self.end_time_str:
            return self.start_time_str
        return f"{self.start_time_str} - {self.end_time_str}"

    def format_markdown(self) -> str:
        return f"**{self.display_time}** {self.speaker}: {self.text}\n\n"

    def format_heading_turn(self) -> str:
        return f"### {self.start_time_str}\n\n**{self.speaker}:** {self.text}\n\n"


_FILLER_WORDS_REGEX = re.compile(
    r"(?:\b(?:um|uh|er|ah)\b[,\s]*|\b(?:like,?\s+)?you\s+know[,\s]*|\b(?:like)[,\s]+(?=\b(?:we|you|i|they|it|the)\b))",
    re.IGNORECASE,
)


def clean_disfluencies(text: str) -> str:
    """Removes verbal clutter and stutter repetitions while preserving natural meaning."""
    if not text:
        return ""
    cleaned = _FILLER_WORDS_REGEX.sub(" ", text)
    cleaned = re.sub(r"^[,\s;:—\-]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([,\.!?:;])", r"\1", cleaned)
    cleaned = re.sub(r"([,\.!?:;]){2,}", r"\1", cleaned)
    cleaned = re.sub(r"^[,\s;:—\-]+", "", cleaned)

    words = cleaned.split()
    if len(words) > 1:
        deduped: List[str] = []
        for w in words:
            w_clean = w.lower().strip(".,!?")
            if not deduped or w_clean != deduped[-1].lower().strip(".,!?") or deduped and any(p in deduped[-1] for p in ".!?"):
                deduped.append(w)
        cleaned = " ".join(deduped)

    if cleaned:
        cleaned = re.sub(r"^[,\s;:—\-]+", "", cleaned)
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned.strip()


def join_speech_fragments(fragments: List[str]) -> str:
    """Joins consecutive speech fragments seamlessly, handling mid-sentence boundaries and cross-chunk continuity."""
    if not fragments:
        return ""
    joined = ""
    for frag in fragments:
        frag_clean = frag.strip()
        if not frag_clean:
            continue
        if not joined:
            joined = frag_clean
            continue
        if joined.endswith("..."):
            joined = joined[:-3].rstrip() + " " + frag_clean
        elif joined.endswith("-"):
            joined = joined[:-1] + frag_clean
        elif joined[-1] not in ".!?,:;—" and frag_clean[0].islower():
            joined += " " + frag_clean
        else:
            joined += " " + frag_clean
    return joined


class ConversationalTurnAggregator:
    """Buffers consecutive raw chunks and emits de-cluttered, readable turns."""

    def __init__(
        self,
        max_silence_gap_seconds: int = 16,
        max_turn_duration_seconds: int = 48,
        on_turn_completed: Optional[Callable[[AggregatedTurn], None]] = None,
    ):
        self.max_silence_gap_seconds = max_silence_gap_seconds
        self.max_turn_duration_seconds = max_turn_duration_seconds
        self.on_turn_completed = on_turn_completed

        self._current_speaker: Optional[str] = None
        self._current_fragments: List[str] = []
        self._current_chunk_indices: List[int] = []
        self._turn_start_sec: float = 0.0
        self._last_chunk_sec: float = 0.0
        self._last_chunk_duration: float = 8.0
        self._turn_counter: int = 0

    def _format_time(self, seconds: int | float) -> str:
        sec_int = int(round(max(0, seconds)))
        m, s = divmod(sec_int, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"[{h:02d}:{m:02d}:{s:02d}]"
        return f"[{m:02d}:{s:02d}]"

    def flush(self) -> Optional[AggregatedTurn]:
        """Flushes the active buffer into a completed AggregatedTurn."""
        if not self._current_fragments:
            return None

        raw_joined = join_speech_fragments(self._current_fragments)
        cleaned_text = clean_disfluencies(raw_joined)

        if not cleaned_text:
            self._current_fragments.clear()
            self._current_chunk_indices.clear()
            return None

        if cleaned_text and cleaned_text[-1] not in ".!?":
            cleaned_text += "."

        self._turn_counter += 1
        start_sec = int(round(self._turn_start_sec))
        end_sec = int(round(self._last_chunk_sec + self._last_chunk_duration))
        turn = AggregatedTurn(
            turn_id=f"turn_{self._turn_counter:04d}",
            speaker=self._current_speaker or "Speaker",
            text=cleaned_text,
            start_seconds=start_sec,
            end_seconds=end_sec,
            start_time_str=self._format_time(start_sec),
            end_time_str=self._format_time(end_sec),
            chunk_indices=list(self._current_chunk_indices),
            is_final=True,
        )

        self._current_fragments.clear()
        self._current_chunk_indices.clear()
        self._current_speaker = None

        if self.on_turn_completed:
            self.on_turn_completed(turn)

        return turn

    def get_current_utterance(self) -> str:
        """Returns the cleaned, joined utterance accumulated so far in the active turn."""
        if not self._current_fragments:
            return ""
        return clean_disfluencies(join_speech_fragments(self._current_fragments))

    def add_chunk(
        self,
        chunk_idx: int,
        seconds: int | float,
        text: str,
        speaker: str,
        duration: Optional[float] = None,
    ) -> Optional[AggregatedTurn]:
        """Ingests a raw chunk. Returns completed turn if speaker changed or turn finalized."""
        cleaned_snippet = text.strip()
        if not cleaned_snippet:
            return None

        dur = float(duration) if duration is not None else 8.0

        completed_turn: Optional[AggregatedTurn] = None

        speaker_changed = (self._current_speaker is not None and self._current_speaker != speaker)
        silence_gap = bool(self._current_fragments and (seconds - self._last_chunk_sec > self.max_silence_gap_seconds))
        duration_exceeded = bool(self._current_fragments and (seconds - self._turn_start_sec >= self.max_turn_duration_seconds))
        hard_limit = bool(self._current_fragments and (seconds - self._turn_start_sec >= self.max_turn_duration_seconds + 20))
        turn_boundary = (duration_exceeded and cleaned_snippet[-1] in ".?!") or hard_limit

        if speaker_changed or silence_gap or turn_boundary:
            completed_turn = self.flush()

        if not self._current_fragments:
            self._current_speaker = speaker
            self._turn_start_sec = float(seconds)

        self._current_fragments.append(cleaned_snippet)
        self._current_chunk_indices.append(chunk_idx)
        self._last_chunk_sec = float(seconds)
        self._last_chunk_duration = dur

        return completed_turn


def _parse_timestamp_seconds(ts: str) -> int:
    """Parses MM:SS or HH:MM:SS timestamp into total seconds."""
    parts = ts.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def _parse_turn_block_content(block_text: str) -> tuple[str, str, str]:
    """Extracts speaker, speech text, and pause marker from a transcript block."""
    pause_match = re.search(r"(\[(?:Natural pause|Recording gap):[^\]]+\])", block_text, re.IGNORECASE)
    pause_marker = pause_match.group(1) if pause_match else ""

    spk_match = re.match(r"^\*\*([^*:]+)(?::\*\*|\*\*:)\s*([\s\S]*)$", block_text)
    if spk_match:
        speaker = spk_match.group(1).strip()
        speech = spk_match.group(2).strip()
    else:
        speaker = "Speaker"
        speech = block_text

    if pause_marker and pause_marker in speech:
        speech = speech.replace(pause_marker, "").strip()

    return speaker, speech, pause_marker


def _format_coalesced_turn(time_str: str, speaker: str, texts: List[str]) -> str:
    """Formats joined and disfluency-cleaned speech texts into a markdown heading block."""
    if not texts:
        return ""
    joined = join_speech_fragments(texts)
    cleaned = clean_disfluencies(joined)
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return f"### [{time_str}]\n\n**{speaker}:** {cleaned}\n\n"


def _should_coalesce(
    curr_speaker: str,
    speaker: str,
    pause_marker: str,
    sec: int,
    curr_last_sec: int,
    curr_start_sec: int,
    max_duration: int,
) -> bool:
    """Checks if a subsequent turn can be coalesced into the current speaker turn."""
    if not curr_speaker or curr_speaker != speaker or bool(pause_marker):
        return False
    return (sec - curr_last_sec <= 24) and (sec - curr_start_sec < max_duration)


def coalesce_transcript_markdown_turns(
    markdown_text: str,
    max_turn_duration_seconds: int = 48,
) -> str:
    """Coalesces consecutive turns from the same speaker in markdown into coherent paragraphs."""
    parts = re.split(r"(?:\n|^)(#{1,3}\s+Transcript\b[^\n]*)", markdown_text, maxsplit=1)
    if len(parts) < 3:
        header_prefix = ""
        body = markdown_text
    else:
        header_prefix = parts[0] + parts[1] + "\n\n"
        body = parts[2]

    heading_pattern = re.compile(r"(?:\n|^)###\s+\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n+")
    tokens = heading_pattern.split(body)
    if len(tokens) <= 1:
        return markdown_text

    leading_intro = tokens[0].strip()
    coalesced_blocks: List[str] = []
    curr_time_str = ""
    curr_speaker = ""
    curr_texts: List[str] = []
    curr_start_sec = 0
    curr_last_sec = 0

    for i in range(1, len(tokens), 2):
        t_str = tokens[i]
        block_text = tokens[i + 1].strip() if (i + 1) < len(tokens) else ""
        sec = _parse_timestamp_seconds(t_str)
        speaker, speech, pause_marker = _parse_turn_block_content(block_text)

        if _should_coalesce(curr_speaker, speaker, pause_marker, sec, curr_last_sec, curr_start_sec, max_turn_duration_seconds):
            curr_texts.append(speech)
            curr_last_sec = sec
            continue

        if curr_texts:
            coalesced_blocks.append(_format_coalesced_turn(curr_time_str, curr_speaker, curr_texts))
        if pause_marker:
            coalesced_blocks.append(f"{pause_marker}\n\n")

        curr_time_str = t_str
        curr_speaker = speaker
        curr_texts = [speech]
        curr_start_sec = sec
        curr_last_sec = sec

    if curr_texts:
        coalesced_blocks.append(_format_coalesced_turn(curr_time_str, curr_speaker, curr_texts))

    result_body = (leading_intro + "\n\n" if leading_intro else "") + "".join(coalesced_blocks)
    return header_prefix + result_body.strip() + "\n"



def _check_vocative(clean_lower: str, attendees: List[Dict[str, Any]]) -> tuple[str, str] | None:
    for att in attendees:
        name = att.get("name", "")
        first_name = name.split()[0].lower() if name else ""
        if first_name and len(first_name) >= 3:
            markers = (f"{first_name},", f", {first_name}?", f" {first_name}, what", f" {first_name}, do you", f" {first_name}, can you", f" {first_name}, could you")
            if any(m in clean_lower for m in markers):
                counter_parties = [a.get("name") for a in attendees if a.get("name") != name]
                if counter_parties:
                    return name, counter_parties[0]
    return None


def _check_self_id(clean_lower: str, attendees: List[Dict[str, Any]]) -> str | None:
    for att in attendees:
        name = att.get("name", "")
        first_name = name.split()[0].lower() if name else ""
        if first_name and (f"this is {first_name}" in clean_lower or f"{first_name} here" in clean_lower):
            return name
    return None


class ConversationalSpeakerTracker:
    """Tracks active conversational turns and dynamically attributes speakers based on roster, vocatives, and turn alternation."""

    def __init__(self, attendees: Optional[List[Any]] = None, primary_speaker: str = "Amit Ayer"):
        self.attendees = [a if isinstance(a, dict) else {"name": str(a).strip()} for a in (attendees or []) if a and (isinstance(a, dict) or str(a).strip())]
        self.primary_speaker = primary_speaker
        self._current_speaker: Optional[str] = None
        self._last_question_addressed_to: Optional[str] = None

    def attribute_chunk(
        self, text: str, audio_source: str = "auto", acoustic_speaker: Optional[str] = None,
        current_sec: Optional[float] = None, default_speaker: Optional[str] = None, **_kwargs: Any,
    ) -> str:
        """Determines most probable speaker for an audio snippet."""
        _ = (audio_source, current_sec)
        valid_acoustic = acoustic_speaker if acoustic_speaker and acoustic_speaker not in ("Speaker", "Host", "Unknown") else None
        clean = (text or "").strip()
        if not clean:
            return valid_acoustic or self._current_speaker or self._fallback_speaker or default_speaker or "Speaker"
        clean_lower = clean.lower()
        voc_result = _check_vocative(clean_lower, self.attendees)
        if voc_result:
            self._last_question_addressed_to, self._current_speaker = voc_result
            return self._current_speaker
        if self._last_question_addressed_to:
            answering, self._last_question_addressed_to = self._last_question_addressed_to, None
            self._current_speaker = answering
            return answering
        self_speaker = _check_self_id(clean_lower, self.attendees)
        if self_speaker:
            self._current_speaker = self_speaker
            return self_speaker
        if valid_acoustic:
            self._current_speaker = valid_acoustic
            return valid_acoustic
        resolved = self._current_speaker or self._fallback_speaker
        return default_speaker if (resolved in ("Speaker", "Host", "Unknown") and default_speaker) else resolved

    @property
    def _fallback_speaker(self) -> str:
        if len(self.attendees) == 1:
            return self.attendees[0].get("name", "Speaker")
        if len(self.attendees) == 2:
            prim_low = self.primary_speaker.lower()
            prim_first = prim_low.split()[0] if prim_low else ""
            other = [a.get("name") for a in self.attendees if a.get("name") and a.get("name", "").lower() != prim_low and (not prim_first or a.get("name", "").lower().split()[0] != prim_first)]
            if other:
                return other[0]
        return "Speaker"


def coalesce_speaker_turns(lines: List[Tuple[int, str, str]], max_words_per_turn: int = 150) -> List[Tuple[int, str, str]]:
    """Coalesces consecutive turns from the same speaker into coherent paragraphs."""
    if not lines:
        return []
    coalesced: List[Tuple[int, str, str]] = []
    curr_line_no, curr_spk, curr_texts = lines[0][0], lines[0][1], [lines[0][2].strip()]
    for line_no, spk, text in lines[1:]:
        clean_t = text.strip()
        if not clean_t:
            continue
        if spk == curr_spk and sum(len(t.split()) for t in curr_texts) < max_words_per_turn:
            curr_texts.append(clean_t)
        else:
            coalesced.append((curr_line_no, curr_spk, join_speech_fragments(curr_texts)))
            curr_line_no, curr_spk, curr_texts = line_no, spk, [clean_t]
    if curr_texts:
        coalesced.append((curr_line_no, curr_spk, join_speech_fragments(curr_texts)))
    return coalesced


