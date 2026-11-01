"""
tests/end_to_end/test_judge_convergence_e2e.py

End-to-End lifecycle test for Wave 3: Judge Convergence & QA Gate Consistency.
Verifies:
1. QA short-circuiting prevents cascading LLM judge evaluations and records non-zero attempts.
2. Malformed scorecard triggers exactly one bounded retry before hard failing.
3. Identical artifact digests reuse cached evaluations with zero duplicate judge calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
from dual_pipeline.transcript_qa_core.eval_cache import clear_evaluation_cache
from dual_pipeline.llm_judge_core.judges.recap_transport import generate_client_recap


class TestJudgeConvergenceE2E:
    def setup_method(self):
        clear_evaluation_cache()

    def teardown_method(self):
        clear_evaluation_cache()

    def test_e2e_deterministic_qa_failure_short_circuit_and_diagnostics(self):
        """QA collapse short-circuits evaluation without LLM calls and records valid attempt diagnostics."""
        f1 = "Alice (00:00:01): Project kickoff.\nBob (00:00:05): Confirmed."
        f2 = "Malformed recap missing all mandatory sections"
        f3 = '{"entities": [], "decisions": [], "risks": []}'
        meta = {
            "attempt": 1,
            "max_attempts": 2,
            "write_diagnostics": False,
        }

        with patch("dual_pipeline.transcript_qa_core.evaluator._run_decomposed_judges") as mock_judge:
            qa_rep, judge_rep, decision = evaluate_judge_and_release(
                f1, f2, f3, source_text=f1, metadata=meta, mock=False, allow_partial_judge=False
            )

        # Verified: Decomposed LLM judges were NOT called on total QA collapse
        mock_judge.assert_not_called()
        assert decision["short_circuit"] is True
        assert decision["short_circuit_reason"] == "DETERMINISTIC_QA_COLLAPSE"
        assert decision["attempt"] == 1
        assert decision["status"] == "FAIL"

        # Diagnostics verified: total_attempts is 1, not 0
        diag = decision.get("judge_failure_diagnostics", {})
        assert diag["metadata"]["total_attempts"] == 1
        assert diag["metadata"]["total_failures"] > 0

    def test_e2e_malformed_scorecard_bounded_retry_and_recovery(self):
        """Malformed scorecard triggers 1 bounded retry; recovers if retry is valid."""
        mock_client = MagicMock()
        mock_recorder = MagicMock()

        # Attempt 1: malformed (missing section_scores)
        bad_resp = MagicMock()
        bad_choice = MagicMock()
        bad_choice.message.content = '{"composite_score": 90, "verdict": "PASS"}'
        bad_resp.choices = [bad_choice]

        # Attempt 2 (retry): valid scorecard with section_scores summing to 92
        good_resp = MagicMock()
        good_choice = MagicMock()
        good_choice.message.content = json.dumps({
            "composite_score": 92,
            "verdict": "PASS",
            "section_scores": {
                "header_and_overview": 10.0,
                "key_updates_technical_substance": 32.0,
                "conditional_extensions": 18.0,
                "appendix_stakeholder_perspectives": 32.0,
            }
        })
        good_resp.choices = [good_choice]

        bounded_client = MagicMock()
        bounded_client.chat.completions.create.side_effect = [bad_resp, good_resp]

        with patch("dual_pipeline.llm_judge_core.judges.recap_transport.without_sdk_retries", return_value=bounded_client):
            res = generate_client_recap(
                mock_client,
                model="gpt-6-luna",
                reasoning_effort="high",
                system_prompt="system",
                user_content="user",
                recorder=mock_recorder,
            )

        assert res["composite_score"] == 92
        assert res["verdict"] == "PASS"
        # Bounded client was called twice (initial + 1 bounded retry)
        assert bounded_client.chat.completions.create.call_count == 2

    def test_e2e_unchanged_digest_avoids_repeated_judge_evaluations(self):
        """Second evaluation of unchanged content hits cache without re-dispatching LLM judge."""
        f1 = "Alice (00:00:01): Kickoff.\nBob (00:00:05): Confirmed."
        f2 = "# Executive Summary\nSubstantive recap.\n# Action & Governance Ledger\n| Item | Owner |\n| --- | --- |\n| Task | Alice |"
        f3 = '{"entities": [{"id": "person:alice"}], "decisions": [{"id": "d1"}], "risks": []}'
        meta = {"attempt": 1, "max_attempts": 2}

        # Mock decomposed judges' evaluate methods
        mock_eval1 = {
            "composite_score": 95, "verdict": "PASS", "evaluator": "Forensic",
            "dimension_1": 23.75, "dimension_2": 23.75, "dimension_3": 23.75, "dimension_4": 23.75,
            "is_valid_scorecard": True,
        }
        mock_eval2 = {
            "composite_score": 92, "verdict": "PASS", "evaluator": "Recap",
            "section_scores": {
                "header_and_overview": 10.0, "key_updates_technical_substance": 32.0,
                "conditional_extensions": 18.0, "appendix_stakeholder_perspectives": 32.0,
            },
            "is_valid_scorecard": True,
        }
        mock_eval3 = {
            "composite_score": 94, "verdict": "PASS", "evaluator": "Graph",
            "dimension_1": 14, "dimension_2": 14, "dimension_3": 19,
            "dimension_4": 14, "dimension_5": 14, "dimension_6": 19,
            "is_valid_scorecard": True,
        }

        mock_qa_pass = {
            "passed": True,
            "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }

        with patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates", return_value=mock_qa_pass), \
             patch("dual_pipeline.transcript_qa_core.evaluator._resolve_client", return_value=(MagicMock(), False)), \
             patch("dual_pipeline.llm_judge_core.judges.forensic.ForensicTranscriptJudge.evaluate", return_value=mock_eval1) as m_f, \
             patch("dual_pipeline.llm_judge_core.judges.recap.ExecutiveRecapJudge.evaluate", return_value=mock_eval2) as m_r, \
             patch("dual_pipeline.llm_judge_core.judges.graph.GraphExtractionJudge.evaluate", return_value=mock_eval3) as m_g:

            qa1, j1, dec1 = evaluate_judge_and_release(
                f1, f2, f3, source_text=f1, metadata=meta, mock=False, force_judge_on_qa_fail=True
            )
            assert m_f.call_count == 1
            assert m_r.call_count == 1
            assert m_g.call_count == 1

            # Second call on identical artifacts: should resolve from cache!
            meta2 = {"attempt": 2, "max_attempts": 2}
            qa2, j2, dec2 = evaluate_judge_and_release(
                f1, f2, f3, source_text=f1, metadata=meta2, mock=False, force_judge_on_qa_fail=True
            )
            # The second call must not invoke the LLM judges again because all 3 were cached
            assert m_f.call_count == 1
            assert m_r.call_count == 1
            assert m_g.call_count == 1
