"""
Single Source of Truth (SSOT) Gold Standard Appendix Validator.

Enforces rich, multi-thematic stakeholder perspectives and contribution breakdowns
in Stage 02 Executive Recaps for meetings with duration >= 15 minutes.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STANDARD_APPENDIX_HEADER = "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"

FORBIDDEN_BOILERPLATE_PATTERN = re.compile(
    r"(?i)\b(?:Core Thesis & Strategic Perspective|Key Contributions & Discussion Focus|Commitments & Next Steps)\b"
)

FORBIDDEN_DISCLAIMER_PATTERNS = re.compile(
    r"(?i)\b(no reliable utterance mapping|lacks speaker mapping|requires speaker[- ]resolved evidence|"
    r"position therefore remains unconfirmed|individual architectural position therefore remains unconfirmed|"
    r"cannot safely be presented as|neither agreement nor disagreement should be inferred|"
    r"establishes (?:his|her|their|its) relevance[^.\n]+?but not|establishes (?:his|her|their|its) participation[^.\n]+?not|"
    r"not equivalent to direct attributable testimony|remains unconfirmed rather than inferred|"
    r"no attributable promise appears|attributable promise appears|cannot establish (?:his|her|their) personal assessment|"
    r"lacks speaker attribution)\b"
)

IGNORED_HEADER_KEYWORDS = (
    "table of", "table:", "notes:", "glossary", "appendix", "detailed stakeholder", "contribution breakdown", "non-speaking",
    "additional participants", "evidence locks", "evidence matrix", "evidence register", "evidence",
    "other active", "other participants", "active participants", "introduction or", "brief contribution",
    "brief introduction", "supporting participants", "roll call", "roll-call", "brief roll",
)


class SpeakerTier(enum.Enum):
    """C-21 Tiered Multi-Thematic Depth Standard (PRIMARY/CO_LEAD/PARTICIPANT/CONTRIBUTOR)."""
    PRIMARY = "primary"
    CO_LEAD = "co_lead"
    PARTICIPANT = "participant"
    CONTRIBUTOR = "contributor"


def _get_c21_floors() -> Dict[SpeakerTier, tuple[int, int]]:
    from dual_pipeline.parameters import get_output2_c21_speaker_tier_floors
    raw = get_output2_c21_speaker_tier_floors()
    return {
        SpeakerTier.PRIMARY: raw["primary"],
        SpeakerTier.CO_LEAD: raw["co_lead"],
        SpeakerTier.PARTICIPANT: raw["participant"],
        SpeakerTier.CONTRIBUTOR: raw["contributor"],
    }


@dataclass
class AppendixAuditResult:
    passed: bool
    has_appendix: bool
    failures: List[str] = field(default_factory=list)
    speaker_sections_count: int = 0
    thematic_perspectives_count: int = 0
    speaker_details: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def resolve_appendix_duration(meeting_dur: Any, text: str) -> int:
    """Safely extracts integer duration in minutes from metadata, dict, or text."""
    if isinstance(meeting_dur, (int, float)):
        return int(meeting_dur)
    if isinstance(meeting_dur, dict):
        d_val = meeting_dur.get("duration_minutes") or meeting_dur.get("meeting_duration_minutes")
        if isinstance(d_val, (int, float)):
            return int(d_val)

    match = re.search(r"(?:Duration:\s*~?|Recording duration:\s*)(\d+)(?::\d+)?(?:\s*m|\s*min|\b)", text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, IndexError):
            pass
    from dual_pipeline.transcript_duration_helper import resolve_meeting_duration
    res = resolve_meeting_duration({}, text)
    return int(res) if res is not None else 0


def _extract_thematic_titles(lines: List[str]) -> List[str]:
    """Extracts thematic perspective titles from paragraph or bullet lines."""
    thematic_titles = []
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        bullet_m = re.match(r"^[-*]\s+\*\*([^*]+?)\*\*:\s+(.*)", clean) or re.match(r"^[-*]\s+\*\*([^*]+?):\*\*[\s]+(.*)", clean)
        if bullet_m:
            thematic_titles.append(bullet_m.group(1).strip())
            continue
        bullet_nobold_m = re.match(r"^[-*]\s+([A-Z][a-zA-Z0-9\s/&—\-\(\),']{3,80}):\s+(.*)", clean)
        if bullet_nobold_m:
            thematic_titles.append(bullet_nobold_m.group(1).strip())
            continue
        para_m = re.match(r"^([A-Z][a-zA-Z0-9\s/&—\-\(\),']{3,80}):\s+(.*)", clean)
        if para_m:
            thematic_titles.append(para_m.group(1).strip())
    return thematic_titles


def _audit_individual_speaker_section(
    sec_clean: str,
    dur_val: int = 30,
    speaker_tier: SpeakerTier = SpeakerTier.PARTICIPANT,
) -> Optional[tuple[str, int, List[str], Optional[str], List[str]]]:
    """Audits a single speaker section against C-21 tiered word/theme floors."""
    lines = [line_item.strip() for line_item in sec_clean.splitlines() if line_item.strip()]
    if not lines:
        return None
    speaker_header = lines[0]
    hdr_lower = speaker_header.lower().strip()
    if any(hdr_lower.startswith(k) or hdr_lower == k for k in IGNORED_HEADER_KEYWORDS):
        return None

    body_lines = lines[1:]
    body_words = sum(len(line_item.split()) for line_item in body_lines)
    themes = _extract_thematic_titles(body_lines)
    if speaker_tier not in (SpeakerTier.PRIMARY, SpeakerTier.CO_LEAD) and themes and (
        all(any(k in t.lower() for k in ("roll call", "introduction", "check-in", "role alignment", "attendance", "presence", "greeting")) for t in themes)
        or (len(body_lines) <= 2 and any(any(k in t.lower() for k in ("roll call", "introduction", "check-in", "role alignment", "attendance", "presence", "greeting")) for t in themes))
    ):
        speaker_tier = SpeakerTier.CONTRIBUTOR

    min_words, min_themes = _get_c21_floors()[speaker_tier]
    tier_label = speaker_tier.value

    # Continuous proportional scaling for short meetings
    scale = min(1.0, max(0.25, dur_val / 30.0))
    scaled_min_words = max(12, int(min_words * scale))
    scaled_min_themes = max(1, int(min_themes * scale))
    effective_min_words = max(12, scaled_min_words - 5) if len(themes) >= scaled_min_themes else scaled_min_words
    if speaker_tier == SpeakerTier.CONTRIBUTOR:
        effective_min_words = min(effective_min_words, 12)
        scaled_min_themes = 1

    if body_words < effective_min_words:
        failure = (
            f"[GATE_2_8_THIN_SPEAKER_SECTION] Output 2 Appendix speaker section '{speaker_header}' "
            f"is thin ({body_words} words < {scaled_min_words} words floor for C-21 {tier_label} tier)."
        )
        return speaker_header, body_words, themes, failure, body_lines

    if len(themes) < scaled_min_themes:
        failure = (
            f"[GATE_2_8_INSUFFICIENT_THEMATIC_DEPTH] Output 2 Appendix speaker section '{speaker_header}' "
            f"has only {len(themes)} thematic perspective(s). C-21 {tier_label} tier requires at least {scaled_min_themes} distinct "
            f"thematic perspectives (e.g. '- **[Theme]**: [Substantive narrative]')."
        )
        return speaker_header, body_words, themes, failure, body_lines

    return speaker_header, body_words, themes, None, body_lines


def _normalize_phrase_key(sentence: str) -> tuple[str, str]:
    clean_s = sentence.strip()
    norm = re.sub(r"[^\w\s]", "", clean_s).lower()
    if "no spoken dialogue recorded in transcript" in norm or "listed as attendee in meeting metadata" in norm:
        return "", ""
    words = norm.split()
    if len(words) >= 24 and len(norm) >= 120:
        return " ".join(words), clean_s
    return "", ""


def _find_speaker_duplication(
    spk_hdr: str,
    body_lines: List[str],
    seen_phrases: Dict[str, str],
) -> Optional[str]:
    body_text = " ".join(re.sub(r"^[-*]\s*(?:\*\*[^*]+\*\*:\s*|\*\*[^*]+:\*\*\s*)?", "", line).strip() for line in body_lines)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", body_text)
    for s in sentences:
        norm_key, clean_s = _normalize_phrase_key(s)
        if not norm_key:
            continue
        first_spk = seen_phrases.get(norm_key)
        if first_spk and first_spk != spk_hdr:
            return (
                f"[GATE_2_8_CROSS_SPEAKER_DUPLICATION] Output 2 Appendix contains identical multi-sentence phrases "
                f"across speakers '{first_spk}' and '{spk_hdr}': '{clean_s[:60]}...'. "
                f"Each speaker's perspective must be authentic and distinct."
            )
        seen_phrases[norm_key] = spk_hdr
    return None


def _check_cross_speaker_deduplication(
    speaker_sections: List[tuple[str, List[str]]],
) -> List[str]:
    """Detects identical multi-sentence phrases copy-pasted across distinct speakers."""
    failures: List[str] = []
    seen_phrases: Dict[str, str] = {}
    for spk_hdr, body_lines in speaker_sections:
        err = _find_speaker_duplication(spk_hdr, body_lines, seen_phrases)
        if err:
            failures.append(err)
    return failures


def _tier_from_share(share: float) -> SpeakerTier:
    if share >= 0.30:
        return SpeakerTier.PRIMARY
    if share >= 0.15:
        return SpeakerTier.CO_LEAD
    if share >= 0.05:
        return SpeakerTier.PARTICIPANT
    return SpeakerTier.CONTRIBUTOR


def _match_speaker_share(clean_hdr: str, shares: Dict[str, float]) -> Optional[float]:
    for spk, share in shares.items():
        spk_l = spk.lower()
        if any(t in spk_l for t in ("room", "team", "speaker", "collective", "mic", "participant", "unknown", "unattributed")):
            continue
        if spk_l in clean_hdr or clean_hdr in spk_l:
            return share
        tok = spk_l.split()[0] if spk_l else ""
        if len(tok) > 2 and clean_hdr.startswith(tok):
            return share
    return None


def _get_speaker_tier(
    spk_hdr: str,
    speaker_shares: Optional[Dict[str, float]] = None,
    primary_speakers: Optional[List[str]] = None,
) -> SpeakerTier:
    """Classify a speaker into a C-21 tier based on turn share."""
    clean_hdr = re.sub(r"^[#\s\-*]+", "", spk_hdr).lower()
    if primary_speakers and any(p.lower() in clean_hdr for p in primary_speakers):
        return SpeakerTier.PRIMARY
    if speaker_shares:
        matched = _match_speaker_share(clean_hdr, speaker_shares)
        if matched is not None:
            return _tier_from_share(matched)
    return SpeakerTier.PARTICIPANT


def _is_primary_speaker(
    spk_hdr: str,
    speaker_shares: Optional[Dict[str, float]] = None,
    primary_speakers: Optional[List[str]] = None,
) -> bool:
    """Legacy shim: returns True for PRIMARY tier. Prefer _get_speaker_tier."""
    return _get_speaker_tier(spk_hdr, speaker_shares, primary_speakers) == SpeakerTier.PRIMARY


def _audit_all_speaker_sections(
    raw_sections: List[str],
    dur_val: int,
    speaker_shares: Optional[Dict[str, float]] = None,
    primary_speakers: Optional[List[str]] = None,
) -> AppendixAuditResult:
    """Iterates through candidate speaker sections and validates density, deduplication, and structure."""
    speaker_sections_count = 0
    total_thematic_count = 0
    speaker_details: Dict[str, Dict[str, Any]] = {}
    valid_sections: List[tuple[str, List[str]]] = []
    failures: List[str] = []

    for sec in raw_sections:
        sec_clean = sec.strip()
        if not sec_clean:
            continue
        first_line = sec_clean.splitlines()[0]
        tier = _get_speaker_tier(first_line, speaker_shares, primary_speakers)
        parsed = _audit_individual_speaker_section(sec_clean, dur_val, speaker_tier=tier)
        if parsed is None:
            continue
        spk_hdr, body_w, themes, fail, body_lines = parsed
        speaker_sections_count += 1
        total_thematic_count += len(themes)
        eval_tier = SpeakerTier.CONTRIBUTOR if (tier != SpeakerTier.PRIMARY and themes and all(
            any(k in t.lower() for k in ("roll call", "introduction", "check-in")) for t in themes
        )) else tier
        speaker_details[spk_hdr] = {
            "body_words": body_w,
            "themes": themes,
            "theme_count": len(themes),
            "speaker_tier": eval_tier.value,
        }
        valid_sections.append((spk_hdr, body_lines))
        if fail:
            failures.append(fail)

    # Cross-speaker deduplication audit
    dupe_failures = _check_cross_speaker_deduplication(valid_sections)
    failures.extend(dupe_failures)

    if speaker_sections_count == 0:
        failures.append(
            f"Output 2 Appendix is superficial (missing individual speaker perspective subsections for meeting duration {dur_val}m >= 15m)."
        )

    passed = len(failures) == 0
    return AppendixAuditResult(
        passed=passed,
        has_appendix=True,
        failures=failures,
        speaker_sections_count=speaker_sections_count,
        thematic_perspectives_count=total_thematic_count,
        speaker_details=speaker_details,
    )


def audit_appendix_gold_standard(
    text: str,
    duration_minutes: Optional[Any] = None,
    attendees: Optional[List[str]] = None,
    speaker_shares: Optional[Dict[str, float]] = None,
    primary_speakers: Optional[List[str]] = None,
) -> AppendixAuditResult:
    """
    Deterministically audits Output 02 Appendix against the SSOT Gold Standard.

    Validates:
    1. Mandatory header presence for meetings >= 15m.
    2. Zero occurrences of defensive cop-out disclaimers or evasive language.
    3. Zero occurrences of generic 3-bullet placeholder boilerplate.
    4. Dedicated speaker subsections per C-21 tiered word floors (from parameters.json):
       PRIMARY (>30% turns): >= 150 words, >= 3 themes
       CO_LEAD (15-30% turns): >= 80 words, >= 2 themes
       PARTICIPANT (5-15% turns): >= 40 words, >= 2 themes
       CONTRIBUTOR (<5% turns): >= 15 words, >= 1 theme
    5. Granular multi-thematic perspectives (bullet format '- **[Theme]**: ...' or paragraph).
    """
    dur_val = resolve_appendix_duration(duration_minutes, text)

    app_match = re.search(
        r"#{1,3}\s+(?:Appendix:\s*Detailed Stakeholder Perspectives[^\n]*|Appendix\b[^\n]*)",
        text,
        re.IGNORECASE,
    )
    if not app_match:
        return AppendixAuditResult(
            passed=False,
            has_appendix=False,
            failures=[
                f"Output 2 is missing mandatory '# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown' "
                f"for meeting duration {dur_val}m."
            ],
        )

    app_text = text[app_match.end():]
    disc_m = FORBIDDEN_DISCLAIMER_PATTERNS.search(app_text)
    if disc_m:
        return AppendixAuditResult(
            passed=False,
            has_appendix=True,
            failures=[
                f"[GATE_2_8_DEFENSIVE_DISCLAIMER_DETECTED] Output 2 Appendix contains evasive defensive disclaimer "
                f"('{disc_m.group(0)}'). Appendix must provide substantive stakeholder perspectives and contributions, "
                f"not meta-commentary on transcript attribution."
            ],
        )

    if FORBIDDEN_BOILERPLATE_PATTERN.search(app_text):
        return AppendixAuditResult(
            passed=False,
            has_appendix=True,
            failures=[
                "[GATE_2_8_BOILERPLATE_DETECTED] Output 2 Appendix contains generic 3-bullet placeholder boilerplate "
                "('Core Thesis & Strategic Perspective'). Substantive SSOT Gold Standard thematic narratives required."
            ],
        )

    if not re.search(r"(?:^|\n)#{1,4}\s+[^\n]+", app_text):
        return AppendixAuditResult(
            passed=False,
            has_appendix=True,
            failures=[
                f"Output 2 Appendix is superficial (missing individual speaker perspective subsections for meeting duration {dur_val}m >= 15m)."
            ],
        )

    raw_sections = re.split(r"(?:^|\n)#{1,4}\s+", app_text)
    return _audit_all_speaker_sections(
        raw_sections[1:],
        dur_val,
        speaker_shares=speaker_shares,
        primary_speakers=primary_speakers,
    )


def is_valid_gold_standard_appendix(
    text: str, duration_minutes: Optional[Any] = None,
    speaker_shares: Optional[Dict[str, float]] = None, primary_speakers: Optional[List[str]] = None,
) -> bool:
    """Returns True if the Appendix satisfies the SSOT Gold Standard."""
    return bool(text) and audit_appendix_gold_standard(
        text, duration_minutes, speaker_shares=speaker_shares, primary_speakers=primary_speakers
    ).passed
