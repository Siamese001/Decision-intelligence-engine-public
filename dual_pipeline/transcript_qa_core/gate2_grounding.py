"""
dual_pipeline.transcript_qa_core.gate2_grounding

Gate 2 grounding sub-checks for Output 02 Executive Recaps:
- Verified time enforcement against metadata
- Standalone heading hygiene (no attached bullets or inline content)
- Claim-level grounding:
  - Proposed architectures (Azure/TLS/SQL/retention) and scores must be labeled as unapproved assumptions
  - Numeric baselines cannot appear as measured results
  - Uncommitted dates must be unscheduled and unverified tasks cannot be 'In Progress'
  - Ungrounded engagement or completion percentages without verified denominator are rejected
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_UNVERIFIED_TIME_PATTERNS = (
    r"not stated",
    r"time_unresolved",
    r"start time not verified",
    r"et window unavailable",
    r"start not established",
    r"unavailable from source",
    r"^00?:\d{2}[–\-]\d{1,2}:\d{2}",
    r"not provided in source",
)

_PROPOSED_ARCHITECTURE_PATTERNS = (
    (r"\bAzure cloud tenant\b", "Azure cloud tenant"),
    (r"\bTLS 1\.3 encryption\b", "TLS 1.3 encryption"),
    (r"\bread-only SQL queries\b", "read-only SQL queries"),
    (r"\b30-day historical event cache\b", "30-day historical event cache"),
)

_UNAPPROVED_LABEL_PATTERNS = (
    r"\(advisor proposal",
    r"\(advisor-proposed",
    r"unapproved assumption",
    r"advisor-proposed hypothes",
    r"candidate option",
    r"candidate scope",
    r"hypotheses pending",
    r"exploratory candidate",
)


def check_verified_time(
    f2_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Enforces that Output 2 specifies verified time from metadata when available."""
    if not metadata or not isinstance(metadata, dict):
        return True, []

    meta_time = str(metadata.get("time") or "").strip()
    if not meta_time or any(re.search(p, meta_time, re.IGNORECASE) for p in _UNVERIFIED_TIME_PATTERNS):
        return True, []

    time_match = re.search(r"(?:\*\*Time:\*\*|Time:)\s*([^\n]+)", f2_text, re.IGNORECASE)
    if not time_match:
        return False, [
            f"[GATE_2_TIME_UNVERIFIED] Output 2 is missing '**Time:**' header, "
            f"despite verified metadata time '{meta_time}'."
        ]

    found_time = time_match.group(1).strip()
    for pat in _UNVERIFIED_TIME_PATTERNS:
        if re.search(pat, found_time, re.IGNORECASE):
            return False, [
                f"[GATE_2_TIME_UNVERIFIED] Output 2 specifies unverified time ('{found_time}') "
                f"despite authoritative verified time in metadata ('{meta_time}'). Use metadata time."
            ]

    return True, []


def check_standalone_headings(f2_text: str) -> Tuple[bool, List[str]]:
    """Verifies that markdown headings stand alone on their own lines without attached content."""
    failures: List[str] = []
    lines = f2_text.splitlines()

    for idx, line in enumerate(lines, 1):
        line_s = line.strip()
        if not re.match(r"^#{1,4}\s+", line_s):
            continue
        # Exclude subject header which legitimately carries subject text
        if re.match(r"^#\s*Subject:", line_s, re.IGNORECASE):
            continue

        # Check for heading with attached em-dash / en-dash bullet or subtitle
        if re.search(r"[-—–]\s*\*\*[^*]+(?::\*\*|\*\*)", line_s):
            failures.append(
                f"[GATE_2_NON_STANDALONE_HEADING] Line {idx}: Heading contains attached bold bullet: '{line_s}'. "
                "Headings must stand alone on their own line with a blank line before section content."
            )
        elif re.search(r"^#{1,4}\s+[^:\n]+:\s+[A-Za-z]{3,}", line_s) and not re.match(r"^#{1,4}\s+(?:Subject|Appendix):", line_s, re.IGNORECASE):
            # Heading followed immediately by colon and sentence content
            failures.append(
                f"[GATE_2_NON_STANDALONE_HEADING] Line {idx}: Heading contains inline sentence content: '{line_s}'. "
                "Headings must stand alone."
            )

    return len(failures) == 0, failures


def _check_proposed_architectures(f2_text: str) -> List[str]:
    """Ensures proposed architectures (Azure, TLS, SQL, retention) are labeled as unapproved assumptions."""
    failures: List[str] = []
    has_advisor_label = any(re.search(pat, f2_text, re.IGNORECASE) for pat in _UNAPPROVED_LABEL_PATTERNS)

    for pat, label in _PROPOSED_ARCHITECTURE_PATTERNS:
        m = re.search(pat, f2_text, re.IGNORECASE)
        if m:
            start = max(0, m.start() - 250)
            end = min(len(f2_text), m.end() + 250)
            surrounding = f2_text[start:end]
            local_labeled = any(re.search(lp, surrounding, re.IGNORECASE) for lp in _UNAPPROVED_LABEL_PATTERNS)
            if not (local_labeled or has_advisor_label):
                failures.append(
                    f"[GATE_2_UNAPPROVED_ARCHITECTURE_PROPOSAL] Proposed architecture feature '{label}' "
                    "appears as an observed design or fact without explicit '(Advisor Proposal — Unapproved Assumption)' labeling."
                )

    if re.search(r"\|\s*\*{0,2}Weighted Composite Score\*{0,2}\s*\|", f2_text, re.IGNORECASE):
        matrix_match = re.search(r"(?m)^#{1,3}\s+Candidate Integration Scored Decision Matrix[\s\S]*?(?=^#{1,3}\s|\Z)", f2_text, re.IGNORECASE)
        matrix_text = matrix_match.group(0) if matrix_match else f2_text
        if not any(re.search(lp, matrix_text, re.IGNORECASE) for lp in _UNAPPROVED_LABEL_PATTERNS):
            failures.append(
                "[GATE_2_UNAPPROVED_SCORED_MATRIX] Candidate Integration Scored Decision Matrix contains scores "
                "without explicit disclaimer labeling them as advisor-proposed hypotheses pending review."
            )

    return failures


