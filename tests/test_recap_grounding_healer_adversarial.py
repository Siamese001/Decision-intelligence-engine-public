"""tests/test_recap_grounding_healer_adversarial.py

Adversarial and edge-case test suite covering:
1. Boundary payloads (empty strings, None metadata, missing headers) in recap_grounding_healer.
2. Malformed inputs (unverified elapsed spans, broken action tables, malformed roster dicts).
3. Overflow & exhaustion (Dimension Findings Table exceeding MAX_FINDINGS_TABLE_LENGTH = 8000).
4. Invariant bypass attempts (audit/SSOT drift HITL prompt omitting Dimension Findings Table,
   and CredentialManager environment isolation for alerting and feature_flags).
"""
from __future__ import annotations

import pytest

from dual_pipeline.alerting import AlertDispatcher
from dual_pipeline.credentials import CredentialManager
from dual_pipeline.feature_flags import FeatureFlagManager, FeatureFlagState
from dual_pipeline.hitl_governance_core.findings_table_validator import (
    MAX_FINDINGS_TABLE_LENGTH,
    verify_audit_findings_table,
)
from dual_pipeline.transcript_qa_core.recap_grounding_healer import (
    heal_action_ledger_status,
    heal_header_attendees,
    heal_header_time,
)


def test_boundary_empty_and_null_payloads_in_recap_healer() -> None:
    """Boundary payload: empty text and None metadata must return cleanly without mutation."""
    changes: list[str] = []
    assert heal_header_time("", None, changes) == ""
    assert heal_header_attendees("", None, changes) == ""
    assert heal_action_ledger_status("", changes) == ""
    assert changes == []


def test_malformed_elapsed_time_header_stripped_cleanly() -> None:
    """Malformed input: unverified elapsed span in metadata strips ungrounded Time header."""
    raw = "**Date:** 10-16-2026\n**Time:** 00:00-15:00\n**Project:** LENS\n"
    changes: list[str] = []
    healed = heal_header_time(raw, {"time": "00:00-15:00"}, changes)
    assert "**Time:**" not in healed
    assert len(changes) == 1


def test_malformed_roster_entries_and_source_grounded_attendees() -> None:
    """Malformed input: non-dict roster entries and missing org tokens do not corrupt header."""
    raw = (
        "**Attendees:**\n"
        "Apex Global Enterprises:\n"
        "- Amit Ayer\n\n"
        "## Meeting Objective\n"
        "Review architecture.\n"
    )
    meta = {
        "roster": ["not-a-dict", {"name": "", "org": ""}, {"name": "Marcus Balk", "org": "Apex Global Enterprises", "role": "Lead"}],
        "attendees": {"Apex Global Enterprises": ["Marcus Balk (Enterprise Architecture Practice Lead, non-speaking)"]},
    }
    healed = heal_header_attendees(raw, metadata=meta, output_1="Marcus Balk spoke.")
    assert "Marcus Balk" in healed


def test_overflow_findings_table_exceeding_8000_char_budget_rejected() -> None:
    """Overflow & exhaustion: Dimension Findings Table exceeding 8000 chars fails closed."""
    header = "| Dimension | Status | Findings |\n| :--- | :--- | :--- |\n| Drift Guard | FAIL | Check |\n"
    huge_row = "| Complexity | WARN | " + ("X" * MAX_FINDINGS_TABLE_LENGTH) + " |\n"
    q = header + huge_row + "How should we proceed with the SSOT drift remediation plan?"
    errs = verify_audit_findings_table(q)
    assert any("FINDINGS_TABLE_LENGTH_EXCEEDED" in e for e in errs)


def test_invariant_bypass_audit_question_without_table_rejected() -> None:
    """Invariant bypass attempt: SSOT drift question without Dimension Findings Table is blocked."""
    q = "We completed the daily architecture audit and found 3 issues. Proceed?"
    errs = verify_audit_findings_table(q)
    assert any("MISSING_DIMENSION_FINDINGS_TABLE" in e for e in errs)


def test_credential_manager_isolation_in_alerting_and_feature_flags() -> None:
    """Verify AlertDispatcher and FeatureFlagManager resolve environment settings via CredentialManager."""
    with CredentialManager.temporary_env(
        {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.example/services/T00/B00/X00",
            "NEE_FLAG_TEST_GOVERNANCE_GATE": "SHADOW",
        }
    ):
        dispatcher = AlertDispatcher()
        assert dispatcher.slack_url == "https://hooks.slack.example/services/T00/B00/X00"

        ff = FeatureFlagManager()
        assert ff.get_state("test_governance_gate") == FeatureFlagState.SHADOW
