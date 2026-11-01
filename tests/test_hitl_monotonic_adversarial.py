"""
tests/test_hitl_monotonic_adversarial.py

Adversarial and edge-case test suite for Wave 2:
Covers unclarified regressions, uphold deduction actions, unauthorized synthetic origins,
and multi-attempt score stability.
"""
import pytest
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


@pytest.mark.adversarial
def test_uphold_deduction_triggers_legitimate_regression_and_rollback():
    # 1. State machine evaluates attempt 1 with score 90
    sm = SelectiveRemediationStateMachine(max_attempts=2)
    sample_content = "# Subject: Test Meeting\n\n# Overview\nSample content."
    sm.record_candidate("output_2", sample_content, 90, verdict="PASS", attempt=1)

    # 2. Attempt 2 regresses to score 85 with deduction upheld
    judge_report = {
        "output_2_evaluation": {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 85.0,
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 30,
                "conditional_extensions": 20,
                "appendix_stakeholder_perspectives": 25,
            },
            "forensic_rationale": "Severe omission of client architecture",
        }
    }
    final_decision = {"status": "FAIL", "output_2_pass": False}
    sm.advance_attempt()

    qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }
    artifacts = {"output_2": sample_content}
    all_passed, failed_stages = sm.evaluate_attempt(
        final_decision=final_decision,
        judge_report=judge_report,
        qa_report=qa_report,
        artifacts=artifacts,
    )
    # Regression was intercepted and output_2 must be marked for retry
    assert all_passed is False
    assert "output_2" in failed_stages
    assert sm.has_regressed("output_2")


@pytest.mark.adversarial
def test_synthetic_approval_rejection_in_judge_clarification():
    """Security check: Synthetic auto-approvals without human origin must be rejected."""
    judge_report = {
        "output_2_evaluation": {
            "status": "PASS",
            "verdict": "HITL_REQUIRED",
            "composite_score": 85.0,
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 30,
                "conditional_extensions": 20,
                "appendix_stakeholder_perspectives": 25,
            },
            "forensic_rationale": "Borderline test",
        }
    }
    final_decision = {"status": "REVIEW_REQUIRED"}
    metadata = {
        "hitl_judge_resolver": lambda envelope: "AFFIRM_VALID",
        "reviewer": "auto_reviewer",
        "approval_source": "ide_stop_hook_auto_approval", # Synthetic origin
        "prior_user_command": None,
    }

    clarified = evaluate_judge_clarification(
        judge_report=judge_report,
        final_decision=final_decision,
        metadata=metadata,
    )
    assert clarified is False
    assert judge_report["output_2_evaluation"]["verdict"] != "PASS_AUTHORIZED_EXCEPTION"