def _check_action_status_and_deadlines(f2_text: str) -> List[str]:
    """Ensures uncommitted dates are unscheduled and unverified tasks are not 'In Progress'."""
    failures: List[str] = []
    table_lines = [
        line for line in f2_text.splitlines()
        if re.match(r"^\|\s*\*{0,2}(?:\d+|action|gate|blocker|milestone|decision)\*{0,2}\s*\|", line, re.IGNORECASE)
    ]
    for line in table_lines:
        raw_parts = line.split("|")
        cells = [c.strip() for c in raw_parts[1:-1]]
        if len(cells) < 4:
            continue
        status = cells[-1]
        timing = cells[-3] if len(cells) >= 5 and cells[-2].startswith("[") else cells[-2]
        is_unscheduled = any(k in timing.lower() for k in ("unscheduled", "post-meeting", "no deadline", "pending", "not stated", "tbd"))
        is_in_progress = status.lower() in ("in progress", "in-progress", "completed", "verified")
        if is_unscheduled and is_in_progress:
            failures.append(
                f"[GATE_2_UNVERIFIED_ACTION_STATUS] Action item with unscheduled/uncommitted timing ('{timing}') "
                f"cannot have status '{status}' when progress was unverified in session. Mark as 'Proposed' or 'Open'."
            )
    return failures


def _check_engagement_percentages(f2_text: str) -> List[str]:
    """Flags ungrounded engagement or completion percentage claims where total denominator is unconfirmed."""
    failures: List[str] = []
    # Match percentage completion claims
    m_pct = re.search(r"\b(\d+(?:\.\d+)?%)\s*(?:completion|completed|engagement percentage|work orders completed)\b", f2_text, re.IGNORECASE)
    if m_pct:
        pct_val = m_pct.group(1)
        # Check if text acknowledges that total required count is unspecified or an unconfirmed estimate
        denominator_noted = bool(re.search(
            r"denominator|total required|unspecified|unit of reference|completed units rather than|unconfirmed estimate",
            f2_text,
            re.IGNORECASE,
        ))
        if not denominator_noted:
            failures.append(
                f"[GATE_2_UNGROUNDED_ENGAGEMENT_PERCENTAGE] Output 2 asserts ungrounded metric '{pct_val}' "
                "without a confirmed denominator or acknowledging that completion is in discrete units."
            )
    return failures


def _check_action_turn_window_owner_alignment(
    f2_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Flags Action Ledger rows that attribute technical scope to a speaker who disclaimed it in Output 01."""
    if not metadata or not isinstance(metadata, dict):
        return []
    f1_text = str(metadata.get("output_1") or metadata.get("clean_transcript") or metadata.get("source_text") or "")
    if not f1_text.strip():
        return []
    from .recap_action_handoff_healer import _reconcile_single_ledger_row, parse_transcript_turns

    turns = parse_transcript_turns(f1_text)
    if not turns:
        return []
    failures: List[str] = []
    for line in f2_text.splitlines():
        _, audit_msg = _reconcile_single_ledger_row(line, turns)
        if audit_msg:
            failures.append(
                f"[GATE_2_15_ACTION_OWNER_HANDOFF_DRIFT] {audit_msg}. "
                "Multi-turn technical handoffs and in-call chat fulfillment must attribute the technical architect."
            )
    return failures


def check_claim_level_grounding(
    f2_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Comprehensive check for claim-level grounding and unapproved proposal isolation."""
    failures: List[str] = []
    failures.extend(_check_proposed_architectures(f2_text))
    failures.extend(_check_action_status_and_deadlines(f2_text))
    failures.extend(_check_engagement_percentages(f2_text))
    failures.extend(_check_action_turn_window_owner_alignment(f2_text, metadata=metadata))
    return len(failures) == 0, failures


def check_gate_2_15(
    f2_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Runs all Gate 2.15 sub-checks (time verification, standalone headings, claim grounding)."""
    g_time, f_time = check_verified_time(f2_text, metadata=metadata)
    g_head, f_head = check_standalone_headings(f2_text)
    g_claim, f_claim = check_claim_level_grounding(f2_text, metadata=metadata)
    all_fails = f_time + f_head + f_claim
    passed = g_time and g_head and g_claim
    details = {
        "passed": passed,
        "claim_grounding_passed": g_claim,
        "headings_standalone": g_head,
        "verified_time_passed": g_time,
        "failures": all_fails,
    }
    return passed, all_fails, details
