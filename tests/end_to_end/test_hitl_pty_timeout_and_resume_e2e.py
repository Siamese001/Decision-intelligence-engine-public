"""End-to-End test for HITL PTY timeout bounds and state machine resume retention.

Verifies:
1. Bounded PTY stdin read and fail-closed timeout during live judge evaluation.
2. State machine checkpoint and frozen artifact retention during pipeline resume.
"""

from __future__ import annotations

import json
import os
import pty
import time
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    CanonicalModality,
    memoize_substage_checkpoints,
)
from dual_pipeline.cli.intake_approval_helper import attach_cli_judge_resolver
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.context_builder import initialize_remediation_state_machine


@pytest.mark.timeout(45)
def test_judge_clarification_pty_timeout_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E flow: borderline judge evaluation in unattended PTY completes within timeout SLA."""
    master_fd, slave_fd = pty.openpty()
    try:
        slave_in = os.fdopen(slave_fd, "r")
        monkeypatch.setattr("sys.stdin", slave_in)
        monkeypatch.setenv("NEE_HITL_STDIN_TIMEOUT_SECONDS", "0.2")

        metadata: dict[str, object] = {
            "meeting_name": "E2E Timeout Pipeline",
            "date": "10/22/2026",
            "project": "core",
            "output_dir": str(tmp_path),
            "hitl_ledger_path": str(tmp_path / "test_ledger.json"),
        }
        attach_cli_judge_resolver(metadata)

        eval_report = {
            "status": "FAIL",
            "overall_score": 87.5,
            "output_2_evaluation": {
                "status": "FAIL",
                "verdict": "FAIL",
                "composite_score": 87.5,
                "rationale": "Borderline quality score requiring operator clarification.",
            },
        }

        final_decision = {
            "status": "FAIL",
            "decision": "FAIL",
            "attempt": 2,
            "max_attempts": 2,
        }

        t_start = time.monotonic()
        clarified = evaluate_judge_clarification(eval_report, final_decision, metadata)
        elapsed = time.monotonic() - t_start

        # Borderline deduction upheld due to unattended PTY timeout
        assert clarified is False
        assert elapsed < 10.0
        assert eval_report["output_2_evaluation"]["status"] == "FAIL"
    finally:
        os.close(master_fd)


@pytest.mark.timeout(45)
def test_resume_preserves_frozen_output_2_checkpoint_e2e(tmp_path: Path) -> None:
    """E2E flow: resuming pipeline preserves frozen output_2 when prior artifacts exist."""
    out_dir = tmp_path / "meeting_run"
    out_dir.mkdir(parents=True)
    attempts_dir = out_dir / "attempts" / "attempt_1"
    attempts_dir.mkdir(parents=True)

    # Output 01 and Output 02 existing from prior run
    recap_content = (
        "# Executive Recap\n\n"
        "## Strategic Synthesis\n"
        "The team conducted an extensive architectural review of pipeline timeout boundaries. "
        "All borderline judge evaluations now enforce bounded stdin selects, preventing hung background PTY processes. "
        "Pipeline resume preserves frozen checkpoints across remediation passes.\n\n"
        "Score: 95.0\n"
    )
    (out_dir / "01_transcript_hitl_resolved_transcript.md").write_text("Valid Transcript " * 20, encoding="utf-8")
    (out_dir / "02_executive_recap_executive_recap.md").write_text(recap_content, encoding="utf-8")

    # Stage checkpoint and sealed passing decision for output_2
    (attempts_dir / "output_2.ckpt.md").write_text(recap_content, encoding="utf-8")
    (attempts_dir / "stage_seals.json").write_text(json.dumps({
        "output_2_seal": True,
        "output_2_pass": True,
    }), encoding="utf-8")
    (attempts_dir / "final_release_decision.json").write_text(json.dumps({
        "output_2_pass": True,
        "decision": "PASS",
    }), encoding="utf-8")

    metadata: dict[str, object] = {
        "meeting_name": "Resume Test Run",
        "date": "10/22/2026",
        "project": "resume_test",
        "output_dir": str(out_dir),
        "resume": True,
        "has_prior_meeting_artifacts": True,
        "remediation_directive_recap": "Old stale directive from attempt 1",
    }

    # Initialize remediation state machine via governed context builder
    sm = initialize_remediation_state_machine(
        metadata=metadata,
        output_dir=out_dir,
        max_judge_retries=2,
        is_pre_resolved_input=False,
        is_notes_mode=False,
    )

    assert isinstance(sm, SelectiveRemediationStateMachine)

    # Hydrate resume checkpoints via memoize_substage_checkpoints
    memoize_substage_checkpoints(out_dir, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)

    # Invariant: output_2 must remain frozen and not wiped by has_prior_meeting_artifacts
    assert sm.is_frozen("output_2") is True
    content = sm.get_frozen_content("output_2")
    assert content is not None
    assert "Score: 95.0" in content
