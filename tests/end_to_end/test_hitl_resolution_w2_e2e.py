"""
tests/end_to_end/test_hitl_resolution_w2_e2e.py

End-to-end qualification suite for Wave 2: Typed Eligibility Service.
Verifies pure deterministic evaluation, policy snapshot binding,
and triple-lock decision semantics without side effects.
"""
from __future__ import annotations

import pytest
from dual_pipeline.hitl_core.margin_evaluator import MarginEvaluator
from dual_pipeline.hitl_governance_core.autonomous_resolution.eligibility import (
    evaluate_decision_eligibility,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.models import (
    AuthorityType,
    DecisionMode,
    DecisionOption,
    DecisionRequest,
    DecisionScope,
    ResolutionTerminalResult,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.policy import (
    load_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.dual_lock import evaluate_dual_lock


class TestHitlResolutionW2E2E:
    """E2E verification of typed decision eligibility and policy binding."""

    @pytest.fixture
    def policy_snapshot(self):
        """Standard governed policy snapshot: margin 0.30, floor 0.65, max_entropy 0.75."""
        return load_policy_snapshot(
            overrides={
                "ambiguity_threshold": 0.30,
                "conviction_floor": 0.65,
                "max_entropy": 0.75,
                "divergence_threshold": 0.20,
                "policy_source": "test_e2e_fixture",
            }
        )

    @pytest.fixture
    def base_scope(self):
        return DecisionScope(
            worktree="/repo/.worktrees/feat_hitl",
            run_id="run_e2e_w2_001",
            wave=2,
            action_target="pipeline_stage_routine_alignment",
        )

    def test_e2e_eligible_routine_two_options_clearance(self, policy_snapshot, base_scope):
        """
        Reproduce 0.95 vs 0.52 candidate incident under governed routine scope:
        Margin = 0.43 (> 0.30), c_top = 0.95 (>= 0.65), K=2 options.
        Must resolve autonomously to opt_01.
        """
        req = DecisionRequest(
            decision_id="DECISION-ALIGNMENT-W3-OWNER-RULES",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(
                    option_id="opt_01",
                    description="Bind owner alignment rules deterministically",
                    confidence=0.95,
                ),
                DecisionOption(
                    option_id="opt_02",
                    description="Defer owner alignment to secondary heuristics",
                    confidence=0.52,
                ),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Align routine owner rules in cache",
        )

        result = evaluate_decision_eligibility(req, policy_snapshot)

        assert result.terminal_result == ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY
        assert result.is_eligible is True
        assert result.selected_option_id == "opt_01"
        assert result.selected_option is not None
        assert result.selected_option.option_id == "opt_01"
        assert result.margin == 0.43
        assert "DUAL_LOCK_CLEARANCE" in result.reason_codes

    def test_e2e_ineligible_ambiguous_margin(self, policy_snapshot, base_scope):
        """
        Reproduce 0.90 vs 0.65 candidate review condition:
        Margin = 0.25 (<= 0.30 ambiguity threshold).
        Must fail Lock 1 and require human review.
        """
        req = DecisionRequest(
            decision_id="DECISION-AMBIGUOUS-MARGIN-001",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(
                    option_id="opt_a",
                    description="Option A with high confidence",
                    confidence=0.90,
                ),
                DecisionOption(
                    option_id="opt_b",
                    description="Option B with competitive confidence",
                    confidence=0.65,
                ),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Resolve ambiguous strategy",
        )

        result = evaluate_decision_eligibility(req, policy_snapshot)

        assert result.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert result.is_eligible is False
        assert result.selected_option_id is None
        assert result.selected_option is None
        assert result.margin == 0.25
        assert "MARGIN_BELOW_OR_EQUAL_THRESHOLD" in result.reason_codes

    def test_e2e_ineligible_conviction_floor_breach(self, policy_snapshot, base_scope):
        """
        Test low absolute confidence despite wide spread:
        Scores 0.60 vs 0.20 (spread = 0.40 > 0.30, but 0.60 < 0.65 floor).
        Must fail Lock 1 with CONFIDENCE_FLOOR_BREACH.
        """
        req = DecisionRequest(
            decision_id="DECISION-FLOOR-BREACH-001",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_low", description="Option Low", confidence=0.60),
                DecisionOption(option_id="opt_lower", description="Option Lower", confidence=0.20),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Routine action with low conviction",
        )

        result = evaluate_decision_eligibility(req, policy_snapshot)

        assert result.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert result.is_eligible is False
        assert "CONFIDENCE_FLOOR_BREACH" in result.reason_codes

    def test_e2e_three_options_entropy_gating(self, policy_snapshot, base_scope):
        """
        Test K=3 multi-option uncertainty:
        Low entropy distribution passes; flat/diffuse distribution triggers ENTROPY_OVERRIDE.
        """
        # Case 1: Skewed distribution (low entropy)
        req_decisive = DecisionRequest(
            decision_id="DECISION-K3-DECISIVE",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Clear winner", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Distractor 1", confidence=0.10),
                DecisionOption(option_id="opt_3", description="Distractor 2", confidence=0.05),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Decisive 3-option choice",
        )
        res_decisive = evaluate_decision_eligibility(req_decisive, policy_snapshot)
        assert res_decisive.terminal_result == ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY
        assert res_decisive.entropy <= policy_snapshot.max_entropy

        # Case 2: Diffuse distribution (high entropy override)
        req_diffuse = DecisionRequest(
            decision_id="DECISION-K3-DIFFUSE",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Top option", confidence=0.70),
                DecisionOption(option_id="opt_2", description="Mid option", confidence=0.25),
                DecisionOption(option_id="opt_3", description="Third option", confidence=0.25),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Diffuse 3-option choice",
        )
        res_diffuse = evaluate_decision_eligibility(req_diffuse, policy_snapshot)
        assert res_diffuse.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert "ENTROPY_OVERRIDE" in res_diffuse.reason_codes

    def test_e2e_option_permutation_and_tie_order_invariance(self, policy_snapshot, base_scope):
        """Test that permuting input option order produces identical deterministic outcome."""
        opts_order_a = [
            DecisionOption(option_id="opt_beta", description="Beta", confidence=0.50),
            DecisionOption(option_id="opt_alpha", description="Alpha", confidence=0.90),
        ]
        opts_order_b = [
            DecisionOption(option_id="opt_alpha", description="Alpha", confidence=0.90),
            DecisionOption(option_id="opt_beta", description="Beta", confidence=0.50),
        ]

        req_a = DecisionRequest(
            decision_id="DECISION-ORDER-A",
            revision=1,
            scope=base_scope,
            options=opts_order_a,
            authority_type=AuthorityType.AUTONOMOUS,
        )
        req_b = DecisionRequest(
            decision_id="DECISION-ORDER-B",
            revision=1,
            scope=base_scope,
            options=opts_order_b,
            authority_type=AuthorityType.AUTONOMOUS,
        )

        res_a = evaluate_decision_eligibility(req_a, policy_snapshot)
        res_b = evaluate_decision_eligibility(req_b, policy_snapshot)

        assert res_a.terminal_result == res_b.terminal_result == ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY
        assert res_a.selected_option_id == res_b.selected_option_id == "opt_alpha"
        assert res_a.margin == res_b.margin == 0.40

    def test_e2e_policy_snapshot_binding_and_immutability(self, base_scope):
        """Verify that policy snapshot locks in thresholds and resists parameter tampering."""
        custom_snap = load_policy_snapshot(
            overrides={"ambiguity_threshold": 0.40, "conviction_floor": 0.70}
        )
        assert custom_snap.ambiguity_threshold == 0.40
        assert custom_snap.conviction_floor == 0.70

        req = DecisionRequest(
            decision_id="DECISION-SNAP-BIND",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.85),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        # Margin is 0.35, which passes 0.30 but fails 0.40 custom snapshot
        res = evaluate_decision_eligibility(req, custom_snap)
        assert res.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert "MARGIN_BELOW_OR_EQUAL_THRESHOLD" in res.reason_codes

    def test_e2e_dual_lock_and_margin_evaluator_snapshot_integration(self, policy_snapshot):
        """Verify MarginEvaluator and evaluate_dual_lock consume PolicySnapshot seamlessly."""
        raw_options = [
            {"label": "Choice 1", "confidence": 0.95},
            {"label": "Choice 2", "confidence": 0.52},
        ]
        # MarginEvaluator integration
        me_res = MarginEvaluator.evaluate(raw_options, policy_snapshot=policy_snapshot)
        assert me_res["decision_resolved_autonomously"] is True
        assert me_res["surface_hitl"] is False

        # DualLock integration
        dl_res = evaluate_dual_lock(
            raw_options,
            context={"category": "routine"},
            policy_snapshot=policy_snapshot,
        )
        assert dl_res.autonomous_permitted is True
        assert dl_res.surface_hitl is False
        assert dl_res.lock1_passed is True
        assert dl_res.lock2_passed is True
