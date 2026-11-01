"""
tests/test_selective_remediation.py

Unit tests for Selective Remediation & Retry State Machine:
- Preserving previously passing artifacts across retry attempts (freezing)
- Targeted judge diff and QA critique prompt injection
- Evaluation against strict >=90 production release criteria
"""

from dual_pipeline.remediation_core import (
    BestCandidateLedger,
    RemediationDiffInjector,
    SelectiveRemediationStateMachine,
)


def test_remediation_diff_injector():
    """Verify targeted diff and QA feedback extraction for failed artifacts."""
    judge_report = {
        "output_1_evaluation": {
            "actionable_remediation_diff": "Replace 'Speaker 1' with 'Amit Ayer' at turn 14.",
            "forensic_rationale": "Unattributed room mic detected in key architectural declaration.",
        },
        "output_3_evaluation": {
            "actionable_remediation_diff": "Prune tactical to-do item from entity graph.",
        },
        "cross_artifact_reconciliation": {
            "failures": ["Date in Output 3 (09/10/2026) does not match Output 2 (09/12/2026)"],
        },
    }
    qa_report = {
        "output_01_forensic_transcript": {
            "failures": ["Line 42 missing timestamp locator"],
        },
    }

    diff_o1 = RemediationDiffInjector.extract_directive("output_1", judge_report, qa_report)
    assert "Replace 'Speaker 1' with 'Amit Ayer'" in diff_o1
    assert "Unattributed room mic detected" in diff_o1
    assert "Line 42 missing timestamp locator" in diff_o1

    diff_o3 = RemediationDiffInjector.extract_directive("output_3", judge_report, qa_report)
    assert "Prune tactical to-do" in diff_o3
    assert "Date in Output 3" in diff_o3


def test_selective_remediation_state_machine_freezing():
    """Verify passing artifacts (score >= 90) are frozen while failing stages retry."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)

    # Attempt 1: Output 2 passes (95), Output 1 fails (85), Output 3 fails (88)
    judge_report_a1 = {
        "output_1_evaluation": {"composite_score": 85, "verdict": "PASS_ADVISORY"},
        "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 88, "verdict": "PASS_ADVISORY"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    final_decision_a1 = {"status": "PASS_ADVISORY"}
    qa_report = {"passed": True}

    all_passed, failed = sm.evaluate_attempt(final_decision_a1, judge_report_a1, qa_report)
    assert all_passed is False
    assert "output_1" in failed
    assert "output_3" in failed
    assert "output_2" not in failed

    # Freeze Output 2
    sm.freeze_artifact("output_2", "EXECUTIVE RECAP CONTENT PASSING")
    assert sm.is_frozen("output_2") is True
    assert sm.is_frozen("output_1") is False

    # Advance attempt
    sm.advance_attempt()
    assert sm.current_attempt == 2
    assert sm.should_retry() is True
    assert sm.get_frozen_content("output_2") == "EXECUTIVE RECAP CONTENT PASSING"


def test_selective_remediation_state_machine_all_pass():
    """Verify all artifacts passing (O1>=90, O2>=95, O3>=95) unlocks overall pass."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    judge_report = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    final_decision = {"status": "PASS"}
    qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }

    all_passed, failed = sm.evaluate_attempt(final_decision, judge_report, qa_report)
    assert all_passed is True
    assert len(failed) == 0


def test_selective_remediation_output2_score_94_fails_and_retried():
    """Verify Output 2 scoring below production threshold (<90) fails and is retried while O1 and O3 freeze."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    judge_report = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 84, "verdict": "FAIL"},
        "output_3_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    final_decision = {"status": "FAIL"}
    qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }

    all_passed, failed = sm.evaluate_attempt(final_decision, judge_report, qa_report)
    assert all_passed is False
    assert "output_2" in failed
    assert "output_1" not in failed
    assert "output_3" not in failed


def test_selective_remediation_output3_score_94_fails_and_retried():
    """Verify Output 3 scoring below production threshold (<90) fails and is retried while O1 and O2 freeze."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    judge_report = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 84, "verdict": "FAIL"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    final_decision = {"status": "FAIL"}
    qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }

    all_passed, failed = sm.evaluate_attempt(final_decision, judge_report, qa_report)
    assert all_passed is False
    assert "output_3" in failed
    assert "output_1" not in failed
    assert "output_2" not in failed

    # Freeze passing artifacts
    sm.freeze_artifact("output_1", "F1_CONTENT")
    sm.freeze_artifact("output_2", "F2_CONTENT")
    assert sm.is_frozen("output_1") is True
    assert sm.is_frozen("output_2") is True
    assert sm.is_frozen("output_3") is False


