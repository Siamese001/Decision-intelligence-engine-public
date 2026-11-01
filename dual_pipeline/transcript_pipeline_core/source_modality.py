"""
dual_pipeline/transcript_pipeline_core/source_modality.py

Single Source of Truth (SSOT) for transcript and audio ingestion modality classification.
Identifies whether incoming meeting material is:
1. AUDIO_ONLY_UNDIARIZED: Audio file without native speaker diarization or pre-existing transcript.
2. AUDIO_DIARIZED: Audio file with native speaker tags or confirmed speaker mappings.
3. TEXT_DIARIZED: Structured transcript with verified speaker labels.
4. TEXT_UNDIARIZED: Unsegmented or unattributed text transcript.
5. NOTES_ONLY: Meeting summary or Copilot notes (Outputs 02 & 03 only).
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Set

from dual_pipeline.transcript_stages_core.constants import _GENERIC_SPEAKER_PATTERN

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".m4v", ".mp4"}
_GENERIC_SPEAKER_REGEX = re.compile(r"\bSpeaker(?:[\s_\-]+([A-Z]|\d+))?\b", re.IGNORECASE)
_NATIVE_SPEAKER_PATTERNS = [
    re.compile(
        r"^(?:\*\*)?\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?[\]\)](?:\*\*)?:?\s*(?:\*\*)?([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\);]+?)(?::\*\*|\*\*:\s*|:\s+|\*\*\s*)",
        re.MULTILINE,
    ),
    re.compile(
        r"^\*\*([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\);]+?)\*\*\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?[\]\)]|\(?\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?[\)])?:?\s*",
        re.MULTILINE,
    ),
    re.compile(r"^\*\*([A-Z0-9][a-zA-Z0-9\s\-\u2013\u2014\.,/\(\);]+?)(?::\*\*|\*\*:\s*|:\s+)", re.MULTILINE),
    re.compile(r"^([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+):\s+\S", re.MULTILINE),
    re.compile(r"^([A-Z][a-zA-Z\s\.,\-\']+?)\s{2,}\d{1,2}:\d{2}", re.MULTILINE),
]


_GENERIC_SPEAKER_FILTER = re.compile(
    r"\b(?:speaker|voice|partner_advisory\s+speaker|partner_advisory\s+team|unknown|unattributed|anonymous|participant|speaker_\d+)\b",
    re.IGNORECASE,
)


def _find_dialogue_body(text: str) -> str:
    body = text
    for footer in ("\n## Ambiguity Lock Register", "\n## Attribution Summary", "\n# Ambiguity Lock Register"):
        if footer in body:
            body = body.split(footer, 1)[0]
    for divider in ("\n## Full transcript", "\n## Live Feed", "\n## Transcript", "\n---"):
        if divider in body:
            parts = body.split(divider, 1)
            if len(parts) > 1 and len(parts[1].strip()) > 50:
                body = parts[1]
                break
    return body


def _is_valid_speaker(spk: str, header_keys: set[str]) -> bool:
    spk_low = spk.rstrip(":").strip().lower()
    if len(spk_low.split()) > 10 or spk_low in header_keys:
        return False
    return not bool(_GENERIC_SPEAKER_FILTER.search(spk_low))


def extract_unique_speakers(text: str) -> set[str]:
    """Extracts unique speaker names from dialogue turns, ignoring metadata headers and generic labels."""
    check_text = _find_dialogue_body(text)
    header_keys = {
        "date", "time", "start time", "end time", "project", "status",
        "provenance", "platform reference", "platform", "attendees",
        "participants", "diarization", "diarization status", "classification",
        "confidentiality", "meeting", "meeting name", "topic", "objective",
        "meeting title", "title", "context", "executive summary",
        "recording gap", "natural pause", "pause", "gap", "session pause",
    }
    speakers = set()
    for pat in _NATIVE_SPEAKER_PATTERNS:
        for m in pat.finditer(check_text):
            spk = m.group(1)
            if _is_valid_speaker(spk, header_keys):
                speakers.add(spk.rstrip(":").strip().lower())
    return speakers


class SourceModality(str, Enum):
    """Governed source modality classifications driving pipeline routing and HITL gates."""
    AUDIO_ONLY_UNDIARIZED = "AUDIO_ONLY_UNDIARIZED"
    AUDIO_DIARIZED = "AUDIO_DIARIZED"
    TEXT_DIARIZED = "TEXT_DIARIZED"
    TEXT_UNDIARIZED = "TEXT_UNDIARIZED"
    TEAMS_NATIVE_TRANSCRIPT = "TEAMS_NATIVE_TRANSCRIPT"
    NOTES_ONLY = "NOTES_ONLY"
    TEXT_UNANCHORED_QUARANTINED = "TEXT_UNANCHORED_QUARANTINED"


class SourceTrustTier(str, Enum):
    """Governed 3-Tier epistemic trust hierarchy across all 16 source types."""
    TIER_1_ACOUSTIC_PRIMARY = "TIER_1_ACOUSTIC_PRIMARY"
    TIER_2_ENTERPRISE_ATTESTED = "TIER_2_ENTERPRISE_ATTESTED"
    TIER_3_UNANCHORED_QUARANTINED = "TIER_3_UNANCHORED_QUARANTINED"


def get_source_trust_tier(modality: SourceModality) -> SourceTrustTier:
    """Maps SourceModality to its governed epistemic trust tier.

    Invariant: NOTES_ONLY does not imply enterprise attestation.
    """
    if modality in (SourceModality.AUDIO_ONLY_UNDIARIZED, SourceModality.AUDIO_DIARIZED):
        return SourceTrustTier.TIER_1_ACOUSTIC_PRIMARY
    if modality == SourceModality.TEAMS_NATIVE_TRANSCRIPT:
        return SourceTrustTier.TIER_2_ENTERPRISE_ATTESTED
    return SourceTrustTier.TIER_3_UNANCHORED_QUARANTINED


def _is_notes_mode(meta: Dict[str, Any]) -> bool:
    return bool(
        meta.get("notes_mode")
        or meta.get("is_notes")
        or meta.get("source_type") in ("copilot_notes", "meeting_notes", "notes")
        or meta.get("pipeline_mode") in ("notes", "copilot")
        or meta.get("mode") in ("notes", "copilot")
    )


def _is_audio_derived(clean_ext: str, meta: Dict[str, Any], transcript_text: str) -> bool:
    src_type = str(meta.get("source_type", "")).upper()
    return bool(
        clean_ext in AUDIO_EXTENSIONS
        or meta.get("_from_audio_transcription")
        or src_type in ("AUDIO_TRANSCRIBED", "AUDIO_ONLY_UNDIARIZED", "LIVE_AUDIO", "AUDIO_DIARIZED")
        or "# Live Meeting Transcript:" in transcript_text
        or "RECORDING_IN_PROGRESS" in transcript_text
        or "Total chunks processed:" in transcript_text
        or "transcript_cache" in str(meta.get("source_path", ""))
        or "transcript_cache" in str(meta.get("input_path", ""))
        or "DIARIZED_ACOUSTIC" in transcript_text
    )


def is_text_modality(metadata: Optional[Dict[str, Any]] = None, transcript_text: str = "") -> bool:
    """Returns True if the source is a text modality rather than authentic audio."""
    meta = metadata or {}
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

    path_str = str(meta.get("source_path") or meta.get("input_path") or meta.get("file_path") or "")
    ext = ("." + path_str.rsplit(".", 1)[-1].lower()) if "." in path_str else ""
    if _is_audio_derived(ext, meta, transcript_text):
        return False

    return True


def extract_all_turn_speakers(text: str) -> set[str]:
    """Extracts all raw speaker names/labels found in turn-label positions in the dialogue."""
    check_text = _find_dialogue_body(text)
    header_keys = {
        "date", "time", "start time", "end time", "project", "status",
        "provenance", "platform reference", "platform", "attendees",
        "participants", "diarization", "diarization status", "classification",
        "confidentiality", "meeting", "meeting name", "topic", "objective",
        "meeting title", "title", "context", "executive summary",
        "recording gap", "natural pause", "pause", "gap", "session pause",
    }
    speakers = set()
    for pat in _NATIVE_SPEAKER_PATTERNS:
        for m in pat.finditer(check_text):
            spk = m.group(1).rstrip(":").strip()
            if spk.lower() not in header_keys and len(spk.split()) <= 6:
                speakers.add(spk)
    return speakers


def _collect_generic_labels(transcript_text: str, turn_speakers: set[str]) -> set[str]:
    generic_labels = {spk for spk in turn_speakers if _GENERIC_SPEAKER_PATTERN.search(spk)}
    if generic_labels:
        return generic_labels
    if not turn_speakers:
        body = _find_dialogue_body(transcript_text)
        return set(_GENERIC_SPEAKER_PATTERN.findall(body))
    return set()


def _resolve_generic_target(m_str: str, speaker_map: Dict[str, Any], normalized_map: Dict[str, Any]) -> Any:
    if m_str in speaker_map:
        return speaker_map[m_str]
    m_low = m_str.lower()
    if m_low in normalized_map:
        return normalized_map[m_low]
    suffix = m_str.split()[-1].lower() if " " in m_str else ""
    if suffix:
        for candidate_key in (suffix, f"speaker {suffix}", f"speaker_{suffix}", f"voice {suffix}"):
            if candidate_key in normalized_map:
                return normalized_map[candidate_key]
    return None


def _has_unmapped_generic_speakers(transcript_text: str, speaker_map: Dict[str, Any]) -> bool:
    turn_speakers = extract_all_turn_speakers(transcript_text)
    generic_labels = _collect_generic_labels(transcript_text, turn_speakers)
    if not generic_labels:
        return False
    normalized_map = {str(k).strip().lower(): v for k, v in speaker_map.items()}
    for m in generic_labels:
        m_str = str(m).strip()
        m_low = m_str.lower()
        target = _resolve_generic_target(m_str, speaker_map, normalized_map)
        if target is None:
            return True
        target_str = str(target).strip()
        if not target_str or _GENERIC_SPEAKER_PATTERN.fullmatch(target_str) or target_str.lower() == m_low:
            return True
    return False


def _extract_effective_speakers(unique_speakers: Set[str], speaker_map: Dict[str, Any]) -> Set[str]:
    effective = set(unique_speakers)
    for v in (speaker_map or {}).values():
        v_str = str(v).strip()
        if v_str and not _GENERIC_SPEAKER_FILTER.search(v_str.lower()) and not _GENERIC_SPEAKER_REGEX.search(v_str):
            effective.add(v_str.lower())
    return effective


def _classify_audio_modality(
    transcript_text: str,
    diar_status: str,
    generic_matches: int,
    speaker_map: Dict[str, Any],
    roster_count: int,
    unique_speakers: Set[str],
) -> SourceModality:
    effective = _extract_effective_speakers(unique_speakers, speaker_map)
    if generic_matches > 0 and _has_unmapped_generic_speakers(transcript_text, speaker_map):
        return SourceModality.AUDIO_ONLY_UNDIARIZED
    if roster_count >= 2 and len(effective) <= 1:
        return SourceModality.AUDIO_ONLY_UNDIARIZED

    has_resolved_diarization = (
        diar_status in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED")
        or "DIARIZED_ACOUSTIC_RESOLVED" in transcript_text
        or (
            diar_status in ("DIARIZED_ACOUSTIC", "DIARIZED")
            and bool(speaker_map)
            and not _has_unmapped_generic_speakers(transcript_text, speaker_map)
        )
    )
    if (
        has_resolved_diarization
        and (generic_matches == 0 or not _has_unmapped_generic_speakers(transcript_text, speaker_map))
        and (roster_count <= 1 or len(effective) >= 2)
    ):
        return SourceModality.AUDIO_DIARIZED
    if not has_resolved_diarization:
        return SourceModality.AUDIO_ONLY_UNDIARIZED
    return SourceModality.AUDIO_DIARIZED


def _classify_text_modality(
    transcript_text: str,
    clean_ext: str,
    diar_status: str,
    generic_matches: int,
    speaker_map: Dict[str, Any],
    roster_count: int,
    unique_speakers: Set[str],
) -> SourceModality:
    effective = _extract_effective_speakers(unique_speakers, speaker_map)
    if "started transcription" in transcript_text.lower():
        return SourceModality.TEAMS_NATIVE_TRANSCRIPT
    if generic_matches > 0 and _has_unmapped_generic_speakers(transcript_text, speaker_map):
        return SourceModality.TEXT_UNDIARIZED
    if roster_count >= 2 and len(effective) <= 1:
        return SourceModality.TEXT_UNDIARIZED
    has_resolved_diarization = (
        diar_status in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED")
        or "DIARIZED_ACOUSTIC_RESOLVED" in transcript_text
    )
    if has_resolved_diarization or len(effective) >= 2:
        return SourceModality.TEXT_DIARIZED
    return SourceModality.TEXT_UNDIARIZED


def classify_source_modality(
    transcript_text: str | Path = "",
    metadata: Optional[Dict[str, Any]] = None,
    input_ext: str = "",
) -> SourceModality:
    """Classifies the source modality of an ingestion candidate.

    Enforces that raw audio inputs lacking confirmed speaker attribution are
    classified as AUDIO_ONLY_UNDIARIZED, requiring rigorous pre-Output 01 HITL.
    Detects single-speaker collapse when multi-attendee sessions contain only
    a single speaker channel.
    """
    clean_ext = input_ext.strip().lower()
    if hasattr(transcript_text, "text"):
        transcript_text = str(transcript_text.text)
    elif isinstance(transcript_text, Path):
        clean_ext = clean_ext or transcript_text.suffix.strip().lower()
        transcript_text = ""
    elif transcript_text is None:
        transcript_text = ""
    else:
        transcript_text = str(transcript_text)

    meta = metadata or {}

    if _is_notes_mode(meta):
        return SourceModality.NOTES_ONLY

    generic_matches = len(_GENERIC_SPEAKER_REGEX.findall(transcript_text))
    speaker_map = meta.get("speaker_map") or {}
    diar_status = str(meta.get("diarization_status", "")).upper()
    if not diar_status:
        if "DIARIZED_ACOUSTIC_RESOLVED" in transcript_text:
            diar_status = "DIARIZED_ACOUSTIC_RESOLVED"
        elif "DIARIZED_ACOUSTIC" in transcript_text:
            diar_status = "DIARIZED_ACOUSTIC"

    roster = meta.get("roster") or meta.get("attendees") or []
    roster_count = len(roster) if isinstance(roster, list) else (
        sum(len(v) for v in roster.values() if isinstance(v, list)) if isinstance(roster, dict) else 0
    )
    unique_speakers = extract_unique_speakers(transcript_text)

    if _is_audio_derived(clean_ext, meta, transcript_text):
        return _classify_audio_modality(
            transcript_text=transcript_text,
            diar_status=diar_status,
            generic_matches=generic_matches,
            speaker_map=speaker_map,
            roster_count=roster_count,
            unique_speakers=unique_speakers,
        )

    return _classify_text_modality(
        transcript_text=transcript_text,
        clean_ext=clean_ext,
        diar_status=diar_status,
        generic_matches=generic_matches,
        speaker_map=speaker_map,
        roster_count=roster_count,
        unique_speakers=unique_speakers,
    )


def distinguish_audio_capture_type(
    transcript_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    input_ext: str = "",
) -> str:
    """Distinguishes live hardware capture from recorded audio batch ASR."""
    meta = metadata or {}
    clean_ext = input_ext.strip().lower()
    if (
        meta.get("is_live_capture")
        or meta.get("live_capture_active")
        or "RECORDING_IN_PROGRESS" in transcript_text
        or "# Live Meeting Transcript:" in transcript_text
        or str(meta.get("source_type", "")).upper() == "LIVE_AUDIO"
    ):
        return "LIVE_CAPTURE"
    if (
        clean_ext in AUDIO_EXTENSIONS
        or meta.get("_from_audio_transcription")
        or "transcript_cache" in str(meta.get("source_path", ""))
        or str(meta.get("source_type", "")).upper() in ("AUDIO_TRANSCRIBED", "AUDIO_RECORDED", "AUDIO_ONLY_UNDIARIZED", "AUDIO_DIARIZED")
    ):
        return "RECORDED_AUDIO_ASR"
    return "NON_AUDIO"
