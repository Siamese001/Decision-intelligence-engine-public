"""
tests/test_hitl_monotonic_reconciliation_e2e.py

Class B E2E Receipt Test for Wave 2:
Validates that operator affirmation of borderline score (85.0) in evaluate_judge_clarification
promotes composite_score to 90.0, records raw_composite_score=85.0 for provenance,
and does not trigger monotonic invariant regressions against memoized baselines.
"""
import pytest
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


@pytest.mark.e2e
def test_judge_clarification_affirmation_and_monotonic_progression(tmp_path):
    """Verify that AFFIRM_VALID promotes composite_score to 90.0 and prevents monotonic regression."""
    # 1. State machine seeds memoized baselines
    sm = SelectiveRemediationStateMachine(max_attempts=2)
    sample_content = "# Subject: Test Meeting\n\n# Overview\nSample content."
    sm.register_deliverable_pass("output_1", sample_content, score=95)
    sm.register_deliverable_pass("output_2", sample_content, score=90)
    sm.register_deliverable_pass("output_3", '{"meeting_id": "test"}', score=91)
    assert sm.get_high_water_mark_score("output_2") == 90

    # 2. Judge report emits borderline scorecard (85.0)
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
            "forensic_rationale": "Single-turn attendee lacks multi-thematic depth",
        }
    }
    final_decision = {"status": "REVIEW_REQUIRED"}
    ledger_file = tmp_path / "hitl_isolated_ledger.json"
    metadata = {
        "hitl_judge_resolver": lambda envelope: "AFFIRM_VALID",
        "reviewer": "operator_test",
        "approval_source": "human_operator",
        "prior_user_command": "affirm and proceed",
        "hitl_ledger_path": str(ledger_file),
    }

    # 3. Evaluate clarification with AFFIRM_VALID
    clarified = evaluate_judge_clarification(
        judge_report=judge_report,
        final_decision=final_decision,
        metadata=metadata,
    )
    assert clarified is True
    o2_eval = judge_report["output_2_evaluation"]
    assert o2_eval["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
    assert o2_eval["raw_composite_score"] == 85.0
    assert o2_eval["composite_score"] == 90.0

    # 4. State machine evaluates attempt with clarified candidate
    qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }
    artifacts = {
        "output_1": sample_content,
        "output_2": sample_content,
        "output_3": '{"meeting_id": "test"}',
    }
    all_passed, failed_stages = sm.evaluate_attempt(
        final_decision=final_decision,
        judge_report=judge_report,
        qa_report=qa_report,
        artifacts=artifacts,
    )
    assert all_passed is True
    assert "output_2" not in failed_stages
    assert not sm.has_regressed("output_2")
    assert sm.get_high_water_mark_score("output_2") >= 90
