#!/usr/bin/env python3
"""Class A Unit Tests: HITL Criticality Evaluation, 20% Variance Gating & Epistemic Entropy.

Verifies:
1. 5-Tier Criticality assignment (Tier 0 Lethal to Tier 4 Autonomous).
2. Epistemic entropy calculation across candidate confidence vectors.
3. Mandatory HITL surfacing for Tier 0 (Irreversible) and Tier 1 (Architectural) operations
   regardless of confidence spread (zero overconfidence bypass).
4. Variance divergence gating (Delta > 20% surfaces high-stakes UI clickdown).
5. Ambiguity gating (Delta <= 20% surfaces close alternatives).
6. Agent-D4 (HitlArbiterAgent) and AntigravityUIBridge end-to-end criticality arbitration.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.hitl_criticality import (
    CriticalityTier,
    calculate_epistemic_entropy,
    evaluate_criticality_score,
)
from dual_pipeline.hitl_governance import (
    HitlSurfacingMode,
    evaluate_hitl_surfacing_gate,
)
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge


class HitlCriticalityAndVarianceTests(unittest.TestCase):
    def test_criticality_tier_assignment(self) -> None:
        """Verify regex and context-based classification into CriticalityTier 0-4."""
        # Tier 0: Irreversible/Destructive
        tier_0_a, _, reasons_0_a = evaluate_criticality_score(operation_text="DROP TABLE search_document")
        self.assertEqual(tier_0_a, CriticalityTier.TIER_0_IRREVERSIBLE)
        self.assertTrue(any("Tier 0" in r for r in reasons_0_a))

        tier_0_b, _, _ = evaluate_criticality_score(context={"is_destructive": True, "category": "graph_cleanup"})
        self.assertEqual(tier_0_b, CriticalityTier.TIER_0_IRREVERSIBLE)

        tier_0_c, _, _ = evaluate_criticality_score(operation_text="touches c19_breach and career_firewall_override")
        self.assertEqual(tier_0_c, CriticalityTier.TIER_0_IRREVERSIBLE)

        # Tier 1: Architectural & Concurrency Refactoring
        tier_1_a, _, reasons_1_a = evaluate_criticality_score(operation_text="fix l0_l6_layer_change and cyclic_import")
        self.assertEqual(tier_1_a, CriticalityTier.TIER_1_ARCHITECTURAL)
        self.assertTrue(any("Tier 1" in r for r in reasons_1_a))

        tier_1_b, _, _ = evaluate_criticality_score(context={"violates_400_line_budget": True})
        self.assertEqual(tier_1_b, CriticalityTier.TIER_1_ARCHITECTURAL)

        tier_1_c, _, _ = evaluate_criticality_score(operation_text="bypass wal_mode_change with raw_sqlite_connect")
        self.assertEqual(tier_1_c, CriticalityTier.TIER_1_ARCHITECTURAL)

        # Tier 2: High-Stake Design Divergence
        tier_2, _, _ = evaluate_criticality_score(operation_text="evaluate commercial design_choice and strategic tradeoff")
        self.assertEqual(tier_2, CriticalityTier.TIER_2_DIVERGENT)

        # Tier 3: Ambiguity
        tier_3, _, _ = evaluate_criticality_score(operation_text="resolve ambiguous entity_resolution vocabulary")
        self.assertEqual(tier_3, CriticalityTier.TIER_3_AMBIGUOUS)

        # Tier 4: Autonomous Routine
        tier_4, _, _ = evaluate_criticality_score(operation_text="format docstrings and run unit test")
        self.assertEqual(tier_4, CriticalityTier.TIER_4_AUTONOMOUS)

    def test_epistemic_entropy_calculation(self) -> None:
        """Verify Shannon entropy over candidate score distributions."""
        # Single option -> zero entropy
        self.assertAlmostEqual(calculate_epistemic_entropy([0.95]), 0.0)

        # Uniform distribution across 2 options -> maximum normalized entropy = 1.0
        self.assertAlmostEqual(calculate_epistemic_entropy([0.5, 0.5]), 1.0)

        # Highly skewed distribution -> low entropy
        skewed_entropy = calculate_epistemic_entropy([0.99, 0.01])
        self.assertLess(skewed_entropy, 0.15)

        # Moderate disagreement -> entropy in middle range
        moderate_entropy = calculate_epistemic_entropy([0.65, 0.35])
        self.assertGreater(moderate_entropy, 0.80)

    def test_tier_0_and_tier_1_override_high_confidence(self) -> None:
        """The Overconfidence Trap: Tier 0 & 1 must surface even if confidence spread is 99% vs 1%."""
        options = [
            {"label": "Drop obsolete schema table", "confidence": "99%"},
            {"label": "Preserve table under read-only flag", "confidence": "1%"},
        ]
        # Margin = 98% > 20%
        # Under normal divergence mode, margin > 20% surfaces. But under ambiguity mode, it would auto-resolve!
        # CriticalityTier.TIER_0_IRREVERSIBLE forces surfacing regardless of mode.
        gate = evaluate_hitl_surfacing_gate(
            options,
            surfacing_mode=HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD,
            criticality_tier=CriticalityTier.TIER_0_IRREVERSIBLE,
        )
        self.assertTrue(gate["should_surface"], "Tier 0 MUST surface regardless of confidence margin.")
        self.assertIn("CRITICALITY_OVERRIDE", gate["reason"])
        self.assertEqual(gate["criticality_tier"], CriticalityTier.TIER_0_IRREVERSIBLE.value)

        # Tier 1 Architectural Refactor
        gate_tier_1 = evaluate_hitl_surfacing_gate(
            options,
            surfacing_mode=HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD,
            criticality_tier=CriticalityTier.TIER_1_ARCHITECTURAL,
        )
        self.assertTrue(gate_tier_1["should_surface"], "Tier 1 MUST surface regardless of confidence margin.")
        self.assertIn("CRITICALITY_OVERRIDE", gate_tier_1["reason"])

    def test_variance_divergence_surfacing_gt_20_percent(self) -> None:
        """Verify HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD surfaces when Delta > 20%."""
        # Delta = 75% - 40% = 35% > 20% -> surfaces
        options_divergent = [
            {"label": "Approach A: Decouple via Subpackage", "confidence": "75%"},
            {"label": "Approach B: In-line Monolithic Split", "confidence": "40%"},
        ]
        gate_div = evaluate_hitl_surfacing_gate(
            options_divergent,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
        )
        self.assertTrue(gate_div["should_surface"])
        self.assertAlmostEqual(gate_div["margin"], 0.35)
        self.assertIn("strictly exceeds the 20% threshold", gate_div["reason"])

        # Delta = 60% - 50% = 10% <= 20% -> does not surface in divergence mode
        options_close = [
            {"label": "Approach A", "confidence": "60%"},
            {"label": "Approach B", "confidence": "50%"},
        ]
        gate_close = evaluate_hitl_surfacing_gate(
            options_close,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
        )
        self.assertFalse(gate_close["should_surface"])
        self.assertAlmostEqual(gate_close["margin"], 0.10)
        self.assertIn("within or equal to the 20% threshold", gate_close["reason"])

    def test_epistemic_entropy_surfacing_trigger(self) -> None:
        """Verify that high candidate entropy triggers surfacing even when margin might otherwise pass."""
        options = [
            {"label": "Candidate 1", "confidence": "55%"},
            {"label": "Candidate 2", "confidence": "45%"},
        ]
        # In divergence mode with threshold 0.20, Delta = 0.10 <= 0.20 would auto-resolve.
        # But setting entropy_threshold=0.50 catches the high entropy (0.99) and forces surfacing.
        gate = evaluate_hitl_surfacing_gate(
            options,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
            entropy_threshold=0.50,
        )
        self.assertTrue(gate["should_surface"])
        self.assertIn("EPISTEMIC_ENTROPY_OVERRIDE", gate["reason"])

    def test_hitl_arbiter_agent_criticality_arbitration(self) -> None:
        """Verify HitlArbiterAgent integrates criticality tier and surfaces atomic card."""
        arbiter = HitlArbiterAgent()
        options = [
            {"key": "opt_a", "label": "Decompose into subpackage", "confidence": "95%"},
            {"key": "opt_b", "label": "Suppress lint rule", "confidence": "5%"},
        ]

        result = arbiter.arbitrate_decision(
            decision_id="dec_refactor_001",
            category="CODE_REFACTORING",
            question="Choose architecture strategy for monolithic file",
            options=options,
            criticality_tier=CriticalityTier.TIER_1_ARCHITECTURAL,
        )
        self.assertTrue(result["should_surface"])
        self.assertEqual(result["status"], "HITL_REQUIRED")
        self.assertIsNotNone(result["atomic_card"])
        self.assertEqual(result["atomic_card"]["decision_id"], "dec_refactor_001")

    def test_antigravity_ui_bridge_criticality_integration(self) -> None:
        """Verify AntigravityUIBridge correctly delegates criticality to the arbiter."""
        bridge = AntigravityUIBridge()
        options = [
            {"key": "opt_purge", "label": "Purge stale database entities", "confidence": "99%"},
            {"key": "opt_archive", "label": "Soft-archive entities", "confidence": "1%"},
        ]

        res = bridge.arbitrate_and_present(
            pipeline_id="pipeline_b",
            decision_id="dec_db_purge_001",
            category="DATABASE_MAINTENANCE",
            question="Select purge strategy",
            options=options,
            criticality_tier=CriticalityTier.TIER_0_IRREVERSIBLE,
        )
        self.assertTrue(res["should_surface"])
        self.assertEqual(res["status"], "HITL_REQUIRED")
        self.assertIsNotNone(res["atomic_card"])

    def test_absolute_confidence_floor_surfacing(self) -> None:
        """When Delta >= 25% but top confidence < 65%, gate must surface to prevent unconvincing auto-approval."""
        # Top = 50%, Runner-up = 20% -> Margin = 30% >= 25%
        # But top_conf = 0.50 < 0.65 floor -> must surface!
        options = [
            {"label": "Candidate A: Tentative Path", "confidence": "50%"},
            {"label": "Candidate B: Remote Alternative", "confidence": "20%"},
        ]
        gate = evaluate_hitl_surfacing_gate(options, margin_threshold=0.25, min_confidence_floor=0.65)
        self.assertTrue(gate["should_surface"])
        self.assertTrue(gate["confidence_floor_breached"])
        self.assertIn("CONFIDENCE_FLOOR_BREACH", gate["reason"])
        self.assertAlmostEqual(gate["margin"], 0.30)

        # When top option satisfies floor (e.g. 75%), autonomous progression passes
        options_conviction = [
            {"label": "Candidate A: High Conviction", "confidence": "75%"},
            {"label": "Candidate B: Remote Alternative", "confidence": "20%"},
        ]
        gate_ok = evaluate_hitl_surfacing_gate(options_conviction, margin_threshold=0.25, min_confidence_floor=0.65)
        self.assertFalse(gate_ok["should_surface"])
        self.assertFalse(gate_ok["confidence_floor_breached"])
        self.assertIn("meets or exceeds the 25% threshold", gate_ok["reason"])

    def test_multi_option_normalized_entropy_gating(self) -> None:
        """Multi-option candidates with high normalized entropy must surface to operator."""
        from dual_pipeline.hitl_governance import calculate_normalized_entropy

        # 4 balanced options -> normalized entropy is near 1.0
        balanced_scores = [0.26, 0.25, 0.25, 0.24]
        norm_h = calculate_normalized_entropy(balanced_scores)
        self.assertGreater(norm_h, 0.95)

        options = [
            {"label": f"Option {i}", "confidence": f"{s * 100:.1f}%"}
            for i, s in enumerate(balanced_scores)
        ]
        gate = evaluate_hitl_surfacing_gate(
            options,
            margin_threshold=0.25,
            normalized_entropy_threshold=0.75,
        )
        self.assertTrue(gate["should_surface"])
        self.assertIn("MULTI_OPTION_ENTROPY_OVERRIDE", gate["reason"])
        self.assertGreater(gate["normalized_entropy"], 0.75)

    def test_gate_telemetry_fields_populated(self) -> None:
        """Gate evaluation must return rich telemetry fields."""
        options = [
            {"label": "Option 1", "confidence": "85%"},
            {"label": "Option 2", "confidence": "50%"},
        ]
        gate = evaluate_hitl_surfacing_gate(options)
        self.assertIn("epistemic_entropy", gate)
        self.assertIn("normalized_entropy", gate)
        self.assertIn("confidence_floor_breached", gate)
        self.assertIn("min_confidence_floor", gate)
        self.assertAlmostEqual(gate["min_confidence_floor"], 0.65)


if __name__ == "__main__":
    unittest.main()
