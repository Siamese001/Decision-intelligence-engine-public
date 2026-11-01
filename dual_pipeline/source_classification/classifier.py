"""dual_pipeline/source_classification/classifier.py

Classifies source content and metadata into structured SourceProfiles.
Enforces orthogonal axes, anti-injection hygiene, deterministic marker evaluation,
and safe handling of mixed sections, unknown authors, and uncorroborated designations.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.source_classification.capability_registry import (
    get_audio_extensions,
    get_correspondence_extensions,
    get_cue_extensions,
    get_document_extensions,
    get_structured_extensions,
    get_visual_extensions,
)
from dual_pipeline.source_classification.models import (
    ApprovalState,
    ClassificationBasis,
    MediaModality,
    SemanticFamily,
    SourceProfile,
    SourceSection,
)

_AUDIO_EXTENSIONS = get_audio_extensions()
_CORRESPONDENCE_EXTENSIONS = get_correspondence_extensions()
_VISUAL_EXTENSIONS = get_visual_extensions()
_STRUCTURED_EXTENSIONS = get_structured_extensions()
_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".text", ".markdown"} | get_cue_extensions())
_DOCUMENT_EXTENSIONS = frozenset(
    (get_document_extensions() | {".doc", ".rtf", ".odt"})
    - _TEXT_EXTENSIONS
    - _VISUAL_EXTENSIONS
    - _STRUCTURED_EXTENSIONS
    - _CORRESPONDENCE_EXTENSIONS
)

# Regexes for deterministic markers
_TRANSCRIPT_TIMESTAMP_RE = re.compile(r"(?:\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?|WEBVTT|\b\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}\.\d{3}\b)")
_EMAIL_HEADER_RE = re.compile(r"(?im)^(?:From|To|Subject|Date|Message-ID):\s+[^\r\n]+")
_SOW_CONTRACT_RE = re.compile(r"(?i)\b(?:statement of work|master services agreement|confidentiality agreement|non-disclosure agreement|sla terms)\b")
_EXECUTED_SIGNATURE_RE = re.compile(r"(?im)^\s*(?:signed\s+by|signature:|in\s+witness\s+whereof|duly\s+authorized|executed\s+by)\b")
_DRAFT_MARKER_RE = re.compile(r"(?i)\b(?:draft|working draft|preliminary|unapproved|pending review)\b")
_MINUTES_RECAP_RE = re.compile(r"(?i)\b(?:meeting summary|key takeaways|action items|executive summary|copilot notes|meeting minutes|ai summary)\b")
_WORKING_SOURCE_RE = re.compile(r"(?i)\b(?:architecture rfc|sprint plan|system design document|implementation plan|technical design doc|product requirements doc|backlog review)\b")
_RESEARCH_INTEL_RE = re.compile(r"(?i)\b(?:whitepaper|market analysis|competitive benchmark|industry study|research report|literature review)\b")


def _sanitize_source_text(content: str) -> str:
    """Strip or ignore adversarial prompt injection attempts seeking to alter pipeline instructions."""
    return re.sub(
        r"(?i)(?:system override|ignore (?:all )?previous instructions|set authority_rank\s*=\s*\d+|elevate trust|grant enterprise attestation)",
        "[UNTRUSTED_INSTRUCTION_STRIPPED]",
        content,
    )


def _detect_media_modality(clean_ext: str, content: str) -> MediaModality:
    if clean_ext in _AUDIO_EXTENSIONS:
        return MediaModality.AUDIO
    if clean_ext in _VISUAL_EXTENSIONS:
        return MediaModality.VISUAL
    if clean_ext in _STRUCTURED_EXTENSIONS:
        return MediaModality.STRUCTURED
    if clean_ext in _DOCUMENT_EXTENSIONS:
        return MediaModality.DOCUMENT
    if clean_ext in _TEXT_EXTENSIONS:
        return MediaModality.TEXT
    if clean_ext in _CORRESPONDENCE_EXTENSIONS:
        return MediaModality.DOCUMENT
    return MediaModality.TEXT


_SECTION_HEADING_RULES: Tuple[Tuple[Tuple[str, ...], SemanticFamily], ...] = (
    (("sow", "contract"), SemanticFamily.COMMERCIAL_CONTRACT),
    (("notes", "minutes", "summary"), SemanticFamily.DERIVED_RECORD),
    (("design", "plan"), SemanticFamily.WORKING_SOURCE),
    (("transcript",), SemanticFamily.RAW_TRANSCRIPT),
)


def _classify_section_heading(line_clean: str) -> Optional[SemanticFamily]:
    if not line_clean.startswith(("#", "==", "--")):
        return None
    for cues, family in _SECTION_HEADING_RULES:
        if any(c in line_clean for c in cues):
            return family
    return None


def _build_section(sec_id: int, family: SemanticFamily, start_line: int, end_line: int) -> SourceSection:
    return SourceSection(
        section_id=f"sec_{sec_id}",
        semantic_family=family,
        start_locator=f"line:{start_line}",
        end_locator=f"line:{end_line}",
        authority_rank=1 if family == SemanticFamily.COMMERCIAL_CONTRACT else 5,
    )


def _detect_sections(content: str) -> List[SourceSection]:
    """Detect demarcated sections with distinct semantic families in composite documents."""
    sections: List[SourceSection] = []
    lines = content.splitlines()
    curr_family: Optional[SemanticFamily] = None
    curr_start = 0

    for idx, line in enumerate(lines):
        detected_fam = _classify_section_heading(line.strip().lower())
        if detected_fam and detected_fam != curr_family:
            if curr_family is not None:
                sections.append(_build_section(len(sections) + 1, curr_family, curr_start + 1, idx))
            curr_family = detected_fam
            curr_start = idx

    if curr_family is not None and curr_start < len(lines):
        sections.append(_build_section(len(sections) + 1, curr_family, curr_start + 1, len(lines)))

    return sections


_EXPLICIT_FAMILY_ALIASES: Dict[str, SemanticFamily] = {
    "contract": SemanticFamily.COMMERCIAL_CONTRACT,
    "sow": SemanticFamily.COMMERCIAL_CONTRACT,
    "notes": SemanticFamily.DERIVED_RECORD,
    "copilot_notes": SemanticFamily.DERIVED_RECORD,
    "meeting_notes": SemanticFamily.DERIVED_RECORD,
    "transcript": SemanticFamily.RAW_TRANSCRIPT,
    "audio_transcribed": SemanticFamily.RAW_TRANSCRIPT,
    "email": SemanticFamily.CORRESPONDENCE,
}


def _resolve_author(meta: Dict[str, Any], sanitized_text: str) -> Optional[str]:
    author = meta.get("author") or meta.get("organizer") or meta.get("sender")
    if not author:
        match_from = re.search(r"(?im)^From:\s*([^\r\n<]+)", sanitized_text)
        if match_from:
            author = match_from.group(1).strip()
    if author in ("Corporate Communications", "UNKNOWN", ""):
        return None
    return author


def _parse_explicit_family(meta: Dict[str, Any]) -> Optional[SemanticFamily]:
    raw_explicit = (
        meta.get("explicit_semantic_family")
        or meta.get("source_family")
        or meta.get("source_type")
    )
    if not raw_explicit:
        return None
    cand = str(raw_explicit).lower()
    if cand in _EXPLICIT_FAMILY_ALIASES:
        return _EXPLICIT_FAMILY_ALIASES[cand]
    try:
        return SemanticFamily(cand)
    except ValueError:
        return None


def _resolve_contract_approval(sanitized_text: str) -> Tuple[ApprovalState, int]:
    if _EXECUTED_SIGNATURE_RE.search(sanitized_text):
        return ApprovalState.EXECUTED, 1
    if _DRAFT_MARKER_RE.search(sanitized_text):
        return ApprovalState.DRAFT, 4
    return ApprovalState.IN_REVIEW, 3


def _resolve_content_signature_family(
    sanitized_text: str, meta: Dict[str, Any]
) -> Optional[Tuple[SemanticFamily, ClassificationBasis, ApprovalState, int]]:
    if _SOW_CONTRACT_RE.search(sanitized_text):
        approval, rank = _resolve_contract_approval(sanitized_text)
        return SemanticFamily.COMMERCIAL_CONTRACT, ClassificationBasis.CONTENT_SIGNATURE, approval, rank
    if _MINUTES_RECAP_RE.search(sanitized_text) or meta.get("notes_mode"):
        return SemanticFamily.DERIVED_RECORD, ClassificationBasis.CONTENT_SIGNATURE, ApprovalState.UNKNOWN, 6
    if _WORKING_SOURCE_RE.search(sanitized_text):
        return SemanticFamily.WORKING_SOURCE, ClassificationBasis.CONTENT_SIGNATURE, ApprovalState.UNKNOWN, 5
    if _RESEARCH_INTEL_RE.search(sanitized_text):
        return SemanticFamily.RESEARCH_INTELLIGENCE, ClassificationBasis.CONTENT_SIGNATURE, ApprovalState.UNKNOWN, 5
    return None


def _resolve_container_family(
    clean_ext: str,
) -> Tuple[SemanticFamily, ClassificationBasis, ApprovalState, int]:
    if clean_ext in _STRUCTURED_EXTENSIONS:
        return SemanticFamily.STRUCTURED_OPERATIONAL, ClassificationBasis.DETERMINISTIC_MARKER, ApprovalState.UNKNOWN, 5
    if clean_ext in _VISUAL_EXTENSIONS:
        return SemanticFamily.VISUAL_COLLATERAL, ClassificationBasis.DETERMINISTIC_MARKER, ApprovalState.UNKNOWN, 5
    if clean_ext in _DOCUMENT_EXTENSIONS or clean_ext in _TEXT_EXTENSIONS:
        return SemanticFamily.UNKNOWN, ClassificationBasis.CONTAINER_FALLBACK, ApprovalState.UNKNOWN, 5
    return SemanticFamily.UNKNOWN, ClassificationBasis.AMBIGUOUS_UNKNOWN, ApprovalState.UNKNOWN, 5


def _resolve_semantic_family_and_rank(
    clean_ext: str,
    sanitized_text: str,
    meta: Dict[str, Any],
    detected_sections: List[SourceSection],
) -> Tuple[SemanticFamily, ClassificationBasis, ApprovalState, int]:
    explicit_fam = _parse_explicit_family(meta)
    if explicit_fam:
        rank = 4 if explicit_fam == SemanticFamily.COMMERCIAL_CONTRACT else 5
        return explicit_fam, ClassificationBasis.EXPLICIT_DESIGNATION, ApprovalState.UNKNOWN, rank
    if len(detected_sections) >= 2:
        return SemanticFamily.MIXED, ClassificationBasis.CONTENT_SIGNATURE, ApprovalState.UNKNOWN, 5
    head = sanitized_text[:2000]
    if clean_ext in _CORRESPONDENCE_EXTENSIONS or len(_EMAIL_HEADER_RE.findall(head)) >= 2:
        return SemanticFamily.CORRESPONDENCE, ClassificationBasis.DETERMINISTIC_MARKER, ApprovalState.UNKNOWN, 7
    if clean_ext in _AUDIO_EXTENSIONS or "started transcription" in sanitized_text.lower() or len(_TRANSCRIPT_TIMESTAMP_RE.findall(head)) >= 2:
        return SemanticFamily.RAW_TRANSCRIPT, ClassificationBasis.DETERMINISTIC_MARKER, ApprovalState.UNKNOWN, 3
    sig_match = _resolve_content_signature_family(sanitized_text, meta)
    if sig_match is not None:
        return sig_match
    return _resolve_container_family(clean_ext)


def _resolve_deliverables(meta: Dict[str, Any]) -> List[str]:
    explicit = meta.get("outputs") or meta.get("requested_outputs")
    if explicit:
        return list(explicit)
    return ["03"] if (meta.get("is_document") or meta.get("document")) else ["01", "02", "03"]


def _apply_anti_injection_downgrade(
    sanitized_text: str, approval_state: ApprovalState, authority_rank: int
) -> Tuple[ApprovalState, int]:
    if "[UNTRUSTED_INSTRUCTION_STRIPPED]" not in sanitized_text:
        return approval_state, authority_rank
    rank = 5 if authority_rank <= 3 else authority_rank
    state = ApprovalState.UNKNOWN if approval_state == ApprovalState.EXECUTED else approval_state
    return state, rank


def _extract_effective_date(
    meta: Dict[str, Any], family: SemanticFamily, sanitized_text: str
) -> Optional[str]:
    eff_date = meta.get("effective_date")
    if eff_date or family != SemanticFamily.COMMERCIAL_CONTRACT:
        return eff_date
    date_match = re.search(
        r"(?i)\beffective\s+(?:as\s+of\s+)?([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})",
        sanitized_text,
    )
    return date_match.group(1) if date_match else None


def classify_source(
    content: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    filename: str = "",
    input_ext: str = "",
) -> SourceProfile:
    """Classifies source content and metadata into a validated, canonical SourceProfile."""
    meta = metadata or {}
    sanitized_text = _sanitize_source_text(content)
    clean_ext = (input_ext or Path(filename).suffix).lower()
    container_type = clean_ext.lstrip(".") or "unknown"
    media_modality = _detect_media_modality(clean_ext, sanitized_text)
    author = _resolve_author(meta, sanitized_text)
    deliverables = _resolve_deliverables(meta)
    detected_sections = _detect_sections(sanitized_text)

    family, basis, approval_state, authority_rank = _resolve_semantic_family_and_rank(
        clean_ext, sanitized_text, meta, detected_sections
    )
    approval_state, authority_rank = _apply_anti_injection_downgrade(
        sanitized_text, approval_state, authority_rank
    )
    obs_date = meta.get("observation_date") or meta.get("date")
    eff_date = _extract_effective_date(meta, family, sanitized_text)
    source_id = meta.get("source_id") or Path(filename).stem or "source_default"

    return SourceProfile(
        source_id=source_id,
        semantic_family=family,
        media_modality=media_modality,
        container_type=container_type,
        confidentiality=meta.get("classification", "internal"),
        author=author,
        approval_state=approval_state,
        observation_date=obs_date,
        effective_date=eff_date,
        deliverable_selection=deliverables,
        classification_basis=basis,
        classification_version="1.0.0",
        authority_rank=authority_rank,
        origin_group_id=meta.get("origin_group_id"),
        parent_transformations=meta.get("parent_transformations", []),
        coverage_scope=meta.get("coverage_scope", {}),
        sections=detected_sections,
    )
