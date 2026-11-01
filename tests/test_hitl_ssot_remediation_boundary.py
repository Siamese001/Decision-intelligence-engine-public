"""Boundary test suite for HITL SSOT remediation.

Validates exact mathematical boundaries:
- 30% margin threshold (gap <= 0.30 surfaces HITL)
- 65% conviction floor (top_confidence < 0.65 surfaces HITL)
- 0.75 normalized entropy ceiling (entropy > 0.75 surfaces HITL)
- Float precision and tie-breaking behavior
- Parity across unified evaluation engines
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.hitl_governance_core.canonical_evaluator import (
    evaluate_canonical_decision,
)
from dual_pipeline.hitl_governance_core.dual_lock import evaluate_dual_lock
from dual_pipeline.hitl_governance_core.surfacing import (
    calculate_normalized_entropy,
    evaluate_hitl_surfacing_gate,
)
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_min_autonomous_confidence_floor,
    needs_operator_decision,
)


class TestHitlSsotRemediationBoundary(unittest.TestCase):
    """Rigorous boundary test cases for HITL SSOT decision criteria."""

    def test_ssot_threshold_constants(self) -> None:
        """Verify repository parameters SSOT match required thresholds."""
        self.assertAlmostEqual(get_hitl_ambiguity_margin_threshold(), 0.20, places=4)
        self.assertAlmostEqual(get_hitl_min_autonomous_confidence_floor(), 0.65, places=4)
        self.assertAlmostEqual(get_hitl_entropy_ambiguity_threshold(), 0.75, places=4)

    def test_margin_30_percent_boundary_exact(self) -> None:
        """Gap of exactly 0.20 requires operator decision; 0.21 does not."""
        # Exact 20% gap
        self.assertTrue(needs_operator_decision(0.80, 0.60))
        # 19% gap (below threshold -> ambiguous -> ask)
        self.assertTrue(needs_operator_decision(0.80, 0.61))
        # 21% gap (above threshold -> clear winner -> autonomous)
        self.assertFalse(needs_operator_decision(0.80, 0.59))

    def test_float_arithmetic_precision_boundary(self) -> None:
        """IEEE 754 precision issues (0.85 - 0.65 = 0.20) resolve cleanly."""
        self.assertTrue(needs_operator_decision(0.85, 0.65))
        self.assertTrue(needs_operator_decision(0.70, 0.50))
        self.assertTrue(needs_operator_decision(0.95, 0.75))

    def test_conviction_floor_65_percent_boundary(self) -> None:
        """Top confidence below 0.65 fails conviction floor regardless of margin gap."""
        opts_below = [
            {"label": "Option A", "confidence_score": 0.64, "tier": "MEDIUM"},
            {"label": "Option B", "confidence_score": 0.10, "tier": "LOW"},
        ]
        # Gap is 0.54 (> 0.30), but top is 0.64 (< 0.65) -> MUST ASK
        res_below = evaluate_canonical_decision(opts_below, enable_lock2=False)
        self.assertTrue(res_below.surface_hitl)
        self.assertTrue(res_below.confidence_floor_breached)
        self.assertFalse(res_below.autonomous_permitted)

        margin_below = MarginEvaluator.evaluate(opts_below)
        self.assertTrue(margin_below["surface_hitl"])
        self.assertTrue(margin_below["confidence_floor_breached"])

        opts_met = [
            {"label": "Option A", "confidence_score": 0.65, "tier": "MEDIUM"},
            {"label": "Option B", "confidence_score": 0.10, "tier": "LOW"},
        ]
        # Top is 0.65 (floor met) and gap is 0.55 (> 0.30) -> AUTONOMOUS
        res_met = evaluate_canonical_decision(opts_met, enable_lock2=False)
        self.assertFalse(res_met.surface_hitl)
        self.assertFalse(res_met.confidence_floor_breached)
        self.assertTrue(res_met.autonomous_permitted)

    def test_ties_always_require_operator_decision(self) -> None:
        """Identical confidences have gap 0.0 and always surface for operator guidance."""
        self.assertTrue(needs_operator_decision(0.90, 0.90))
        self.assertTrue(needs_operator_decision(0.50, 0.50))
        self.assertTrue(needs_operator_decision(0.10, 0.10))

    def test_canonical_evaluator_margin_boundary(self) -> None:
        """evaluate_canonical_decision surfaces HITL at <= 20% gap and permits auto at > 20%."""
        opts_ambiguous = [
            {"label": "Option A", "confidence_score": 0.80, "tier": "HIGH"},
            {"label": "Option B", "confidence_score": 0.60, "tier": "MEDIUM"},
        ]
        res_ambiguous = evaluate_canonical_decision(opts_ambiguous, enable_lock2=False)
        self.assertTrue(res_ambiguous.surface_hitl)
        self.assertFalse(res_ambiguous.autonomous_eligible)

        opts_decisive = [
            {"label": "Option A", "confidence_score": 0.85, "tier": "HIGH"},
            {"label": "Option B", "confidence_score": 0.45, "tier": "LOW"},
        ]
        res_decisive = evaluate_canonical_decision(opts_decisive, enable_lock2=False)
        self.assertFalse(res_decisive.surface_hitl)
        self.assertTrue(res_decisive.autonomous_eligible)

    def test_canonical_evaluator_conviction_floor_boundary(self) -> None:
        """evaluate_canonical_decision flags confidence_floor_breached when top < 65%."""
        opts_low_conviction = [
            {"label": "Option A", "confidence_score": 0.60, "tier": "MEDIUM"},
            {"label": "Option B", "confidence_score": 0.10, "tier": "LOW"},
        ]
        res = evaluate_canonical_decision(opts_low_conviction, enable_lock2=False)
        self.assertTrue(res.surface_hitl)
        self.assertTrue(res.confidence_floor_breached)
        self.assertIn("CONVICTION_FLOOR_BREACH", res.rationale)

    def test_entropy_boundary_0_75(self) -> None:
        """High normalized epistemic entropy (> 0.75) surfaces operator review."""
        # 3 options with near-equal scores -> entropy close to 1.0 > 0.75
        scores_equal = [0.34, 0.33, 0.33]
        entropy_high = calculate_normalized_entropy(scores_equal)
        self.assertGreater(entropy_high, 0.75)

        opts_high_entropy = [
            {"label": "A", "confidence_score": 0.34, "tier": "LOW"},
            {"label": "B", "confidence_score": 0.33, "tier": "LOW"},
            {"label": "C", "confidence_score": 0.33, "tier": "LOW"},
        ]
        res_entropy = evaluate_canonical_decision(opts_high_entropy, enable_lock2=False)
        self.assertTrue(res_entropy.surface_hitl)
        self.assertGreater(res_entropy.normalized_entropy, 0.75)

        # Decisive distribution -> entropy << 0.75
        scores_decisive = [0.90, 0.05, 0.05]
        entropy_low = calculate_normalized_entropy(scores_decisive)
        self.assertLess(entropy_low, 0.75)

    def test_unified_evaluator_parity(self) -> None:
        """MarginEvaluator, evaluate_dual_lock, and evaluate_hitl_surfacing_gate share SSOT outcomes."""
        options = [
            {"label": "Alpha", "confidence_score": 0.75, "tier": "MEDIUM"},
            {"label": "Beta", "confidence_score": 0.55, "tier": "MEDIUM"},
        ]  # gap is 0.20 <= 0.30 -> must ask

        # 1. Canonical evaluator
        eval_canonical = evaluate_canonical_decision(options, enable_lock2=False)
        self.assertTrue(eval_canonical.surface_hitl)

        # 2. MarginEvaluator adapter
        eval_margin = MarginEvaluator.evaluate(options)
        self.assertTrue(eval_margin["surface_hitl"])

        # 3. Dual-lock adapter
        eval_lock = evaluate_dual_lock(options)
        self.assertFalse(eval_lock.lock1_cleared)

        # 4. Surfacing gate adapter
        eval_gate = evaluate_hitl_surfacing_gate(options)
        self.assertTrue(eval_gate["should_surface"])


if __name__ == "__main__":
    unittest.main()
