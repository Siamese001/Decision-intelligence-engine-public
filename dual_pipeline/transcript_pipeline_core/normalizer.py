"""
Text normalizers and file utilities for transcript processing.
Supports WebVTT (.vtt), SRT (.srt), CoPilot JSON (.json), Microsoft Teams docx,
and varied third-party text transcript formats.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from dual_pipeline.transcript_pipeline_core.source_spans import SourceSpan, NormalizedTurn, NormalizedTranscript, merge_spans_into_turns

from .format_parsers import (
    format_timestamp,
    parse_copilot_json,
    parse_vtt_or_srt,
)

logger = logging.getLogger(__name__)

# Backwards-compatible aliases
_format_timestamp = format_timestamp
_parse_vtt_or_srt = parse_vtt_or_srt
_parse_copilot_json = parse_copilot_json

_P_BRACKET_PRE = re.compile(
    r"^(?:#{1,4}\s+)?(?:\*\*)?\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)[\]\)](?:\*\*)?:?\s*(?:\*\*)?([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\)\'’ʼ;]+?)(?:\*\*)?(?::(?:\*\*)?\s*(.*)|(?:\*\*)?\s*)$",
    re.IGNORECASE,
)
_P_SPEAKER_PRE = re.compile(
    r"^(?:\*\*)?([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\)\'’ʼ;]+?)(?::\*\*|\*\*:|:\s*\*\*|\*\*:?|:)?\s*[\[\(](\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)[\]\)]:?\s*(.*)",
    re.DOTALL | re.IGNORECASE,
)
_P_TEAMS = re.compile(
    r"^([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\)\'’ʼ;]+?)\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)\s*(.*)",
    re.DOTALL | re.IGNORECASE,
)
_P_PLAIN_COLON = re.compile(
    r"^(?:\*\*)?([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\)\'’ʼ;]+?)(?:\*\*)?:\s*(.*)", re.DOTALL
)


def _sanitize_filename(name: str) -> Union[str, NormalizedTranscript]:
    """Remove filesystem-unsafe characters for Windows/POSIX compatibility."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).replace(" ", "_")


