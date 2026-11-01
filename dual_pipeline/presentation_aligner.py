"""Deterministic Slide-to-Transcript and Speaker Alignment Engine.

Correlates spoken dialogue, turns, and speaker assignments with extracted
PowerPoint slides, tables, and presenter notes to produce high-signal,
evidence-bound alignment maps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from dual_pipeline.pptx_source import PPTXPresentation

# Regex to detect spoken slide references: e.g. "Slide 4", "slides 4 and 5", "Slide 8 through 12"
SLIDE_REF_REGEX = re.compile(
    r"\b(?:slide|slides)\s+(\d+)(?:\s*(?:and|,|through|to|-)\s*(\d+))?",
    re.IGNORECASE,
)


@dataclass
class SlideMention:
    """Represents a spoken reference to one or more slides."""
    speaker: str
    turn_index: int
    raw_text: str
    slide_numbers: List[int]
    timestamp: Optional[str] = None


@dataclass
class TurnAlignment:
    """Alignment between a single transcript turn and presentation slide(s)."""
    turn_index: int
    speaker: str
    timestamp: str
    turn_text: str
    referenced_slides: List[int]
    matched_slides: List[int]  # via keyword/content overlap
    primary_slide: Optional[int] = None
    slide_evidence_locators: List[str] = field(default_factory=list)


@dataclass
class PresentationAlignment:
    """Overall alignment between transcript and presentation."""
    presentation_title: str
    slide_count: int
    total_turns: int
    mentions: List[SlideMention]
    turn_alignments: List[TurnAlignment]
    speaker_slide_coverage: Dict[str, List[int]]  # speaker -> slides attributed
    unreferenced_slides: List[int]

    def get_slide_evidence_pack(self) -> List[Dict[str, Any]]:
        """Export structured slide evidence records suitable for graph ingestion."""
        evidence_items = []
        for ta in self.turn_alignments:
            for s_num in set(ta.referenced_slides + ta.matched_slides):
                evidence_items.append({
                    "evidence_id": f"ev-slide-{s_num:02d}",
                    "slide_number": s_num,
                    "locator": f"slide:{s_num:02d}",
                    "speaker": ta.speaker,
                    "timestamp": ta.timestamp,
                    "referenced_in_turn": ta.turn_index,
                })
        return evidence_items


def parse_transcript_turns(transcript_text: str) -> List[Dict[str, Any]]:
    """Parse dialogue turns from standard transcript markdown.
    
    Supports formats:
      **Speaker:** [00:01:05] Spoken text...
      Speaker: Spoken text...
    """
    turn_pattern = re.compile(
        r"(?:###\s*\[(?P<time_h>\d{1,2}:\d{2}(?::\d{2})?)\]\s*)?(?:^\s*\*\*(?P<speaker_b>[^*]+)\*\*:?|^\s*(?P<speaker_p>[A-Z][a-zA-Z\s.,-]+):)\s*(?:\[(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\])?\s*(?P<text>[^\n]+(?:\n(?!\s*(?:###|\*\*|\b[A-Z][a-zA-Z\s.,-]+:))[^\n]+)*)",
        re.MULTILINE,
    )
    turns: List[Dict[str, Any]] = []
    idx = 1
    for match in turn_pattern.finditer(transcript_text):
        spk = match.group("speaker_b") or match.group("speaker_p") or "Unknown"
        spk = spk.rstrip(":").strip()
        spk_clean = spk.lower()
        if spk_clean in (
            "date", "time", "attendees", "participants", "roster",
            "platform reference", "subject", "classification", "audiences",
            "primary reference presentation", "overview", "transcript",
        ):
            continue
        ts = match.group("time") or match.group("time_h") or ""
        txt = match.group("text") or ""
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            turns.append({
                "turn_index": idx,
                "speaker": spk,
                "timestamp": ts,
                "text": txt,
            })
            idx += 1
    return turns


def _extract_slide_numbers_from_turn(turn_text: str, max_slide: int) -> List[int]:
    """Find all explicit slide numbers mentioned in a turn."""
    found: Set[int] = set()
    for m in SLIDE_REF_REGEX.finditer(turn_text):
        s1 = int(m.group(1))
        if 1 <= s1 <= max_slide:
            found.add(s1)
        if m.group(2):
            s2 = int(m.group(2))
            if 1 <= s2 <= max_slide:
                # If s2 > s1 and range is <= 15 slides, expand range
                if s2 > s1 and (s2 - s1) <= 15 and ("through" in m.group(0).lower() or "to" in m.group(0).lower() or "-" in m.group(0)):
                    found.update(range(s1, s2 + 1))
                else:
                    found.add(s2)
    return sorted(found)


def _compute_keyword_overlap(words_a: Set[str], words_b: Set[str]) -> float:
    """Compute Jaccard-like overlap for significant words."""
    if not words_a or not words_b:
        return 0.0
    intersection = words_a.intersection(words_b)
    return len(intersection) / min(len(words_a), len(words_b))


def _tokenize(text: str) -> Set[str]:
    """Tokenize and filter stop words."""
    stop_words = {
        "the", "and", "or", "to", "in", "of", "for", "with", "on", "at", "by", "from",
        "up", "about", "into", "over", "after", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "but", "if", "because",
        "as", "until", "while", "this", "that", "these", "those", "we", "our", "you", "your",
    }
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return {w for w in words if w not in stop_words}


def align_transcript_presentation(
    transcript_text: str,
    presentation: PPTXPresentation,
) -> PresentationAlignment:
    """Align parsed transcript turns against an extracted PPTXPresentation."""
    turns = parse_transcript_turns(transcript_text)
    mentions: List[SlideMention] = []
    turn_alignments: List[TurnAlignment] = []
    speaker_coverage: Dict[str, Set[int]] = {}
    all_referenced_slides: Set[int] = set()

    # Pre-tokenize presentation slides
    slide_tokens: Dict[int, Set[str]] = {}
    for s in presentation.slides:
        full_slide_text = f"{s.title} {' '.join(s.paragraphs)} {s.speaker_notes}"
        slide_tokens[s.slide_number] = _tokenize(full_slide_text)

    for turn in turns:
        turn_idx = turn["turn_index"]
        spk = turn["speaker"]
        ts = turn["timestamp"]
        txt = turn["text"]

        # 1. Explicit mention detection
        explicit_slides = _extract_slide_numbers_from_turn(txt, presentation.slide_count)
        if explicit_slides:
            mentions.append(SlideMention(
                speaker=spk,
                turn_index=turn_idx,
                raw_text=txt,
                slide_numbers=explicit_slides,
                timestamp=ts,
            ))
            all_referenced_slides.update(explicit_slides)
            speaker_coverage.setdefault(spk, set()).update(explicit_slides)

        # 2. Content & keyword overlap matching (for turns without explicit slide references)
        matched_slides: List[int] = []
        turn_toks = _tokenize(txt)
        if turn_toks:
            best_slide = None
            best_score = 0.0
            for s_num, s_toks in slide_tokens.items():
                score = _compute_keyword_overlap(turn_toks, s_toks)
                if score >= 0.40 and score > best_score:
                    best_score = score
                    best_slide = s_num
            if best_slide and best_slide not in explicit_slides:
                matched_slides.append(best_slide)
                speaker_coverage.setdefault(spk, set()).add(best_slide)

        # 3. Formulate locators
        combined_slides = sorted(set(explicit_slides + matched_slides))
        locators = [f"slide:{s:02d}" for s in combined_slides]

        # Primary slide heuristic: explicit first, else matched
        primary = explicit_slides[0] if explicit_slides else (matched_slides[0] if matched_slides else None)

        turn_alignments.append(TurnAlignment(
            turn_index=turn_idx,
            speaker=spk,
            timestamp=ts,
            turn_text=txt,
            referenced_slides=explicit_slides,
            matched_slides=matched_slides,
            primary_slide=primary,
            slide_evidence_locators=locators,
        ))

    unref = [s.slide_number for s in presentation.slides if s.slide_number not in all_referenced_slides]

    return PresentationAlignment(
        presentation_title=presentation.title,
        slide_count=presentation.slide_count,
        total_turns=len(turns),
        mentions=mentions,
        turn_alignments=turn_alignments,
        speaker_slide_coverage={k: sorted(v) for k, v in speaker_coverage.items()},
        unreferenced_slides=unref,
    )


def inject_slide_annotations(
    transcript_text: str,
    alignment: PresentationAlignment,
    presentation: Optional[PPTXPresentation] = None,
) -> str:
    """Produce an enhanced transcript with inline slide annotations where slides were referenced."""
    slide_lookup = {s.slide_number: s for s in presentation.slides} if presentation else {}
    turns = alignment.turn_alignments
    if not turns:
        return transcript_text

    # Detect header section (up to ## Transcript or first turn)
    transcript_match = re.search(r"^## Transcript\s*$", transcript_text, re.MULTILINE)
    if transcript_match:
        header = transcript_text[:transcript_match.end()]
        rest = transcript_text[transcript_match.end():]
    else:
        sep_match = re.search(r"\n---\s*\n", transcript_text)
        if sep_match:
            header = transcript_text[:sep_match.end()]
            rest = transcript_text[sep_match.end():]
        else:
            header = ""
            rest = transcript_text

    # Detect footer section (e.g. trailing --- followed by Ambiguity Lock Register, Evidence Locks, etc.)
    footer_match = re.search(r"\n---\s*\n\s*##\s+(?:Ambiguity|Evidence|Decision|Lock)", rest)
    if footer_match:
        footer = rest[footer_match.start():]
    else:
        footer = ""

    annotated_turns = []
    for ta in turns:
        time_str = f"[{ta.timestamp}] " if ta.timestamp else ""
        turn_line = f"**{ta.speaker}:** {time_str}{ta.turn_text}"

        # Add slide annotation callout if explicit slide references exist
        if ta.referenced_slides:
            slide_descs = []
            for s_num in ta.referenced_slides:
                s_obj = slide_lookup.get(s_num)
                title = f": {s_obj.title}" if (s_obj and s_obj.title) else ""
                slide_descs.append(f"Slide {s_num}{title}")
            joined = ", ".join(slide_descs)
            annotation = f"\n> *[Referenced Presentation Deck: {joined}]*"
            annotated_turns.append(turn_line + "\n" + annotation)
        else:
            annotated_turns.append(turn_line)

    new_body = "\n\n" + "\n\n".join(annotated_turns)
    if header:
        return header + new_body + footer
    return new_body.strip() + footer
