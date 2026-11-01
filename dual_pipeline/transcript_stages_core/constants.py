"""Constants, regexes, and prompt loading utilities for transcript stages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY

# Namespaces
CLIENT_SAFE_NAMESPACE = "CLIENT_SAFE"
AGENTIC_LEVERAGE_NAMESPACE = "AGENTIC_LEVERAGE"
INTERNAL_ADVISORY_NAMESPACE = "INTERNAL_ADVISORY"

AGENTIC_EXTRACTION_PROMPT = CANONICAL_PROMPT_REGISTRY.get_prompt("AGENTIC_EXTRACTION")


# Passive / observation verbs that must never start an action item
_PASSIVE_VERBS = re.compile(
    r"^\s*(Acknowledge|Note|Consider|Monitor|Be aware|Discuss|"
    r"Explore|Observe|Reflect|Recognize|Understand|Appreciate|Keep in mind)",
    re.IGNORECASE,
)

from dual_pipeline.core import _GENERIC_SPEAKER_PATTERN

# PyAnnote specific acoustic cluster pattern (e.g. SPEAKER_00)
_PYANNOTE_SPEAKER_PATTERN = re.compile(
    r"\bSPEAKER_\d+\b",
    re.IGNORECASE,
)

# Metadata and document structure header labels that must not be counted as speaker turns
_BASE_METADATA_HEADER_KEYS = {
    "date", "time", "project", "status", "platform", "platform reference",
    "attendees", "participants", "objective", "note", "notes", "transcript",
    "classification", "audiences", "meeting title", "title", "context",
    "executive summary", "discussion priorities", "action items", "decisions",
    "diarization", "diarization status", "ambiguity lock register",
    "ambiguity register", "attribution summary", "natural pause",
    "recording gap", "pause", "gap", "session pause", "referenced stakeholders",
    "meeting date", "recorded transcript range", "source", "editorial note",
    "attendee roster", "full transcript", "additional voices",
    "additional voices in the source transcript",
    "speaker labels", "speaker tags", "speaker attribution",
}
_PROSE_COLON_HEADER_KEYS = {
    "purpose", "interviewee", "interviewer", "core pain", "top feature requests",
    "key adoption risk", "training expectation", "findings", "findings and analysis",
    "analysis", "summary", "background", "overview", "recommendation", "recommendations",
    "problem", "solution", "challenge", "challenges", "risk", "risks", "outcome",
    "outcomes", "goal", "goals", "question", "answer", "takeaways", "key takeaways",
    "observation", "observations", "pain points", "feature requests", "agenda", "slide",
    "timestamp", "topic", "category", "impact", "priority", "scope", "owner", "target",
}
METADATA_HEADER_KEYS = _BASE_METADATA_HEADER_KEYS | _PROSE_COLON_HEADER_KEYS

_NAME_PARTICLES = frozenset({"de", "del", "la", "las", "los", "van", "von", "da", "dos", "das", "al", "bin"})


def is_valid_colon_speaker_label(spk: str) -> bool:
    """Validate whether a raw 'Label:' prefix represents a plausible human speaker name."""
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import is_blacklisted_attendee

    clean = re.sub(r"\s*\(.*?\)", "", spk.strip().strip("[]*:")).strip()
    if not clean or len(clean.split()) > 4 or is_blacklisted_attendee(clean):
        return False
    if clean.lower() in METADATA_HEADER_KEYS or _GENERIC_SPEAKER_PATTERN.search(clean):
        return False
    if re.match(
        r"^(?:speaker|unknown|unattributed|interviewee|interviewer|participant|moderator|host|narrator)(?:[_\-\s]+[a-z0-9]+)?$",
        clean,
        re.IGNORECASE,
    ):
        return False
    tokens = clean.split()
    return all(t[0].isupper() or t.lower() in _NAME_PARTICLES for t in tokens if t)

# Speaker-label patterns that indicate native diarization
_NATIVE_DIARIZATION_PATTERN = re.compile(
    r"(?:^|\n)(?:\*\*)?(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?([A-Z][a-zA-Z0-9\s\-\.\'\u2013\u2014;/()]+?)(?::\*\*|\*\*:|:|\*\*)(?:\n|\s+\S)",
    re.MULTILINE,
)


def _is_clean_dialogue_line(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith(("#", "---", "===", ">")):
        return False
    return not (s.startswith(("- ", "* ")) and not re.search(r"\[\d{1,2}:\d{2}", s))


def _is_valid_native_speaker(spk: str) -> bool:
    spk_clean = re.sub(r"\s*\(.*?\)", "", spk.strip().strip("[]*:")).strip().lower()
    if not spk_clean or spk_clean in METADATA_HEADER_KEYS or _GENERIC_SPEAKER_PATTERN.search(spk):
        return False
    return any(c.isalpha() for c in spk_clean)


def count_native_diarized_turns(transcript: str) -> int:
    """Count authentic native speaker dialogue turns, excluding headings, metadata, and generic labels."""
    if not transcript:
        return 0
    clean_text = "\n".join(l for l in transcript.splitlines() if _is_clean_dialogue_line(l))
    return sum(
        1 for m in _NATIVE_DIARIZATION_PATTERN.finditer(clean_text)
        if _is_valid_native_speaker(m.group(1))
    )

# Stage template paths (backward compatibility)
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_STAGES_DIR = _BASE_DIR / "config" / "prompts" / "synthesis" / "stages"
_STAGE_PATHS = {
    "forensic": _STAGES_DIR / "01_forensic_normalizer.md",
    "teams_forensic": _STAGES_DIR / "01_teams_raw_normalizer.md",
    "audio_forensic": _STAGES_DIR / "01_forensic_normalizer.md",
    "recap":    _STAGES_DIR / "02_executive_recap.md",
    "graph":    _STAGES_DIR / "03_graph_extractor.md",
}

_STAGE_KEY_MAP = {
    "forensic": "STAGE_01_FORENSIC",
    "teams_forensic": "STAGE_01_TEAMS_RAW",
    "audio_forensic": "AUDIO_STREAM_TRANSCRIPT_SYNTHESIS",
    "audio_stream": "AUDIO_STREAM_TRANSCRIPT_SYNTHESIS",
    "recap": "STAGE_02_RECAP",
    "graph": "STAGE_03_GRAPH",
}


def _load_stage_prompt(stage_key: str, archetype: Optional[Any] = None) -> str:
    """Load an archetype-adaptive stage prompt template via canonical OutputContracts or registry."""
    from dual_pipeline.prompts import (
        OUTPUT1_CONTRACT,
        OUTPUT2_CONTRACT,
        OUTPUT3_CONTRACT,
        MeetingArchetype,
    )
    arch = MeetingArchetype.from_string(archetype) if archetype else MeetingArchetype.GENERAL
    if stage_key == "recap":
        return OUTPUT2_CONTRACT.compile_stage_prompt(arch)
    if stage_key == "graph":
        return OUTPUT3_CONTRACT.compile_stage_prompt(arch)
    if stage_key == "forensic":
        return OUTPUT1_CONTRACT.compile_stage_prompt(arch)

    registry_key = _STAGE_KEY_MAP.get(stage_key, stage_key)
    return CANONICAL_PROMPT_REGISTRY.get_prompt(registry_key)


