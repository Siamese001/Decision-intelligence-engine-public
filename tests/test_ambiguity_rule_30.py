#!/usr/bin/env python3
"""Unit tests for Wave 4: 30% ambiguity rule via SSOT accessor needs_operator_decision().

Enforces:
- Exact 30.0% boundary asks (inclusive).
- 30.01% does not ask; 29.99% asks.
- Ties ask.
- Float arithmetic precision (0.9 - 0.6 = 0.30000000000000004) asks correctly.
- Integration across MarginEvaluator, evaluate_dual_lock, and AmbiguityRecord.
"""

from __future__ import annotations

import unittest

from dual_pipeline.ambiguity_core.models import (
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)
from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.hitl_governance_core.dual_lock import evaluate_dual_lock
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    needs_operator_decision,
)


class TestAmbiguityRule30(unittest.TestCase):
    """Test suite verifying 30% ambiguity margin SSOT behavior."""

    def test_ssot_threshold_is_30_percent(self) -> None:
        """Verifies that the SSOT threshold is currently 0.30."""
        thresh = get_hitl_ambiguity_margin_threshold()
        self.assertEqual(thresh, 0.30, f"Expected SSOT threshold 0.30, got {thresh}")

    def test_exact_30_percent_boundary_asks(self) -> None:
        """Verifies exact 30.0% boundary cases ask (inclusive <= 0.30)."""
        exact_cases = [
            (0.80, 0.50),
            (0.70, 0.40),
            (0.50, 0.20),
            (0.30, 0.00),
            (1.00, 0.70),
        ]
        for top, second in exact_cases:
            with self.subTest(top=top, second=second):
                res = needs_operator_decision(top, second)
                self.assertTrue(
                    res,
                    f"needs_operator_decision({top}, {second}) should be True for exact 30% gap",
                )

    def test_below_30_percent_asks(self) -> None:
        """Verifies gaps strictly below 30% ask."""
        below_cases = [
            (0.7999, 0.50),
            (0.60, 0.40),
            (0.55, 0.50),
            (0.51, 0.50),
        ]
        for top, second in below_cases:
            with self.subTest(top=top, second=second):
                self.assertTrue(needs_operator_decision(top, second))

    def test_above_30_percent_does_not_ask(self) -> None:
        """Verifies gaps strictly above 30% do NOT ask."""
        above_cases = [
            (0.8001, 0.50),
            (0.85, 0.50),
            (0.90, 0.50),
            (0.7001, 0.40),
        ]
        for top, second in above_cases:
            with self.subTest(top=top, second=second):
                self.assertFalse(needs_operator_decision(top, second))

    def test_ties_ask(self) -> None:
        """Verifies ties (0% spread) always ask."""
        ties = [
            (0.50, 0.50),
            (0.85, 0.85),
            (0.00, 0.00),
        ]
        for top, second in ties:
            with self.subTest(top=top, second=second):
                self.assertTrue(needs_operator_decision(top, second))

    def test_float_arithmetic_precision(self) -> None:
        """Verifies Decimal eliminates IEEE-754 floating point representation issues."""
        float_cases = [
            (0.9, 0.6),  # 0.9 - 0.6 = 0.30000000000000004
            (0.7, 0.4),  # 0.7 - 0.4 = 0.29999999999999993
            (0.3, 0.0),
            (0.85, 0.55),
        ]
        for top, second in float_cases:
            with self.subTest(top=top, second=second):
                self.assertTrue(
                    needs_operator_decision(top, second),
                    f"Float jitter failed for ({top}, {second})",
                )

    def test_margin_evaluator_30_percent_boundary(self) -> None:
        """Verifies MarginEvaluator.evaluate() behavior at the 30% boundary."""
        opts_boundary = [
            {"id": "opt1", "text": "Option A", "confidence": 0.80},
            {"id": "opt2", "text": "Option B", "confidence": 0.50},
        ]
        res_b = MarginEvaluator.evaluate(opts_boundary)
        self.assertFalse(res_b["decision_resolved_autonomously"])
        self.assertTrue(res_b["surface_hitl"])
        self.assertIn("MARGIN_BELOW_THRESHOLD", res_b["reason_codes"])

        opts_above = [
            {"id": "opt1", "text": "Option A", "confidence": 0.85},
            {"id": "opt2", "text": "Option B", "confidence": 0.50},
        ]
        res_a = MarginEvaluator.evaluate(opts_above)
        self.assertTrue(res_a["decision_resolved_autonomously"])
        self.assertFalse(res_a["surface_hitl"])

    def test_dual_lock_30_percent_boundary(self) -> None:
        """Verifies evaluate_dual_lock behavior at the 30% boundary."""
        opts_boundary = [
            {"id": "opt1", "label": "Option A", "confidence": 0.80, "tier": "HIGH"},
            {"id": "opt2", "label": "Option B", "confidence": 0.50, "tier": "MEDIUM"},
        ]
        res_b = evaluate_dual_lock(opts_boundary)
        self.assertFalse(res_b.lock1_passed)
        self.assertTrue(res_b.surface_hitl)
        self.assertFalse(res_b.autonomous_permitted)

        opts_above = [
            {"id": "opt1", "label": "Option A", "confidence": 0.85, "tier": "HIGH"},
            {"id": "opt2", "label": "Option B", "confidence": 0.50, "tier": "MEDIUM"},
        ]
        res_a = evaluate_dual_lock(opts_above)
        self.assertTrue(res_a.lock1_passed)
        self.assertFalse(res_a.surface_hitl)
        self.assertTrue(res_a.autonomous_permitted)

    def test_ambiguity_record_create_30_percent_boundary(self) -> None:
        """Verifies AmbiguityRecord.create() behavior at the 30% boundary."""
        cands_boundary = [
            CandidateOption(option_id="c1", text="Cand 1", confidence=0.80, rationale="r1"),
            CandidateOption(option_id="c2", text="Cand 2", confidence=0.50, rationale="r2"),
        ]
        rec_b = AmbiguityRecord.create(
            ambiguity_id="amb-test-1",
            domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
            segment_id="seg-1",
            source_excerpt="excerpt",
            candidates=cands_boundary,
        )
        self.assertTrue(rec_b.requires_hitl)

        cands_above = [
            CandidateOption(option_id="c1", text="Cand 1", confidence=0.85, rationale="r1"),
            CandidateOption(option_id="c2", text="Cand 2", confidence=0.50, rationale="r2"),
        ]
        rec_a = AmbiguityRecord.create(
            ambiguity_id="amb-test-2",
            domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
            segment_id="seg-2",
            source_excerpt="excerpt",
            candidates=cands_above,
        )
        self.assertFalse(rec_a.requires_hitl)


if __name__ == "__main__":
    unittest.main()
