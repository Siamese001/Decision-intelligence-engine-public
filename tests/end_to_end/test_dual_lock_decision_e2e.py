"""
tests/end_to_end/test_dual_lock_decision_e2e.py

End-to-End Governance Test Suite for the Dual-Lock Decision Invariant.
Verifies the complete fail-closed decision governance matrix:
    AutonomousResolutionPermitted = Lock_1 (Mathematical) AND Lock_2 (Criticality)
Ensures that architectural, destructive, policy, and governance actions strictly
mandate human operator review regardless of confidence spreads.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.classifier import (
    DecisionCategory,
)
from dual_pipeline.hitl_governance_core.dual_lock import (
    evaluate_dual_lock,
)
from dual_pipeline.hitl_governance_core.layman_clarity import (
    build_layman_option,
    contains_jargon,
)


class DualLockDecisionE2ETests(unittest.TestCase):
    """End-to-end tests for the Dual-Lock Decision Invariant."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    def test_routine_action_passes_dual_lock(self) -> None:
        """Routine action with decisive margin passes both Lock 1 and Lock 2."""
        opt_a = build_layman_option(
            label="Format standard recap tables",
            plain_english_summary="Format the output tables to improve readability across executive summaries.",
            impact_statement="Improves visual presentation and preserves underlying data structure.",
            confidence_score=0.95,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Retain unformatted text blocks",
            plain_english_summary="Leave the narrative summaries in unformatted markdown blocks.",
            impact_statement="Maintains legacy styling with lower visual clarity.",
            confidence_score=0.60,
            recommended=False,
        )

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["dual_pipeline/utils/formatters.py"],
            action_description="Format summary output presentation",
            context={"question": "Which layout should be applied to executive summaries?"},
        )

        self.assertTrue(result.lock1_passed)
        self.assertTrue(result.lock2_passed)
        self.assertTrue(result.autonomous_permitted)
        self.assertFalse(result.surface_hitl)
        self.assertEqual(result.category, DecisionCategory.ROUTINE)
        self.assertGreaterEqual(result.margin, 0.30)
        self.assertIn("DUAL_LOCK_CLEARANCE", result.reasons[0])
        self.assertIsNone(result.ask_question_payload)

    def test_tactical_narrow_margin_fails_lock1(self) -> None:
        """Tactical action with narrow confidence margin (<15%) fails Lock 1 and surfaces HITL."""
        opt_a = build_layman_option(
            label="Optimize query token caching",
            plain_english_summary="Store repeated query token segments in temporary memory cache.",
            impact_statement="Reduces execution time for repeated queries.",
            confidence_score=0.70,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Bypass query token caching",
            plain_english_summary="Compute query tokens on each request without caching.",
            impact_statement="Ensures strictly deterministic execution with slightly higher response time.",
            confidence_score=0.65,
            recommended=False,
        )

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["dual_pipeline/query_core/cache_helper.py"],
            action_description="Choose caching strategy for search execution",
            context={"question": "Which query caching strategy should be used?"},
        )

        self.assertFalse(result.lock1_passed)
        self.assertTrue(result.lock2_passed)
        self.assertFalse(result.autonomous_permitted)
        self.assertTrue(result.surface_hitl)
        self.assertAlmostEqual(result.margin, 0.05, places=3)
        self.assertTrue(any("LOCK_1_SPREAD_BREACH" in r for r in result.reasons))
        self.assertIsNotNone(result.ask_question_payload)

    def test_architectural_scope_blocks_autonomous_resolution_despite_high_spread(self) -> None:
        """Architectural file changes fail Lock 2 even with extreme confidence spread (0.99 vs 0.01)."""
        opt_a = build_layman_option(
            label="Update data storage schema definitions",
            plain_english_summary="Refactor primary database schema definitions for active graph storage.",
            impact_statement="Updates foundational database structures and eliminates schema migration risk.",
            confidence_score=0.99,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Retain existing schema definitions",
            plain_english_summary="Preserve existing storage structures without modifications.",
            impact_statement="Prevents schema migration risk while requiring compatibility shims.",
            confidence_score=0.01,
            recommended=False,
        )

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["dual_pipeline/storage/sqlite_store.py"],
            action_description="Refactor database schema layout",
            context={"question": "Should database schema changes be approved?"},
        )

        # Lock 1 passes mathematically (0.98 spread >= 0.15), but Lock 2 strictly blocks it
        self.assertTrue(result.lock1_passed)
        self.assertFalse(result.lock2_passed)
        self.assertFalse(result.autonomous_permitted)
        self.assertTrue(result.surface_hitl)
        self.assertEqual(result.category, DecisionCategory.ARCHITECTURAL)
        self.assertTrue(any("LOCK_2_BREACH (ARCHITECTURAL)" in r for r in result.reasons))
        self.assertIsNotNone(result.ask_question_payload)

    def test_destructive_verb_blocks_autonomous_resolution_despite_high_spread(self) -> None:
        """Destructive actions ('retire', 'delete', 'drop', etc.) fail Lock 2 regardless of spread."""
        opt_a = build_layman_option(
            label="Retire and purge legacy background worker",
            plain_english_summary="Permanently decommission and delete the legacy background task runner.",
            impact_statement="Frees system resources and removes deprecated code paths.",
            confidence_score=0.95,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Keep background worker running in standby mode",
            plain_english_summary="Maintain the worker process in an idle state without handling tasks.",
            impact_statement="Preserves emergency fallback capacity while consuming idle resources.",
            confidence_score=0.05,
            recommended=False,
        )

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["dual_pipeline/services/worker.py"],
            action_description="Retire and purge legacy background worker component",
            context={"question": "Should the legacy worker component be retired?"},
        )

        self.assertTrue(result.lock1_passed)
        self.assertFalse(result.lock2_passed)
        self.assertFalse(result.autonomous_permitted)
        self.assertTrue(result.surface_hitl)
        self.assertEqual(result.category, DecisionCategory.DESTRUCTIVE)
        self.assertTrue(any("LOCK_2_BREACH (DESTRUCTIVE)" in r for r in result.reasons))

    def test_governance_scope_blocks_autonomous_resolution(self) -> None:
        """Targeting governance surface files (AGENTS.md, rules/) fails Lock 2."""
        opt_a = build_layman_option(
            label="Update operational decision governance guidelines",
            plain_english_summary="Update system governance guidelines to clarify human approval procedures.",
            impact_statement="Improves verification standards and ensures operational reliability.",
            confidence_score=0.92,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Preserve existing operational guidelines",
            plain_english_summary="Maintain current governance rules without wording revisions.",
            impact_statement="Preserves familiar procedures without adding clarity.",
            confidence_score=0.40,
            recommended=False,
        )

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["AGENTS.md"],
            action_description="Refine decision guidelines",
            context={"question": "Authorize governance rule update?"},
        )

        self.assertTrue(result.lock1_passed)
        self.assertFalse(result.lock2_passed)
        self.assertFalse(result.autonomous_permitted)
        self.assertTrue(result.surface_hitl)
        self.assertEqual(result.category, DecisionCategory.GOVERNANCE)
        self.assertTrue(any("LOCK_2_BREACH (GOVERNANCE)" in r for r in result.reasons))

    def test_policy_modification_blocks_autonomous_resolution(self) -> None:
        """Actions containing policy keywords ('threshold', 'margin') fail Lock 2."""
        opt_a = {
            "label": "Adjust ambiguity_margin parameter to 0.12",
            "plain_english_summary": "Adjust the decision boundary threshold from fifteen percent to twelve percent.",
            "impact_statement": "Alters the sensitivity threshold for escalating decisions to human operators.",
            "confidence_score": 0.88,
        }
        opt_b = {
            "label": "Maintain current threshold",
            "plain_english_summary": "Keep the decision boundary threshold at fifteen percent.",
            "impact_statement": "Preserves established human review frequency.",
            "confidence_score": 0.30,
        }

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            action_description="Modify ambiguity_margin parameter in configuration",
            context={"question": "Authorize policy threshold change?"},
        )

        self.assertTrue(result.lock1_passed)
        self.assertFalse(result.lock2_passed)
        self.assertFalse(result.autonomous_permitted)
        self.assertTrue(result.surface_hitl)
        self.assertEqual(result.category, DecisionCategory.POLICY)
        self.assertTrue(any("LOCK_2_BREACH (POLICY)" in r for r in result.reasons))

    def test_low_top_conviction_floor_breach(self) -> None:
        """Top option confidence below conviction floor (65%) fails Lock 1 despite high spread."""
        opt_a = {
            "label": "Alternative Approach A",
            "plain_english_summary": "Execute alternative strategy with limited empirical backing.",
            "impact_statement": "May introduce operational uncertainty due to low confidence.",
            "confidence_score": 0.60,
        }
        opt_b = {
            "label": "Alternative Approach B",
            "plain_english_summary": "Execute secondary strategy with very low confidence.",
            "impact_statement": "High risk of operational failure.",
            "confidence_score": 0.35,
        }

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["dual_pipeline/utils/cleaner.py"],
            action_description="Select processing strategy",
            context={"question": "Which processing strategy should be chosen?"},
        )

        # Spread is 0.25 (>= 0.15), but top score 0.60 is below 0.65 floor
        self.assertFalse(result.lock1_passed)
        self.assertTrue(result.lock2_passed)
        self.assertFalse(result.autonomous_permitted)
        self.assertTrue(result.surface_hitl)
        self.assertTrue(any("LOCK_1_CONVICTION_FLOOR_BREACH" in r for r in result.reasons))

    def test_dual_lock_generates_schema_compliant_layman_payload(self) -> None:
        """Surfaced HITL decisions generate valid interactive ask_user_question payloads without jargon."""
        opt_a = build_layman_option(
            label="Consolidate testing gateways",
            plain_english_summary="Unify all integration verification runs through the primary governance gateway.",
            impact_statement="Ensures uniform verification standards and prevents test evasion across all waves.",
            confidence_score=0.85,
            recommended=True,
        )
        opt_b = build_layman_option(
            label="Maintain separate testing runners",
            plain_english_summary="Keep separate standalone runners for different module categories.",
            impact_statement="Increases maintenance overhead and introduces risk of divergent test coverage.",
            confidence_score=0.75,
            recommended=False,
        )

        result = evaluate_dual_lock(
            options=[opt_a, opt_b],
            target_files=["contracts/dual_pipeline/schemas/hitl-option.schema.json"],
            action_description="Select verification gateway architecture",
            context={"question": "Which testing verification architecture should be adopted?"},
        )

        self.assertTrue(result.surface_hitl)
        payload = result.ask_user_question_payload
        self.assertIsNotNone(payload)
        self.assertIn("questions", payload)
        self.assertEqual(len(payload["questions"]), 1)

        q_entry = payload["questions"][0]
        self.assertEqual(q_entry["question"], "Which testing verification architecture should be adopted?")
        self.assertEqual(len(q_entry["options"]), 2)

        # Check options formatting and zero jargon
        for opt_text in q_entry["options"]:
            self.assertFalse(contains_jargon(opt_text), f"Jargon found in: {opt_text}")
        self.assertTrue(q_entry["options"][0].startswith("(Recommended)"))


if __name__ == "__main__":
    unittest.main()
