from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class SourceSpan:
    """Represents a bounded piece of evidence from a source container."""
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    speaker_label: Optional[str] = None
    text: str = ""
    cue_id: Optional[str] = None
    paragraph_index: Optional[int] = None
    line_range: Optional[tuple[int, int]] = None
    record_id: Optional[str] = None

@dataclass
class NormalizedTurn:
    """A canonical transcript turn mapped to its original source spans."""
    speaker: Optional[str]
    start_time: Optional[str]
    end_time: Optional[str]
    text: str
    spans: List[SourceSpan] = field(default_factory=list)

@dataclass
class NormalizedTranscript:
    """Holds both the compatibility markdown text and the structured turns."""
    text: str
    turns: List[NormalizedTurn] = field(default_factory=list)

    def __str__(self) -> str:
        return self.text

def _parse_sec(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        parts = [float(p) for p in ts.strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return float(ts)
    except Exception:
        return None


def merge_spans_into_turns(spans: List[SourceSpan], max_merge_drift_sec: float = 0.0) -> List[NormalizedTurn]:
    """Merges consecutive same-speaker spans into normalized turns without drifting distinct timestamps."""
    turns: List[NormalizedTurn] = []
    curr_turn: Optional[NormalizedTurn] = None

    for span in spans:
        spk = span.speaker_label
        text = span.text.strip()
        if not text:
            continue

        can_merge = False
        if curr_turn and curr_turn.speaker == spk and spk is not None:
            if not span.start_time or span.start_time == curr_turn.start_time:
                can_merge = True
            elif max_merge_drift_sec > 0:
                s0 = _parse_sec(curr_turn.start_time)
                s1 = _parse_sec(span.start_time)
                if s0 is not None and s1 is not None and 0 <= (s1 - s0) <= max_merge_drift_sec:
                    can_merge = True

        if can_merge and curr_turn is not None:
            curr_turn.text = (curr_turn.text + " " + text).strip()
            if not curr_turn.end_time or (span.end_time and span.end_time > curr_turn.end_time):
                curr_turn.end_time = span.end_time
            curr_turn.spans.append(span)
        else:
            if curr_turn:
                turns.append(curr_turn)
            curr_turn = NormalizedTurn(
                speaker=spk,
                start_time=span.start_time,
                end_time=span.end_time,
                text=text,
                spans=[span]
            )

    if curr_turn:
        turns.append(curr_turn)

    return turns


def spans_to_speaker_utterances(spans: List[SourceSpan]) -> List[dict]:
    """Converts a sequence of SourceSpans into structured complete-thought SpeakerUtterances."""
    from ..audio_ingestion_core.utterance_assembler import assemble_utterances

    elements = []
    for s in spans:
        txt = s.text.strip()
        if not txt:
            continue
        st_sec = 0.0
        if s.start_time:
            parts = [float(p) for p in s.start_time.split(":")]
            st_sec = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1] if len(parts) == 2 else float(s.start_time)
        en_sec = st_sec
        if s.end_time:
            parts = [float(p) for p in s.end_time.split(":")]
            en_sec = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1] if len(parts) == 2 else float(s.end_time)

        elements.append({
            "speaker": s.speaker_label or "Unknown",
            "start": st_sec,
            "end": en_sec,
            "text": txt,
        })
    return assemble_utterances(elements, timing_provenance="interpolated_word")