def test_monotonic_score_governor_tracking_and_rollback():
    """Verify BestCandidateLedger tracks attempts, detects regressions, and restores high-water mark."""
    ledger = BestCandidateLedger()

    # Attempt 1: Output 3 scores 70
    rec1 = ledger.record_candidate(
        artifact_key="output_3",
        attempt=1,
        content='{"nodes": ["attempt_1"]}',
        composite_score=70,
        verdict="FAIL",
    )
    assert rec1.regressed is False
    assert ledger.get_high_water_mark_score("output_3") == 70
    assert ledger.has_regressed("output_3") is False

    # Attempt 2: Output 3 scores 82 (improvement)
    rec2 = ledger.record_candidate(
        artifact_key="output_3",
        attempt=2,
        content='{"nodes": ["attempt_2_high_water"]}',
        composite_score=82,
        verdict="PASS_ADVISORY",
    )
    assert rec2.regressed is False
    assert ledger.get_high_water_mark_score("output_3") == 82
    assert ledger.has_regressed("output_3") is False
    assert ledger.get_best_candidate("output_3").attempt == 2

    # Attempt 3: Output 3 regresses to 68
    rec3 = ledger.record_candidate(
        artifact_key="output_3",
        attempt=3,
        content='{"nodes": ["attempt_3_degraded"]}',
        composite_score=68,
        verdict="FAIL",
    )
    assert rec3.regressed is True
    assert rec3.prior_score == 82
    assert ledger.has_regressed("output_3") is True
    # High-water mark remains Attempt 2 (82)
    assert ledger.get_high_water_mark_score("output_3") == 82
    assert ledger.get_best_candidate("output_3").attempt == 2

    # Restoration returns Attempt 2 candidate
    content, best_rec = ledger.restore_best_candidate("output_3")
    assert best_rec.attempt == 2
    assert best_rec.composite_score == 82
    assert 'attempt_2_high_water' in content


def test_monotonic_state_machine_intercepts_regression():
    """Verify SelectiveRemediationStateMachine auto-intercepts regressions in evaluate_attempt."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)

    artifacts = {
        "output_1": "F1_CONTENT",
        "output_2": "F2_CONTENT",
        "output_3": '{"graph": "attempt_1"}',
    }

    # Attempt 1: Output 3 scores 70
    judge_a1 = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 70, "verdict": "FAIL"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    qa = {"passed": True}
    all_p, failed = sm.evaluate_attempt({"status": "FAIL"}, judge_a1, qa, artifacts=artifacts)
    assert all_p is False
    assert "output_3" in failed

    # Advance to Attempt 2: Output 3 scores 82
    sm.advance_attempt()
    artifacts["output_3"] = '{"graph": "attempt_2_best"}'
    judge_a2 = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 82, "verdict": "FAIL"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    all_p, failed = sm.evaluate_attempt({"status": "FAIL"}, judge_a2, qa, artifacts=artifacts)
    assert sm.get_high_water_mark_score("output_3") == 82

    # Advance to Attempt 3: Output 3 regresses to 68
    sm.advance_attempt()
    artifacts["output_3"] = '{"graph": "attempt_3_regressed"}'
    judge_a3 = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 68, "verdict": "FAIL"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    all_p, failed = sm.evaluate_attempt({"status": "FAIL"}, judge_a3, qa, artifacts=artifacts)
    # Regression was intercepted and artifacts["output_3"] restored to Attempt 2
    assert sm.has_regressed("output_3") is True
    assert artifacts["output_3"] == '{"graph": "attempt_2_best"}'
    assert sm.get_best_candidate_content("output_3") == '{"graph": "attempt_2_best"}'


def test_diff_injector_extracts_protected_paths_and_forbidden_mutations():
    """Verify RemediationDiffInjector extracts protected_paths and forbidden_mutations."""
    judge_report = {
        "output_2_evaluation": {
            "actionable_remediation_diff": "Clarify action item owner.",
            "critique_diff_envelope": {
                "target_path": "# Action & Governance Ledger",
                "observed_defect": "Synthetically attributed action item to Jake.",
                "required_delta": "State no forward commitments assigned.",
                "protected_paths": ["Header", "Meeting Objective", "Key Discussion Points"],
                "forbidden_mutations": ["Do not fabricate action items.", "Do not invent timestamps > 00:28:00."],
            },
        },
    }
    directive = RemediationDiffInjector.extract_directive("output_2", judge_report)
    assert "PROTECTED SECTIONS (MUST PRESERVE VERBATIM" in directive
    assert "Meeting Objective" in directive
    assert "FORBIDDEN MUTATIONS (STRICTLY PROHIBITED)" in directive
    assert "Do not fabricate action items" in directive
    assert "Do not invent timestamps > 00:28:00" in directive

