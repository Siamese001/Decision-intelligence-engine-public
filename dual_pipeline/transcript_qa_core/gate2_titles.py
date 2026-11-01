"""
dual_pipeline/transcript_qa_core/gate2_titles.py

Gate 2.12: Executive Recap attendee title completeness linter.
Enforces that company-grouped attendees section and Appendix headers do not contain
placeholders or ungrounded title omissions.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import _is_placeholder

PLACEHOLDER_TITLE_PATTERNS = [
    re.compile(r"\(specific title not provided\)", re.I),
    re.compile(r"\(title not provided\)", re.I),
    re.compile(r"\[Title Pending\]", re.I),
    re.compile(r"\(specific role not provided\)", re.I),
    re.compile(r"\(role not listed\)", re.I),
    re.compile(r"\[role not listed\]", re.I),
    re.compile(r"\(title not provided in source metadata\)", re.I),
    re.compile(r"\[title not provided in source metadata\]", re.I),
    re.compile(r"\bRole Not Listed\b", re.I),
]


def check_gate_2_12_attendee_titles(
    f2_text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Gate 2.12: Asserts Output 02 contains no placeholder attendee titles in header or Appendix."""
    failures: List[str] = []

    # 1. Literal placeholder pattern scan across entire Output 02
    for pat in PLACEHOLDER_TITLE_PATTERNS:
        matches = pat.findall(f2_text)
        if matches:
            failures.append(
                f"[GATE_2_12_PLACEHOLDER_TITLE_DETECTED] Found {len(matches)} instance(s) of ungrounded placeholder "
                f"pattern '{pat.pattern}' in Output 02. All attendee titles must be resolved before release."
            )

    # 2. Company-grouped attendees section check
    att_block_m = re.search(
        r"(?:Attendees|Participants)[:\s*]*\n([\s\S]*?)(?=\n#{1,3}|\n\*\*Meeting Objective|\n\*\*Executive Summary|\Z)",
        f2_text,
        re.IGNORECASE,
    )
    if att_block_m:
        lines = [line.strip() for line in att_block_m.group(1).splitlines() if line.strip().startswith(("-", "*"))]
        for line in lines:
            m_comp = re.match(r"^[-*]\s*(?:\*\*)?[^:]+(?:\*\*)?:\s*(.*)$", line)
            content = m_comp.group(1) if m_comp else re.sub(r"^[-*]\s*", "", line)
            for m in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\(([^)]+)\)", content):
                name, role = m.group(1).strip(), m.group(2).strip()
                is_auth_participant = False
                if role.lower() == "participant":
                    f1_text = (meta.get("forensic_transcript") or meta.get("output_1_text") or "") if meta else ""
                    if f1_text and re.search(rf"\|\s*{re.escape(name)}\s*\|\s*[^|]+\|\s*Participant\s*\|", f1_text, re.IGNORECASE) or meta and (name in str(meta.get("attendees", "")) or name in (meta.get("speaker_map") or {}).values()):
                        is_auth_participant = True

                if not is_auth_participant and ("specific title not provided" in role.lower() or _is_placeholder(role)):
                    failures.append(
                        f"[GATE_2_12_UNRESOLVED_TITLE] Attendee '{name}' in Output 02 header has placeholder role '{role}'."
                    )

    # 3. Appendix headers check: format `# [Speaker Name] — [Organization], [Role / Title]`
    app_headers = re.findall(r"^#{1,3}\s+([^—\n]+)—\s*([^,\n]+),\s*([^\n]+)$", f2_text, re.MULTILINE)
    for name, _org, role in app_headers:
        clean_role = role.strip()
        is_auth_participant = False
        if clean_role.lower() == "participant":
            f1_text = (meta.get("forensic_transcript") or meta.get("output_1_text") or "") if meta else ""
            if f1_text and re.search(rf"\|\s*{re.escape(name.strip())}\s*\|\s*[^|]+\|\s*Participant\s*\|", f1_text, re.IGNORECASE) or meta and (name.strip() in str(meta.get("attendees", "")) or name.strip() in (meta.get("speaker_map") or {}).values()):
                is_auth_participant = True

        if not is_auth_participant and ("specific title not provided" in clean_role.lower() or _is_placeholder(clean_role)):
            failures.append(
                f"[GATE_2_12_APPENDIX_PLACEHOLDER_TITLE] Attendee '{name.strip()}' in Appendix has placeholder title '{clean_role}'."
            )

    passed = len(failures) == 0
    return passed, failures, {
        "passed": passed,
        "title_violations_count": len(failures),
    }


def check_live_meeting_subject_heading(
    f2_text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Audit # Subject: line for email readiness and live meeting title/date completeness."""
    failures: List[str] = []
    subj_m = re.search(r"^#\s*Subject:\s*(.+)$", f2_text, re.MULTILINE)
    if not subj_m:
        failures.append("Output 2 missing email-ready '# Subject:' header.")
        return False, failures

    subj_line = subj_m.group(1).strip()
    effective_meta = meta or {}
    is_live = bool(
        effective_meta.get("is_live")
        or effective_meta.get("enforce_title_date")
        or effective_meta.get("source_modality") == "LIVE_AUDIO"
        or effective_meta.get("source_type") in ("AUDIO_LIVE", "LIVE_CAPTURE", "LIVE")
    )
    if is_live:
        has_date = bool(re.search(r"\b(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})\b", subj_line))
        if not has_date:
            failures.append(
                f"Gate 2.1 Subject Audit: Live meeting '# Subject:' line must include meeting date (format: ' | MM/DD/YYYY'). Found: '{subj_line}'"
            )
        meeting_name = effective_meta.get("meeting_name") or effective_meta.get("title") or effective_meta.get("meeting")
        if meeting_name:
            norm_name = re.sub(r"[_\-]+", " ", str(meeting_name)).strip().lower()
            norm_subj = re.sub(r"[_\-]+", " ", subj_line).strip().lower()
            name_tokens = [tok for tok in norm_name.split() if len(tok) > 2]
            if norm_name not in norm_subj and not any(tok in norm_subj for tok in name_tokens):
                failures.append(
                    f"Gate 2.1 Subject Audit: Live meeting '# Subject:' line must include meeting name '{meeting_name}'. Found: '{subj_line}'"
                )
    return True, failures

