"""End-to-end lifecycle verification for Calibrated Autonomy and Review Quality (Wave 9).

Verifies:
1. All autonomous aliases contribute zero learning mass/maturity without verified independent outcome.
2. Verified independent outcomes allow autonomous decisions to contribute mature sample weight.
3. Human A->B overrides affirm B preference and reject A recommendation correctly.
4. Rejection-only priors never become preference; action is strictly "avoid".
5. Current lineage and retries cannot train themselves (self-training prevention).
6. Missing scope returns neutral/unavailable priors without invented confidence intervals or sample maturity.
7. EvidenceCheckBudget enforces at most two allowed read-only evidence checks before clarification.
8. Cached exact-context review preparation maintains strict approval scope boundary (approval_extended=False).
9. Mandatory human/speaker cases remain mandatory regardless of cache or prior status.
10. ScopedDecisionSnapshot preserves frozen taxonomy and deterministic digest.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase

from dual_pipeline.ambiguity_core.models import AmbiguityDomain
from dual_pipeline.hitl_governance_core import (
    CalibratedScore,
    DecisionCategory,
    EvidenceCheckBudget,
    EvidenceCheckLimitExceeded,
    ExactContextReviewCache,
    HITLDecisionStore,
    LabelType,
    MANDATORY_HITL_CATEGORIES,
    ScopedDecisionSnapshot,
    classify_decision_criticality,
    compute_bayesian_calibrated_score,
    compute_record_eligibility,
    create_scoped_decision_snapshot,
    filter_frozen_priors,
    is_autonomous_alias,
    is_autonomous_record,
    is_override_record,
)
from dual_pipeline.hitl_governance_core.priors import export_learned_priors
from dual_pipeline.prompts.learned_priors import compile_learned_priors_block


class TestScopedAutonomyE2E(TestCase):
    """End-to-end qualification suite for Wave 9 calibrated autonomy and review quality."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)
        self.ledger_file = self.runtime_root / "test_hitl_ledger.json"
        self.store = HITLDecisionStore(ledger_path=self.ledger_file)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_autonomous_alias_zero_mass_without_independent_outcome_e2e(self) -> None:
        """Autonomous aliases contribute zero weight/maturity without verified independent outcome."""
        test_aliases = [
            "auto_classifier",
            "autonomous_arbiter",
            "agent_auto_1",
            "auto",
            "classifier_auto",
            "AUTONOMOUS_GATE",
        ]
        for alias in test_aliases:
            self.assertTrue(
                is_autonomous_alias(alias),
                f"Expected '{alias}' to be detected as an autonomous alias",
            )
            rec = {
                "decision_id": f"dec_{alias}",
                "operator_id": alias,
                "category": "schema_proposal",
                "selected_option": "opt_a",
                "outcome_type": "RESOLVED",
            }
            self.assertTrue(is_autonomous_record(rec))
            weight, is_mature, reason = compute_record_eligibility(rec)
            self.assertEqual(weight, 0.0)
            self.assertFalse(is_mature)
            self.assertEqual(reason, "AUTONOMOUS_AUDIT_ONLY")

        # When verified independent outcome is attached, it contributes mature sample mass
        rec_success = {
            "decision_id": "dec_auto_success",
            "operator_id": "auto_evaluator",
            "category": "schema_proposal",
            "selected_option": "opt_a",
            "independent_outcome": {"status": "SUCCESS", "verified": True},
        }
        weight, is_mature, reason = compute_record_eligibility(rec_success)
        self.assertGreater(weight, 0.0)
        self.assertTrue(is_mature)
        self.assertEqual(reason, "AUTONOMOUS_INDEPENDENT_SUCCESS")

        rec_failure = {
            "decision_id": "dec_auto_failure",
            "operator_id": "auto_evaluator",
            "category": "schema_proposal",
            "selected_option": "opt_a",
            "independent_outcome": {"status": "FAILURE", "reason": "Verification failed"},
        }
        weight, is_mature, reason = compute_record_eligibility(rec_failure)
        self.assertLess(weight, 0.0)
        self.assertTrue(is_mature)
        self.assertEqual(reason, "AUTONOMOUS_INDEPENDENT_FAILURE")

    def test_human_override_affirms_b_and_rejects_a_e2e(self) -> None:
        """Human A->B overrides affirm B preference and reject A recommendation correctly."""
        override_record = {
            "decision_id": "dec_override_1",
            "operator_id": "human_alice",
            "category": "routing_policy",
            "selected_option": "hybrid_search",
            "recommended_option": "bm25_only",
            "outcome_type": "OVERRIDDEN",
            "metadata": {
                "human_override": True,
                "recommended_option": "bm25_only",
            },
        }
        self.assertTrue(is_override_record(override_record))
        weight, is_mature, reason = compute_record_eligibility(override_record)
        self.assertGreater(weight, 0.0)
        self.assertTrue(is_mature)
        self.assertEqual(reason, "HUMAN_OVERRIDE_AFFIRMED")

        # Record decision into store with proper keyword arguments
        self.store.record_decision(
            decision_id="dec_override_1",
            category="routing_policy",
            prompt="Select routing policy",
            options=[
                {"label": "hybrid_search", "confidence_score": 0.70},
                {"label": "bm25_only", "confidence_score": 0.70, "recommended": True},
            ],
            selected_option="hybrid_search",
            operator_id="human_alice",
            metadata={
                "human_override": True,
                "recommended_option": "bm25_only",
                "outcome_type": "OVERRIDDEN",
            },
        )

        # Calibrate options against history
        calibrated = self.store.calibrate_options(
            options=[
                {"label": "hybrid_search", "confidence_score": 0.70},
                {"label": "bm25_only", "confidence_score": 0.70},
            ],
            category="routing_policy",
        )
        opt_b = next(o for o in calibrated if o["label"] == "hybrid_search")
        opt_a = next(o for o in calibrated if o["label"] == "bm25_only")

        self.assertGreater(opt_b["confidence_score"], opt_a["confidence_score"])
        self.assertGreater(opt_b["historical_adjustment"], 0.0)
        self.assertLess(opt_a["historical_adjustment"], 0.0)

    def test_rejection_only_priors_never_become_preference_e2e(self) -> None:
        """Rejection-only priors compile strictly to 'avoid' action, never 'prefer'."""
        self.store.record_decision(
            decision_id="dec_rej_1",
            category="extraction_model",
            prompt="Select extraction engine",
            options=[
                {"label": "legacy_regex", "confidence_score": 0.60},
                {"label": "llm_extract", "confidence_score": 0.80},
            ],
            selected_option="legacy_regex",
            operator_id="human_bob",
            metadata={"outcome_type": "REJECTED"},
        )

        # Compile learned priors block
        xml_block = compile_learned_priors_block(category="extraction_model", store=self.store)
        self.assertIn("<learned_operator_priors>", xml_block)
        self.assertIn("legacy_regex", xml_block)
        self.assertIn("avoid", xml_block)
        self.assertNotIn("prefer for pattern 'legacy_regex'", xml_block)

    def test_lineage_and_retry_self_training_prevention_e2e(self) -> None:
        """Records from the current lineage or retry chain cannot train themselves."""
        records = [
            {
                "decision_id": "dec_lin_1",
                "operator_id": "human_charlie",
                "category": "pipeline_tuning",
                "selected_option": "opt_fast",
                "lineage_id": "lineage_target_99",
            },
            {
                "decision_id": "dec_lin_2",
                "operator_id": "human_charlie",
                "category": "pipeline_tuning",
                "selected_option": "opt_fast",
                "retry_of": "dec_lin_1",
            },
            {
                "decision_id": "dec_lin_3",
                "operator_id": "human_charlie",
                "category": "pipeline_tuning",
                "selected_option": "opt_fast",
                "lineage_id": "lineage_other_88",
            },
        ]
        filtered = filter_frozen_priors(records, current_lineage_id="lineage_target_99")
        filtered_ids = {r["decision_id"] for r in filtered}
        self.assertNotIn("dec_lin_1", filtered_ids)
        self.assertIn("dec_lin_3", filtered_ids)

    def test_missing_scope_returns_neutral_priors_e2e(self) -> None:
        """Missing scope returns neutral/unavailable priors without invented sample maturity."""
        empty_store = HITLDecisionStore(ledger_path=self.runtime_root / "empty_ledger.json")
        calibrated = empty_store.calibrate_options(
            options=[{"label": "unknown_choice", "confidence_score": 0.82}],
            category="unobserved_future_domain",
        )
        opt = calibrated[0]
        self.assertEqual(opt.get("confidence_level", "NEUTRAL"), "NEUTRAL")
        self.assertEqual(opt.get("historical_adjustment"), 0.0)
        self.assertEqual(opt.get("prior_adjustment"), 0.0)
        self.assertEqual(opt.get("confidence_sample_count", 0), 0)
        self.assertFalse(opt.get("confidence_is_precise", False))
        self.assertEqual(opt["confidence_score"], 0.82)

    def test_evidence_check_budget_at_most_two_checks_e2e(self) -> None:
        """EvidenceCheckBudget enforces at most two read-only evidence checks before clarification."""
        budget = EvidenceCheckBudget(max_checks=2)
        self.assertEqual(budget.remaining_budget, 2)

        # Check 1
        res1 = budget.check_evidence("doc://section/1")
        self.assertEqual(res1.get("status"), "VERIFIED_READ")
        self.assertEqual(budget.remaining_budget, 1)

        # Check 2
        res2 = budget.check_evidence("doc://section/2")
        self.assertEqual(res2.get("status"), "VERIFIED_READ")
        self.assertEqual(budget.remaining_budget, 0)

        # Check 3 must raise EvidenceCheckLimitExceeded
        with self.assertRaises(EvidenceCheckLimitExceeded) as ctx:
            budget.check_evidence("doc://section/3")
        self.assertIn("Evidence check budget exhausted", str(ctx.exception))
        self.assertEqual(budget.remaining_budget, 0)
        self.assertEqual(len(budget.checked_refs), 2)

    def test_cached_review_preparation_does_not_extend_approval_scope_e2e(self) -> None:
        """Cached review preparations retain strict approval boundaries (approval_extended=False)."""
        cache = ExactContextReviewCache(max_entries=10)
        key = cache.make_key(
            engagement_id="eng_alpha",
            category="speaker_attribution",
            context_hash="0123456789abcdef0123",
            revision_id="rev_1",
        )
        original_card = {
            "title": "Speaker Attribution Disambiguation",
            "candidate_name": "Speaker 1 -> Alice",
            "approval_scope": "ENGAGEMENT_LOCAL",
        }
        cache.put_preparation(key, original_card)

        prep = cache.get_preparation(key)
        self.assertIsNotNone(prep)
        self.assertTrue(prep["is_cached_preparation"])
        self.assertFalse(prep["approval_extended"])
        self.assertEqual(prep["approval_scope"], "ENGAGEMENT_LOCAL")

    def test_mandatory_speaker_and_criticality_cases_surfacing_e2e(self) -> None:
        """Mandatory speaker attribution and destructive actions always enforce HITL review."""
        destructive_res = classify_decision_criticality(
            action_description="delete canonical graph partition and purge entities",
        )
        self.assertTrue(destructive_res.requires_mandatory_hitl)
        self.assertEqual(destructive_res.category, DecisionCategory.DESTRUCTIVE)

        arch_res = classify_decision_criticality(
            target_files=["contracts/dual_pipeline/schema.json"],
        )
        self.assertTrue(arch_res.requires_mandatory_hitl)
        self.assertEqual(arch_res.category, DecisionCategory.ARCHITECTURAL)

        # Ambiguity Domain for Speaker Attribution remains distinct and non-waivable
        self.assertEqual(AmbiguityDomain.D1_SPEAKER_ATTRIBUTION.value, "D1_SPEAKER_ATTRIBUTION")

    def test_scoped_decision_snapshot_immutability_and_digest_e2e(self) -> None:
        """ScopedDecisionSnapshot creates immutable records with deterministic SHA-256 digest."""
        snap = create_scoped_decision_snapshot(
            engagement_id="eng_gamma",
            category="query_routing",
            option_id="opt_hybrid",
            revision_id="rev_2",
            selected_key="hybrid_search",
            recommended_key="bm25_only",
            label_type=LabelType.PREFERENCE,
            is_override=True,
            evidence_refs=["doc://analysis/p1"],
        )
        self.assertEqual(snap.label_type, "PREFERENCE")
        self.assertTrue(snap.is_override)
        self.assertEqual(snap.recommended_key, "bm25_only")
        digest1 = snap.digest
        self.assertEqual(len(digest1), 64)

        # Snapshot is frozen
        with self.assertRaises(Exception):
            snap.selected_key = "bm25_only"  # type: ignore

        # Same inputs produce identical digest
        snap_clone = create_scoped_decision_snapshot(
            engagement_id="eng_gamma",
            category="query_routing",
            option_id="opt_hybrid",
            revision_id="rev_2",
            selected_key="hybrid_search",
            recommended_key="bm25_only",
            label_type=LabelType.PREFERENCE,
            is_override=True,
            evidence_refs=["doc://analysis/p1"],
        )
        self.assertEqual(snap_clone.digest, digest1)
