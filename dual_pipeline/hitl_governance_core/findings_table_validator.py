#!/usr/bin/env python3
"""dual_pipeline/hitl_governance_core/findings_table_validator.py

Enforces the Mandatory Pre-HITL Dimension Findings Table contract for audit and
SSOT drift remediation questions while relaxing the context output limit (up to
8,000 characters) for structured Markdown dimension tables and technical context.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

MAX_FINDINGS_TABLE_LENGTH: int = 8000

AUDIT_FINDINGS_TRIGGER = re.compile(
    r"\b(?:ssot\s+drift|drift\s+remediation|architecture\s+audit|"
    r"health\s+scorecard|anti-pattern\s+inventory|daily\s+drift)\b",
    re.IGNORECASE,
)

TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$")


def split_findings_table_context(question_text: str) -> Tuple[Optional[str], str]:
    """Separate a structured Markdown Dimension Findings Table and its surrounding
    technical context from the trailing decision question sentence.

    Returns:
        (table_block, active_prompt) when a valid Markdown table with a 'Dimension'
        column and at least 2 data rows is present; otherwise (None, question_text).
    """
    if not question_text or "|" not in question_text:
        return None, question_text

    all_lines = [line.strip() for line in question_text.splitlines() if line.strip()]
    table_lines = [line for line in all_lines if line.startswith("|") and line.endswith("|")]

    if len(table_lines) < 3:
        return None, question_text

    header = table_lines[0].lower()
    sep = table_lines[1]
    data_rows = table_lines[2:]
    if "dimension" not in header or not TABLE_SEPARATOR_RE.match(sep) or len(data_rows) < 1:
        return None, question_text

    # Separate the final question sentence from all preceding context + table lines
    last_line = all_lines[-1]
    if not (last_line.startswith("|") and last_line.endswith("|")):
        context_lines = all_lines[:-1]
        active_prompt = last_line
    else:
        context_lines = all_lines
        active_prompt = "How should we proceed with the remediation plan?"

    table_block = "\n".join(context_lines)
    return table_block, active_prompt


GENERIC_SUMMARY_CELL_RE = re.compile(
    r"^\|\s*(?:Source Review|Hygiene Review|Structure Review|Timeline Review|Governance Review)\s*\|"
    r"\s*Identified\s+\d+\s+verified\s+high-confidence\s+staging\s+note",
    re.IGNORECASE,
)


def _check_itemized_hygiene_table_rows(table_block: str) -> List[str]:
    errors: List[str] = []
    if "8-agent council verdict" not in table_block.lower():
        return errors
    for line in table_block.splitlines():
        if GENERIC_SUMMARY_CELL_RE.match(line.strip()):
            errors.append(
                "GENERIC_SUMMARY_ROW_PROHIBITED: Hygiene HITL findings table must display itemized "
                "record details (entity, speaker, statement text, and source context) rather than a "
                "collapsed 'Identified N verified high-confidence staging note(s)' summary row."
            )
            break
    return errors


def verify_audit_findings_table(question_text: str) -> List[str]:
    """Enforce that audit/drift HITL questions include a valid Dimension Findings Table."""
    errors: List[str] = []
    if not question_text or not isinstance(question_text, str):
        return errors

    table_block, _ = split_findings_table_context(question_text)
    is_audit_question = bool(AUDIT_FINDINGS_TRIGGER.search(question_text))

    if is_audit_question and table_block is None:
        errors.append(
            "MISSING_DIMENSION_FINDINGS_TABLE: Audit and drift remediation HITL questions "
            "must include a Markdown table ('| Dimension | ... |') showing each dimension "
            "of the findings before the question prompt."
        )
        return errors

    if table_block is not None:
        errors.extend(_check_itemized_hygiene_table_rows(table_block))
        if len(table_block) > MAX_FINDINGS_TABLE_LENGTH:
            errors.append(
                f"FINDINGS_TABLE_LENGTH_EXCEEDED: Dimension findings table is {len(table_block)} "
                f"characters (maximum budget is {MAX_FINDINGS_TABLE_LENGTH} characters)."
            )

    return errors
