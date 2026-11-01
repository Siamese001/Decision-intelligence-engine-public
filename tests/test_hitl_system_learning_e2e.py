#!/usr/bin/env python3
"""End-to-End Test Suite for Continuous HITL Prior Calibration and System Learning.

Verifies:
1. Multi-round Bayesian posterior convergence across 10 decision cycles.
2. Anti-contamination firewall: autonomous self-affirmation is discounted (w=0.05) vs human operator (w=1.0).
3. Absolute confidence floor enforcement (c_top < 0.65 surfaces even when margin Delta >= 0.25).
4. Frontier model binding to GPT-6-Astra (high) across HITL arbiter agents.
5. Export of learned priors to hitl_prior_calibration.json with cryptographic digest.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.decision.agents.hitl_arbiter_agent import get_hitl_arbiter_card
from dual_pipeline.hitl_governance import (
    HITLDecisionStore,
    evaluate_hitl_surfacing_gate,
)
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_autonomous_feedback_weight,
    get_hitl_bayesian_prior_strength,
    get_hitl_divergence_margin_threshold,
    get_hitl_entropy_ambiguity_threshold,
    get_hitl_human_feedback_weight,
    get_hitl_min_autonomous_confidence_floor,
)


class TestHITLSystemLearningE2E(unittest.TestCase):
    """E2E verification of continuous HITL learning, Bayesian calibration, and governance safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_01_ssot_parameters_and_gpt56_model_binding(self) -> None:
        """Verify all SSOT parameters and GPT-6-Astra (high) model binding."""
        self.assertEqual(get_hitl_ambiguity_margin_threshold(), 0.30)
        self.assertEqual(get_hitl_divergence_margin_threshold(), 0.20)
        self.assertEqual(get_hitl_min_autonomous_confidence_floor(), 0.65)
        self.assertEqual(get_hitl_entropy_ambiguity_threshold(), 0.75)
        self.assertEqual(get_hitl_bayesian_prior_strength(), 4.0)
        self.assertEqual(get_hitl_human_feedback_weight(), 1.0)
        self.assertEqual(get_hitl_autonomous_feedback_weight(), 0.05)

        card = get_hitl_arbiter_card()
        self.assertEqual(card.model_binding.model_id, "gpt-6.1-sol")
        self.assertEqual(card.model_binding.reasoning_effort, "high")

    def test_02_multi_round_bayesian_learning_convergence(self) -> None:
        """Simulate 10 rounds of human operator feedback showing continuous posterior convergence."""
        category = "entity_resolution_reconciliation"
        base_options = [
            {"key": "canonical_merge", "label": "Merge with existing canonical entity", "confidence": "58%"},
            {"key": "create_isolated", "label": "Create new isolated node", "confidence": "52%"},
        ]

        # Round 0: Ambiguous (Delta = 6% < 25%) -> Surfaces to operator
        gate_r0 = evaluate_hitl_surfacing_gate(base_options)
        self.assertTrue(gate_r0["should_surface"])
        self.assertAlmostEqual(gate_r0["margin"], 0.06, places=2)

        prev_margin = gate_r0["margin"]
        prev_score = 0.58

        # Simulate 10 consecutive human operator affirmations for canonical_merge
        for r in range(1, 11):
            self.store.record_decision(
                decision_id=f"dec_learning_r{r:02d}",
                category=category,
                prompt=f"Round {r} candidate reconciliation",
                options=base_options,
                selected_option=base_options[0],
                operator_id="human_expert_reviewer",
                rationale=f"Operator verified canonical alignment round {r}",
            )

            calibrated = self.store.calibrate_options(base_options, category=category, prior_strength=4.0)
            gate = evaluate_hitl_surfacing_gate(calibrated)

            top_opt = next(o for o in calibrated if o["key"] == "canonical_merge")
            runner_up = next(o for o in calibrated if o["key"] == "create_isolated")

            # Confidence score must monotonically increase
            self.assertGreater(top_opt["confidence_score"], prev_score)
            prev_score = top_opt["confidence_score"]

            # Margin must widen
            self.assertGreater(gate["margin"], prev_margin)
            prev_margin = gate["margin"]

            # Affirmations count correctly tracked
            self.assertEqual(top_opt["prior_affirmations"], r)
            self.assertEqual(runner_up["prior_rejections"], r)

        # By Round 10, confidence is decisively HIGH and margin well exceeds 25%
        final_calibrated = self.store.calibrate_options(base_options, category=category, prior_strength=4.0)
        final_gate = evaluate_hitl_surfacing_gate(final_calibrated)
        self.assertFalse(final_gate["should_surface"])
        self.assertGreater(final_gate["margin"], 0.50)
        self.assertGreater(final_calibrated[0]["confidence_score"], 0.85)

    def test_03_anti_contamination_firewall(self) -> None:
        """Prove autonomous engine decisions (w=0.05) cannot fabricate high confidence without human input."""
        category = "isolated_claim_validation"
        options = [
            {"key": "approve_claim", "label": "Approve speculative claim", "confidence": "52%"},
            {"key": "reject_claim", "label": "Reject ungrounded claim", "confidence": "48%"},
        ]

        # 10 autonomous decisions affirming approve_claim
        for r in range(10):
            self.store.record_decision(
                decision_id=f"dec_auto_{r}",
                category=category,
                prompt="Autonomous batch check",
                options=options,
                selected_option=options[0],
                operator_id="autonomous_engine",
            )

        calibrated_auto = self.store.calibrate_options(options, category=category, prior_strength=4.0)
        top_auto = next(o for o in calibrated_auto if o["key"] == "approve_claim")

        # 10 * 0.05 = 0.50 effective weight -> minimal score increase (<= 0.05)
        self.assertLess(top_auto["confidence_score"] - 0.52, 0.06)
        # Still ambiguous -> must NOT proceed autonomously
        gate_auto = evaluate_hitl_surfacing_gate(calibrated_auto)
        self.assertTrue(gate_auto["should_surface"])

        # Now, exactly ONE authentic human confirmation is recorded
        self.store.record_decision(
            decision_id="dec_human_01",
            category=category,
            prompt="Human review check",
            options=options,
            selected_option=options[0],
            operator_id="human_operator_lead",
        )

        calibrated_with_human = self.store.calibrate_options(options, category=category, prior_strength=4.0)
        top_with_human = next(o for o in calibrated_with_human if o["key"] == "approve_claim")

        # Human weight (1.0) creates significantly larger boost than 10 autonomous rounds combined
        human_boost = top_with_human["confidence_score"] - top_auto["confidence_score"]
        self.assertGreater(human_boost, 0.06)

    def test_04_confidence_floor_enforcement_prevents_weak_auto_approval(self) -> None:
        """Delta >= 25% but top confidence < 65% must surface to prevent unconvincing autonomous actions."""
        # Top = 45%, Runner-up = 15% -> Delta = 30% >= 25%
        weak_options = [
            {"key": "guess_a", "label": "Speculative Path A", "confidence": "45%"},
            {"key": "guess_b", "label": "Remote Path B", "confidence": "15%"},
        ]

        gate = evaluate_hitl_surfacing_gate(weak_options, margin_threshold=0.25, min_confidence_floor=0.65)
        self.assertTrue(gate["should_surface"])
        self.assertTrue(gate["confidence_floor_breached"])
        self.assertIn("CONFIDENCE_FLOOR_BREACH", gate["reason"])

        # Satisfying the 65% floor allows autonomous progression
        conviction_options = [
            {"key": "proven_a", "label": "Evidence-Backed Path A", "confidence": "70%"},
            {"key": "remote_b", "label": "Remote Path B", "confidence": "15%"},
        ]
        gate_conviction = evaluate_hitl_surfacing_gate(conviction_options, margin_threshold=0.25, min_confidence_floor=0.65)
        self.assertFalse(gate_conviction["should_surface"])
        self.assertFalse(gate_conviction["confidence_floor_breached"])

    def test_05_export_learned_priors_ledger(self) -> None:
        """Verify export_learned_priors creates a digest-sealed persistent calibration report."""
        category = "governance_classification"
        options = [
            {"key": "confidential_partner_advisory", "label": "PartnerAdvisory Confidential", "confidence": "80%"},
            {"key": "internal_nee", "label": "NEE Internal", "confidence": "60%"},
        ]
        self.store.record_decision(
            decision_id="dec_priors_001",
            category=category,
            prompt="Classify document",
            options=options,
            selected_option=options[0],
            operator_id="human_compliance_officer",
        )

        export_target = Path(self.temp_dir.name) / "reports" / "hitl_prior_calibration.json"
        res = self.store.export_learned_priors(export_target)

        self.assertTrue(export_target.exists())
        self.assertEqual(res["schema_version"], "1.0.0")
        self.assertIn("priors_digest", res)
        self.assertGreater(len(res["priors_digest"]), 10)
        self.assertEqual(res["total_records_analyzed"], 1)
        self.assertIn(category, res["learned_priors"])
        self.assertIn("confidential_partner_advisory", res["learned_priors"][category])
        self.assertEqual(res["learned_priors"][category]["confidential_partner_advisory"]["affirmed_weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
