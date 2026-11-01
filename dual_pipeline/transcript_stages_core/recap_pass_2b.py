"""Pass 2B: Action & Governance Ledger synthesis and forensic validation for Stage 02 recap.

Enforces:
- H-6 5-Step Owner Attribution Waterfall
- Relative-date calculation against meeting header date (MM/DD/YYYY)
- Prohibition of unassigned/TBD filler rows
- Zero-commitments sentinel when no action items exist
- Multi-attempt retry loop on structure or governance failures
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from config.request_deadline import check_deadline, deadline_scope, remaining_timeout
from config.transport import SiblingPassAbortedError

from .call_ledger import D02CallLedger
from .external_synthesizer import call_gemini_generate_content

logger = logging.getLogger(__name__)

DEFAULT_PASS_2B_DEADLINE_SEC = 60.0

ZERO_COMMITMENTS_SENTINEL = "No forward commitments or action items were assigned during this discussion."
ALT_ZERO_COMMITMENTS_SENTINEL = "No formal actions were established in this session; open initiatives remain unresolved questions."

PASS_2B_SYSTEM_PROMPT = (
    "You are an executive governance analyst synthesizing the Action & Governance Ledger for Artifact 02. "
    "Extract all action items, commitments, decisions, milestones, owners, and dates into a structured Markdown table:\n"
    "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
    "Columns MUST be: # (numeric ID), Type (Action / Blocker / Gate / Milestone), Item / Description (active imperative verb + concrete deliverable), "
    "Accountable Owner (single named DRI resolved via the H-6 5-Step Owner Attribution Waterfall: "
    "1. Explicit verbal acceptance—unless overridden in the adjacent multi-turn exchange by live in-call chat fulfillment ('I just sent it in the chat') or an explicit technical scope disclaimer ('our work is not technical') paired with a requester naming the technical architect ('engineer [Name]'), in which case assign the technical architect and expand Evidence Locators across the full handoff window, "
    "2. Role-implied owner in <project_resources>, 3. Request with affirmation; "
    "NEVER 'Team', 'All', 'Unassigned', or 'Unassigned (Pending Owner Assignment)'), "
    "Target Timing (resolve relative dates/cadences like 'by Friday', 'end of day', 'Wednesday review', or 'next sprint' "
    "against the meeting header Date into exact MM/DD/YYYY or explicit cadence/milestone; NEVER emit lazy 'TBD' when timing cues exist), "
    "Evidence Locators (transcript timestamp e.g. [12:34]), and Status (Open / In Progress / Pending).\n"
    "H-6 EXCLUSION RULE: If an idea or suggestion has neither an identifiable single-person owner nor timing/milestone context, "
    "do NOT put it in the Action Ledger (it belongs in Key Discussion Points). Never emit 'Unassigned' + 'TBD' filler rows.\n"
    "If no forward commitments or action items were assigned, you MUST emit:\n"
    f"'{ZERO_COMMITMENTS_SENTINEL}'\n"
    "Do not include conversational preamble."
)


class Pass2BStructureError(ValueError):
    """Raised when Pass 2B fails to produce a markdown table or the zero-commitments sentinel."""
    pass


def extract_remediation_from_user_content(user_content: str) -> Tuple[Optional[str], Optional[str]]:
    """Extracts prior candidate and critique directive from user_content XML blocks if present."""
    prior_match = re.search(r"<prior_recap_candidate>([\s\S]*?)</prior_recap_candidate>", user_content)
    prior_candidate = prior_match.group(1).strip() if prior_match else None

    critique_match = re.search(
        r"CRITIQUE DIRECTIVE:\s*\n([\s\S]*?)(?:\n\nSURGICAL|\n</remediation_directive>)",
        user_content,
    )
    if not critique_match:
        critique_match = re.search(r"<remediation_directive>([\s\S]*?)</remediation_directive>", user_content)
    critique = critique_match.group(1).strip() if critique_match else None

    return prior_candidate, critique


_FORBIDDEN_PASS_2B_OWNERS = {
    "unassigned",
    "unassigned (pending owner assignment)",
    "not assigned",
    "tbd",
    "tbc",
    "unknown",
    "pending",
    "pending owner",
    "team",
    "all",
    "n/a",
    "none",
}

_LAZY_TIMING_PATTERNS = {
    "tbd",
    "tbd - unspecified",
    "unspecified",
    "pending",
    "unscheduled",
    "n/a",
    "none",
    "unknown",
}


def validate_pass_2b_structure(text: str) -> bool:
    """Validate that Pass 2B produced either the zero-commitments sentinel or a valid Markdown table."""
    stripped = text.strip()
    if ZERO_COMMITMENTS_SENTINEL.lower() in stripped.lower() or ALT_ZERO_COMMITMENTS_SENTINEL.lower() in stripped.lower():
        return True

    ledger_match = re.search(r"#+\s*(?:\d+\.\s*)?Action & Governance Ledger[\s\S]*", stripped, re.IGNORECASE)
    eval_text = ledger_match.group(0) if ledger_match else stripped
    lines = [ln.strip() for ln in eval_text.splitlines() if ln.strip()]
    table_lines = [ln for ln in lines if ln.startswith("|") and ln.endswith("|")]
    if len(table_lines) < 3:
        raise Pass2BStructureError(
            f"Pass 2B output must contain at least a header, delimiter, and one data row (found {len(table_lines)} table lines)."
        )

    headers = [c.strip().lower() for c in table_lines[0].strip("|").split("|")]
    owner_col_idx: Optional[int] = None
    timing_col_idx: Optional[int] = None

    for idx, h in enumerate(headers):
        if "owner" in h or "accountable" in h or "dri" in h:
            owner_col_idx = idx
        elif "timing" in h or "due" in h or "target" in h or "date" in h:
            timing_col_idx = idx

    data_rows = 0
    for row_line in table_lines[2:]:
        cols = [c.strip() for c in row_line.strip("|").split("|")]
        if not cols or all(re.match(r"^[-:\s]+$", c) for c in cols):
            continue
        first_col = cols[0].strip().rstrip(".")
        is_data_row = bool(re.match(r"^(?:[A-Za-z]+[-_]?\d+\.?|\d+\.?)$", first_col)) or (len(first_col) > 0 and first_col[0].isdigit())
        if not is_data_row and any("owner" in c.lower() for c in cols):
            continue
        data_rows += 1

        owner_val = cols[owner_col_idx].strip() if owner_col_idx is not None and len(cols) > owner_col_idx else ""
        timing_val = cols[timing_col_idx].strip() if timing_col_idx is not None and len(cols) > timing_col_idx else ""

        if owner_val:
            owner_clean = owner_val.lower().strip(" *`_")
            if (
                owner_clean in _FORBIDDEN_PASS_2B_OWNERS
                or owner_clean.startswith("unassigned")
                or owner_clean.startswith("pending owner")
                or bool(re.match(r"^Speaker\s+[A-Z0-9]+$", owner_clean, re.IGNORECASE))
            ):
                raise Pass2BStructureError(
                    f"Pass 2B contains forbidden unresolved owner '{owner_val}'. "
                    "Apply the H-6 Owner Attribution Waterfall or omit unowned discussion ideas."
                )

        if timing_col_idx is not None and len(cols) > timing_col_idx:
            timing_val = cols[timing_col_idx].strip()
            timing_clean = timing_val.lower().strip(" *`_")
            if (
                timing_clean in _LAZY_TIMING_PATTERNS
                or timing_clean.startswith("tbd")
                or timing_clean in ("---", "-", "")
            ):
                raise Pass2BStructureError(
                    f"Pass 2B contains ungrounded Target Timing '{timing_val}' for owner '{owner_val}'. "
                    "Resolve relative timing/cadence against the meeting header date or omit non-action items."
                )

    if data_rows == 0:
        raise Pass2BStructureError("Pass 2B table contains zero action item data rows.")
    return True


def execute_pass_2b(
    model: str,
    user_content: str,
    max_retries: int = 2,
    deadline_seconds: float = DEFAULT_PASS_2B_DEADLINE_SEC,
    ledger: Optional[D02CallLedger] = None,
    system_prompt: Optional[str] = None,
    abort_event: Optional[threading.Event] = None,
    cached_content: Optional[str] = None,
) -> str:
    """Pass 2B: Action & Governance Ledger extraction with strict validation and auto-retries."""
    from .recap_modular_dag import _build_cached_pass_content, _intercept_pass_retry

    with deadline_scope(deadline_seconds):
        last_err = ""
        for attempt in range(max_retries + 1):
            check_deadline()
            if abort_event is not None and abort_event.is_set():
                raise SiblingPassAbortedError("Pass 2B aborted because a sibling pass failed terminally.")

            retry_note = (
                f"RETRY DIRECTIVE: Previous Pass 2B output failed validation ({last_err}). "
                "You MUST resolve relative dates against the meeting date, attribute every action to a single named DRI "
                "via the H-6 Waterfall, and omit any unowned or 'TBD' discussion items."
                if attempt > 0
                else ""
            )
            if cached_content:
                req_system_prompt = ""
                req_user_content = _build_cached_pass_content(
                    PASS_2B_SYSTEM_PROMPT, system_prompt, user_content, retry_note=retry_note
                )
            else:
                prefix = f"{system_prompt.strip()}\n\n" if system_prompt and system_prompt.strip() else ""
                prompt = f"{prefix}{PASS_2B_SYSTEM_PROMPT}"
                if retry_note:
                    prompt = f"{prompt}\n\n{retry_note}"
                req_system_prompt = prompt
                req_user_content = user_content

            _intercept_pass_retry(attempt, req_system_prompt, req_user_content)
            try:
                kwargs_2b: Dict[str, Any] = {
                    "model": model,
                    "system_prompt": req_system_prompt,
                    "user_content": req_user_content,
                    "json_mode": False,
                    "deadline_seconds": remaining_timeout(deadline_seconds),
                    "pass_id": "2B",
                    "pass_attempt": attempt + 1,
                    "ledger": ledger,
                    "abort_event": abort_event,
                }
                if cached_content is not None:
                    kwargs_2b["cached_content"] = cached_content
                import dual_pipeline.transcript_stages_core.recap_modular_dag as dag
                call_fn = getattr(dag, "call_gemini_generate_content", call_gemini_generate_content)
                raw = call_fn(**kwargs_2b).strip()
                check_deadline()
                validate_pass_2b_structure(raw)
                return raw
            except Pass2BStructureError as err:
                last_err = str(err)
                logger.warning("Pass 2B attempt %d failed structure/governance validation: %s", attempt + 1, err)
                if attempt == max_retries:
                    if abort_event is not None:
                        abort_event.set()
                    raise Pass2BStructureError(f"Pass 2B failed after {max_retries} retries: {err}") from err
            except Exception as exc:
                logger.error("Pass 2B failed with non-retryable error: %s", exc)
                if abort_event is not None:
                    abort_event.set()
                raise
        if abort_event is not None:
            abort_event.set()
        raise Pass2BStructureError(f"Pass 2B failed to produce valid output after {max_retries} retries")
