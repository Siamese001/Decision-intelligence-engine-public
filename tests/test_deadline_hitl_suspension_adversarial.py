"""Adversarial and regression tests for Wave 1: Interactive Deadline Suspension & Authorized Exception Verdict Gate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from config.request_deadline import (
    RequestDeadlineExceeded,
    check_deadline,
    current_suspended_seconds,
    deadline_scope,
    suspend_deadline,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.release_policy import ReleaseVerdict
from dual_pipeline.transcript_qa_core.evaluator import _eval_output1, _eval_output_n
from dual_pipeline.transcript_qa_core.judge_failure_diagnostics import JudgeFailureDiagnosticsCollector


def test_suspend_deadline_preserves_compute_budget_across_two_hour_interactive_wait() -> None:
    """Exact production regression (Run #2): 2-hour interactive pause inside 3600s deadline_scope."""
    fake_clock = [1000.0]

    def fake_monotonic() -> float:
        return fake_clock[0]

    with patch("config.request_deadline.time.monotonic", side_effect=fake_monotonic):
        with deadline_scope(3600.0) as outer_dl:
            assert outer_dl is not None
            fake_clock[0] += 400.0  # 400s of active compute
            assert pytest.approx(outer_dl.remaining(), rel=1e-3) == 3200.0

            with suspend_deadline():
                fake_clock[0] += 7380.0  # 2h 03m interactive operator wait
                # While suspended, remaining budget is frozen at 3200.0s
                assert pytest.approx(outer_dl.remaining(), rel=1e-3) == 3200.0
                with suspend_deadline():
                    fake_clock[0] += 120.0  # Nested suspension must not double-count

            # After interactive wait completes, full 3200s compute budget remains
            check_deadline()
            assert pytest.approx(outer_dl.remaining(), rel=1e-3) == 3200.0
            assert pytest.approx(current_suspended_seconds(), rel=1e-3) == 7500.0

        # Genuine compute exhaustion after resume still raises RequestDeadlineExceeded
        with pytest.raises(RequestDeadlineExceeded):
            with deadline_scope(100.0):
                fake_clock[0] += 101.0
                check_deadline()


def test_pass_authorized_exception_honored_by_evaluator_and_diagnostics() -> None:
    """Verify PASS_AUTHORIZED_EXCEPTION in 85-89 band passes _eval_output_n and diagnostics."""
    out2_card = {
        "composite_score": 87,
        "verdict": "PASS_AUTHORIZED_EXCEPTION",
        "status": "PASS_AUTHORIZED_EXCEPTION",
        "authorized_exception": True,
        "section_scores": {
            "header_and_overview": 9,
            "key_updates_technical_substance": 31,
            "conditional_extensions": 17,
            "appendix_stakeholder_perspectives": 30,
        },
        "forensic_rationale": "Minor conciseness deduction affirmed by operator.",
    }
    verdict, pass_flag = _eval_output_n(2, out2_card, 87, 90)
    assert verdict == ReleaseVerdict.PASS
    assert pass_flag is True

    out1_card = {
        "composite_score": 91,
        "verdict": "PASS_AUTHORIZED_EXCEPTION",
        "status": "PASS_AUTHORIZED_EXCEPTION",
        "authorized_exception": True,
        "dimension_1": 23,
        "dimension_2": 23,
        "dimension_3": 23,
        "dimension_4": 22,
        "forensic_rationale": "Borderline transcript score affirmed by operator.",
    }
    v1, p1 = _eval_output1(out1_card, 91)
    assert v1 == ReleaseVerdict.PASS
    assert p1 is True

    collector = JudgeFailureDiagnosticsCollector()
    judge_report = {"output_2_evaluation": out2_card}
    fail_count = collector.extract_from_judge_report(judge_report, attempt=3, retry_count=2)
    assert fail_count == 0
    assert len(collector.failures) == 0
    assert "output_02_executiverecapjudge" in collector.successful_results


def test_judge_clarification_suspends_deadline_and_reuses_signed_ledger_on_resume(tmp_path: Path) -> None:
    """Verify evaluate_judge_clarification suspends deadline during prompt and reuses signed approval on resume."""
    fake_clock = [5000.0]

    def fake_monotonic() -> float:
        return fake_clock[0]

    ledger_path = tmp_path / "hitl_decision_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_path, use_sqlite=False)

    judge_report = {
        "status": "FAIL",
        "overall_verdict": "FAIL",
        "output_1_evaluation": {"status": "PASS", "verdict": "PASS", "composite_score": 96},
        "output_2_evaluation": {
            "status": "HITL_REQUIRED",
            "verdict": "HITL_REQUIRED",
            "composite_score": 87.0,
            "section_scores": {
                "header_and_overview": 9,
                "key_updates_technical_substance": 31,
                "conditional_extensions": 17,
                "appendix_stakeholder_perspectives": 30,
            },
            "forensic_rationale": "Borderline deduction on recap depth.",
        },
        "output_3_evaluation": {"status": "PASS", "verdict": "PASS", "composite_score": 92},
    }
    final_decision = {
        "attempt": 3,
        "max_attempts": 3,
        "status": "FAIL",
        "output_1_deterministic_gates_pass": True,
        "output_1_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_2_pass": False,
        "output_3_deterministic_gates_pass": True,
        "output_3_pass": True,
    }
    meta = {
        "meeting_name": "DocIQ Q4 Scope",
        "date": "09/23/2026",
        "project": "nea",
        "attendees": "Jack, PartnerAdvisory",
        "reviewer": "amit_operator",
        "hitl_ledger_path": str(ledger_path),
    }

    def slow_interactive_resolver(_envelope: object) -> str:
        fake_clock[0] += 7200.0  # 2-hour operator pause
        return "AFFIRM_VALID"

    with patch("config.request_deadline.time.monotonic", side_effect=fake_monotonic):
        with deadline_scope(3600.0) as dl:
            clarified = evaluate_judge_clarification(
                judge_report,
                final_decision,
                meta,
                store=store,
                interactive_resolver=slow_interactive_resolver,
            )
            assert clarified is True
            assert dl is not None
            assert pytest.approx(dl.remaining(), rel=1e-3) == 3600.0
            check_deadline()

    assert judge_report["output_2_evaluation"]["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
    assert final_decision["output_2_pass"] is True
    assert final_decision["status"] == "PASS"

    # Simulate --resume with no interactive resolver bound: must restore signed approval from ledger
    resumed_report = {
        "status": "FAIL",
        "overall_verdict": "FAIL",
        "output_1_evaluation": {"status": "PASS", "verdict": "PASS", "composite_score": 96},
        "output_2_evaluation": {
            "status": "HITL_REQUIRED",
            "verdict": "HITL_REQUIRED",
            "composite_score": 87.0,
            "section_scores": {
                "header_and_overview": 9,
                "key_updates_technical_substance": 31,
                "conditional_extensions": 17,
                "appendix_stakeholder_perspectives": 30,
            },
            "forensic_rationale": "Borderline deduction on recap depth.",
        },
        "output_3_evaluation": {"status": "PASS", "verdict": "PASS", "composite_score": 92},
    }
    resumed_decision = {
        "attempt": 3,
        "max_attempts": 3,
        "status": "FAIL",
        "output_1_deterministic_gates_pass": False,  # Deterministic gate failed on Output 01
        "output_1_pass": False,
        "output_2_deterministic_gates_pass": True,
        "output_2_pass": False,
        "output_3_deterministic_gates_pass": True,
        "output_3_pass": True,
    }
    resumed_meta = dict(meta, resume=True)
    clarified_resume = evaluate_judge_clarification(
        resumed_report,
        resumed_decision,
        resumed_meta,
        store=store,
        interactive_resolver=None,
    )
    # Output 02 is clarified via prior signed ledger entry, but overall status stays FAIL because Output 01 failed
    assert clarified_resume is False
    assert resumed_report["output_2_evaluation"]["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
    assert resumed_decision["output_2_pass"] is True
    assert resumed_decision["output_1_pass"] is False
    assert resumed_decision["status"] == "FAIL"
