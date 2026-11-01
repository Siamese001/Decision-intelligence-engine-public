"""tests/test_hitl_margin_enforcement_adversarial.py

Adversarial and boundary test suite for > 20% HITL ambiguity margin enforcement.
Covers:
1. Exact floating point boundary conditions: 0.2001 (autonomous) vs 0.2000 (HITL) vs 0.1999 (HITL).
2. Option descriptions with prose words like 'remove', 'discard', 'clean', 'threshold' do not void > 20% margin.
3. Multi-option sets with > 20% lead and conviction >= 0.65 pass Lock 1 even with high entropy distribution.
4. Tier-0 destructive operations (drop table, rm -rf, pr_merge_authorization) remain strictly non-waivable.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest import TestCase

from dual_pipeline.hitl_criticality import CriticalityTier, evaluate_criticality_score
from dual_pipeline.hitl_governance_core import DecisionCategory, classify_decision_criticality
from dual_pipeline.hitl_governance_core.canonical_evaluator import evaluate_canonical_decision
from dual_pipeline.parameters import needs_operator_decision


class HitlMarginEnforcementAdversarialTests(TestCase):
    """Adversarial, boundary, and invariant stress tests for HITL margin enforcement."""

    def test_exact_floating_point_20pct_boundary(self) -> None:
        """Delta > 0.20 resolves autonomously; Delta <= 0.20 requires operator review."""
        # 1. Delta strictly greater than 0.20 (0.2001)
        self.assertFalse(
            needs_operator_decision(0.8001, 0.6000),
            "Margin of 0.2001 (> 0.20) must resolve autonomously.",
        )

        # 2. Delta exactly 0.20 (0.2000)
        self.assertTrue(
            needs_operator_decision(0.8000, 0.6000),
            "Exact 0.2000 margin (<= 0.20) must require operator decision.",
        )

        # 3. Delta strictly less than 0.20 (0.1999)
        self.assertTrue(
            needs_operator_decision(0.7999, 0.6000),
            "Margin of 0.1999 (< 0.20) must require operator decision.",
        )

    def test_prose_words_in_options_do_not_void_passing_margin(self) -> None:
        """Everyday phrases like 'remove clutter' or 'budget threshold' do not trigger Lock 2 on tactical choices."""
        options = [
            {
                "label": "Option A: Clean up cache",
                "plain_english_summary": "Removes temporary queue clutter and discards stale cache entries.",
                "confidence": 0.85,
            },
            {
                "label": "Option B: Retain cache",
                "plain_english_summary": "Preserves existing entries within memory threshold limits.",
                "confidence": 0.50,
            },
        ]
        eval_res = evaluate_canonical_decision(
            options=options,
            context={"category": "OPERATIONAL", "question": "Select cache cleanup mode"},
        )
        self.assertTrue(
            eval_res.autonomous_permitted,
            "Prose words like 'removes' or 'threshold' in option descriptions must not void a 35% margin.",
        )
        self.assertFalse(eval_res.surface_hitl)

    def test_multi_option_high_entropy_vector_with_qualifying_margin(self) -> None:
        """3-option set with > 20% margin and conviction >= 0.65 passes Lock 1 without entropy block."""
        # [0.66, 0.45, 0.40]: delta = 0.21 > 0.20, c_top = 0.66 >= 0.65
        options = [
            {"label": "Approach 1", "confidence": 0.66},
            {"label": "Approach 2", "confidence": 0.45},
            {"label": "Approach 3", "confidence": 0.40},
        ]
        eval_res = evaluate_canonical_decision(
            options=options,
            context={"category": "OPERATIONAL", "question": "Choose approach"},
        )
        self.assertTrue(
            eval_res.lock1_passed,
            "Qualifying margin (0.21 > 0.20) with top confidence >= 0.65 must pass Lock 1.",
        )
        self.assertNotIn("ENTROPY_OVERRIDE", eval_res.reason_codes)

    def test_tier_0_destructive_operations_remain_strictly_non_waivable(self) -> None:
        """Catastrophic operations (DROP TABLE, PR merge) mandate HITL review regardless of confidence margin."""
        # 1. DROP TABLE statement with 99% vs 1% spread
        options_drop = [
            {"label": "DROP TABLE knowledge_nodes", "confidence": 0.99},
            {"label": "Keep table", "confidence": 0.01},
        ]
        eval_drop = evaluate_canonical_decision(
            options=options_drop,
            action_description="DROP TABLE knowledge_nodes",
            category="DESTRUCTIVE",
        )
        self.assertFalse(eval_drop.autonomous_permitted)
        self.assertTrue(eval_drop.surface_hitl)
        self.assertFalse(eval_drop.lock2_passed)

        # 2. PR merge authorization
        eval_merge = evaluate_canonical_decision(
            options=[
                {"label": "Approve PR merge of feat/foo into main", "confidence": 0.98},
                {"label": "Hold PR", "confidence": 0.02},
            ],
            action_description="Authorize PR merge into main",
            category="GOVERNANCE",
        )
        self.assertFalse(eval_merge.autonomous_permitted)
        self.assertTrue(eval_merge.surface_hitl)
