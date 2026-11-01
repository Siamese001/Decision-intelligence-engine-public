"""
tests/test_judge_reliability_controls_adversarial.py

Adversarial and edge-case verification for Wave 4 Judge Reliability Controls:
1. Mandatory provenance fields (judge_model, prompt_digest, rubric_version, sample_count, is_fallback).
2. Self-evaluation prevention (Decision D1: judge must never be the generator model).
3. Provider 429 quota exhaustion routing to HITL (ERR_PROVIDER_429_HITL) with cache exclusion.
4. N-sample median evaluation and spread gating (spread > 5.0 pts triggers ERR_SAMPLE_SPREAD_EXCEEDED).
5. Release authority fail-closed rejection on missing provenance.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.judges import (
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
)
from dual_pipeline.llm_judge_core.sampling import (
    MAX_ALLOWED_SPREAD,
    check_self_evaluation,
    evaluate_sampled_judge,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    Output1ScorecardV2,
    Output2ScorecardV2,
    Output3ScorecardV2,
    validate_versioned_judge_scorecard,
)
from dual_pipeline.release_policy import (
    _check_judge_report,
    compute_release_evaluation_digest,
    validate_micro_judge_scorecard,
    validate_scorecard_provenance,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    is_borderline_or_hitl,
    update_evaluation_cache,
    check_cache,
    clear_evaluation_cache,
)


def _make_valid_provenance_card(out_num: int = 1, score: float = 92.0) -> Dict[str, Any]:
    if out_num == 1:
        dim = score / 4.0
        return {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": score,
            "dimension_1": dim,
            "dimension_2": dim,
            "dimension_3": dim,
            "dimension_4": dim,
            "dimension_1_forensic_grounding_and_fidelity": dim,
            "dimension_2_attribution_and_room_mic_hygiene": dim,
            "dimension_3_chronological_and_timestamp_integrity": dim,
            "dimension_4_asr_cleanup_and_domain_preservation": dim,
            "judge_model": "test-judge-frontier",
            "prompt_digest": "a1b2c3d4e5f60718",
            "rubric_version": "2.0",
            "sample_count": 3,
            "is_fallback": False,
            "forensic_rationale": "High quality adversarial test card.",
        }
    elif out_num == 2:
        return {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": score,
            "meeting_archetype": "STRATEGIC_DISCOVERY",
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 34,
                "conditional_extensions": 18,
                "appendix_stakeholder_perspectives": 30,
            },
            "judge_model": "test-judge-frontier",
            "prompt_digest": "b2c3d4e5f6071829",
            "rubric_version": "2.0",
            "sample_count": 3,
            "is_fallback": False,
            "forensic_rationale": "Valid recap card.",
        }
    else:
        dim = score / 4.0
        return {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": score,
            "dimension_1_executive_clarity": dim,
            "dimension_2_technical_substance": dim,
            "dimension_3_cross_functional_alignment": dim,
            "dimension_4_forward_momentum": dim,
            "judge_model": "test-judge-frontier",
            "prompt_digest": "c3d4e5f60718293a",
            "rubric_version": "2.0",
            "sample_count": 3,
            "is_fallback": False,
            "forensic_rationale": "Valid graph card.",
        }


class TestJudgeProvenanceAdversarial(unittest.TestCase):
    """Test mandatory provenance fields on scorecards under strict policy."""

    def test_missing_provenance_fields_fail_validation(self):
        base_card = _make_valid_provenance_card(1, 92.0)
        ok, errs = validate_scorecard_provenance(base_card)
        self.assertTrue(ok, f"Baseline should pass: {errs}")

        mandatory_fields = ["judge_model", "prompt_digest", "rubric_version", "sample_count", "is_fallback"]
        for field in mandatory_fields:
            corrupted = dict(base_card)
            del corrupted[field]
            ok, errs = validate_scorecard_provenance(corrupted)
            self.assertFalse(ok, f"Should fail without '{field}'")
            self.assertTrue(any(field in e for e in errs))

            # Also check validate_micro_judge_scorecard with require_provenance=True
            ok_m, errs_m = validate_micro_judge_scorecard(corrupted, output_num=1, require_provenance=True)
            self.assertFalse(ok_m)
            self.assertTrue(any(field in e for e in errs_m))

    def test_null_provenance_fields_fail_validation(self):
        base_card = _make_valid_provenance_card(2, 92.0)
        for field in ["judge_model", "prompt_digest", "rubric_version", "sample_count", "is_fallback"]:
            corrupted = dict(base_card)
            corrupted[field] = None
            ok, errs = validate_scorecard_provenance(corrupted)
            self.assertFalse(ok, f"Should fail when '{field}' is None")

    def test_versioned_contracts_enforce_provenance(self):
        base_card = _make_valid_provenance_card(1, 92.0)
        del base_card["prompt_digest"]
        # Output1ScorecardV2 should reject missing prompt_digest
        ok, errs = validate_versioned_judge_scorecard(base_card, output_num=1)
        self.assertFalse(ok)


class TestSelfEvaluationPrevention(unittest.TestCase):
    """Test Decision D1: Judge must never match generator model."""

    def test_check_self_evaluation_exact_match(self):
        err = check_self_evaluation("gpt-5.6-sol", {"generator_model": "gpt-5.6-sol"})
        self.assertIsNotNone(err)
        self.assertIn("SELF-EVALUATION REJECTED", err)

    def test_check_self_evaluation_normalized_and_substring_match(self):
        # Prefix / version substring match
        err = check_self_evaluation("gpt-4o", {"generator_model": "gpt-4o-2024-08-06"})
        self.assertIsNotNone(err)
        self.assertIn("Decision D1", err)

        err2 = check_self_evaluation("gemini-2.0-flash", {"producer_model": "gemini-2.0-flash-exp"})
        self.assertIsNotNone(err2)

    def test_check_self_evaluation_independent_models_pass(self):
        err = check_self_evaluation("gpt-5.6-sol", {"generator_model": "gemini-2.0-flash"})
        self.assertIsNone(err)

        err2 = check_self_evaluation("claude-3-5-sonnet", {"model": "gpt-4o"})
        self.assertIsNone(err2)

    def test_forensic_judge_fails_early_on_self_eval(self):
        judge = ForensicTranscriptJudge(model="gpt-4o")
        meta = {"generator_model": "gpt-4o"}
        res = judge.evaluate("Transcript text", "Source text", metadata=meta)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(res["composite_score"], 0)
        self.assertIn("ERR_SELF_EVALUATION", res.get("defect_codes", []))

    def test_recap_judge_fails_early_on_self_eval(self):
        judge = ExecutiveRecapJudge(model="gpt-5.6-sol")
        meta = {"producer_model": "gpt-5.6-sol"}
        res = judge.evaluate("Recap text", "Ref text", metadata=meta)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(res["composite_score"], 0)
        self.assertIn("ERR_SELF_EVALUATION", res.get("defect_codes", []))

    def test_graph_judge_fails_early_on_self_eval(self):
        judge = GraphExtractionJudge(model="gemini-2.0-flash")
        meta = {"generator_model": "gemini-2.0-flash"}
        res = judge.evaluate("Graph text", "Ref text", metadata=meta)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(res["composite_score"], 0)
        self.assertIn("ERR_SELF_EVALUATION", res.get("defect_codes", []))


class TestSamplingAndSpreadGating(unittest.TestCase):
    """Test N-sample median evaluation with spread gating."""

    def test_n_sample_median_consistent_pass(self):
        # 3 samples with spread <= 5.0 (90, 92, 94 -> spread 4.0, median 92)
        sample_cards = [
            _make_valid_provenance_card(1, 90.0),
            _make_valid_provenance_card(1, 94.0),
            _make_valid_provenance_card(1, 92.0),
        ]
        result = evaluate_sampled_judge(sample_cards=sample_cards, n_samples=3)
        self.assertEqual(result["composite_score"], 92.0)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["sample_spread"], 4.0)
        self.assertNotIn("ERR_SAMPLE_SPREAD_EXCEEDED", result.get("defect_codes", []))

    def test_n_sample_spread_exceeded_triggers_hitl(self):
        # 3 samples with spread > 5.0 (85, 91, 95 -> spread 10.0, median 91)
        sample_cards = [
            _make_valid_provenance_card(1, 85.0),
            _make_valid_provenance_card(1, 91.0),
            _make_valid_provenance_card(1, 95.0),
        ]
        result = evaluate_sampled_judge(sample_cards=sample_cards, n_samples=3)
        self.assertEqual(result["verdict"], "HITL_REQUIRED")
        self.assertEqual(result["status"], "HITL_REQUIRED")
        self.assertEqual(result["sample_spread"], 10.0)
        self.assertIn("ERR_SAMPLE_SPREAD_EXCEEDED", result.get("defect_codes", []))
        self.assertIn("JUDGE SPREAD EXCEEDED", result.get("forensic_rationale", ""))

    def test_spread_boundary_conditions(self):
        # Exactly 5.0 spread -> passes gate
        c1 = [
            _make_valid_provenance_card(1, 90.0),
            _make_valid_provenance_card(1, 92.0),
            _make_valid_provenance_card(1, 95.0),
        ]
        r1 = evaluate_sampled_judge(sample_cards=c1, n_samples=3, max_spread=5.0)
        self.assertEqual(r1["verdict"], "PASS")

        # 5.01 spread -> triggers HITL
        c2 = [
            _make_valid_provenance_card(1, 90.0),
            _make_valid_provenance_card(1, 92.0),
            _make_valid_provenance_card(1, 95.01),
        ]
        r2 = evaluate_sampled_judge(sample_cards=c2, n_samples=3, max_spread=5.0)
        self.assertEqual(r2["verdict"], "HITL_REQUIRED")
        self.assertIn("ERR_SAMPLE_SPREAD_EXCEEDED", r2.get("defect_codes", []))

    def test_single_sample_failure_routes_to_hitl(self):
        sample_cards = [
            _make_valid_provenance_card(1, 92.0),
            {
                "status": "HITL_REQUIRED",
                "verdict": "HITL_REQUIRED",
                "composite_score": None,
                "defect_codes": ["ERR_PROVIDER_429_HITL"],
            },
            _make_valid_provenance_card(1, 94.0),
        ]
        result = evaluate_sampled_judge(sample_cards=sample_cards, n_samples=3)
        self.assertEqual(result["verdict"], "HITL_REQUIRED")
        self.assertEqual(result["status"], "HITL_REQUIRED")

    def test_empty_samples_fail_closed(self):
        result = evaluate_sampled_judge(sample_cards=[])
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["composite_score"], 0)
        self.assertIn("ERR_NO_SAMPLES", result.get("defect_codes", []))


class TestCacheBorderlineExclusion(unittest.TestCase):
    """Test that borderline [85, 90), HITL, and spread exceeded cards are never cached."""

    def setUp(self):
        clear_evaluation_cache()

    def tearDown(self):
        clear_evaluation_cache()

    def test_borderline_score_not_cached(self):
        # 88.0 is borderline [85, 90)
        card = _make_valid_provenance_card(1, 88.0)
        self.assertTrue(is_borderline_or_hitl(card))

        update_evaluation_cache("out1:test_borderline_sha", card)
        cached = check_cache("out1:test_borderline_sha", allow_failure=True)
        self.assertIsNone(cached, "Borderline card [85, 90) must not be retrieved from cache")

    def test_spread_exceeded_not_cached(self):
        card = _make_valid_provenance_card(1, 92.0)
        card["verdict"] = "HITL_REQUIRED"
        card["status"] = "HITL_REQUIRED"
        card["defect_codes"] = ["ERR_SAMPLE_SPREAD_EXCEEDED"]
        self.assertTrue(is_borderline_or_hitl(card))

        update_evaluation_cache("out1:test_spread_sha", card)
        cached = check_cache("out1:test_spread_sha", allow_failure=True)
        self.assertIsNone(cached, "HITL spread exceeded card must not be cached")

    def test_solid_passing_card_is_cached(self):
        # 94.0 is solid passing (>= 90.0)
        card = _make_valid_provenance_card(1, 94.0)
        self.assertFalse(is_borderline_or_hitl(card))

        update_evaluation_cache("out1:test_solid_sha", card)
        cached = check_cache("out1:test_solid_sha", min_score=90)
        self.assertIsNotNone(cached, "Solid passing card should be cached")
        self.assertEqual(cached.get("composite_score"), 94.0)


class TestReleaseAuthorityFailClosed(unittest.TestCase):
    """Test that release authority fails closed when provenance is missing."""

    def test_check_judge_report_rejects_missing_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_dir = Path(tmpdir)
            report_file = pkg_dir / "llm_judge_evaluation.json"

            card1 = _make_valid_provenance_card(1, 92.0)
            del card1["prompt_digest"] # missing provenance
            card2 = _make_valid_provenance_card(2, 92.0)
            card3 = _make_valid_provenance_card(3, 92.0)

            report = {
                "mode": "meeting",
                "output_1_evaluation": card1,
                "output_2_evaluation": card2,
                "output_3_evaluation": card3,
            }
            digest = compute_release_evaluation_digest(report)
            report["evaluation_receipt_digest"] = digest
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                _check_judge_report(report_file, "test_pkg")
            self.assertIn("malformed Output 1 scorecard", str(ctx.exception))
            self.assertIn("Missing mandatory provenance field: 'prompt_digest'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
