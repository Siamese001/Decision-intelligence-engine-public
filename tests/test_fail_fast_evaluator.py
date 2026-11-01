"""
Tests for fail-fast QA short-circuiting in transcript QA evaluator.
Asserts that when deterministic QA fails, expensive LLM judge calls are skipped.
"""

from unittest.mock import patch

from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release


def test_short_circuit_on_qa_gate_failure():
    """Verify that when deterministic QA fails, LLM judges are NOT invoked."""
    broken_f1 = "Not a transcript"
    broken_f2 = "Not an executive recap"
    broken_f3 = "{ invalid json"

    with patch("dual_pipeline.transcript_qa_core.evaluator._run_decomposed_judges") as mock_judge:
        qa_report, judge_report, final_decision = evaluate_judge_and_release(
            f1_text=broken_f1,
            f2_text=broken_f2,
            f3_text=broken_f3,
            source_text="Some source",
            mock=True,
            auto_heal=False,
        )

        assert qa_report["passed"] is False
        assert mock_judge.call_count == 0
        assert judge_report["evaluation_mode"] == "SKIPPED_DETERMINISTIC_QA_FAILURE"
        assert judge_report["status"] == "FAIL"
        assert judge_report["skipped_reason"] == "DETERMINISTIC_QA_FAILED"
        assert final_decision["final_pass"] is False
        assert final_decision["authoritative_write_authorized"] is False
        assert final_decision["output_1_llm_score_pass"] is False
        assert final_decision["output_2_llm_score_pass"] is False
        assert final_decision["output_3_llm_score_pass"] is False


def test_force_judge_on_qa_fail_override():
    """Verify that passing force_judge_on_qa_fail=True bypasses short-circuiting."""
    broken_f1 = "Not a transcript"
    broken_f2 = "Not an executive recap"
    broken_f3 = "{ invalid json"

    with patch("dual_pipeline.transcript_qa_core.evaluator._run_decomposed_judges") as mock_judge:
        mock_judge.return_value = (
            {"composite_score": 50, "verdict": "FAIL"},
            {"composite_score": 50, "verdict": "FAIL"},
            {"composite_score": 50, "verdict": "FAIL"},
            {"status": "FAIL", "passed": False},
        )
        qa_report, judge_report, final_decision = evaluate_judge_and_release(
            f1_text=broken_f1,
            f2_text=broken_f2,
            f3_text=broken_f3,
            source_text="Some source",
            mock=True,
            auto_heal=False,
            force_judge_on_qa_fail=True,
        )

        assert qa_report["passed"] is False
        assert mock_judge.call_count == 1
        assert judge_report["evaluation_mode"] == "DECOMPOSED_CONCURRENT"