def _normalize_speaker_name(name: str) -> str:
    """Normalize speaker names, cleaning corporate suffixes and flipping 'Lastname, Firstname'."""
    name = name.strip().strip("*_")
    # Strip trailing corporate/metadata annotations like " — PartnerAdvisory", " — NEE", " - PartnerAdvisory", etc.
    name = re.sub(
        r"\s*[\u2013\u2014\-]\s*(?:PartnerAdvisory|NEE|Apex Global|APEX|Apex Global Enterprises|Client|Vendor)(?:\s*;.*)?$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Strip parenthetical annotations like "(surname unverified)", "(IM)", "(conference-room endpoint)"
    name = re.sub(
        r"\s*\([^)]*(?:unverified|inferred|screenshot|endpoint|IM)[^)]*\)",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Strip remaining trailing annotations like "— NEE; not in screenshot" if any (require surrounding whitespace for ASCII hyphen to preserve hyphenated names like Al-Mansoor)
    name = re.sub(r"(?:\s*[\u2013\u2014]\s*|\s+\-\s+).*$", "", name)
    name = name.strip()
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and all(parts):
            name = f"{parts[1]} {parts[0]}"
    return name.strip()


def _extract_docx_text(path: Path) -> Union[str, NormalizedTranscript]:
    """Extract paragraph text from docx via XML parsing."""
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(path) as z:
        xml_content = z.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        paras = []
        for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = [
                node.text
                for node in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
                if node.text
            ]
            if texts:
                paras.append("".join(texts))
        return "\n".join(paras)


def _normalize_teams_text(raw_text: str) -> Union[str, NormalizedTranscript]:
    """Normalize raw Microsoft Teams docx export turns into timestamped turns."""
    return normalize_raw_input_text(raw_text)


def _clean_body(val: Optional[str]) -> Union[str, NormalizedTranscript]:
    """Clean turn body text."""
    return val.lstrip(":").strip() if val else ""


def _match_timestamped_turn(line: str) -> Optional[Tuple[str, Optional[str], str]]:
    """Match timestamped turn patterns (bracket, speaker-prefixed, or teams)."""
    mb = _P_BRACKET_PRE.match(line)
    if mb and len(mb.group(2).split()) <= 10:
        return mb.group(2).strip(), format_timestamp(mb.group(1).strip()), _clean_body(mb.group(3))

    mc = _P_SPEAKER_PRE.match(line)
    if mc and len(mc.group(1).split()) <= 10:
        return mc.group(1).strip(), format_timestamp(mc.group(2).strip()), _clean_body(mc.group(3))

    ma = _P_TEAMS.match(line)
    if ma and len(ma.group(1).split()) <= 10:
        return ma.group(1).strip(), format_timestamp(ma.group(2).strip()), _clean_body(ma.group(3))

    return None


def _match_plain_colon(line: str) -> Optional[Tuple[str, Optional[str], str]]:
    """Match plain speaker: text without timestamp."""
    if line.startswith("http") or line.startswith("#") or line.startswith("|") or line.startswith(">"):
        return None
    if re.match(r"^\d{1,2}:\d{2}(?::\d{2})?$", line.strip()):
        return None
    md = _P_PLAIN_COLON.match(line)
    if md and len(md.group(1).split()) <= 8:
        cand_spk = md.group(1).strip()
        cand_clean = cand_spk.strip("*_ \t").lower()
        from dual_pipeline.transcript_stages_core.constants import METADATA_HEADER_KEYS
        if cand_clean in METADATA_HEADER_KEYS or any(cand_clean.startswith(k) for k in METADATA_HEADER_KEYS):
            return None
        return cand_spk, None, _clean_body(md.group(2))
    return None


def _match_turn_line(line_clean: str) -> Optional[Tuple[str, Optional[str], str]]:
    """Match line against known transcript patterns."""
    return _match_timestamped_turn(line_clean) or _match_plain_colon(line_clean)


def _try_parse_structured_format(clean_text: str, ext_lower: str) -> Optional[str]:
    """Parse JSON or WebVTT/SRT if detected."""
    if ext_lower == ".json" or (clean_text.startswith(("{", "[")) and clean_text.endswith(("}", "]"))):
        parsed_json = parse_copilot_json(clean_text)
        if parsed_json != clean_text:
            return parsed_json

    if ext_lower in (".vtt", ".srt") or clean_text.startswith("WEBVTT") or "-->" in clean_text[:500]:
        parsed_vtt = parse_vtt_or_srt(clean_text)
        if parsed_vtt != clean_text:
            return parsed_vtt

    return None


class _TextTurnBuilder:
    """Helper class to accumulate and segment text dialogue turns."""

    def __init__(self) -> None:
        self.spans: List[SourceSpan] = []
        self.curr_spk: Optional[str] = None
        self.curr_time: Optional[str] = None
        self.curr_body: List[str] = []
        self.turn_counter: int = 0

    def handle_transcription_marker(self) -> None:
        self.flush()
        self.curr_spk, self.curr_time, self.curr_body = None, None, []

    def add_match(self, spk: str, ts: Optional[str], body: str, line_index: int) -> None:
        self.flush()
        self.curr_spk = _normalize_speaker_name(spk)
        self.curr_time = ts
        self.curr_body = [body] if body else []
        self.turn_counter += 1

    def append_body(self, line: str) -> None:
        if self.curr_spk:
            self.curr_body.append(line)

    def flush(self) -> None:
        if self.curr_spk and self.curr_body:
            self.spans.append(SourceSpan(
                speaker_label=self.curr_spk,
                start_time=self.curr_time,
                text=" ".join(self.curr_body).strip()
            ))

def _parse_line_turns(lines: List[str]) -> List[SourceSpan]:
    """Scan lines and assemble turns."""
    builder = _TextTurnBuilder()
    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("#") or line_clean.startswith("|") or line_clean.startswith(">"):
            continue

        low = line_clean.lower()
        if "started transcription" in low or "stopped transcription" in low:
            builder.handle_transcription_marker()
            continue
        if "meeting recording" in low or re.match(r"^\d+m\s+\d+s$", low):
            continue
        if re.match(
            r"^(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
            low,
        ):
            continue

        matched = _match_turn_line(line_clean)
        if matched is not None:
            spk, ts, body = matched
            builder.add_match(spk, ts, body, line_index=i)
        else:
            builder.append_body(line_clean)

    builder.flush()
    return builder.spans


def _is_raw_transcript_format(clean: str) -> bool:
    if "### [" in clean or clean.startswith("WEBVTT") or "-->" in clean[:500]:
        return True
    if len(re.findall(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", clean)) >= 3:
        return True
    return len(re.findall(r"^[A-Z][a-zA-Z\s,]{2,30}\s+\d{1,2}:\d{2}(?:\s*[AP]M)?$", clean, re.MULTILINE)) >= 2


def is_copilot_notes(raw_text: str) -> bool:
    """
    Detect whether the raw input text represents pre-processed Copilot notes,
    AI meeting summaries, or structured speaker-topic bullet notes rather than
    a raw timestamped transcript.
    """
    if not raw_text or not raw_text.strip():
        return False

    clean = raw_text.strip()
    if _is_raw_transcript_format(clean):
        return False

    speaker_headers = len(re.findall(r"^#{2,5}\s+\*\*([A-Za-z0-9\s\.\,\'\-]+?)\*\*\s*$", clean, re.MULTILINE))
    topic_bullets = len(re.findall(r"^\s*[-*]\s+\*\*([^\n\:]+?)\*\*:\s+", clean, re.MULTILINE))
    standard_bullets = len(re.findall(r"^\s*[-*]\s+", clean, re.MULTILINE))
    has_notes_kw = bool(
        re.search(
            r"(?:^|[\r\n])(?:\#+\s*)?(?:copilot\s+(?:notes|summary|recap)|meeting\s+(?:notes|summary|recap)|key\s+takeaways|discussion\s+summary)",
            clean,
            re.IGNORECASE,
        )
    )

    if speaker_headers >= 1 and (topic_bullets >= 1 or standard_bullets >= 2):
        return True
    if has_notes_kw and (standard_bullets >= 2 or topic_bullets >= 2):
        return True
    return bool(topic_bullets >= 3)


def normalize_copilot_notes(raw_text: str) -> Union[str, NormalizedTranscript]:
    """
    Standardize and clean pre-processed Copilot notes or meeting summaries.
    Ensures uniform line breaks, strips trailing whitespace, and normalizes headers.
    """
    if not raw_text:
        return ""
    lines = [line.rstrip() for line in raw_text.replace("\r\n", "\n").splitlines()]
    cleaned_lines: List[str] = []
    consecutive_blank = 0
    for line in lines:
        if not line:
            consecutive_blank += 1
            if consecutive_blank <= 1:
                cleaned_lines.append("")
        else:
            consecutive_blank = 0
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip() + "\n"


def _is_document_source(metadata: Optional[Dict[str, Any]]) -> bool:
    if not metadata:
        return False
    return bool(metadata.get("document") or metadata.get("is_document") or metadata.get("source_type") == "document")


def _is_notes_input(metadata: Optional[Dict[str, Any]], clean_text: str) -> bool:
    if metadata and (
        metadata.get("notes_mode")
        or metadata.get("is_notes")
        or metadata.get("source_type") in ("copilot_notes", "notes", "meeting_notes")
        or metadata.get("outputs") == ["02", "03"]
    ):
        return True
    return is_copilot_notes(clean_text)


def _is_preresolved_hitl_transcript(clean_text: str) -> bool:
    markers = ("HITL-Resolved Transcript", "Ambiguity Lock Register", "Forensic Attribution Notice", "Live Meeting Transcript", "DIARIZED_ACOUSTIC")
    if "# " in clean_text and any(m in clean_text for m in markers):
        return True
    return "**Date:**" in clean_text and ("**Participants:**" in clean_text or "**Attendees:**" in clean_text)


def normalize_raw_input_text(
    raw_text: str,
    input_ext: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Union[str, NormalizedTranscript]:
    """
    Universal normalizer for any raw transcript input:
    - WebVTT / SRT (.vtt, .srt)
    - CoPilot JSON / Teams JSON (.json)
    - Microsoft Teams docx text (Speaker 10:15 AM or Speaker 10:15)
    - Bracketed turns ([10:15] Speaker: Text)
    - Markdown turns (**Speaker:** [10:15] Text)
    - Unformatted dialogue (Speaker: Text)
    - Processed Copilot / Meeting notes (#### **Speaker** / - **Topic**: Text)
    """
    clean_text = raw_text.strip()
    if not clean_text:
        raise ValueError("Fail-Fast: Cannot normalize empty transcript input text")

    if _is_notes_input(metadata, clean_text):
        return normalize_copilot_notes(clean_text)

    if _is_document_source(metadata):
        return clean_text

    # Never mangle pre-resolved HITL transcripts that already contain headers or lock registers
    if _is_preresolved_hitl_transcript(clean_text):
        if "### [" in clean_text or _parse_line_turns(clean_text.splitlines()):
            return raw_text
        raise ValueError("Fail-Fast: Pre-resolved transcript metadata found, but file contains zero dialogue turns")

    structured = _try_parse_structured_format(clean_text, input_ext.lower())
    if structured is not None:
        return structured

    if "### [" in clean_text and "**" in clean_text:
        return raw_text

    turns_spans = _parse_line_turns(clean_text.splitlines())
    if not turns_spans:
        raise ValueError("Fail-Fast: Normalizer failed to parse any dialogue turns from input text. Refusing to emit unparsed text.")

    turns = merge_spans_into_turns(turns_spans)
    text_out = "\n".join(f"### [{t.start_time or '00:00:00'}]\n\n**{t.speaker or 'Unknown'}:** {t.text}\n" for t in turns)
    return NormalizedTranscript(text=text_out, turns=turns)
