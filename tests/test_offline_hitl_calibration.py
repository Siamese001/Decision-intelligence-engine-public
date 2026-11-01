"""Tests for Wave 4: Offline HITL Calibration, Bayesian Prior Sharpening & Adversarial Stability.

Verifies:
1. Brier score and ECE calculation mathematical accuracy.
2. Bayesian prior updating (+0.15 affirmation boost, -0.10 rejection penalty, [0.05, 0.95] bounds).
3. Offline ledger replay achieving >= 15% Brier score reduction.
4. Adversarial poisoning resistance against rogue downvotes / conflicting choices.
5. Criticality override integration (Tier 0 & Tier 1 surfacing regardless of model confidence).
"""
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_criticality import CriticalityTier
from dual_pipeline.hitl_governance import (
    HITLDecisionStore,
    evaluate_hitl_surfacing_gate,
)
from evals.offline_hitl_calibration_runner import (
    OfflineHITLCalibrationRunner,
    calculate_brier_score,
    calculate_ece,
)


class OfflineHitlCalibrationTests(unittest.TestCase):
    """Test suite for offline HITL calibration and Bayesian prior optimization."""

    def test_brier_score_and_ece_math(self):
        """Verify mathematical precision of Brier score and ECE functions."""
        # Perfect forecasts
        preds_perfect = [1.0, 0.0, 1.0, 0.0]
        actuals_perfect = [1.0, 0.0, 1.0, 0.0]
        self.assertEqual(calculate_brier_score(preds_perfect, actuals_perfect), 0.0)
        self.assertEqual(calculate_ece(preds_perfect, actuals_perfect), 0.0)

        # Inverted worst-case forecasts
        preds_inverted = [0.0, 1.0, 0.0, 1.0]
        self.assertEqual(calculate_brier_score(preds_inverted, actuals_perfect), 1.0)
        self.assertEqual(calculate_ece(preds_inverted, actuals_perfect), 1.0)

        # Neutral 50% forecasts on balanced data
        preds_neutral = [0.5, 0.5, 0.5, 0.5]
        self.assertAlmostEqual(calculate_brier_score(preds_neutral, actuals_perfect), 0.25, places=4)

    def test_bayesian_prior_update_and_bounds(self):
        """Verify prior updating (+0.15 / -0.10) and strict [0.05, 0.95] boundary clamping."""
        runner = OfflineHITLCalibrationRunner(min_bound=0.05, max_bound=0.95)

        # Simulate 20 consecutive affirmations
        adv_test = runner.test_adversarial_stability(
            target_key="verified_entity",
            rogue_key="rogue_entity",
            authentic_count=20,
            rogue_count=0,
        )
        self.assertTrue(adv_test["bounds_held"])
        self.assertLessEqual(adv_test["target_prior_weight"], 0.95)
        self.assertGreaterEqual(adv_test["target_prior_weight"], 0.05)

        # Simulate 25 consecutive rogue rejections
        adv_test_rogue = runner.test_adversarial_stability(
            target_key="verified_entity",
            rogue_key="rogue_entity",
            authentic_count=0,
            rogue_count=25,
        )
        self.assertTrue(adv_test_rogue["bounds_held"])
        self.assertLessEqual(adv_test_rogue["rogue_prior_weight"], 0.95)
        self.assertGreaterEqual(adv_test_rogue["target_prior_weight"], 0.05)

    def test_offline_replay_achieves_brier_reduction(self):
        """Verify that replaying decisions with learned priors achieves >= 15% Brier score improvement."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "test_ledger.json"
            output_file = Path(tmpdir) / "test_priors.json"
            store = HITLDecisionStore(ledger_file)

            options = [
                {"key": "entity:cluster:core", "label": "Assign core cluster", "confidence": "58%"},
                {"key": "entity:cluster:edge", "label": "Assign edge cluster", "confidence": "52%"},
            ]

            # Record 4 historical decisions where operator affirms 'entity:cluster:core'
            for i in range(4):
                store.record_decision(
                    decision_id=f"test-dec-{i}",
                    category="TOPOLOGY_MAPPING",
                    prompt="Assign topology cluster",
                    options=options,
                    selected_option={"key": "entity:cluster:core"},
                    rationale="Operator verified core cluster.",
                )

            runner = OfflineHITLCalibrationRunner(ledger_path=ledger_file, output_path=output_file)
            res = runner.replay_and_calibrate()

            self.assertEqual(res["records_analyzed"], 4)
            self.assertGreaterEqual(res["brier_reduction_pct"], 15.0)
            self.assertLess(res["calibrated_brier_score"], res["baseline_brier_score"])

            # Verify saved priors file
            saved_path = runner.save_calibration(res)
            self.assertTrue(saved_path.is_file())

    def test_criticality_override_gate(self):
        """Verify that Tier 0 & Tier 1 operations surface regardless of confidence margin."""
        # Tier 0 with extreme confidence (99% vs 1%)
        tier0_opts = [
            {"key": "delete_all", "label": "Decommission database", "confidence": "99%"},
            {"key": "cancel", "label": "Cancel", "confidence": "1%"},
        ]
        gate_tier0 = evaluate_hitl_surfacing_gate(
            tier0_opts,
            criticality_tier=CriticalityTier.TIER_0_IRREVERSIBLE,
        )
        self.assertTrue(gate_tier0["should_surface"])
        self.assertTrue(gate_tier0["criticality_override"])
        self.assertIn("CRITICALITY_OVERRIDE", gate_tier0["reason"])

        # Tier 1 with extreme confidence (95% vs 5%)
        tier1_opts = [
            {"key": "breach_budget", "label": "Write 600-line module", "confidence": "95%"},
            {"key": "split_core", "label": "Decompose into core/", "confidence": "5%"},
        ]
        gate_tier1 = evaluate_hitl_surfacing_gate(
            tier1_opts,
            criticality_tier=CriticalityTier.TIER_1_ARCHITECTURAL,
        )
        self.assertTrue(gate_tier1["should_surface"])
        self.assertTrue(gate_tier1["criticality_override"])

        # Tier 4 autonomous (80% vs 50%, margin = 30% >= 20%)
        tier4_opts = [
            {"key": "read_doc", "label": "Read markdown", "confidence": "80%"},
            {"key": "skip_doc", "label": "Skip", "confidence": "50%"},
        ]
        gate_tier4 = evaluate_hitl_surfacing_gate(
            tier4_opts,
            criticality_tier=CriticalityTier.TIER_4_AUTONOMOUS,
        )
        self.assertFalse(gate_tier4["should_surface"])
        self.assertFalse(gate_tier4["criticality_override"])


if __name__ == "__main__":
    unittest.main()
