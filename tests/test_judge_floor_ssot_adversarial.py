"""
tests/test_judge_floor_ssot_adversarial.py

Adversarial and property-based verification for Floor SSOT & Code-Computed Verdicts across Outputs 1-3.

Edge-Case Categories Covered:
1. Boundary payload: Exact boundary values (floor vs floor - 0.01), null vs 0/100 composite scores on skipped cards.
2. Malformed input: Unauthorized skip reasons, mode mismatches, frozen card SHA mismatches, nested incomplete spans.
3. Invariant bypass: High composite score attempting to bypass floor breach; LLM PASS overwritten to FAIL; forged spans.
4. AST / SSOT architectural integrity: AST verification confirming no hardcoded floor dicts outside contracts.
5. Property-based testing: Hypothesis generation across continuous dimension spaces validating floor invariants.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
import unittest

from hypothesis import given, settings, strategies as st

from dual_pipeline.prompts.contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT3_CONTRACT,
    get_dimension_floors_for_output,
    get_output_contract,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    validate_versioned_judge_scorecard,
)
from dual_pipeline.release_policy import (
    validate_micro_judge_scorecard,
)


def _make_out1_card(
    d1: float,
    d2: float,
    d3: float,
    d4: float,
    verdict: Optional[str] = None,
    for_versioned: bool = False,
) -> Dict[str, Any]:
    comp = round(d1 + d2 + d3 + d4, 2)
    eff_verdict = verdict or ("PASS" if comp >= 90.0 else ("PASS_ADVISORY" if comp >= 80.0 else "FAIL"))
    card: Dict[str, Any] = {
        "rubric_version": "2.0",
        "verdict": eff_verdict,
        "composite_score": comp,
        "effective_model": "test-judge-model",
        "effective_provider": "local",
        "prompt_digest": "a1b2c3d4e5f6",
        "evaluated_span_coverage": 1.0,
        "dimension_1_forensic_grounding_and_fidelity": d1,
        "dimension_2_attribution_and_room_mic_hygiene": d2,
        "dimension_3_chronological_and_timestamp_integrity": d3,
        "dimension_4_asr_cleanup_and_domain_preservation": d4,
        "evidence_references": ["turn-1"],
        "forensic_rationale": "High fidelity test payload.",
    }
    if not for_versioned:
        card["status"] = eff_verdict
        card["evidence_id_references"] = ["turn-1"]
    return card


def _make_out3_card(
    dims: List[float],
    verdict: Optional[str] = None,
    for_versioned: bool = False,
) -> Dict[str, Any]:
    comp = round(sum(dims), 2)
    eff_verdict = verdict or ("PASS" if comp >= 85.0 else "FAIL")
    card: Dict[str, Any] = {
        "rubric_version": "2.0",
        "verdict": eff_verdict,
        "composite_score": comp,
        "effective_model": "test-judge-model",
        "effective_provider": "local",
        "prompt_digest": "a1b2c3d4e5f6",
        "evaluated_span_coverage": 1.0,
        "dimension_1_strategic_edge": dims[0],
        "dimension_2_knowledge_layer_density": dims[1],
        "dimension_3_semantic_atomicity": dims[2],
        "dimension_4_predicate_specificity": dims[3],
        "dimension_5_exclusion_of_action_items": dims[4],
        "dimension_6_evidence_grounding_and_schema": dims[5],
        "forensic_rationale": "Strategic graph signal projection.",
    }
    if not for_versioned:
        card["status"] = eff_verdict
    return card


class TestJudgeFloorBoundaryPayloads(unittest.TestCase):
    """Category 1: Boundary payload tests for floor SSOT and scorecard validation."""

    def test_out1_exact_floor_boundary(self):
        floors = OUTPUT1_CONTRACT.get_dimension_floors()
        base = 25.0
        # Exactly at floor (14.0) with others at 25.0 -> sum 89.0 -> PASS_ADVISORY is allowed
        card_at_floor = _make_out1_card(14.0, base, base, base, verdict="PASS_ADVISORY")
        ok, errs = validate_micro_judge_scorecard(card_at_floor, output_num=1)
        self.assertTrue(ok, f"Expected success at exact floor: {errs}")

        # Just below floor (13.99) -> PASS_ADVISORY must fail closed
        card_below_floor = _make_out1_card(13.99, base, base, base, verdict="PASS_ADVISORY")
        ok_below, errs_below = validate_micro_judge_scorecard(card_below_floor, output_num=1)
        self.assertFalse(ok_below)
        self.assertTrue(any("below mandatory floor" in e for e in errs_below))

    def test_out3_exact_floor_boundary(self):
        floors = OUTPUT3_CONTRACT.get_dimension_floors()
        # Canonical floors: [11.0, 11.0, 14.0, 11.0, 11.0, 14.0]
        # At exact floors with composite >= 85.0
        card_at = _make_out3_card([15.0, 15.0, 20.0, 15.0, 15.0, 14.0], verdict="PASS")
        ok, errs = validate_micro_judge_scorecard(card_at, output_num=3)
        self.assertTrue(ok, f"Expected success at exact floor: {errs}")

        # 13.99 on dim 6 (floor 14.0) with composite 93.99
        card_below = _make_out3_card([15.0, 15.0, 20.0, 15.0, 15.0, 13.99], verdict="PASS")
        ok_b, errs_b = validate_micro_judge_scorecard(card_below, output_num=3)
        self.assertFalse(ok_b)
        self.assertTrue(any("below mandatory floor" in e for e in errs_b))

    def test_skipped_card_composite_score_null_boundary(self):
        # Skipped cards must strictly have composite_score: None
        card_null = {
            "status": "SKIPPED",
            "verdict": "NOT_APPLICABLE",
            "composite_score": None,
            "reason": "NOT_APPLICABLE_NOTES_MODE",
            "pipeline_mode": "notes",
        }
        ok, errs = validate_micro_judge_scorecard(card_null, output_num=1)
        self.assertTrue(ok, f"Expected null composite to pass: {errs}")

        # composite_score: 0 must fail
        card_zero = {**card_null, "composite_score": 0}
        ok_zero, errs_zero = validate_micro_judge_scorecard(card_zero, output_num=1)
        self.assertFalse(ok_zero)
        self.assertTrue(any("must have composite_score: null" in e for e in errs_zero))

        # composite_score: 100 must fail
        card_100 = {**card_null, "composite_score": 100}
        ok_100, errs_100 = validate_micro_judge_scorecard(card_100, output_num=1)
        self.assertFalse(ok_100)
        self.assertTrue(any("must have composite_score: null" in e for e in errs_100))


class TestJudgeFloorMalformedInput(unittest.TestCase):
    """Category 2: Malformed input tests covering skip reasons, SHA verification, and incomplete spans."""

    def test_unauthorized_skip_reason(self):
        card = {
            "status": "SKIPPED",
            "verdict": "NOT_APPLICABLE",
            "composite_score": None,
            "reason": "USER_REQUESTED_SKIP",
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("unauthorized reason" in e for e in errs))

    def test_skip_reason_pipeline_mode_mismatch(self):
        # NOTES reason in DOCUMENT mode
        card = {
            "status": "SKIPPED",
            "verdict": "NOT_APPLICABLE",
            "composite_score": None,
            "reason": "NOT_APPLICABLE_NOTES_MODE",
            "pipeline_mode": "document",
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("does not match pipeline mode" in e for e in errs))

    def test_frozen_card_sha_mismatch(self):
        card = {
            "status": "SKIPPED",
            "verdict": "PASS",
            "composite_score": None,
            "reason": "FROZEN_PRIOR_ARTIFACT",
            "candidate_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "frozen_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=1)
        self.assertFalse(ok)
        self.assertTrue(any("does not match frozen SHA" in e for e in errs))

    def test_frozen_card_candidate_sha_param_mismatch(self):
        sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        wrong_sha = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        card = {
            "status": "SKIPPED",
            "verdict": "PASS",
            "composite_score": None,
            "reason": "FROZEN_PRIOR_ARTIFACT",
            "candidate_sha256": sha,
            "frozen_sha256": sha,
        }
        ok, errs = validate_micro_judge_scorecard(card, output_num=1, candidate_sha256=wrong_sha)
        self.assertFalse(ok)
        self.assertTrue(any("does not match candidate SHA" in e for e in errs))

    def test_nested_incomplete_evaluated_span_fail_closed(self):
        base_card = _make_out1_card(25.0, 25.0, 25.0, 25.0, verdict="PASS")

        # 1. At root
        c1 = {**base_card, "incomplete_evaluated_span": True}
        ok1, errs1 = validate_micro_judge_scorecard(c1, output_num=1)
        self.assertFalse(ok1)
        self.assertTrue(any("Incomplete evaluated span" in e for e in errs1))

        # 2. In metadata
        c2 = {**base_card, "metadata": {"incomplete_evaluated_span": True}}
        ok2, errs2 = validate_micro_judge_scorecard(c2, output_num=1)
        self.assertFalse(ok2)
        self.assertTrue(any("Incomplete evaluated span" in e for e in errs2))

        # 3. In execution
        c3 = {**base_card, "execution": {"incomplete_evaluated_span": True}}
        ok3, errs3 = validate_micro_judge_scorecard(c3, output_num=1)
        self.assertFalse(ok3)
        self.assertTrue(any("Incomplete evaluated span" in e for e in errs3))

        # 4. In raw_judge_scorecard
        c4 = {**base_card, "raw_judge_scorecard": {"incomplete_evaluated_span": True}}
        ok4, errs4 = validate_micro_judge_scorecard(c4, output_num=1)
        self.assertFalse(ok4)
        self.assertTrue(any("Incomplete evaluated span" in e for e in errs4))

        # 5. evaluated_span_coverage < 1.0
        c5 = {**base_card, "evaluated_span_coverage": 0.85}
        ok5, errs5 = validate_micro_judge_scorecard(c5, output_num=1)
        self.assertFalse(ok5)
        self.assertTrue(any("Incomplete evaluated span" in e for e in errs5))


class TestJudgeFloorInvariantBypass(unittest.TestCase):
    """Category 3: Invariant bypass tests ensuring LLM PASS verdicts cannot bypass code-computed floors."""

    def test_high_composite_cannot_override_floor_breach(self):
        card = _make_out3_card([10.0, 15.0, 20.0, 15.0, 15.0, 10.0], verdict="PASS")
        ok, errs = validate_micro_judge_scorecard(card, output_num=3)
        self.assertFalse(ok)
        self.assertTrue(any("Output 3 dimensions below mandatory floor" in e for e in errs))

    def test_verdict_fail_is_valid_when_floor_breached(self):
        card = _make_out3_card([10.0, 15.0, 20.0, 15.0, 15.0, 10.0], verdict="FAIL")
        ok, errs = validate_micro_judge_scorecard(card, output_num=3)
        self.assertTrue(ok, f"FAIL verdict for floor-breached scorecard must be valid: {errs}")

    def test_versioned_contracts_enforce_floor_ssot(self):
        # Output 1 versioned contract
        card_out1_breach = _make_out1_card(12.0, 25.0, 25.0, 25.0, verdict="PASS", for_versioned=True)
        ok1, errs1 = validate_versioned_judge_scorecard(card_out1_breach, output_num=1)
        self.assertFalse(ok1)
        self.assertTrue(any("below mandatory floor" in e for e in errs1))

        # Output 3 versioned contract
        card_out3_breach = _make_out3_card([10.0, 15.0, 20.0, 15.0, 15.0, 20.0], verdict="PASS", for_versioned=True)
        ok3, errs3 = validate_versioned_judge_scorecard(card_out3_breach, output_num=3)
        self.assertFalse(ok3)
        self.assertTrue(any("below mandatory floor" in e for e in errs3))


class TestJudgeFloorSSOTArchitectureIntegrity(unittest.TestCase):
    """Category 4: AST verification ensuring dimension floors are defined in exactly ONE SSOT."""

    def test_contract_get_dimension_floors_ssot(self):
        o1_floors = OUTPUT1_CONTRACT.get_dimension_floors()
        self.assertEqual(len(o1_floors), 4)
        for k, flr in o1_floors.items():
            self.assertEqual(flr, 14.0)

        o3_floors = OUTPUT3_CONTRACT.get_dimension_floors()
        self.assertEqual(len(o3_floors), 6)
        self.assertEqual(o3_floors["dimension_1_strategic_edge"], 11.0)
        self.assertEqual(o3_floors["dimension_2_knowledge_layer_density"], 11.0)
        self.assertEqual(o3_floors["dimension_3_semantic_atomicity"], 14.0)
        self.assertEqual(o3_floors["dimension_4_predicate_specificity"], 11.0)
        self.assertEqual(o3_floors["dimension_5_exclusion_of_action_items"], 11.0)
        self.assertEqual(o3_floors["dimension_6_evidence_grounding_and_schema"], 14.0)

    def test_no_hardcoded_floor_dicts_in_consumer_modules(self):
        """Verify that consumer modules do not define standalone floor dict literals."""
        repo_root = Path(__file__).resolve().parent.parent
        consumer_files = [
            repo_root / "dual_pipeline" / "release_policy.py",
            repo_root / "dual_pipeline" / "prompts" / "contracts" / "versioned_judge_contracts.py",
            repo_root / "dual_pipeline" / "llm_judge_core" / "contracts" / "judge_criterion_contract.py",
            repo_root / "dual_pipeline" / "llm_judge_core" / "judges" / "graph.py",
            repo_root / "dual_pipeline" / "llm_judge_core" / "judge_clarification.py",
            repo_root / "dual_pipeline" / "llm_judge_core" / "forensic_checks.py",
            repo_root / "dual_pipeline" / "document_pipeline_core" / "quality.py",
        ]

        for fpath in consumer_files:
            self.assertTrue(fpath.is_file(), f"Consumer file missing: {fpath}")
            tree = ast.parse(fpath.read_text(encoding="utf-8"))

            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and "FLOOR" in target.id.upper():
                            if isinstance(node.value, ast.Dict):
                                literal_vals = [
                                    getattr(v, "value", getattr(v, "n", None))
                                    for v in node.value.values
                                    if isinstance(v, (ast.Constant, ast.Num)) and getattr(v, "value", getattr(v, "n", None)) in (11, 11.0, 14, 14.0)
                                ]
                                self.assertEqual(
                                    len(literal_vals),
                                    0,
                                    f"File {fpath.name} contains hardcoded floor dict literal in {target.id}: {literal_vals}",
                                )


class TestJudgeFloorPropertyBased(unittest.TestCase):
    """Category 5: Hypothesis property-based testing of floor validation."""

    @given(
        d1=st.floats(min_value=0.0, max_value=25.0),
        d2=st.floats(min_value=0.0, max_value=25.0),
        d3=st.floats(min_value=0.0, max_value=25.0),
        d4=st.floats(min_value=0.0, max_value=25.0),
    )
    @settings(max_examples=40, deadline=None)
    def test_out1_floor_property_invariant(self, d1: float, d2: float, d3: float, d4: float):
        d1, d2, d3, d4 = round(d1, 2), round(d2, 2), round(d3, 2), round(d4, 2)
        any_below_floor = any(d < 14.0 for d in (d1, d2, d3, d4))

        card = _make_out1_card(d1, d2, d3, d4, verdict="PASS")
        ok, errs = validate_micro_judge_scorecard(card, output_num=1)

        if any_below_floor:
            self.assertFalse(ok)
            self.assertTrue(any("below mandatory floor" in e or "does not match policy expected" in e for e in errs))
        elif sum((d1, d2, d3, d4)) >= 90.0:
            self.assertTrue(ok, f"Expected valid scorecard but got errors: {errs}")


if __name__ == "__main__":
    unittest.main()
