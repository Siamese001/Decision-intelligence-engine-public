"""
tests/test_hitl_prior_consumption_e2e.py

End-to-end verification that historical ledger feedback calibrates options
prior to margin gating, respecting ParameterRegistry SSOT weights and bounds (Wave 6).
"""
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance import (
    HITLDecisionStore,
    HitlSurfacingMode,
)
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
from dual_pipeline.parameters import (
    get_hitl_autonomous_feedback_weight,
    get_hitl_bayesian_prior_strength,
    get_hitl_human_feedback_weight,
)


class HitlPriorConsumptionE2ETests(unittest.TestCase):
    """E2E verification of prior consumption, Bayesian bounds, and bridge calibration."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.ledger_file = self.root / ".local" / "ledger" / "hitl_decision_ledger.json"
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self.store = HITLDecisionStore(ledger_path=self.ledger_file)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_ssot_parameter_defaults(self) -> None:
        """Verify SSOT defaults in ParameterRegistry."""
        self.assertEqual(get_hitl_bayesian_prior_strength(), 4.0)
        self.assertEqual(get_hitl_human_feedback_weight(), 1.0)
        self.assertEqual(get_hitl_autonomous_feedback_weight(), 0.05)

    def test_historical_affirmations_boost_confidence_within_bounds(self) -> None:
        """Verify operator affirmations boost posterior score while strictly clamping to [0.05, 0.95]."""
        category = "entity_routing"
        # Seed 5 human affirmations for "Option A"
        for i in range(5):
            self.store.record_decision(
                decision_id=f"hist-{i}",
                category=category,
                prompt="Select target entity",
                options=[
                    {"label": "Option A", "confidence_score": 0.70},
                    {"label": "Option B", "confidence_score": 0.65},
                ],
                selected_option={"label": "Option A", "confidence_score": 0.70},
                operator_id="human_expert",
            )

        new_options = [
            {"label": "Option A", "confidence_score": 0.70},
            {"label": "Option B", "confidence_score": 0.65},
        ]
        calibrated = self.store.calibrate_options(new_options, category=category)
        opt_a = next(o for o in calibrated if "Option A" in o["label"])
        opt_b = next(o for o in calibrated if "Option B" in o["label"])

        # Opt A boosted by affirmations
        self.assertGreater(opt_a["confidence_score"], 0.70)
        self.assertIn("prior_adjustment", opt_a)
        self.assertGreater(opt_a["prior_adjustment"], 0.0)
        self.assertLessEqual(opt_a["confidence_score"], 0.95)

        # Opt B penalized by rejections
        self.assertLess(opt_b["confidence_score"], 0.65)
        self.assertGreaterEqual(opt_b["confidence_score"], 0.05)

    def test_autonomous_decisions_receive_discounted_weight(self) -> None:
        """Verify autonomous feedback is weighted at autonomous_feedback_weight (0.05)."""
        category = "auto_test"
        # 1 human affirmation
        self.store.record_decision(
            decision_id="human-1",
            category=category,
            prompt="Decision",
            options=[{"label": "Target", "confidence_score": 0.50}],
            selected_option={"label": "Target", "confidence_score": 0.50},
            operator_id="human_operator",
        )
        human_cal = self.store.calibrate_options([{"label": "Target", "confidence_score": 0.50}], category=category)[0]

        # Reset ledger
        if self.ledger_file.exists():
            self.ledger_file.unlink()
        sqlite_file = self.ledger_file.with_suffix(".sqlite")
        if sqlite_file.exists():
            sqlite_file.unlink()
        self.store = HITLDecisionStore(ledger_path=self.ledger_file)

        # 1 autonomous resolution
        self.store.record_decision(
            decision_id="auto-1",
            category=category,
            prompt="Decision",
            options=[{"label": "Target", "confidence_score": 0.50}],
            selected_option={"label": "Target", "confidence_score": 0.50},
            operator_id="autonomous_engine",
        )
        auto_cal = self.store.calibrate_options([{"label": "Target", "confidence_score": 0.50}], category=category)[0]

        # Human affirmation should produce significantly larger boost than autonomous
        human_delta = human_cal["prior_adjustment"]
        auto_delta = auto_cal["prior_adjustment"]
        self.assertGreater(human_delta, auto_delta)
        self.assertAlmostEqual(auto_cal["affirmed_weight"], 0.05)
        self.assertAlmostEqual(human_cal["affirmed_weight"], 1.0)

    def test_bridge_arbitrate_and_present_calibrates_prior_to_margin_gating(self) -> None:
        """Verify bridge calibrates options first, converting ambiguous spread into autonomous proceed."""
        category = "margin_learning"
        # Initially: Option A (70%) vs Option B (60%) -> Delta = 10% < 15% -> Ambiguous, requires HITL
        bridge = AntigravityUIBridge(
            ledger_path=self.ledger_file,
            surfacing_mode=HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD,
            margin_threshold=0.15,
        )

        res_initial = bridge.arbitrate_and_present(
            pipeline_id="test-pipe",
            decision_id="dec-init",
            category=category,
            question="Choose entity",
            options=[
                {"label": "Option A", "confidence_score": 0.70},
                {"label": "Option B", "confidence_score": 0.60},
            ],
            auto_record_ledger=False,
        )
        # Margin is 10% <= 15% threshold -> HITL required
        self.assertTrue(res_initial["should_surface"])
        self.assertEqual(res_initial["status"], "HITL_REQUIRED")

        # Now operator repeatedly confirms Option A 6 times
        for i in range(6):
            self.store.record_decision(
                decision_id=f"op-{i}",
                category=category,
                prompt="Choose entity",
                options=[
                    {"label": "Option A", "confidence_score": 0.70},
                    {"label": "Option B", "confidence_score": 0.60},
                ],
                selected_option={"label": "Option A", "confidence_score": 0.70},
                operator_id="human_expert",
            )

        # Re-evaluate same options: learned priors boost Option A and penalize Option B
        res_calibrated = bridge.arbitrate_and_present(
            pipeline_id="test-pipe",
            decision_id="dec-learned",
            category=category,
            question="Choose entity",
            options=[
                {"label": "Option A", "confidence_score": 0.70},
                {"label": "Option B", "confidence_score": 0.60},
            ],
            auto_record_ledger=False,
        )

        # Calibrated margin should now exceed 15%, auto-approving without surfacing
        self.assertFalse(res_calibrated["should_surface"])
        self.assertEqual(res_calibrated["status"], "AUTONOMOUS_APPROVED")
        self.assertGreater(res_calibrated["margin"], 0.15)


if __name__ == "__main__":
    unittest.main()
