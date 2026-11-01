"""Unit tests for Wave 3: Wiring calibrate_options into evaluate_hitl_surfacing_gate."""
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core import (
    HITLDecisionStore,
    evaluate_hitl_surfacing_gate,
)


class TestHITLSurfacingGateCalibration(unittest.TestCase):
    """Verifies calibrate_options integration into the surfacing gate."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_hitl_surf_cal_"))
        self.ledger_path = self.temp_dir / "hitl_decision_ledger.json"
        self.sqlite_path = self.temp_dir / "hitl_decision_ledger.sqlite"
        self.store = HITLDecisionStore(ledger_path=self.ledger_path, sqlite_path=self.sqlite_path, use_sqlite=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_uncalibrated_options_calibrated_via_decision_store(self) -> None:
        """Surfacing gate applies calibration when store and category are provided."""
        category = "entity_resolution"
        # Seed 5 affirmations for opt_a
        for i in range(5):
            self.store.record_decision(
                decision_id=f"dec_seed_{i}",
                category=category,
                prompt="Select resolution",
                options=[{"key": "opt_a", "label": "Option A"}, {"key": "opt_b", "label": "Option B"}],
                selected_option={"key": "opt_a", "label": "Option A"},
                operator_id="human_operator",
            )

        raw_options = [
            {"key": "opt_a", "label": "Option A", "confidence": "68%"},
            {"key": "opt_b", "label": "Option B", "confidence": "60%"},
        ]

        gate_res = evaluate_hitl_surfacing_gate(
            raw_options,
            decision_store=self.store,
            category=category,
            margin_threshold=0.15,
        )

        self.assertTrue(gate_res["calibration_applied"])
        self.assertGreater(gate_res["historical_sample_count"], 0)
        self.assertIn("calibrated_options", gate_res)
        self.assertEqual(len(gate_res["calibrated_options"]), 2)

        # Top option score must have been boosted by the 5 affirmations
        opt_a_cal = next(o for o in gate_res["calibrated_options"] if o.get("key") == "opt_a")
        self.assertGreater(opt_a_cal["confidence_score"], 0.68)
        self.assertIn("confidence_interval", opt_a_cal)
        self.assertIn("confidence_lower", opt_a_cal)
        self.assertIn("confidence_upper", opt_a_cal)

    def test_pre_calibrated_options_not_double_calibrated(self) -> None:
        """Options with existing historical_adjustment or pre_calibrated=True avoid re-calibration."""
        category = "entity_resolution"
        options = [
            {
                "key": "opt_a",
                "label": "Option A",
                "confidence_score": 0.85,
                "historical_adjustment": 0.10,
                "confidence_sample_count": 12,
            },
            {
                "key": "opt_b",
                "label": "Option B",
                "confidence_score": 0.65,
                "historical_adjustment": -0.05,
                "confidence_sample_count": 8,
            },
        ]

        gate_res = evaluate_hitl_surfacing_gate(
            options,
            decision_store=self.store,
            category=category,
            pre_calibrated=True,
        )

        self.assertFalse(gate_res["calibration_applied"])
        self.assertEqual(gate_res["historical_sample_count"], 20)
        self.assertEqual(gate_res["top_confidence"], 0.85)

    def test_calibration_widens_margin_and_enables_autonomous_resolution(self) -> None:
        """Historical affirmations boost top option enough to cross the 15% threshold autonomously."""
        category = "roster_reconciliation"
        # Seed 8 affirmations for match_alias
        for i in range(8):
            self.store.record_decision(
                decision_id=f"dec_roster_{i}",
                category=category,
                prompt="Reconcile roster",
                options=[{"key": "match_alias", "label": "Match"}, {"key": "create_new", "label": "New"}],
                selected_option={"key": "match_alias", "label": "Match"},
                operator_id="human_operator",
            )

        # Raw scores: 70% vs 60% -> margin 10% (< 15% -> would surface)
        raw_options = [
            {"key": "match_alias", "label": "Match alias", "confidence": "70%"},
            {"key": "create_new", "label": "Create new record", "confidence": "60%"},
        ]

        # Without store: margin 10% < 15% -> surfaces
        gate_raw = evaluate_hitl_surfacing_gate(raw_options, margin_threshold=0.15)
        self.assertTrue(gate_raw["should_surface"])
        self.assertAlmostEqual(gate_raw["margin"], 0.10, places=2)

        # With store: calibration boosts match_alias and penalizes create_new -> margin >= 15% -> auto-proceeds!
        gate_cal = evaluate_hitl_surfacing_gate(
            raw_options,
            decision_store=self.store,
            category=category,
            margin_threshold=0.15,
        )
        self.assertTrue(gate_cal["calibration_applied"])
        self.assertGreaterEqual(gate_cal["margin"], 0.15)
        self.assertFalse(gate_cal["should_surface"])


if __name__ == "__main__":
    unittest.main()
