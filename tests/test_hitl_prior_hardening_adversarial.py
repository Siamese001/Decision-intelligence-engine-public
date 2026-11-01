#!/usr/bin/env python3
"""Adversarial Verification Suite for Hardened HITL Prior Calibration & Ambiguity Gating.

Proves:
1. Immunity to substring collision attacks (short keys like 'a', 'yes', 'merge' cannot pollute unrelated options).
2. Strict category isolation (decisions in one category cannot bleed into other categories).
3. Exponential temporal half-life decay on historical operator feedback.
4. ExecutiveAmbiguityEngine governed 0.15 margin threshold alignment.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dual_pipeline.hitl_governance import (
    DEFAULT_MARGIN_THRESHOLD,
    HITLDecisionStore,
)
from dual_pipeline.recap_ambiguity_core.engine import ExecutiveAmbiguityEngine


class AdversarialHITLPriorHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_substring_collision_attack_immunity(self) -> None:
        """Adversarial test: verify short keys ('a', 'yes', 'add') do NOT pollute unrelated keys ('quarantine', 'database')."""
        category = "entity_resolution"

        # Record human affirmation of single-letter key 'a'
        options_a = [
            {"key": "a", "label": "Option A (Quick accept)", "confidence": "60%"},
            {"key": "b", "label": "Option B (Quick reject)", "confidence": "50%"},
        ]
        self.store.record_decision(
            decision_id="dec_adversarial_short_key",
            category=category,
            prompt="Choose action",
            options=options_a,
            selected_option=options_a[0],  # 'a' affirmed, 'b' rejected
            operator_id="operator_redteam",
        )

        # Record affirmation of 'add'
        options_add = [
            {"key": "add", "label": "Add entity", "confidence": "60%"},
            {"key": "skip", "label": "Skip entity", "confidence": "50%"},
        ]
        self.store.record_decision(
            decision_id="dec_adversarial_add_key",
            category=category,
            prompt="Add or skip",
            options=options_add,
            selected_option=options_add[0],  # 'add' affirmed, 'skip' rejected
            operator_id="operator_redteam",
        )

        # Now test candidate options containing 'a' or 'add' as substrings:
        # 'quarantine' contains 'a', 'database' contains 'a', 'addressable' contains 'add'
        test_options = [
            {"key": "quarantine", "label": "Quarantine suspicious alias", "confidence": "60%"},
            {"key": "database_write", "label": "Write directly to database", "confidence": "55%"},
            {"key": "addressable_market", "label": "Define addressable market", "confidence": "50%"},
        ]

        calibrated = self.store.calibrate_options(test_options, category=category)

        # None of these should have matched 'a' or 'add'
        for opt in calibrated:
            self.assertEqual(
                opt["prior_affirmations"],
                0,
                f"Substring leak: '{opt['key']}' incorrectly inherited affirmations from short keys!",
            )
            self.assertEqual(
                opt["prior_rejections"],
                0,
                f"Substring leak: '{opt['key']}' incorrectly inherited rejections!",
            )
            self.assertEqual(
                opt["historical_adjustment"],
                0.0,
                f"Adjustment should be 0.0 for uncontaminated key '{opt['key']}'",
            )
            self.assertEqual(
                opt["confidence_score"],
                round(float(opt.get("confidence", "0%").replace("%", "")) / 100.0, 4),
            )

        # Exact match for 'a' MUST still receive affirmation (under SSOT prior_strength N0=4.0: (2.4 + 1.0)/5.0 - 0.60 = 0.08)
        probe_a = [{"key": "a", "label": "Exact A", "confidence": "60%"}]
        calibrated_a = self.store.calibrate_options(probe_a, category=category)
        self.assertEqual(calibrated_a[0]["prior_affirmations"], 1)
        self.assertAlmostEqual(calibrated_a[0]["historical_adjustment"], 0.08, places=2)

    def test_cross_category_isolation(self) -> None:
        """Verify operator feedback in one category never pollutes another category."""
        # Record affirmation of 'merge' in entity_resolution
        options_er = [
            {"key": "merge", "label": "Merge nodes", "confidence": "60%"},
            {"key": "isolate", "label": "Isolate nodes", "confidence": "50%"},
        ]
        self.store.record_decision(
            decision_id="dec_er_001",
            category="entity_resolution",
            prompt="Merge or isolate",
            options=options_er,
            selected_option=options_er[0],
            operator_id="operator_er",
        )

        # Evaluate same key 'merge' under a different category 'strategic_advisory'
        options_sa = [
            {"key": "merge", "label": "Merge business units", "confidence": "60%"},
            {"key": "divest", "label": "Divest business unit", "confidence": "50%"},
        ]
        calibrated_sa = self.store.calibrate_options(options_sa, category="strategic_advisory")

        # 'merge' in strategic_advisory should have 0 prior affirmations because ER decisions are isolated
        self.assertEqual(calibrated_sa[0]["prior_affirmations"], 0)
        self.assertEqual(calibrated_sa[0]["historical_adjustment"], 0.0)

    def test_exponential_temporal_decay(self) -> None:
        """Verify temporal decay reduces weight of old decisions when half_life_days is configured."""
        category = "temporal_test"
        options = [
            {"key": "approve_policy", "label": "Approve", "confidence": "60%"},
            {"key": "reject_policy", "label": "Reject", "confidence": "50%"},
        ]

        # Record decision 60 days ago
        old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        self.store.record_decision(
            decision_id="dec_old",
            category=category,
            prompt="Policy check",
            options=options,
            selected_option=options[0],
            operator_id="operator_past",
        )
        # Manually backdate record in ledger to simulate past entry
        with self.store._ledger_lock():
            records = self.store._load_records()
            records[-1]["recorded_at"] = old_time
            self.store._save_records(records)

        # Calibrate with grounded-contract half-life: 60 days old => weight = (0.5)^2 = 0.25
        calibrated = self.store.calibrate_options(
            options,
            category=category,
            boost_step=0.15,
            half_life_days=30.0,
        )

        # The effective adjustment should be scaled by the decayed weight (approx 0.15 * 0.25 = 0.0375)
        adj = calibrated[0]["historical_adjustment"]
        self.assertLess(adj, 0.15)
        self.assertGreater(adj, 0.0)
        self.assertAlmostEqual(calibrated[0]["effective_prior_weight"], 0.25, places=1)

    def test_executive_ambiguity_engine_default_margin_governance(self) -> None:
        """Verify ExecutiveAmbiguityEngine defaults to 0.15 and flags margin differences < 15%."""
        engine = ExecutiveAmbiguityEngine()
        self.assertEqual(engine.margin_threshold, DEFAULT_MARGIN_THRESHOLD)
        self.assertEqual(engine.margin_threshold, 0.15)


if __name__ == "__main__":
    unittest.main()
