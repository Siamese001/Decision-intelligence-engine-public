"""
tests/end_to_end/test_edge_cases_retry_limits_e2e.py

End-to-End lifecycle test verifying bifurcated retry limits and early termination.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.qa_runner import (
    evaluate_exit_gates_and_verification,
)


def test_e2e_remediation_lifecycle_bounded_to_one_retry():
    """Verify that remediation retry attempts never exceed max_judge_retries=1."""
    attempt_records = []

    def mock_eval_fn(*args, **kwargs):
        attempt_records.append(len(attempt_records) + 1)
        qa_report = {"passed": False, "failures": ["Content clarity issue"]}
        judge_report = {"status": "FAIL", "failures": ["Refinement needed"]}
        final_decision = {
            "status": "FAIL",
            "authoritative_write_authorized": False,
            "output_1_pass": False,
            "output_1_deterministic_gates_pass": True,
            "output_1_llm_score_pass": False,
        }
        return qa_report, judge_report, final_decision

    # Mock state machine and tracker
    tracker = MagicMock()
    recorder = MagicMock()
    x_gates = MagicMock()
    state_machine = MagicMock()
    state_machine.has_regressed.return_value = False

    # Attempt 1 fails -> triggers remediation
    # Attempt 2 fails -> exhausts retries (1 attempt + 1 retry = 2 total executions)
    max_judge_retries = 1

    # Verify that attempting beyond max_judge_retries raises RuntimeError
    try:
        # Simulate Attempt 2 where attempt > max_judge_retries
        evaluate_exit_gates_and_verification(
            attempt=2,
            max_judge_retries=max_judge_retries,
            artifacts={},
            forensic_transcript="Transcript text",
            executive_recap="Recap text",
            graph_json="{}",
            transcript_text="Source text",
            u0_context={},
            metadata={},
            state_machine=state_machine,
            x_gates=x_gates,
            tracker=tracker,
            recorder=recorder,
            provider_type="offline",
            judge_model="mock",
            reasoning_effort="low",
            is_notes_mode=False,
        )
        raise AssertionError("Expected evaluate_exit_gates_and_verification to raise terminal failure after 1 retry")
    except RuntimeError as exc:
        assert "Mandatory QA Gate or Micro-Judge evaluation failed after 2 attempts" in str(exc)
