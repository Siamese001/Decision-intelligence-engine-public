"""
Wave 2 Adversarial & Edge-Case Tests: Judge Deadline & Freeze Isolation.

Covers:
1. Invariant bypass attempt (failing deterministic QA gate on Output 03 MUST unfreeze Output 03 and block pin reuse).
2. Overflow & exhaustion (double timeout across both high-effort and medium-effort fallback fails closed).
3. Boundary payload (empty/missing frozen content or stale affiliation invalidates frozen state).
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.judges.forensic import _generate_client_forensic
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.eval_cache import resolve_pins_and_cache
from dual_pipeline.transcript_qa_core.frozen_gates import (
    apply_frozen_to_qa_report,
    check_frozen_dependency_validity,
)


def test_adversarial_failing_qa_gate_unfreezes_output3_and_blocks_pin_alignment() -> None:
    """Invariant bypass attempt: If re-anchored Output 03 fails deterministic QA gates, it MUST unfreeze."""
    sm = SelectiveRemediationStateMachine(max_attempts=2)
    f3_attempt1 = '{"metadata": {}, "graph_mutations": {"assertion_proposals": []}}'
    f3_tampered = '{"metadata": {}, "graph_mutations": {"corrupted": true}}'

    sm.register_deliverable_pass("output_3", f3_attempt1, score=93)
    qa_report = {
        "output_01_forensic_transcript": {"status": "PASS", "passed": True, "failures": []},
        "output_02_executive_recap": {"status": "PASS", "passed": True, "failures": []},
        "output_03_canonical_signal_projection": {
            "status": "FAIL",
            "passed": False,
            "failures": ["GATE_3_UNANCHORED_QUOTE"],
        },
    }
    apply_frozen_to_qa_report(
        qa_report,
        sm,
        metadata={"state_machine": sm},
        candidate_texts={"output_3": f3_tampered},
    )

    assert sm.is_frozen("output_3") is False
    assert qa_report["output_03_canonical_signal_projection"]["passed"] is False

    old_sha = hashlib.sha256(f3_attempt1.encode("utf-8")).hexdigest()
    pinned = {"output_3": {"composite_score": 93, "verdict": "PASS", "artifact_sha256": old_sha}}
    _, _, out3_pin, _, _, _ = resolve_pins_and_cache(
        f1_text="f1",
        f2_text="f2",
        f3_text=f3_tampered,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        pinned=pinned,
        metadata={"state_machine": sm},
    )
    # Pinned SHA must NOT be rewritten when Output 03 was unfrozen due to QA failure
    assert pinned["output_3"]["artifact_sha256"] == old_sha
    assert out3_pin is None


def test_adversarial_double_timeout_exhaustion_returns_structured_timeout_error() -> None:
    """Overflow & exhaustion: When both primary (75s) and retry (90s) time out, fail closed with TimeoutError."""
    import pytest

    def _always_timeout(fn, *, deadline_seconds, heartbeat_interval, operation_name):
        raise TimeoutError(f"{operation_name} timed out after {deadline_seconds}s")

    with (
        patch("config.transport.execute_with_deadline_and_heartbeat", side_effect=_always_timeout),
        pytest.raises(TimeoutError, match="Retry: Medium Effort.*90.0s"),
    ):
        _generate_client_forensic(
            client=MagicMock(),
            model="gpt-5.6-sol",
            reasoning_effort="high",
            system_prompt="sys",
            user_content="usr",
            recorder=MagicMock(),
            metadata={},
        )


def test_adversarial_empty_frozen_content_and_upstream_change_invalidate_freeze() -> None:
    """Boundary payload: Empty frozen content or upstream_transcript_changed invalidates frozen status."""
    sm = SelectiveRemediationStateMachine(max_attempts=2)
    valid, err = check_frozen_dependency_validity("output_3", "", sm, {})
    assert valid is False
    assert "Missing frozen content" in str(err)

    sm.freeze_artifact("output_3", '{"valid": true}')
    valid2, err2 = check_frozen_dependency_validity(
        "output_3", '{"valid": true}', sm, {"upstream_transcript_changed": True}
    )
    assert valid2 is False
    assert "Upstream transcript or dependency changed" in str(err2)
