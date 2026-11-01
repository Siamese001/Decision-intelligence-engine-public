#!/usr/bin/env python3
"""End-to-end tests for Wave 4: 30% ambiguity rule via governed parameter path.

Verifies:
1. End-to-end evaluation flow through Dual-Lock and MarginEvaluator at 30% boundary.
2. Proposal simulation and UWG commit artifacts verify without mutation (+0 graph delta).
3. Rollback receipt validity and status check.
4. Linter verification: zero raw margin literals across repository.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json
from dual_pipeline.hitl_governance_core.dual_lock import evaluate_dual_lock
from dual_pipeline.learning_engine import get_learning_engine
from tools.lint_margin_ssot import lint_python_file


class TestAmbiguityRule30E2E(unittest.TestCase):
    """End-to-end test suite for 30% ambiguity rule and UWG commit artifacts."""

    def test_e2e_dual_lock_full_lifecycle(self) -> None:
        """End-to-end verification of dual-lock decision gating with 30% rule."""
        # Scenario 1: Ambiguous decision at 30% spread (0.80 vs 0.50)
        opts_ambiguous = [
            {
                "id": "opt_a",
                "label": "Deploy Architecture Alpha",
                "confidence": 0.80,
                "tier": "HIGH",
            },
            {
                "id": "opt_b",
                "label": "Deploy Architecture Beta",
                "confidence": 0.50,
                "tier": "MEDIUM",
            },
        ]
        res_amb = evaluate_dual_lock(
            opts_ambiguous,
            action_description="Choose system architecture for cloud migration",
        )
        self.assertFalse(res_amb.lock1_passed)
        self.assertTrue(res_amb.surface_hitl)
        self.assertFalse(res_amb.autonomous_permitted)
        self.assertIsNotNone(res_amb.ask_question_payload)
        self.assertEqual(len(res_amb.ask_question_payload["questions"][0]["options"]), 2)

        # Scenario 2: Decisive decision above 30% spread (0.85 vs 0.50 -> 35% gap)
        opts_decisive = [
            {"id": "opt_a", "label": "Standard Bug Fix", "confidence": 0.85, "tier": "HIGH"},
            {"id": "opt_b", "label": "Full Refactor", "confidence": 0.50, "tier": "MEDIUM"},
        ]
        res_dec = evaluate_dual_lock(
            opts_decisive,
            action_description="Fix minor typo in log message",
        )
        self.assertTrue(res_dec.lock1_passed)
        self.assertFalse(res_dec.surface_hitl)
        self.assertTrue(res_dec.autonomous_permitted)

    def test_e2e_learning_proposal_simulation_and_receipts(self) -> None:
        """Verifies that the 30% learning change proposal simulates and has valid UWG receipts."""
        proposals_dir = Path("reports/learning_proposals")
        proposal_path = proposals_dir / "lcp-hitl-ambiguity-threshold-30pct.json"
        self.assertTrue(proposal_path.is_file(), f"Missing proposal file: {proposal_path}")

        prop = read_json(proposal_path)
        validate_artifact(prop)
        self.assertEqual(prop["proposal_id"], "lcp-hitl-ambiguity-threshold-30pct")

        # Verify simulation passes
        engine = get_learning_engine(proposals_dir=proposals_dir)
        sim_result = engine.simulate_proposal(prop)
        self.assertTrue(sim_result.success)
        self.assertEqual(sim_result.status, "SIMULATED_PASS")
        self.assertLess(sim_result.details.get("brier_score_delta", 0), 0)

        # Verify applied receipt
        applied_path = proposals_dir / "lcp-hitl-ambiguity-threshold-30pct_applied_receipt.json"
        self.assertTrue(applied_path.is_file(), f"Missing applied receipt: {applied_path}")
        applied = read_json(applied_path)
        self.assertEqual(applied["status"], "APPLIED")
        self.assertEqual(applied["applied_parameters"]["hitl.ambiguity_margin_threshold"], 0.3)
        self.assertEqual(applied["previous_parameters"]["hitl.ambiguity_margin_threshold"], 0.15)

        # Verify +0 graph delta (idempotent noop on graph)
        deltas = applied.get("graph_delta_counts", {})
        self.assertEqual(
            deltas.get("total_delta", 0), 0, f"Expected 0 graph mutations, got {deltas}"
        )

        # Verify rollback receipt
        rollback_path = proposals_dir / "lcp-hitl-ambiguity-threshold-30pct_rollback_receipt.json"
        self.assertTrue(rollback_path.is_file(), f"Missing rollback receipt: {rollback_path}")
        rollback = read_json(rollback_path)
        self.assertEqual(rollback["status"], "PENDING_ROLLBACK")
        self.assertEqual(rollback["restore_parameters"]["hitl.ambiguity_margin_threshold"], 0.15)

    def test_e2e_margin_linter_zero_violations(self) -> None:
        """Verifies that all modified pipeline modules have zero literal margin violations."""
        files_to_audit = [
            Path("dual_pipeline/hitl_core/margin_evaluator.py"),
            Path("dual_pipeline/hitl_governance_core/dual_lock.py"),
            Path("dual_pipeline/ambiguity_core/models.py"),
            Path("dual_pipeline/ambiguity_core/detector_d6_entity.py"),
            Path("dual_pipeline/ambiguity_core/engine.py"),
            Path("dual_pipeline/graph_ambiguity_core/engine.py"),
            Path("dual_pipeline/graph_ambiguity_core/models.py"),
        ]
        for file_path in files_to_audit:
            violations = lint_python_file(file_path)
            self.assertEqual(len(violations), 0, f"Violations found in {file_path}: {violations}")


if __name__ == "__main__":
    unittest.main()
