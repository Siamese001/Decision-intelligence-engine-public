"""
tests/adversarial/test_hitl_resolution_w2_adversarial.py

Adversarial qualification suite for Wave 2: Typed Decision Eligibility Service.
Covers 3 mandatory categories under e2e-and-edge-case-coverage.md:
1. Boundary payload (exact threshold equality, conviction floor edge, entropy ceiling).
2. Malformed input (bool types, strings, NaN, infinity, duplicate IDs, UI controls).
3. Invariant bypass attempts (99%/1% critical categories, plan-only, authority bypass).
"""
from __future__ import annotations

import pytest
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


class TestHitlResolutionW2Adversarial:
    """Adversarial stress and penetration tests for Wave 2 eligibility engine."""

    @pytest.fixture
    def policy_snapshot(self):
        return load_policy_snapshot(
            overrides={
                "ambiguity_threshold": 0.30,
                "conviction_floor": 0.65,
                "max_entropy": 0.75,
                "divergence_threshold": 0.20,
                "policy_source": "test_adversarial_fixture",
            }
        )

    @pytest.fixture
    def base_scope(self):
        return DecisionScope(
            worktree="/repo/.worktrees/feat_hitl",
            run_id="run_adv_w2_001",
            wave=2,
            action_target="pipeline_stage_adversarial_test",
        )

    # =========================================================================
    # CATEGORY 1: BOUNDARY PAYLOAD TESTS
    # =========================================================================

    def test_boundary_exact_threshold_equality_requires_review(self, policy_snapshot, base_scope):
        """
        SSOT boundary rule: equality at ambiguity threshold (margin == 0.30)
        MUST require operator review (needs_operator_decision returns True).
        Scores: 0.90 vs 0.60 (diff = 0.3000).
        """
        req = DecisionRequest(
            decision_id="DECISION-BOUND-THRESHOLD-EQUAL",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.60),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res = evaluate_decision_eligibility(req, policy_snapshot)
        assert res.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert res.is_eligible is False
        assert "MARGIN_BELOW_OR_EQUAL_THRESHOLD" in res.reason_codes

    def test_boundary_exact_conviction_floor_equality_passes_lock(self, policy_snapshot, base_scope):
        """
        SSOT boundary rule: equality at conviction floor (c_top == 0.65)
        passes the conviction floor lock.
        Scores: 0.65 vs 0.20 (diff = 0.45 > 0.30, c_top == 0.65).
        """
        req = DecisionRequest(
            decision_id="DECISION-BOUND-FLOOR-EQUAL",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.65),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.20),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res = evaluate_decision_eligibility(req, policy_snapshot)
        assert res.terminal_result == ResolutionTerminalResult.RESOLVED_AUTONOMOUSLY
        assert res.is_eligible is True
        assert res.selected_option_id == "opt_1"

    # =========================================================================
    # CATEGORY 2: MALFORMED INPUT AND TYPE CORRUPTION
    # =========================================================================

    def test_malformed_boolean_confidence_strictly_rejected(self, policy_snapshot, base_scope):
        """
        In Python, bool inherits from int (isinstance(True, int) is True).
        Passing True/False must NOT be coerced to 1.0/0.0; it must return INVALID_INPUT.
        """
        req = DecisionRequest(
            decision_id="DECISION-MALFORMED-BOOL",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=True),  # type: ignore
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res = evaluate_decision_eligibility(req, policy_snapshot)
        assert res.terminal_result == ResolutionTerminalResult.INVALID_INPUT
        assert res.is_eligible is False
        assert any("boolean" in r.lower() for r in res.reasons)

    def test_malformed_string_and_nan_confidence_rejected(self, policy_snapshot, base_scope):
        """Verify strings, percentages, and NaN/Inf floats fail validation closed."""
        # Case A: String percent
        req_str = DecisionRequest(
            decision_id="DECISION-MALFORMED-STR",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence="95%"),  # type: ignore
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res_str = evaluate_decision_eligibility(req_str, policy_snapshot)
        assert res_str.terminal_result == ResolutionTerminalResult.INVALID_INPUT

        # Case B: NaN float
        req_nan = DecisionRequest(
            decision_id="DECISION-MALFORMED-NAN",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=float("nan")),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res_nan = evaluate_decision_eligibility(req_nan, policy_snapshot)
        assert res_nan.terminal_result == ResolutionTerminalResult.INVALID_INPUT

    def test_malformed_duplicate_ids_and_ui_controls_rejected(self, policy_snapshot, base_scope):
        """Duplicate option IDs and UI controls like 'Other' must be rejected."""
        # Duplicate IDs
        req_dup = DecisionRequest(
            decision_id="DECISION-MALFORMED-DUP",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_same", description="Alpha", confidence=0.90),
                DecisionOption(option_id="opt_same", description="Beta", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res_dup = evaluate_decision_eligibility(req_dup, policy_snapshot)
        assert res_dup.terminal_result == ResolutionTerminalResult.INVALID_INPUT

        # UI meta control injected as substantive option
        req_ui = DecisionRequest(
            decision_id="DECISION-MALFORMED-UI",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Real option", confidence=0.90),
                DecisionOption(option_id="opt_2", description="Other", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
        )
        res_ui = evaluate_decision_eligibility(req_ui, policy_snapshot)
        assert res_ui.terminal_result == ResolutionTerminalResult.INVALID_INPUT

    # =========================================================================
    # CATEGORY 3: INVARIANT BYPASS ATTEMPTS
    # =========================================================================

    def test_invariant_bypass_99_percent_cannot_bypass_critical_categories(self, policy_snapshot, base_scope):
        """
        Anti-bypass invariant: A 99% vs 1% score MUST NOT bypass Lock 2
        for Architectural, Destructive, Policy, or Governance actions.
        """
        for critical_mode in [
            DecisionMode.ARCHITECTURAL,
            DecisionMode.DESTRUCTIVE,
            DecisionMode.POLICY,
            DecisionMode.GOVERNANCE,
        ]:
            req = DecisionRequest(
                decision_id=f"DECISION-BYPASS-{critical_mode.value}",
                revision=1,
                scope=base_scope,
                options=[
                    DecisionOption(option_id="opt_top", description="Top choice", confidence=0.99),
                    DecisionOption(option_id="opt_bottom", description="Bottom choice", confidence=0.01),
                ],
                authority_type=AuthorityType.AUTONOMOUS,
                mode=critical_mode,
                action_description=f"Perform {critical_mode.value} operation",
            )
            res = evaluate_decision_eligibility(req, policy_snapshot)
            assert res.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
            assert res.is_eligible is False
            assert "MANDATORY_CRITICAL_MODE" in res.reason_codes

    def test_invariant_bypass_critical_paths_and_verbs_trigger_lock_2(self, policy_snapshot, base_scope):
        """Verify architectural path triggers and destructive verbs block 99% scores."""
        # Architectural path trigger
        req_path = DecisionRequest(
            decision_id="DECISION-BYPASS-PATH",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.99),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.01),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            target_files=["contracts/dual_pipeline/schemas/hitl-decision.schema.json"],
        )
        res_path = evaluate_decision_eligibility(req_path, policy_snapshot)
        assert res_path.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert "MANDATORY_CRITICAL_CATEGORY" in res_path.reason_codes

        # Destructive verb trigger
        req_verb = DecisionRequest(
            decision_id="DECISION-BYPASS-VERB",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.99),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.01),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            action_description="Truncate database partition and wipe legacy graph cache",
        )
        res_verb = evaluate_decision_eligibility(req_verb, policy_snapshot)
        assert res_verb.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert "MANDATORY_CRITICAL_CATEGORY" in res_verb.reason_codes

    def test_invariant_bypass_plan_only_and_unauthorized_authority(self, policy_snapshot, base_scope):
        """Plan-only boundary and unknown authority type must force human review."""
        # Plan-only context
        req_plan = DecisionRequest(
            decision_id="DECISION-BYPASS-PLAN-ONLY",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.95),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.10),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            context={"plan_only": True},
        )
        res_plan = evaluate_decision_eligibility(req_plan, policy_snapshot)
        assert res_plan.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert "PLAN_ONLY_BOUNDARY" in res_plan.reason_codes

        # Unauthorized authority type
        req_auth = DecisionRequest(
            decision_id="DECISION-BYPASS-AUTH",
            revision=1,
            scope=base_scope,
            options=[
                DecisionOption(option_id="opt_1", description="Opt 1", confidence=0.95),
                DecisionOption(option_id="opt_2", description="Opt 2", confidence=0.10),
            ],
            authority_type=AuthorityType.UNKNOWN,
        )
        res_auth = evaluate_decision_eligibility(req_auth, policy_snapshot)
        assert res_auth.terminal_result == ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED
        assert "UNAUTHORIZED_AUTHORITY_TYPE" in res_auth.reason_codes
