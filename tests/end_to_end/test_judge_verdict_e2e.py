"""
tests/end_to_end/test_judge_verdict_e2e.py

End-to-End lifecycle tests verifying:
1. Frozen cards lifecycle (matching candidate SHA-256, null composite_score, release authorization, tamper rejection).
2. Notes mode lifecycle (skipped card, null composite_score, NOT_APPLICABLE verdict, quality aggregates, release clearance).
3. Code-computed verdict overwrite lifecycle (LLM PASS overwritten to FAIL on floor breach, raw provenance preserved).
4. Incomplete evaluated span release gate block (coverage < 1.0 or flag set blocks authoritative write).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.quality_aggregates import (
    compute_quality_aggregate_score,
)
from dual_pipeline.llm_judge_core.judges.graph_provenance import (
    assign_with_raw_scorecard,
)
from dual_pipeline.prompts.contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT3_CONTRACT,
)
from dual_pipeline.release_policy import (
    validate_micro_judge_scorecard,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    _build_skip_eval,
    clear_evaluation_cache,
)
from dual_pipeline.transcript_qa_core.evaluator import (
    evaluate_judge_and_release,
)


def _make_valid_out1_card(score: int = 90) -> Dict[str, Any]:
    dim = score / 4.0
    eff_verdict = "PASS" if score >= 90 else "PASS_ADVISORY"
    return {
        "status": eff_verdict,
        "verdict": eff_verdict,
        "composite_score": score,
        "dimension_1_forensic_grounding_and_fidelity": dim,
        "dimension_2_attribution_and_room_mic_hygiene": dim,
        "dimension_3_chronological_and_timestamp_integrity": dim,
        "dimension_4_asr_cleanup_and_domain_preservation": dim,
        "evidence_id_references": ["turn-1"],
        "evidence_references": ["turn-1"],
        "forensic_rationale": "High fidelity transcript matching audio.",
    }


def _make_valid_out2_card(score: int = 90) -> Dict[str, Any]:
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
        "executive_rationale": "Structured executive recap.",
    }


def _make_valid_out3_card(score: int = 92) -> Dict[str, Any]:
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": float(score),
        "dimension_1_strategic_edge": 14.0,
        "dimension_2_knowledge_layer_density": 14.0,
        "dimension_3_semantic_atomicity": 18.0,
        "dimension_4_predicate_specificity": 14.0,
        "dimension_5_exclusion_of_action_items": 14.0,
        "dimension_6_evidence_grounding_and_schema": 18.0,
        "forensic_rationale": "Strategic graph signal projection.",
    }


class TestJudgeVerdictE2E(unittest.TestCase):
    """E2E lifecycle suite for Judge Verdict and Floor Governance."""

    def setUp(self) -> None:
        clear_evaluation_cache()

    def tearDown(self) -> None:
        clear_evaluation_cache()

    def test_frozen_card_lifecycle_e2e(self) -> None:
        """E2E verification of frozen scorecard generation, SHA matching, and tamper rejection."""
        f1_content = "Alice: Ground truth transcript text."
        f1_sha = hashlib.sha256(f1_content.encode("utf-8")).hexdigest()

        # 1. Pipeline attempt 2 builds short-circuit skip eval from frozen QA section
        qa_report = {
            "passed": True,
            "output_01_forensic_transcript": {
                "passed": True,
                "status": "SKIPPED",
                "frozen": True,
                "sha256": f1_sha,
            },
        }

        skip_card = _build_skip_eval(qa_report, "output_01_forensic_transcript", f1_content)

        # Invariant checks on the emitted skip card
        self.assertEqual(skip_card["status"], "SKIPPED")
        self.assertEqual(skip_card["verdict"], "PASS")
        self.assertIsNone(skip_card["composite_score"], "Frozen card must emit composite_score: null")
        self.assertEqual(skip_card["reason"], "FROZEN_PRIOR_ARTIFACT")
        self.assertEqual(skip_card["candidate_sha256"], f1_sha)
        self.assertEqual(skip_card["frozen_sha256"], f1_sha)

        # 2. Scorecard validation accepts untampered frozen card matching candidate SHA
        ok, errs = validate_micro_judge_scorecard(
            skip_card, output_num=1, candidate_sha256=f1_sha, expected_sha256=f1_sha
        )
        self.assertTrue(ok, f"Expected frozen card to pass validation: {errs}")

        # 3. Tamper attempt: candidate SHA altered -> validation fails closed
        tampered_sha = hashlib.sha256(b"Tampered content").hexdigest()
        ok_tampered, errs_tampered = validate_micro_judge_scorecard(
            skip_card, output_num=1, candidate_sha256=tampered_sha
        )
        self.assertFalse(ok_tampered)
        self.assertTrue(any("does not match candidate SHA" in e for e in errs_tampered))

    @patch("dual_pipeline.transcript_qa_core.evaluator._resolve_client", return_value=(MagicMock(), False))
    @patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates")
    @patch("dual_pipeline.transcript_qa_core.evaluator.DecomposedMultiJudge")
    def test_notes_mode_lifecycle_e2e(
        self, mock_judge_cls: MagicMock, mock_qa: MagicMock, mock_client: MagicMock
    ) -> None:
        """E2E verification of notes mode: O1 skipped with null composite, quality aggregation, release clear."""
        mock_qa.return_value = {
            "passed": True,
            "output_01_forensic_transcript": {"passed": True, "status": "SKIPPED", "reason": "NOT_APPLICABLE_NOTES_MODE"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }
        judge_inst = MagicMock()
        mock_judge_cls.return_value = judge_inst

        judge_inst.judge_recap.evaluate.return_value = _make_valid_out2_card(90)
        judge_inst.judge_graph.evaluate.return_value = _make_valid_out3_card(92)
        judge_inst.reconciler.reconcile.return_value = {"status": "SUCCESS", "passed": True}

        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text="Informal notes line 1\nInformal notes line 2",
            f2_text="# 1. Meeting Objective\nRecap for notes mode.\n# 2. Action & Governance Ledger\n| Item | Action | DRI | Status |\n| 1 | Test | Alice | Done |",
            f3_text=json.dumps({"entities": [], "decisions": [], "risks": []}),
            metadata={"meeting_name": "Quick Sync", "pipeline_mode": "notes", "archetype": "GENERAL"},
            force_judge_on_qa_fail=False,
        )

        o1_eval = judge_rep["output_1_evaluation"]
        # Output 1 must be cleanly skipped with null composite and NOT_APPLICABLE verdict
        self.assertEqual(o1_eval["status"], "SKIPPED")
        self.assertIsNone(o1_eval["composite_score"], "Notes mode must have composite_score: null")
        self.assertEqual(o1_eval["verdict"], "NOT_APPLICABLE")
        self.assertEqual(o1_eval["reason"], "NOT_APPLICABLE_NOTES_MODE")
        self.assertTrue(o1_eval.get("is_valid_scorecard"))

        # Output 2 and Output 3 are evaluated and pass
        self.assertTrue(judge_rep["output_2_evaluation"].get("is_valid_scorecard"))
        self.assertTrue(judge_rep["output_3_evaluation"].get("is_valid_scorecard"))

        # Quality aggregate does not crash on None composite score, cleanly averaging O2 and O3
        qual_score = compute_quality_aggregate_score(judge_rep)
        self.assertIsNotNone(qual_score)
        self.assertEqual(qual_score, 91.0)

        # Release decision authorizes write without requiring Output 1 score
        self.assertTrue(final_dec.get("output_1_llm_score_pass"))
        self.assertTrue(final_dec.get("output_2_llm_score_pass"))
        self.assertTrue(final_dec.get("output_3_llm_score_pass"))
        self.assertTrue(final_dec.get("authoritative_write_authorized"))

    def test_floor_breach_code_overwrite_lifecycle_e2e(self) -> None:
        """E2E verification: LLM PASS verdict on floor breach is overwritten to FAIL with provenance preserved."""
        # Output 3 has dim 1 at 9.0 (floor is 11.0, non-borderline)
        raw_llm_scorecard = {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 94,
            "dimension_1_strategic_edge": 9,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "forensic_rationale": "LLM incorrectly affirmed high quality despite strategic defect.",
        }

        dim_keys = [
            "dimension_1_strategic_edge", "dimension_2_knowledge_layer_density",
            "dimension_3_semantic_atomicity", "dimension_4_predicate_specificity",
            "dimension_5_exclusion_of_action_items", "dimension_6_evidence_grounding_and_schema",
        ]
        floors = OUTPUT3_CONTRACT.get_dimension_floors()

        # 1. Code-computed verdict overwrite
        processed = assign_with_raw_scorecard(raw_llm_scorecard, dim_keys, floors)

        self.assertEqual(processed["verdict"], "FAIL", "Code must overwrite LLM PASS to FAIL on floor breach")
        self.assertIn("Per-dimension floor failure", processed["forensic_rationale"])
        self.assertEqual(processed["raw_judge_scorecard"]["verdict"], "PASS", "Raw LLM verdict must be preserved in provenance")

        # 2. Release policy validation verifies that raw card claiming PASS is rejected
        raw_claiming_pass = {**raw_llm_scorecard, "verdict": "PASS"}
        ok, errs = validate_micro_judge_scorecard(raw_claiming_pass, output_num=3)
        self.assertFalse(ok)
        self.assertTrue(any("below mandatory floor" in e for e in errs))

        # 3. Scorecard with code-assigned FAIL is valid under release policy
        ok_fail, _ = validate_micro_judge_scorecard(processed, output_num=3)
        self.assertTrue(ok_fail)

    @patch("dual_pipeline.transcript_qa_core.evaluator._resolve_client", return_value=(MagicMock(), False))
    @patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates")
    @patch("dual_pipeline.transcript_qa_core.evaluator.DecomposedMultiJudge")
    def test_incomplete_evaluated_span_blocks_release_e2e(
        self, mock_judge_cls: MagicMock, mock_qa: MagicMock, mock_client: MagicMock
    ) -> None:
        """E2E verification: Incomplete evaluated span flag strictly blocks authoritative release."""
        mock_qa.return_value = {
            "passed": True,
            "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }
        judge_inst = MagicMock()
        mock_judge_cls.return_value = judge_inst

        judge_inst.judge_forensic.evaluate.return_value = _make_valid_out1_card(92)
        judge_inst.judge_recap.evaluate.return_value = _make_valid_out2_card(90)

        # Output 3 has high score (92) but incomplete span flag set
        out3_card = _make_valid_out3_card(92)
        out3_card["incomplete_evaluated_span"] = True
        out3_card["evaluated_span_coverage"] = 0.75
        judge_inst.judge_graph.evaluate.return_value = out3_card
        judge_inst.reconciler.reconcile.return_value = {"status": "SUCCESS", "passed": True}

        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text="Alice: Ground truth dialogue.",
            f2_text="# 1. Meeting Objective\nRecap.\n# 2. Action & Governance Ledger\n| 1 | Task | Bob | Done |",
            f3_text=json.dumps({"entities": [], "decisions": []}),
            metadata={"meeting_name": "Full Session"},
            force_judge_on_qa_fail=False,
        )

        o3_eval = judge_rep["output_3_evaluation"]
        # Incomplete span flag invalidates PASS verdict
        self.assertFalse(o3_eval.get("is_valid_scorecard"))
        self.assertEqual(o3_eval.get("verdict"), "FAIL")
        self.assertFalse(final_dec.get("output_3_llm_score_pass"))
        self.assertFalse(final_dec.get("authoritative_write_authorized"), "Release MUST fail closed on incomplete evaluated span")


if __name__ == "__main__":
    unittest.main()
