"""Adversarial and edge-case tests for operator_class Bayesian prior weighting."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dual_pipeline.hitl_governance_core.bayesian import (
    _accumulate_historical_weights,
    _compute_record_weight,
    get_hitl_autonomous_feedback_weight,
    get_hitl_human_feedback_weight,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore


class OperatorClassWeightingEdgeCasesTests(unittest.TestCase):
    """Verify that operator classifications prevent synthetic feedback contamination of Bayesian priors."""

    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.auto_w = get_hitl_autonomous_feedback_weight()  # 0.05
        self.human_w = get_hitl_human_feedback_weight()  # 1.0

    def test_test_class_records_receive_zero_weight(self) -> None:
        """Adversarial test decisions (operator_class='TEST' or test IDs) receive weight 0.0."""
        cases = [
            {"operator_class": "TEST", "operator_id": "human_operator"},
            {"operator_id": "test_suite_runner"},
            {"operator_id": "mock_judge"},
            {"operator_id": "pytest_fixture"},
            {"is_test": True, "operator_id": "human_operator"},
            {"metadata": {"is_test": True}, "operator_id": "human_operator"},
            {"metadata": {"operator_class": "TEST"}, "operator_id": "human_operator"},
        ]
        for c in cases:
            w = _compute_record_weight(c, half_life_days=None, now=self.now)
            self.assertEqual(w, 0.0, f"Expected weight 0.0 for test record {c}, got {w}")

    def test_autonomous_decisions_clamped_to_autonomous_weight(self) -> None:
        """Autonomous engines, autonomous context, and auto outcomes receive 0.05 weight."""
        cases = [
            {"operator_class": "AUTONOMOUS", "operator_id": "human_operator"},
            {"operator_id": "autonomous_engine"},
            {"operator_id": "autonomous_context"},
            {"operator_id": "auto_engine"},
            {"operator_id": "auto_pipeline"},
            {"outcome_type": "AUTONOMOUS", "operator_id": "operator_unknown"},
            {"metadata": {"outcome_type": "AUTONOMOUS"}, "operator_id": "operator_unknown"},
            {"metadata": {"operator_class": "AUTONOMOUS"}, "operator_id": "operator_unknown"},
        ]
        for c in cases:
            w = _compute_record_weight(c, half_life_days=None, now=self.now)
            self.assertEqual(w, self.auto_w, f"Expected weight {self.auto_w} for {c}, got {w}")

    def test_human_operator_receives_full_weight(self) -> None:
        """Authentic human decisions receive full 1.0 feedback weight."""
        cases = [
            {"operator_class": "HUMAN", "operator_id": "Amit Ayer"},
            {"operator_id": "Amit Ayer"},
            {"operator_id": "human_operator", "outcome_type": "AFFIRMED"},
            {"metadata": {"operator_class": "HUMAN"}, "operator_id": "reviewer_jane"},
        ]
        for c in cases:
            w = _compute_record_weight(c, half_life_days=None, now=self.now)
            self.assertEqual(w, self.human_w, f"Expected weight {self.human_w} for {c}, got {w}")

    def test_accumulate_weights_excludes_test_and_attenuates_auto(self) -> None:
        """Historical accumulator correctly aggregates human, auto, and test decisions."""
        history = [
            # 1 human decision for option_a -> +1.0
            {
                "category": "risk",
                "operator_class": "HUMAN",
                "selected_key": "opt_a",
                "options": ["opt_a", "opt_b"],
            },
            # 10 autonomous decisions for option_a -> 10 * 0.05 = +0.5
            *[
                {
                    "category": "risk",
                    "operator_class": "AUTONOMOUS",
                    "selected_key": "opt_a",
                    "options": ["opt_a", "opt_b"],
                }
                for _ in range(10)
            ],
            # 50 test decisions for option_b -> 50 * 0.0 = +0.0
            *[
                {
                    "category": "risk",
                    "operator_class": "TEST",
                    "selected_key": "opt_b",
                    "options": ["opt_a", "opt_b"],
                }
                for _ in range(50)
            ],
        ]
        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(
            history, category="risk", half_life_days=None, now=self.now
        )
        self.assertAlmostEqual(aff_w.get("opt_a", 0.0), 1.5, places=3)
        self.assertAlmostEqual(aff_w.get("opt_b", 0.0), 0.0, places=3)

    def test_ledger_record_decision_persists_operator_class(self) -> None:
        """HITLDecisionStore persists operator_class in recorded entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "test_ledger.json"
            store = HITLDecisionStore(ledger_path=ledger_file, use_sqlite=False)
            rec = store.record_decision(
                decision_id="dec_001",
                category="architecture",
                prompt="Select database strategy",
                options=["sqlite", "postgres"],
                selected_option="sqlite",
                operator_id="autonomous_context",
                operator_class="AUTONOMOUS",
            )
            self.assertEqual(rec["operator_class"], "AUTONOMOUS")
            self.assertEqual(rec["outcome_type"], "AUTONOMOUS")

            records = store.get_history()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["operator_class"], "AUTONOMOUS")


if __name__ == "__main__":
    unittest.main()
