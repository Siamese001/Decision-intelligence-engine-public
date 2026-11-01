"""
tests/test_judge_scorecard_enforcement_wave4.py

Wave 4 Verification Suite for Meeting Output LLM-as-a-Judge Hardening.
Verifies:
1. Strict scorecard validation immediately after micro-judge responses and before writing to cache.
2. Malformed card {"composite_score": 100} fails in full evaluate_judge_and_release for Output 1, Output 2, and Output 3.
3. Cache and pin reads strictly reject malformed cards (check_pin, check_cache, update_evaluation_cache).
4. Monotonic candidate selection (MonotonicGuard) compares validated decision vectors; composite-only improvement cannot restore an invalid card.
5. HITL borderline clarification (evaluate_judge_clarification) requires valid scorecard structure before affirming.
6. Lazy MasterVectorEvaluator initialization ensures DecomposedMultiJudge does not load heavy vector backend upon instantiation.
7. Quality aggregate score calculations reject invalid cards while preserving explicit SKIPPED/NOT_APPLICABLE semantics.
8. Authoritative release gate (_check_judge_report) fails closed on malformed scorecards.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Dict
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.quality_aggregates import (
    compute_quality_aggregate_score,
    get_evaluated_composite_scores,
)
from dual_pipeline.llm_judge_core.judge_clarification import (
    evaluate_judge_clarification,
)
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.release_policy import (
    _check_judge_report,
    compute_release_evaluation_digest,
    validate_micro_judge_scorecard,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    _EVALUATION_CACHE,
    check_cache,
    check_pin,
    update_evaluation_cache,
)
from dual_pipeline.transcript_qa_core.evaluator import (
    evaluate_judge_and_release,
)
from dual_pipeline.transcript_qa_core.monotonic_guard import (
    MonotonicGuard,
)


def _make_valid_out1_card(score: int = 90, verdict: Optional[str] = None) -> Dict[str, Any]:
    dim = score / 4.0
    eff_verdict = verdict or ("PASS" if score >= 85 else "PASS_ADVISORY")
    return {
        "status": "PASS",
        "verdict": eff_verdict,
        "composite_score": score,
        "dimension_1": dim,
        "dimension_2": dim,
        "dimension_3": dim,
        "dimension_4": dim,
        "evidence_id_references": ["turn-1", "turn-2"],
        "forensic_rationale": "High fidelity transcript matching ground truth audio.",
        "judge_model": "test-judge-forensic",
        "prompt_digest": "digest_o1_test",
        "rubric_version": "2.0",
        "sample_count": 1,
        "is_fallback": False,
    }


def _make_valid_out2_card(score: int = 90) -> Dict[str, Any]:
    # Specs: header_and_overview max 10, key_updates max 35, cond_ext max 20, appendix max 35
    scale = score / 100.0
    s1 = round(10.0 * scale, 2)
    s2 = round(35.0 * scale, 2)
    s3 = round(20.0 * scale, 2)
    s4 = round(score - (s1 + s2 + s3), 2)
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": score,
        "section_scores": {
            "header_and_overview": s1,
            "key_updates_technical_substance": s2,
            "conditional_extensions": s3,
            "appendix_stakeholder_perspectives": s4,
        },
        "executive_rationale": "Structured executive recap covering technical milestones.",
        "judge_model": "test-judge-recap",
        "prompt_digest": "digest_o2_test",
        "rubric_version": "2.0",
        "sample_count": 1,
        "is_fallback": False,
    }


def _make_valid_out3_card(score: int = 92) -> Dict[str, Any]:
    # 6 dimensions: 15, 15, 20, 15, 15, 20 (floors: 11, 11, 14, 11, 11, 14)
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": 92.0,
        "dimension_1_strategic_edge": 14.0,
        "dimension_2_knowledge_layer_density": 14.0,
        "dimension_3_semantic_atomicity": 18.0,
        "dimension_4_predicate_specificity": 14.0,
        "dimension_5_exclusion_of_action_items": 14.0,
        "dimension_6_evidence_grounding_and_schema": 18.0,
        "evidence_id_references": ["stmt-1", "stmt-2"],
        "graph_rationale": "Strong semantic atomicity and valid entity schema grounding.",
        "judge_model": "test-judge-graph",
        "prompt_digest": "digest_o3_test",
        "rubric_version": "2.0",
        "sample_count": 1,
        "is_fallback": False,
    }


class TestWave4ScorecardStrictValidation(unittest.TestCase):
    """Verifies that validate_micro_judge_scorecard catches all invalid and malformed scorecards."""

    def test_malformed_composite_100_rejected_across_all_outputs(self) -> None:
        """The council defect payload {'composite_score': 100, 'verdict': 'PASS'} is rejected for O1, O2, and O3."""
        malformed = {"composite_score": 100, "verdict": "PASS"}

        # Output 1
        ok1, errs1 = validate_micro_judge_scorecard(malformed, output_num=1)
        self.assertFalse(ok1)
        self.assertTrue(any("dimension" in e for e in errs1))

        # Output 2
        ok2, errs2 = validate_micro_judge_scorecard(malformed, output_num=2)
        self.assertFalse(ok2)
        self.assertTrue(any("section_scores" in e for e in errs2))

        # Output 3
        ok3, errs3 = validate_micro_judge_scorecard(malformed, output_num=3)
        self.assertFalse(ok3)
        self.assertTrue(any("dimension" in e for e in errs3))

    def test_output1_floor_violation_fails_closed(self) -> None:
        """Output 1 scorecard with a dimension below 14.0 cannot yield verdict PASS."""
        card = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 82.0,
            "dimension_1": 12.0,  # Below 14.0 floor
            "dimension_2": 23.0,
            "dimension_3": 23.0,
            "dimension_4": 24.0,
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("mandatory floor 14.0" in e for e in errs))

    def test_output3_floor_violation_fails_closed(self) -> None:
        """Output 3 scorecard with a dimension below floor cannot yield verdict PASS."""
        card = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 86.0,
            "dimension_1_strategic_edge": 9.0,  # Below 11.0 floor
            "dimension_2_knowledge_layer_density": 15.0,
            "dimension_3_semantic_atomicity": 18.0,
            "dimension_4_predicate_specificity": 14.0,
            "dimension_5_exclusion_of_action_items": 14.0,
            "dimension_6_evidence_grounding_and_schema": 16.0,
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=3)
        self.assertFalse(ok)
        self.assertTrue(any("below mandatory floor 11" in e for e in errs))

    def test_composite_sum_mismatch_fails_closed(self) -> None:
        """Scorecard where composite_score differs from dimension sum fails validation."""
        card = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 99.0,  # Sum is 80.0
            "dimension_1": 20.0,
            "dimension_2": 20.0,
            "dimension_3": 20.0,
            "dimension_4": 20.0,
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("does not match dimension sum" in e for e in errs))

    def test_skipped_reason_whitelist(self) -> None:
        """Legitimate SKIPPED reasons are accepted; unauthorized skip stubs fail closed."""
        valid_notes = {"status": "SKIPPED", "composite_score": None, "verdict": "NOT_APPLICABLE", "reason": "NOT_APPLICABLE_NOTES_MODE"}
        ok, _ = validate_micro_judge_scorecard(valid_notes, output_num=1)
        self.assertTrue(ok)

        # Fake 100 on SKIPPED card must be rejected
        fake_100_skip = {"status": "SKIPPED", "composite_score": 100, "verdict": "PASS", "reason": "NOT_APPLICABLE_NOTES_MODE"}
        ok_fake, errs_fake = validate_micro_judge_scorecard(fake_100_skip, output_num=1)
        self.assertFalse(ok_fake)
        self.assertTrue(any("composite_score: null" in e for e in errs_fake))

        unauthorized_skip = {"status": "SKIPPED", "composite_score": None, "verdict": "NOT_APPLICABLE", "reason": "UNAUTHORIZED_BYPASS"}
        ok, errs = validate_micro_judge_scorecard(unauthorized_skip, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("unauthorized reason" in e for e in errs))

        # FROZEN_PRIOR_ARTIFACT with verdict PASS must be accepted
        frozen_pass = {"status": "SKIPPED", "composite_score": 90.0, "verdict": "PASS", "reason": "FROZEN_PRIOR_ARTIFACT"}
        ok_frozen, _ = validate_micro_judge_scorecard(frozen_pass, output_num=1)
        self.assertTrue(ok_frozen)


class TestWave4EvaluateJudgeAndReleaseScorecardEnforcement(unittest.TestCase):
    """Verifies that evaluate_judge_and_release fails closed on malformed cards for each output."""

    def setUp(self) -> None:
        from dual_pipeline.transcript_qa_core.eval_cache import clear_evaluation_cache
        clear_evaluation_cache()

    def tearDown(self) -> None:
        from dual_pipeline.transcript_qa_core.eval_cache import clear_evaluation_cache
        clear_evaluation_cache()

    @patch("dual_pipeline.transcript_qa_core.evaluator._resolve_client", return_value=(MagicMock(), False))
    @patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates")
    @patch("dual_pipeline.transcript_qa_core.evaluator.DecomposedMultiJudge")
    def test_output1_malformed_composite_100_fails_release(
        self, mock_judge_cls: MagicMock, mock_qa: MagicMock, mock_client: MagicMock
    ) -> None:
        """Output 1 returning {'composite_score': 100} fails j1_pass and blocks authoritative write."""
        mock_qa.return_value = {
            "passed": True,
            "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }
        judge_inst = MagicMock()
        mock_judge_cls.return_value = judge_inst

        # O1 returns malformed composite 100, O2 and O3 return valid cards
        judge_inst.judge_forensic.evaluate.return_value = {"composite_score": 100, "verdict": "PASS"}
        judge_inst.judge_recap.evaluate.return_value = _make_valid_out2_card(90)
        judge_inst.judge_graph.evaluate.return_value = _make_valid_out3_card(92)
        judge_inst.reconciler.reconcile.return_value = {"status": "SUCCESS", "passed": True}

        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text="Alice: Hello world.",
            f2_text="# 1. Meeting Objective\nReview.",
            f3_text=json.dumps({"entities": [], "decisions": [], "risks": []}),
            metadata={"meeting_name": "Test Meeting", "attendees": ["Alice"]},
            force_judge_on_qa_fail=True,
        )

        # O1 evaluation must be stamped invalid and fail
        o1_eval = judge_rep["output_1_evaluation"]
        self.assertFalse(o1_eval.get("is_valid_scorecard"))
        self.assertEqual(o1_eval.get("verdict"), "FAIL")
        self.assertFalse(final_dec.get("output_1_llm_score_pass"))
        self.assertFalse(final_dec.get("authoritative_write_authorized"))

    @patch("dual_pipeline.transcript_qa_core.evaluator._resolve_client", return_value=(MagicMock(), False))
    @patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates")
    @patch("dual_pipeline.transcript_qa_core.evaluator.DecomposedMultiJudge")
    def test_output2_malformed_composite_100_fails_release(
        self, mock_judge_cls: MagicMock, mock_qa: MagicMock, mock_client: MagicMock
    ) -> None:
        """Output 2 returning {'composite_score': 100} fails j2_pass and blocks authoritative write."""
        mock_qa.return_value = {
            "passed": True,
            "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }
        judge_inst = MagicMock()
        mock_judge_cls.return_value = judge_inst

        judge_inst.judge_forensic.evaluate.return_value = _make_valid_out1_card(90)
        judge_inst.judge_recap.evaluate.return_value = {"composite_score": 100, "verdict": "PASS"}
        judge_inst.judge_graph.evaluate.return_value = _make_valid_out3_card(92)
        judge_inst.reconciler.reconcile.return_value = {"status": "SUCCESS", "passed": True}

        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text="Alice: Hello world.",
            f2_text="# 1. Meeting Objective\nReview.",
            f3_text=json.dumps({"entities": [], "decisions": [], "risks": []}),
            metadata={"meeting_name": "Test Meeting", "attendees": ["Alice"]},
            force_judge_on_qa_fail=True,
        )

        o2_eval = judge_rep["output_2_evaluation"]
        self.assertFalse(o2_eval.get("is_valid_scorecard"))
        self.assertEqual(o2_eval.get("verdict"), "FAIL")
        self.assertFalse(final_dec.get("output_2_llm_score_pass"))
        self.assertFalse(final_dec.get("authoritative_write_authorized"))

    @patch("dual_pipeline.transcript_qa_core.evaluator._resolve_client", return_value=(MagicMock(), False))
    @patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates")
    @patch("dual_pipeline.transcript_qa_core.evaluator.DecomposedMultiJudge")
    def test_output3_malformed_composite_100_fails_release(
        self, mock_judge_cls: MagicMock, mock_qa: MagicMock, mock_client: MagicMock
    ) -> None:
        """Output 3 returning {'composite_score': 100} fails j3_pass and blocks authoritative write."""
        mock_qa.return_value = {
            "passed": True,
            "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }
        judge_inst = MagicMock()
        mock_judge_cls.return_value = judge_inst

        judge_inst.judge_forensic.evaluate.return_value = _make_valid_out1_card(90)
        judge_inst.judge_recap.evaluate.return_value = _make_valid_out2_card(90)
        judge_inst.judge_graph.evaluate.return_value = {"composite_score": 100, "verdict": "PASS"}
        judge_inst.reconciler.reconcile.return_value = {"status": "SUCCESS", "passed": True}

        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text="Alice: Hello world.",
            f2_text="# 1. Meeting Objective\nReview.",
            f3_text=json.dumps({"entities": [], "decisions": [], "risks": []}),
            metadata={"meeting_name": "Test Meeting", "attendees": ["Alice"]},
            force_judge_on_qa_fail=True,
        )

        o3_eval = judge_rep["output_3_evaluation"]
        self.assertFalse(o3_eval.get("is_valid_scorecard"))
        self.assertEqual(o3_eval.get("verdict"), "FAIL")
        self.assertFalse(final_dec.get("output_3_llm_score_pass"))
        self.assertFalse(final_dec.get("authoritative_write_authorized"))


class TestWave4CacheAndPinPurity(unittest.TestCase):
    """Verifies that malformed scorecards cannot be pinned or cached."""

    def test_check_pin_rejects_malformed_pinned_card(self) -> None:
        """check_pin rejects a card containing only composite_score: 100 even if SHA matches."""
        raw_text = "Alice: Test transcript."
        sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        pinned = {
            "output_1": {
                "composite_score": 100,
                "verdict": "PASS",
                "artifact_sha256": sha,
            }
        }
        res = check_pin(pinned, "output_1", "output_01", min_score=85, expected_sha=sha, text=raw_text)
        self.assertIsNone(res)

    def test_check_pin_accepts_valid_scorecard(self) -> None:
        """check_pin returns valid scorecard when all dimensions and checksums pass."""
        raw_text = "Alice: Test transcript."
        sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        card = _make_valid_out1_card(92)
        card["artifact_sha256"] = sha
        pinned = {"output_1": card}

        res = check_pin(pinned, "output_1", "output_01", min_score=85, expected_sha=sha, text=raw_text)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("source"), "PINNED_CACHE")

    def test_update_evaluation_cache_refuses_malformed_cards(self) -> None:
        """update_evaluation_cache does not insert invalid cards into memory cache."""
        k1 = "out1:test_key_k1"
        k2 = "out2:test_key_k2"
        k3 = "out3:test_key_k3"
        malformed = {"composite_score": 100, "verdict": "PASS"}

        # Clear test keys
        _EVALUATION_CACHE.pop(k1, None)
        _EVALUATION_CACHE.pop(k2, None)
        _EVALUATION_CACHE.pop(k3, None)

        update_evaluation_cache(k1, malformed, k2, malformed, k3, malformed)
        self.assertNotIn(k1, _EVALUATION_CACHE)
        self.assertNotIn(k2, _EVALUATION_CACHE)
        self.assertNotIn(k3, _EVALUATION_CACHE)

    def test_check_cache_rejects_manually_injected_malformed_entry(self) -> None:
        """check_cache filters out invalid cards that bypass insertion checks."""
        bad_key = "out1:corrupted_cache_key"
        _EVALUATION_CACHE[bad_key] = {"composite_score": 100, "verdict": "PASS"}
        try:
            res = check_cache(bad_key, min_score=85)
            self.assertIsNone(res)
        finally:
            _EVALUATION_CACHE.pop(bad_key, None)


class TestWave4MonotonicGuardEnforcement(unittest.TestCase):
    """Verifies that MonotonicGuard enforces validated decision vectors."""

    def test_invalid_scorecard_cannot_establish_initial_baseline(self) -> None:
        """Candidate with invalid scorecard is rejected; baseline is not established."""
        guard = MonotonicGuard(artifact_name="output_1")
        malformed = {"composite_score": 100, "verdict": "PASS"}
        eff_text, eff_card, improved = guard.evaluate_candidate(
            attempt=1, composite_score=100, artifact_text="raw", scorecard=malformed
        )
        self.assertFalse(improved)
        self.assertIsNone(guard.best_checkpoint)

    def test_higher_composite_score_with_invalid_scorecard_rolls_back(self) -> None:
        """Candidate with higher composite but missing dimensions rolls back to prior valid baseline."""
        guard = MonotonicGuard(artifact_name="output_1")
        valid1 = _make_valid_out1_card(80)
        eff_text, eff_card, improved = guard.evaluate_candidate(
            attempt=1, composite_score=80, artifact_text="attempt1_text", scorecard=valid1
        )
        self.assertTrue(improved)
        self.assertEqual(guard.best_score, 80)

        # Attempt 2 has higher score 95, but scorecard is malformed
        malformed2 = {"composite_score": 95, "verdict": "PASS"}
        eff_text2, eff_card2, improved2 = guard.evaluate_candidate(
            attempt=2, composite_score=95, artifact_text="attempt2_text", scorecard=malformed2
        )
        self.assertFalse(improved2)
        # Rolled back to attempt 1
        self.assertEqual(eff_text2, "attempt1_text")
        self.assertEqual(eff_card2["composite_score"], 80)
        self.assertEqual(guard.best_score, 80)

    def test_valid_scorecard_improvement_is_accepted(self) -> None:
        """Valid scorecard with higher composite score becomes new best checkpoint."""
        guard = MonotonicGuard(artifact_name="output_1")
        valid1 = _make_valid_out1_card(80)
        guard.evaluate_candidate(attempt=1, composite_score=80, artifact_text="att1", scorecard=valid1)

        valid2 = _make_valid_out1_card(92)
        eff_text, eff_card, improved = guard.evaluate_candidate(
            attempt=2, composite_score=92, artifact_text="att2", scorecard=valid2
        )
        self.assertTrue(improved)
        self.assertEqual(eff_text, "att2")
        self.assertEqual(guard.best_score, 92)


class TestWave4JudgeClarificationEnforcement(unittest.TestCase):
    """Verifies that borderline HITL clarification requires valid scorecard structure."""

    def test_borderline_clarification_fails_closed_on_malformed_card(self) -> None:
        """Clarification rejects candidate with malformed scorecard even if resolver affirms."""
        resolver = MagicMock(return_value="AFFIRM_VALID")
        judge_rep = {
            "output_1_evaluation": {"composite_score": 85, "verdict": "HITL_REQUIRED"},
        }
        final_dec = {"status": "FAIL", "authoritative_write_authorized": False}
        meta = {
            "reviewer": "operator_human",
            "approval_source": "operator_human",
            "prior_user_command": "operator affirmative confirmation",
            "interactive_judge_resolver": resolver,
        }

        evaluate_judge_clarification(judge_rep, final_dec, metadata=meta)
        # Should NOT affirm because card is malformed
        self.assertEqual(judge_rep["output_1_evaluation"].get("verdict"), "HITL_REQUIRED")
        self.assertFalse(final_dec.get("authoritative_write_authorized"))


class TestWave4LazyEvaluatorIsolation(unittest.TestCase):
    """Verifies that DecomposedMultiJudge does not eagerly instantiate heavy vector models."""

    def test_decomposed_multi_judge_evaluator_is_lazy(self) -> None:
        """Instantiating DecomposedMultiJudge leaves _evaluator as None."""
        judge = DecomposedMultiJudge(model="test-model", reasoning_effort="high")
        self.assertIsNone(judge._evaluator)
        self.assertIsNotNone(judge.judge_forensic)
        self.assertIsNotNone(judge.judge_recap)
        self.assertIsNotNone(judge.judge_graph)


class TestWave4QualityAggregatesAndReleaseGate(unittest.TestCase):
    """Verifies that quality aggregates and release gates reject invalid cards."""

    def test_get_evaluated_composite_scores_rejects_malformed_cards(self) -> None:
        """Quality aggregates exclude cards that fail micro judge validation."""
        judge_rep = {
            "output_1_evaluation": _make_valid_out1_card(90),
            "output_2_evaluation": {"composite_score": 100, "verdict": "PASS"},  # Malformed
            "output_3_evaluation": {"status": "SKIPPED", "reason": "NOT_APPLICABLE_DOCUMENT_MODE"},
        }
        scores = get_evaluated_composite_scores(judge_rep)
        self.assertIn("output_1", scores)
        self.assertEqual(scores["output_1"], 90.0)
        self.assertNotIn("output_2", scores)
        self.assertNotIn("output_3", scores)

        agg = compute_quality_aggregate_score(judge_rep)
        self.assertEqual(agg, 90.0)

    def test_check_judge_report_rejects_malformed_output3(self) -> None:
        """_check_judge_report raises RuntimeError on malformed Output 3 scorecard."""
        with tempfile.TemporaryDirectory() as td:
            j_file = Path(td) / "llm_judge_evaluation.json"
            report = {
                "evaluator_model": "test-model",
                "output_3_evaluation": {"composite_score": 100, "verdict": "PASS"},
            }
            report["evaluation_receipt_digest"] = compute_release_evaluation_digest(report)
            j_file.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                _check_judge_report(j_file, "test_pkg")
            self.assertIn("malformed Output 3 scorecard", str(ctx.exception))


class TestWave2Output2AndOutput3AntiBolstering(unittest.TestCase):
    """Wave 2 verification for Output 2 composite recomputation/floors and Output 3 vacuous graph rejection."""

    def test_output2_finalize_envelope_recomputes_composite_and_enforces_floors(self) -> None:
        """_finalize_envelope recomputes composite_score after penalties and fails below-floor dimensions."""
        from dual_pipeline.llm_judge_core.judges.recap import _finalize_envelope

        # Raw LLM claims composite_score=94 and PASS, but section_scores after penalty sum to 83 with conditional_extensions=8.0 (<11.0 floor)
        raw_res = {
            "composite_score": 94,
            "verdict": "PASS",
            "section_scores": {
                "header_and_overview": 9.0,
                "key_updates_technical_substance": 32.0,
                "conditional_extensions": 8.0,
                "appendix_stakeholder_perspectives": 34.0,
            },
            "executive_rationale": "Test rationale.",
            "defect_codes": ["UNASSIGNED_ACTION_ITEM"],
        }
        finalized = _finalize_envelope(raw_res)
        self.assertEqual(finalized["composite_score"], 83)
        self.assertEqual(finalized["verdict"], "FAIL")
        self.assertTrue(any("FLOOR_VIOLATION" in code for code in finalized.get("defect_codes", [])))

    def test_output2_borderline_85_to_89_defaults_to_fail_not_hitl(self) -> None:
        """_finalize_envelope sets verdict='FAIL' for [85, 90) instead of auto-tagging HITL_REQUIRED."""
        from dual_pipeline.llm_judge_core.judges.recap import _finalize_envelope

        raw_res = {
            "composite_score": 88,
            "verdict": "PASS",
            "section_scores": {
                "header_and_overview": 8.0,
                "key_updates_technical_substance": 31.0,
                "conditional_extensions": 17.0,
                "appendix_stakeholder_perspectives": 32.0,
            },
            "executive_rationale": "Solid recap but slightly below 90 threshold.",
        }
        finalized = _finalize_envelope(raw_res)
        self.assertEqual(finalized["composite_score"], 88)
        self.assertEqual(finalized["verdict"], "FAIL")

    def test_output2_section_and_dimension_floor_validation(self) -> None:
        """validate_micro_judge_scorecard rejects Output 2 cards with composite >= 90 if a section or dimension is below floor."""
        card_sec_fail = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 90.0,
            "section_scores": {
                "header_and_overview": 4.0,  # Below 5.5 floor
                "key_updates_technical_substance": 34.0,
                "conditional_extensions": 18.0,
                "appendix_stakeholder_perspectives": 34.0,
            },
            "executive_rationale": "Header missing key context.",
        }
        ok_sec, errs_sec = validate_micro_judge_scorecard(card_sec_fail, output_num=2)
        self.assertFalse(ok_sec)
        self.assertTrue(any("header_and_overview" in e and "mandatory floor" in e for e in errs_sec))

        card_dim_fail = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 90.0,
            "section_scores": {
                "header_and_overview": 9.0,
                "key_updates_technical_substance": 32.0,
                "conditional_extensions": 18.0,
                "appendix_stakeholder_perspectives": 31.0,
            },
            "dimension_1_executive_altitude": 25.0,
            "dimension_2_technical_substance": 25.0,
            "dimension_3_strategic_synthesis": 13.0,  # Below 14.0 floor
            "dimension_4_scoping_and_modality": 27.0,
            "executive_rationale": "Weak attribution.",
        }
        ok_dim, errs_dim = validate_micro_judge_scorecard(card_dim_fail, output_num=2)
        self.assertFalse(ok_dim)
        self.assertTrue(any("dimension_3" in e and "mandatory floor 14.0" in e for e in errs_dim))

    def test_output3_vacuous_graph_extraction_fails_closed(self) -> None:
        """Vacuous graph extraction (0 entities, 0 signals) on substantive source (>= 500 chars) fails closed with score 15."""
        from dual_pipeline.llm_judge_core.graph_checks import apply_deterministic_graph_scoring
        from dual_pipeline.llm_judge_core.judges.graph import _build_mock_graph_result

        substantive_source = ("David discussed the cloud migration architecture and governance controls with Alice. " * 10)
        self.assertGreaterEqual(len(substantive_source), 500)

        # 1. Mock graph result in DOCUMENT_MODE with 0 entities/signals must fail closed
        mock_res = _build_mock_graph_result(
            is_bipartite=False,
            leverage_scorecards=[],
            has_c19_leak=False,
            archetype="DOCUMENT_MODE",
            f3_parsed={"graph_mutations": {"entity_upserts": [], "relationship_signals": [], "decision_updates": []}, "evidence_index": {}},
            f1_text=substantive_source,
        )
        self.assertEqual(mock_res["verdict"], "FAIL")
        self.assertEqual(mock_res["composite_score"], 15)
        self.assertIn("ERR_VACUOUS_GRAPH_EXTRACTION", mock_res.get("defect_codes", []))

        # 2. apply_deterministic_graph_scoring in DOCUMENT_MODE with 0 entities/signals must cap score to 15 and FAIL
        parsed_eval = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": None,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 100,
            "verdict": "PASS",
        }
        scored = apply_deterministic_graph_scoring(
            parsed_eval,
            f3_parsed={"graph_mutations": {"entity_upserts": [], "relationship_signals": [], "decision_updates": []}, "evidence_index": {}},
            f1_text=substantive_source,
            archetype="DOCUMENT_MODE",
        )
        self.assertEqual(scored["verdict"], "FAIL")
        self.assertEqual(scored["composite_score"], 15)
        self.assertIn("ERR_VACUOUS_GRAPH_EXTRACTION", scored.get("defect_codes", []))


if __name__ == "__main__":
    unittest.main()

