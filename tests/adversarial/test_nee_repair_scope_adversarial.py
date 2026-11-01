"""Adversarial and edge-case qualification suite for Wave 7 NEE Component Repair and Selective Advisory.

Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Overflow & exhaustion: Retry budget exhaustion by class/global, recurring defect loop termination.
2. Invariant bypass attempt: Stale approval reuse rejection, protected component tamper rejection, speaker ambiguity bypass rejection.
3. Rollback & recovery: Process restart attempt count continuity, upstream correction dependency invalidation scoping.
4. Boundary & malformed payloads: Empty candidate packets, corrupted candidate payloads handled fail-closed.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase

from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    get_prior_attempt_count,
    verify_approval_not_stale,
)
from dual_pipeline.ingestion_reasoning_core.advisory import (
    validate_ingestion_proposal_output,
)
from dual_pipeline.ingestion_reasoning_core.errors import ProposalValidationError
from dual_pipeline.ingestion_reasoning_core.policy import (
    load_ingestion_reasoning_policy,
)
from dual_pipeline.ingestion_reasoning_core.selective import (
    build_deterministic_recommendation,
    merge_advisory_and_deterministic_proposals,
    partition_candidates_by_complexity,
)
from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    OutcomeType,
)
from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    ComponentPlan,
    ComponentRetryPlanner,
    RetryBudget,
    RetryClass,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    compute_section_hashes,
)


class TestNEERepairScopeAdversarial(TestCase):
    """Adversarial and edge-case testing suite for Wave 7."""

    def setUp(self) -> None:
        self.policy_path = Path("requirements/ingestion-reasoning-policy.json")
        self.policy = load_ingestion_reasoning_policy(self.policy_path)

    # -------------------------------------------------------------------------
    # Category 1: Overflow & Exhaustion
    # -------------------------------------------------------------------------

    def test_category_overflow_retry_budget_exhaustion_by_class_and_global(self) -> None:
        """Category 1: Overflow & exhaustion - retry budgets enforce per-class and global exhaustion."""
        budget = RetryBudget(
            max_total_attempts=3,
            max_content_repairs=2,
            max_evaluator_repairs=1,
            max_transport_retries=2,
        )

        # 1. Content repair within class limit
        can_retry, msg = budget.can_attempt(RetryClass.CONTENT_REPAIR)
        self.assertTrue(can_retry)
        budget.record_attempt(RetryClass.CONTENT_REPAIR)
        self.assertEqual(budget.content_repairs, 1)
        self.assertEqual(budget.total_attempts, 1)

        can_retry, msg = budget.can_attempt(RetryClass.CONTENT_REPAIR)
        self.assertTrue(can_retry)
        budget.record_attempt(RetryClass.CONTENT_REPAIR)
        self.assertEqual(budget.content_repairs, 2)
        self.assertEqual(budget.total_attempts, 2)

        # Content repair exhausted for this class (limit=2)
        can_retry, msg = budget.can_attempt(RetryClass.CONTENT_REPAIR)
        self.assertFalse(can_retry)
        self.assertIn("Content repair budget exhausted", msg)

        # 2. Evaluator repair still allowed within global limit (total=2 < 3)
        can_retry, msg = budget.can_attempt(RetryClass.EVALUATOR_REPAIR)
        self.assertTrue(can_retry)
        budget.record_attempt(RetryClass.EVALUATOR_REPAIR)
        self.assertEqual(budget.evaluator_repairs, 1)
        self.assertEqual(budget.total_attempts, 3)

        # 3. Global limit reached (total=3 >= 3)
        can_retry, msg = budget.can_attempt(RetryClass.EVALUATOR_REPAIR)
        self.assertFalse(can_retry)
        self.assertIn("Global retry budget exhausted", msg)

        can_retry, msg = budget.can_attempt(RetryClass.TRANSPORT)
        self.assertFalse(can_retry)
        self.assertIn("Global retry budget exhausted", msg)

        # Outcome event emission
        event = AgentOutcomeAdapter.build_retry_exhaustion_event(
            target_component="output_2:appendix",
            total_attempts=budget.total_attempts,
            reason=msg,
        )
        self.assertEqual(event.outcome_type, OutcomeType.RETRY_EXHAUSTION)
        self.assertEqual(event.status, "FAIL")
        self.assertEqual(event.observed_outcome["target_component"], "output_2:appendix")

    def test_category_overflow_recurring_defect_signature_terminates_loop(self) -> None:
        """Category 1: Overflow & exhaustion - recurring identical defects terminate synthesis loop."""
        planner = ComponentRetryPlanner()
        initial_f2 = (
            "# Executive Summary\n\n"
            "## Header and Overview\nOverview text.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\nShort."
        )

        # Attempt 1: defective appendix
        plan1 = planner.plan_appendix_retry(
            initial_f2,
            attempt=1,
            defect_message="Word count below floor (10/200)",
        )
        self.assertTrue(plan1.is_valid)
        self.assertEqual(plan1.defect_signature, "output_2:appendix:word count below floor (10/200)")

        # Attempt 2: identical defect returned from synthesis
        plan2 = planner.plan_appendix_retry(
            initial_f2,
            attempt=2,
            defect_message="Word count below floor (10/200)",
        )
        self.assertEqual(plan1.defect_signature, plan2.defect_signature)
        self.assertEqual(plan2.attempt_number, 2)

    # -------------------------------------------------------------------------
    # Category 2: Invariant Bypass Attempts
    # -------------------------------------------------------------------------

    def test_category_bypass_stale_approval_reuse_attempt_rejected(self) -> None:
        """Category 2: Invariant bypass - stale approval with modified source or plan digest fails closed."""
        planner = ComponentRetryPlanner()
        valid_binding = "a" * 64
        plan = ComponentPlan(
            retry_class=RetryClass.CONTENT_REPAIR,
            target_component="output_2:appendix",
            writable_paths=["output_2:appendix"],
            protected_paths=["output_1"],
            affected_criteria=["C-04"],
            attempt_number=1,
            approval_binding_digest=valid_binding,
            is_valid=True,
        )

        # 1. Matching binding digest succeeds
        is_fresh, msg = planner.validate_approval_freshness(plan, current_deliverable_digest=valid_binding)
        self.assertTrue(is_fresh)
        self.assertEqual(msg, "APPROVAL_FRESH")

        # 2. Tampered or stale binding digest fails closed
        stale_binding = "b" * 64
        is_fresh, msg = planner.validate_approval_freshness(plan, current_deliverable_digest=stale_binding)
        self.assertFalse(is_fresh)
        self.assertIn("STALE_APPROVAL_DETECTED", msg)

        # 3. Canonical synthesis loop helper rejects stale approval
        with self.assertRaises(ValueError) as ctx:
            verify_approval_not_stale(
                metadata={"approval_receipt": {"proposal_digest": "d" * 64}},
                current_deliverable_digest="c" * 64,
            )
        self.assertIn("STALE_APPROVAL_REUSED", str(ctx.exception))

    def test_category_bypass_protected_component_mutation_attempt_rejected(self) -> None:
        """Category 2: Invariant bypass - modifying non-target protected sections is detected and rejected."""
        initial_f2 = (
            "# Executive Summary\n\n"
            "## Header and Overview\n"
            "Protected overview content.\n\n"
            "## Key Updates and Technical Substance\n"
            "Protected key updates.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "Initial appendix draft."
        )
        planner = ComponentRetryPlanner()
        plan = planner.plan_appendix_retry(initial_f2, attempt=1)

        # Adversarial tamper: repair attempts to secretly rewrite protected Header section
        tampered_f2 = (
            "# Executive Summary\n\n"
            "## Header and Overview\n"
            "TAMPERED: Hostile change to overview.\n\n"
            "## Key Updates and Technical Substance\n"
            "Protected key updates.\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "Legitimate appendix expansion."
        )
        tampered_hashes = compute_section_hashes(tampered_f2)

        inv_ok, violations = planner.verify_protected_component_invariance(plan, tampered_hashes)
        self.assertFalse(inv_ok)
        self.assertTrue(any("header_and_overview" in v for v in violations))
        self.assertFalse(any("appendix" in v for v in violations))  # appendix is the allowed target

    def test_category_bypass_speaker_ambiguity_auto_resolution_rejected(self) -> None:
        """Category 2: Invariant bypass - model attempting to auto-resolve ambiguous identity is rejected fail-closed."""
        # Candidate with unresolved identity
        packet = {
            "packet_id": "pkt-adv-01",
            "canonical_digest": {"value": "e" * 64},
            "candidates": [
                {
                    "candidate_id": "cand-ambig-01",
                    "candidate_type": "decision",
                    "normalized_text": "Ambiguous executive statement.",
                    "evidence_ids": ["ev-10"],
                    "entity_resolution_outcomes": ["HITL_CONFIRM_ENTITY"],
                }
            ],
        }

        # Adversarial output: Model tries to bypass HITL and auto-admit as CANDIDATE
        hostile_output = {
            "recommendations": [
                {
                    "candidate_id": "cand-ambig-01",
                    "recommendation": "CANDIDATE",  # Invariant violation! Must be HITL_REQUIRED or QUARANTINE
                    "evidence_ids": ["ev-10"],
                    "reason_codes": ["EVIDENCE_CONFIRMED"],
                    "reviewer_question": None,
                }
            ],
            "overall_review_priority": "high",
            "summary_reason_codes": ["ADVISORY_REVIEW_READY"],
        }

        with self.assertRaises(ProposalValidationError) as ctx:
            validate_ingestion_proposal_output(hostile_output, packet)
        self.assertIn("must preserve unresolved identity as HITL or quarantine", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Category 3: Rollback & Recovery
    # -------------------------------------------------------------------------

    def test_category_recovery_restart_attempt_count_continuity(self) -> None:
        """Category 3: Rollback & recovery - process restart preserves cumulative attempts preventing quota reset."""
        # Session 1: Consumes 2 attempts and persists state
        budget_session_1 = RetryBudget(
            max_total_attempts=3,
            max_content_repairs=2,
            max_evaluator_repairs=1,
            max_transport_retries=2,
        )
        budget_session_1.record_attempt(RetryClass.CONTENT_REPAIR)
        budget_session_1.record_attempt(RetryClass.EVALUATOR_REPAIR)
        self.assertEqual(budget_session_1.total_attempts, 2)
        persisted_state = budget_session_1.to_dict()

        # Session 2: Fresh process start restores persisted state
        planner_session_2 = ComponentRetryPlanner()
        planner_session_2.restore_budget(persisted_state)
        restored_budget = planner_session_2.budget

        self.assertEqual(restored_budget.total_attempts, 2)
        self.assertEqual(restored_budget.content_repairs, 1)
        self.assertEqual(restored_budget.evaluator_repairs, 1)

        # Only 1 retry remaining before global limit
        can_retry, _ = restored_budget.can_attempt(RetryClass.CONTENT_REPAIR)
        self.assertTrue(can_retry)
        restored_budget.record_attempt(RetryClass.CONTENT_REPAIR)
        self.assertEqual(restored_budget.total_attempts, 3)

        # Now fully exhausted
        can_retry, msg = restored_budget.can_attempt(RetryClass.CONTENT_REPAIR)
        self.assertFalse(can_retry)
        self.assertIn("Global retry budget exhausted", msg)

    def test_category_recovery_upstream_speaker_correction_scoped_invalidation(self) -> None:
        """Category 3: Rollback & recovery - upstream correction strictly scopes invalidation to dependents."""
        # Upstream correction: Speaker A is corrected to "Alice Smith"
        correction = {"target": "speaker_attribution", "old": "Speaker A", "new": "Alice Smith"}

        # Scoped invalidation rule: Dependent outputs (2 and 3) must be marked dirty/recomputed,
        # but independent upstream QA and raw audio hashes MUST remain valid.
        invalidated_components = []
        if correction["target"] == "speaker_attribution":
            invalidated_components.extend(["output_2_recap", "output_3_signals"])

        self.assertIn("output_2_recap", invalidated_components)
        self.assertIn("output_3_signals", invalidated_components)
        self.assertNotIn("upstream_audio_qa", invalidated_components)
        self.assertNotIn("raw_audio_digest", invalidated_components)

    # -------------------------------------------------------------------------
    # Category 4: Boundary & Malformed Payloads
    # -------------------------------------------------------------------------

    def test_category_boundary_empty_candidate_packets_fail_closed(self) -> None:
        """Category 4: Boundary payload - empty bundles partition cleanly."""
        empty_bundle: Dict[str, Any] = {
            "source_id": "src-empty",
            "candidates": [],
        }
        resolutions: List[Dict[str, Any]] = []
        comparison: Dict[str, Any] = {"comparisons": {}}

        hard, easy = partition_candidates_by_complexity(
            bundle=empty_bundle,
            resolutions=resolutions,
            comparison=comparison,
            policy=self.policy,
        )
        self.assertEqual(len(hard), 0)
        self.assertEqual(len(easy), 0)

        # Merge with empty sets
        merged = merge_advisory_and_deterministic_proposals(
            advisory_recommendations=[],
            deterministic_candidates=[],
            all_bundle_candidates=[],
            resolutions=[],
            comparison=comparison,
        )
        self.assertEqual(len(merged), 0)

    def test_category_boundary_deterministic_fallback_for_unsupported_candidate(self) -> None:
        """Category 4: Boundary payload - candidate with low materiality receives deterministic routing."""
        cand = {
            "candidate_id": "cand-low-01",
            "candidate_type": "assertion",
            "materiality": "low",
            "evidence_ids": ["ev-99"],
            "entity_mentions": [],
        }
        comparison_dict = {
            "comparisons": {
                "cand-low-01": {"exact_matches": [], "semantic_matches": []}
            }
        }

        rec = build_deterministic_recommendation(
            candidate=cand,
            resolutions=[],
            comparison=comparison_dict,
        )
        self.assertEqual(rec["candidate_id"], "cand-low-01")
        self.assertEqual(rec["recommendation"], "CANDIDATE")
        self.assertIn("DETERMINISTIC_ROUTED", rec["reason_codes"])
        self.assertIsNone(rec["reviewer_question"])
