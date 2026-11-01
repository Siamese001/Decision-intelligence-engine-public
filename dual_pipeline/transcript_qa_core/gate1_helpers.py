"""
dual_pipeline/transcript_qa_core/gate1_helpers.py

Shared parsing, turn extraction, and roster resolution utilities for Gate 1 QA.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

GENERIC_SPEAKER_PATTERN = re.compile(
    r"\b(?:Speaker(?:[\s_\-]+[A-Z\d]+)?|Voice(?:[\s_\-]+[A-Z\d]+)?|Speaker\s+Unknown|Unknown(?:\s+Speaker)?|Unattributed|Anonymous|Participant(?:[\s_\-]+[A-Z\d]+)?|SPEAKER_\d+)\b",
    re.IGNORECASE,
)
UNIVERSAL_TURN_PATTERN = re.compile(
    r"(?:^|\n)(?:#{1,3}\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)[\]\)]\s*\n+)?"
    r"(?:\*\*)?(?:\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)[\]\)])?(?:\*\*)?:?[^\S\r\n]*"
    r"(?:\*\*)?\[?([A-Za-z0-9][a-zA-Z0-9\s\-\.,/\(\)\'’ʼ]+?)\]?(?:\*\*)?(?:\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)\])?(?::\*\*|\*\*:?|:(?!\d))[^\S\r\n]*"
    r"(?:\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?(?:\s*[-–—]\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?)[\]\)])?:?[^\S\r\n]*(.*)",
    re.MULTILINE,
)
TURN_MATCH_PATTERN = UNIVERSAL_TURN_PATTERN
RESERVED_TURN_LABELS = {
    "date", "time", "project", "status", "platform reference",
    "attendees", "participants", "objective", "note", "transcript",
    "classification", "audiences", "referenced stakeholders",
    "referenced stakeholders (not in attendance)",
    "natural pause", "recording gap", "pause", "gap", "system",
    "partner_advisory", "google", "client", "apex_global", "apex_global energy", "apex", "partner", "vendor",
}
C19_AFFILIATION_MARKERS = ("client", "partner", "unknown")

MULTI_TURN_SPEAKER_PATTERN = re.compile(
    r"(?:^|\n)(?:\*\*([^*:\n]+):?\*\*|([A-Z][a-zA-Z0-9 _\-\.\'\(\)]+):)\s*\[\d{1,2}:\d{2}"
    r"|(?:^|\n)\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(?:\*\*)?([A-Z][a-zA-Z0-9 _\-\.\'\(\)]+):"
    r"|(?:^|\n)\*\*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*([^:\*\n]+):\*\*"
    r"|<v\s+([^>]+)>"
    r"|(?:^|\n)\*\*([A-Z][a-zA-Z0-9 _\-\.\'\(\)]+?)(?:\s*\([A-Z0-9\s/_\-]+\))?:\*\*\s*(?:\[\d{1,2}:\d{2})?",
    re.MULTILINE,
)


def _is_corporate_affiliation_fallback(speaker: str) -> bool:
    """True if speaker is an organization-grounded fallback (e.g. PartnerAdvisory Speaker, Client Speaker)."""
    s = speaker.strip().strip("[]*:").strip().lower()
    if s in (
        "partner_advisory speaker", "client speaker", "partner speaker", "vendor speaker",
        "external speaker", "partner_advisory team", "client team",
    ):
        return True
    if s.endswith(" speaker"):
        prefix = s[:-8].strip()
        if prefix in ("client", "partner_advisory", "partner", "vendor", "external"):
            return True
    return False


def _is_generic_speaker(speaker: str) -> bool:
    if _is_corporate_affiliation_fallback(speaker):
        return False
    s = speaker.strip().strip("[]*:").strip().lower()
    if s in (
        "speaker", "unknown", "unattributed", "unknown speaker", "speaker unknown",
        "participant", "anonymous", "voice", "unresolved",
        "unidentified", "unidentified speaker", "guest", "room",
    ):
        return True
    if "(room)" in s or "room mic" in s or s.startswith("collective:"):
        return True
    if s.endswith(" speaker"):
        prefix = s[:-8].strip()
        if prefix in ("guest", "room", "unknown"):
            return True
    if re.match(r"^(?:speaker|voice|participant)[\s_\-]+([a-z\d]+)$", s):
        return True
    if re.match(r"^speaker_\d+$", s, re.IGNORECASE):
        return True
    return False


def compute_released_text_coverage(f1_text: str) -> Dict[str, Any]:
    """Computes exact turn- and word-level named label coverage on released markdown dialogue."""
    turns = _extract_all_turns(f1_text)
    total_turns = len(turns)
    generic_turns = 0
    named_turns = 0
    generic_words = 0
    named_words = 0
    one_word_turns = 0
    le3_word_turns = 0

    for _, spk, txt in turns:
        words = txt.split()
        wc = len(words)
        if wc == 1:
            one_word_turns += 1
        if wc <= 3:
            le3_word_turns += 1
        if _is_generic_speaker(spk):
            generic_turns += 1
            generic_words += wc
        else:
            named_turns += 1
            named_words += wc

    total_words = generic_words + named_words
    turn_cov = round((named_turns / total_turns * 100.0), 2) if total_turns > 0 else 0.0
    word_cov = round((named_words / total_words * 100.0), 2) if total_words > 0 else 0.0

    return {
        "total_displayed_turns": total_turns,
        "one_word_turns": one_word_turns,
        "at_most_3_words": le3_word_turns,
        "generic_client_turns": generic_turns,
        "generic_turns": generic_turns,
        "named_turns": named_turns,
        "released_dialogue_words": total_words,
        "generic_client_words": generic_words,
        "generic_words": generic_words,
        "released_named_words": named_words,
        "named_turn_coverage_pct": turn_cov,
        "named_word_coverage_pct": word_cov,
        "released_coverage_gate_status": "PASS" if word_cov >= 95.0 else "FAIL",
    }


def _extract_all_turns(f1_text: str) -> List[Tuple[str, str, str]]:
    """Extract list of (timestamp, speaker, text) across all markdown turn formats."""
    body = f1_text
    transcript_section_match = re.search(
        r"(?:^|\n)##\s+(?:Forensic Transcript|Transcript|Forensic Dialogue)\b.*?\n",
        body,
        re.IGNORECASE,
    )
    if transcript_section_match:
        body = body[transcript_section_match.end():]
        if "## Ambiguity Lock Register" in body:
            body = body.split("## Ambiguity Lock Register", 1)[0]
        elif "## Ambiguity Register" in body:
            body = body.split("## Ambiguity Register", 1)[0]
    else:
        body = re.sub(
            r"(?:^|\n)##\s+Ambiguity(?:\s+Lock)?\s+Register\b.*?(?=\n## |\Z)",
            "",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )

    turns: List[Tuple[str, str, str]] = []
    for m in UNIVERSAL_TURN_PATTERN.finditer(body):
        ts = m.group(1) or m.group(2) or m.group(4) or m.group(5) or ""
        spk = m.group(3).strip()
        if not any(c.isalpha() for c in spk):
            continue
        spk_lower = spk.lower()
        if (
            spk_lower in RESERVED_TURN_LABELS
            or spk_lower.startswith("referenced")
            or spk_lower.startswith("attribution")
            or spk_lower.startswith("recording gap")
            or spk_lower.startswith("natural pause")
            or spk_lower.startswith("pause")
            or spk_lower.startswith("asr artifact")
        ):
            continue
        txt = m.group(6).strip() if m.group(6) else ""
        turns.append((ts, spk, txt))
    return turns


def _name_from_item(item: Any) -> Optional[str]:
    """Extract string name from dict or string entry."""
    if isinstance(item, dict):
        val = item.get("name")
        return str(val).strip() if val else None
    if item:
        s = str(item).strip()
        return s if s else None
    return None


def _extract_from_dict(d: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for members in d.values():
        if isinstance(members, list):
            names.extend([n for m in members if (n := _name_from_item(m))])
    return names


def _extract_roster_names(meta: Dict[str, Any], f1_text: Optional[str] = None) -> List[str]:
    """Extract list of expected attendee names from metadata roster or f1_text."""
    roster_raw = meta.get("roster") or meta.get("attendees") or []
    if isinstance(roster_raw, list):
        names = [n for item in roster_raw if (n := _name_from_item(item))]
    elif isinstance(roster_raw, dict):
        names = _extract_from_dict(roster_raw)
    elif isinstance(roster_raw, str):
        names = [cleaned for part in re.split(r"[;,]", roster_raw) if (cleaned := re.sub(r"\(.*?\)", "", part).strip())]
    else:
        names = []
    if not names and f1_text and (part_match := re.search(r"\*\*Participants:\*\*\s*(.*?)(?:---|\n##|\n\*\*|\Z)", f1_text, re.DOTALL)):
        for line in part_match.group(1).splitlines():
            line_str = line.strip()
            if not line_str.startswith(("-", "*")):
                continue
            content = re.sub(r"^[\-\*]\s*", "", line_str)
            content = re.sub(r"\(.*?\)", "", content).strip()
            parts = re.split(r"\s+[\-\u2013\u2014]+\s+", content)
            name_candidate = parts[0].strip() if parts else content
            clean_p = re.sub(r"[\-\*#]", "", name_candidate).strip()
            if clean_p and clean_p.lower() not in RESERVED_TURN_LABELS and not clean_p.lower().startswith("referenced stakeholders"):
                names.append(clean_p)
    return names


def _extract_distinct_turn_speakers(f1_text: str) -> set[str]:
    """Extract set of clean lowercase speaker labels across all turn formats."""
    matches = MULTI_TURN_SPEAKER_PATTERN.findall(f1_text)
    speakers: set[str] = set()
    for m in matches:
        spk = next((item for item in m if item), "").strip().lower()
        if spk and spk not in RESERVED_TURN_LABELS:
            speakers.add(spk)
    return speakers


def _is_single_speaker_session(f1_text: str, roster_names: List[str]) -> bool:
    """Check if transcript represents a single-participant session."""
    if len(roster_names) > 1:
        return False
    distinct_spk = _extract_distinct_turn_speakers(f1_text)
    if not distinct_spk or all(_is_generic_speaker(s) for s in distinct_spk):
        return False
    return len(distinct_spk) <= 1


def is_text_modality(metadata: Optional[Dict[str, Any]] = None, transcript_text: str = "") -> bool:
    """Returns True if the source is a text modality rather than authentic audio."""
    meta = metadata or {}
    if (
        meta.get("has_acoustic_diarization")
        or str(meta.get("diarization_status") or "").upper() == "DIARIZED_ACOUSTIC_RESOLVED"
        or str(meta.get("canonical_modality") or "").upper() == "AUDIO"
        or meta.get("is_audio")
        or meta.get("_from_audio_transcription")
        or meta.get("audio_path")
    ):
        return False
    mod = str(meta.get("source_modality") or meta.get("source_type") or "").upper()
    if mod in {
        "AUDIO_ONLY_UNDIARIZED", "AUDIO_DIARIZED", "LIVE_AUDIO", "AUDIO_RECORDED", "AUDIO_TRANSCRIBED",
    }:
        return False
    if mod in {
        "TEXT_ONLY", "VTT", "RAW_TEAMS", "TEAMS_NATIVE_TRANSCRIPT",
        "TEXT_DIARIZED", "TEXT_UNDIARIZED", "DIARIZED_TEXT", "UNDIARIZED_TEXT",
        "NOTES_ONLY", "COPILOT_NOTES", "MEETING_NOTES", "DOCUMENT",
    }:
        return True

    path_str = str(meta.get("source_path") or meta.get("input_path") or meta.get("file_path") or meta.get("audio_path") or "")
    ext = ("." + path_str.rsplit(".", 1)[-1].lower()) if "." in path_str else ""
    if ext in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".mp4", ".m4v"} or meta.get("_from_audio_transcription") or meta.get("is_audio"):
        return False
    if "# Live Meeting Transcript:" in transcript_text or "RECORDING_IN_PROGRESS" in transcript_text or "DIARIZED_ACOUSTIC" in transcript_text:
        return False

    return True


def check_transcript_formatting_and_provenance(
    f1_text: str,
    meta: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Validates Output 01 turn formatting integrity, label syntax, and modality provenance."""
    failures: List[str] = []

    # 1. Syntactic turn header and asterisk duplication checks
    if re.search(r"\*\*###\s*\[", f1_text):
        failures.append("Output 1 contains corrupted header syntax: '**### ['. Turn headers must use unbolded markdown headings '### [HH:MM:SS]'.")
    if re.search(r"\*\*[^*:\n]+?:\*\*\*\*+", f1_text):
        failures.append("Output 1 contains corrupted duplicated asterisks in speaker label (e.g. '**Speaker:****').")
    if re.search(r"###\s*\[\d{1,2}:\*\*", f1_text):
        failures.append("Output 1 contains split timestamp/speaker header corruption (e.g. '### [00:**').")

    # 2. Modality provenance gate: text modalities must never claim acoustic verification
    if is_text_modality(meta, f1_text):
        forbidden_acoustic_phrases = [
            "Multi-Tier Acoustic Isolation Protocol",
            "forensic acoustic identification",
            "resolved via acoustic profile",
        ]
        for phrase in forbidden_acoustic_phrases:
            if phrase.lower() in f1_text.lower():
                failures.append(
                    f"Modality Provenance Gate: Output 1 is derived from a text modality but claims unsupported acoustic verification: '{phrase}'."
                )

    return len(failures) == 0, failures

