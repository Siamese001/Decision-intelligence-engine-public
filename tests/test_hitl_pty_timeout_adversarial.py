"""Adversarial and boundary test suite for HITL PTY timeouts and deadline suspension.

Edge-case categories covered:
1. Boundary payload: zero-second timeout, empty stdin, EOF, non-interactive mode.
2. Invariant bypass attempt: attempting to suspend deadline past max_suspend_seconds ceiling.
3. Concurrency & race / PTY stdin: simulated unattended PTY file descriptors without input.
"""

from __future__ import annotations

import os
import pty
import time
from unittest.mock import MagicMock, patch

import pytest

from config.request_deadline import (
    Deadline,
    RequestDeadlineExceeded,
    current_suspended_seconds,
    deadline_scope,
    read_stdin_line_with_timeout,
    suspend_deadline,
)
from dual_pipeline.cli.intake_approval_helper import attach_cli_judge_resolver
from dual_pipeline.llm_judge_core.judge_clarification import (
    _find_prior_signed_hitl_clarification,
    _record_signed_hitl_clarification,
    evaluate_judge_clarification,
)


@pytest.mark.timeout(30)
def test_read_stdin_line_with_timeout_empty_or_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Category 1: Boundary payload - non-interactive mode and zero timeout return None immediately."""
    monkeypatch.setenv("NEE_NON_INTERACTIVE", "1")
    res = read_stdin_line_with_timeout(timeout_seconds=5.0)
    assert res is None

    monkeypatch.delenv("NEE_NON_INTERACTIVE", raising=False)
    res_zero = read_stdin_line_with_timeout(timeout_seconds=0.0)
    assert res_zero is None


@pytest.mark.timeout(30)
def test_suspend_deadline_capped_by_max_suspend_seconds() -> None:
    """Category 2: Invariant bypass attempt - suspending deadline cannot pause beyond max_suspend_seconds."""
    with pytest.raises(RequestDeadlineExceeded):
        with deadline_scope(0.3) as dl:
            assert dl is not None
            # Suspend with a tight cap of 0.05s, then sleep for 0.12s
            with suspend_deadline(max_suspend_seconds=0.05):
                time.sleep(0.12)
                # In-flight pause must be clamped to 0.05s
                in_flight = current_suspended_seconds()
                assert in_flight <= 0.06

            # After exiting, dl.suspended_seconds should also be capped at 0.05s
            assert dl.suspended_seconds <= 0.06
            # Remaining time must have decreased and eventually expire
            time.sleep(0.25)
            dl.remaining()


@pytest.mark.timeout(30)
def test_cli_judge_resolver_unattended_pty_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Category 3: Concurrency / PTY stdin - unattended PTY times out and returns UPHOLD_DEDUCTION."""
    master_fd, slave_fd = pty.openpty()
    try:
        slave_in = os.fdopen(slave_fd, "r")
        monkeypatch.setattr("sys.stdin", slave_in)
        monkeypatch.setenv("NEE_HITL_STDIN_TIMEOUT_SECONDS", "0.1")

        metadata: dict[str, object] = {}
        attach_cli_judge_resolver(metadata)
        resolver = metadata["hitl_judge_resolver"]
        assert callable(resolver)

        envelope = MagicMock()
        envelope.question = "Affirm borderline score (87.1)?"
        envelope.calibrated_options = [
            {"key": "AFFIRM_VALID", "label": "Affirm Valid"},
            {"key": "UPHOLD_DEDUCTION", "label": "Uphold Deduction"},
        ]

        t0 = time.monotonic()
        decision = resolver(envelope)
        elapsed = time.monotonic() - t0

        assert decision == "UPHOLD_DEDUCTION"
        assert elapsed < 2.0
    finally:
        os.close(master_fd)


@pytest.mark.timeout(30)
def test_find_prior_signed_clarification_tolerates_healed_subject_digest(tmp_path: object) -> None:
    """Category 4: State recovery - lookup matches signed approval despite mutated subject fields."""
    from pathlib import Path
    ledger_file = Path(str(tmp_path)) / "hitl_decision_ledger.json"
    metadata = {
        "meeting_name": "Project Apollo Weekly",
        "date": "10/22/2026",
        "project": "apollo",
        "attendees": "Alice Smith, Bob Jones",
        "hitl_ledger_path": str(ledger_file),
    }

    receipt = {"receipt_digest": "sha256-test-receipt"}
    _record_signed_hitl_clarification(
        metadata=metadata,
        store=None,
        short_key="output_2",
        eval_key="output_2_evaluation",
        composite=88.5,
        receipt=receipt,
        op_id="test_operator",
    )

    assert ledger_file.is_file()
    assert metadata.get("alr_subject_sha256") is not None

    # Simulate healing mutating attendee list and project case
    healed_metadata = {
        "meeting_name": "Project Apollo Weekly",
        "date": "10-22-2026",
        "project": "APOLLO",
        "roster": [{"name": "Alice Smith (Lead)"}, {"name": "Bob Jones (Staff)"}],
        "hitl_ledger_path": str(ledger_file),
        "resume": True,
        # Pinned subject SHA preserved across pipeline stages
        "alr_subject_sha256": metadata["alr_subject_sha256"],
    }

    found = _find_prior_signed_hitl_clarification(healed_metadata, None, "output_2")
    assert found is not None
    assert found.get("payload", {}).get("decision") == "APPROVE"
