"""
dual_pipeline/cli/copilot_attempt_ledger.py

CLI facade for the Copilot attempt ledger and diagnostic recorder.
"""
from __future__ import annotations

from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_DEADLINE_EXHAUSTION,
    CATEGORY_GRAPH_BINDING,
    CATEGORY_REJECTED_EVIDENCE,
    CATEGORY_RENDERING_FAILURE,
    CATEGORY_SESSION_DISCOVERY,
    CATEGORY_STALE_CONTEXT,
    CATEGORY_UNHANDLED_ERROR,
    CATEGORY_USAGE,
    CopilotAttemptRecord,
    categorize_copilot_terminal_reason,
    create_attempt_record,
    extract_context_freshness,
    get_source_revision,
    record_copilot_attempt,
)

__all__ = [
    "CATEGORY_ABSENT_CANDIDATES",
    "CATEGORY_DEADLINE_EXHAUSTION",
    "CATEGORY_GRAPH_BINDING",
    "CATEGORY_REJECTED_EVIDENCE",
    "CATEGORY_RENDERING_FAILURE",
    "CATEGORY_SESSION_DISCOVERY",
    "CATEGORY_STALE_CONTEXT",
    "CATEGORY_UNHANDLED_ERROR",
    "CATEGORY_USAGE",
    "CopilotAttemptRecord",
    "categorize_copilot_terminal_reason",
    "create_attempt_record",
    "extract_context_freshness",
    "get_source_revision",
    "record_copilot_attempt",
]
