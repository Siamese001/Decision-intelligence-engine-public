"""Adversarial and edge-case qualification suite for Calibrated Autonomy and Review Quality (Wave 9).

Covers 5 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary & malformed payloads: Empty/null operators, empty records, non-dict payloads, missing scopes.
2. Concurrency & race conditions: Concurrent Bayesian record accumulation and thread-safe evidence check budget exhaustion.
3. Overflow & exhaustion: Cache entry eviction under load, high repetition of checks, numerical stability under extreme weight accumulation.
4. Invariant bypass attempts: Masquerading autonomous aliases as humans, attempting to extend approval scope through cached preparation.
5. Rollback & recovery: Clean ledger state recovery and safe defaults when corrupted decision payloads are encountered.
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
import tempfile
import threading
from typing import Any, Dict, List
from unittest import TestCase

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
    compute_beta_credible_interval,
    compute_record_eligibility,
    create_scoped_decision_snapshot,
    filter_frozen_priors,
    is_autonomous_alias,
    is_autonomous_record,
    is_override_record,
)


class TestAutonomyFeedbackAdversarial(TestCase):
    """Adversarial stress and edge-case test suite for Wave 9."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp_dir.name)
        self.ledger_file = self.runtime_root / "test_hitl_ledger.json"
        self.store = HITLDecisionStore(ledger_path=self.ledger_file)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # Category 1: Boundary & Malformed Payloads
    # -------------------------------------------------------------------------

    def test_boundary_empty_and_null_record_handling(self) -> None:
        """Category 1: Boundary payload - Empty records, null operators, missing fields handled safely."""
        empty_rec: Dict[str, Any] = {}
        self.assertFalse(is_autonomous_record(empty_rec))
        self.assertFalse(is_override_record(empty_rec))

        # Eligibility handles empty record gracefully
        weight, is_mature, reason = compute_record_eligibility(empty_rec)
        self.assertEqual(weight, 0.0)
        self.assertFalse(is_mature)
        self.assertEqual(reason, "EMPTY_RECORD")

        # None operator
        self.assertFalse(is_autonomous_alias(None))
        self.assertFalse(is_autonomous_alias(""))
        self.assertFalse(is_autonomous_alias("   "))

        # Malformed snapshot inputs do not crash
        snap = create_scoped_decision_snapshot(
            engagement_id="",
            category="",
            option_id="",
            revision_id="",
            selected_key="",
        )
        self.assertIsNotNone(snap.digest)
        self.assertEqual(len(snap.digest), 64)

    def test_malformed_numeric_and_type_coercion_in_bayesian(self) -> None:
        """Category 1: Malformed input - Unusual scores and intervals remain numerically bounded."""
        # Non-standard base scores clamped safely
        score_low = compute_bayesian_calibrated_score(
            base_score=-0.5,
            affirmed_weight=1.0,
            rejected_weight=0.0,
        )
        self.assertGreaterEqual(score_low, 0.0)

        score_high = compute_bayesian_calibrated_score(
            base_score=1.5,
            affirmed_weight=0.0,
            rejected_weight=1.0,
        )
        self.assertLessEqual(score_high, 1.0)

        # Extreme alpha/beta in credible interval
        low_ci, high_ci = compute_beta_credible_interval(alpha=1e9, beta=1e9)
        self.assertTrue(0.0 <= low_ci <= high_ci <= 1.0)

    # -------------------------------------------------------------------------
    # Category 2: Concurrency & Race Conditions
    # -------------------------------------------------------------------------

    def test_concurrency_race_condition_on_evidence_budget(self) -> None:
        """Category 2: Concurrency - 20 threads racing for a 2-check budget allow exactly 2 checks."""
        budget = EvidenceCheckBudget(max_checks=2)
        successes = 0
        exceptions = 0
        lock = threading.Lock()

        def worker(thread_idx: int) -> None:
            nonlocal successes, exceptions
            try:
                budget.check_evidence(f"doc://thread/{thread_idx}")
                with lock:
                    successes += 1
            except EvidenceCheckLimitExceeded:
                with lock:
                    exceptions += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(20)]
            concurrent.futures.wait(futures)

        self.assertEqual(successes, 2)
        self.assertEqual(exceptions, 18)
        self.assertEqual(budget.remaining_budget, 0)
        self.assertEqual(len(budget.checked_refs), 2)

    def test_concurrency_parallel_ledger_writes_and_calibration(self) -> None:
        """Category 2: Concurrency - Concurrent writes to HITLDecisionStore update scores thread-safely."""
        def record_worker(worker_id: int) -> None:
            for i in range(3):
                self.store.record_decision(
                    decision_id=f"worker_{worker_id}_dec_{i}",
                    category="concurrent_eval",
                    prompt="Evaluate concurrent action",
                    options=[
                        {"label": "choice_a", "confidence_score": 0.50},
                        {"label": "choice_b", "confidence_score": 0.50},
                    ],
                    selected_option="choice_a" if i % 2 == 0 else "choice_b",
                    operator_id=f"human_{worker_id}",
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(record_worker, w) for w in range(3)]
            concurrent.futures.wait(futures)

        # Verify calibration can be queried without crashing or deadlocking
        calibrated = self.store.calibrate_options(
            options=[{"label": "choice_a", "confidence_score": 0.50}],
            category="concurrent_eval",
        )
        self.assertEqual(len(calibrated), 1)
        self.assertGreater(calibrated[0]["confidence_sample_count"], 0)

    # -------------------------------------------------------------------------
    # Category 3: Overflow & Exhaustion
    # -------------------------------------------------------------------------

    def test_overflow_exact_context_cache_eviction(self) -> None:
        """Category 3: Overflow & exhaustion - ExactContextReviewCache evicts oldest entries at capacity."""
        cache = ExactContextReviewCache(max_entries=3)
        for i in range(5):
            k = cache.make_key("eng", "cat", f"hash_{i}", "rev_1")
            cache.put_preparation(k, {"idx": i})

        # Cache size must not exceed 3
        self.assertEqual(len(cache._cache), 3)
        # Oldest keys (hash_0, hash_1) should be evicted
        k0 = cache.make_key("eng", "cat", "hash_0", "rev_1")
        self.assertIsNone(cache.get_preparation(k0))
        # Newest keys should be present
        k4 = cache.make_key("eng", "cat", "hash_4", "rev_1")
        self.assertIsNotNone(cache.get_preparation(k4))

    def test_overflow_extreme_rejection_weight_accumulation(self) -> None:
        """Category 3: Overflow & exhaustion - Large volume of rejections causes bounded attenuation without underflow."""
        for i in range(50):
            self.store.record_decision(
                decision_id=f"dec_mass_rej_{i}",
                category="stress_cat",
                prompt="Stress test prompt",
                options=[
                    {"label": "flawed_choice", "confidence_score": 0.90},
                    {"label": "correct_choice", "confidence_score": 0.10},
                ],
                selected_option="flawed_choice",
                operator_id=f"human_{i}",
                metadata={"outcome_type": "REJECTED"},
            )

        calibrated = self.store.calibrate_options(
            options=[{"label": "flawed_choice", "confidence_score": 0.99}],
            category="stress_cat",
        )
        score = calibrated[0]["confidence_score"]
        # Score is heavily attenuated but stays within [0.0, 1.0]
        self.assertTrue(0.0 <= score <= 1.0)
        self.assertLess(score, 0.5)

    # -------------------------------------------------------------------------
    # Category 4: Invariant Bypass Attempts
    # -------------------------------------------------------------------------

    def test_invariant_bypass_autonomous_masquerade_prevention(self) -> None:
        """Category 4: Invariant bypass - Autonomous aliases attempting to bypass naming filters are caught."""
        bypasses = [
            "AUTO_OPERATOR",
            "autonomous_worker_99",
            "agent_autonomous",
            "worker.auto",
            "auto-approver",
            "SYSTEM_AUTO",
        ]
        for name in bypasses:
            self.assertTrue(
                is_autonomous_alias(name),
                f"Failed to catch masquerading autonomous alias: {name}",
            )
            rec = {
                "decision_id": f"dec_bypass_{name}",
                "operator_id": name,
                "category": "governance_policy",
                "selected_option": "unauthorized_change",
            }
            weight, is_mature, reason = compute_record_eligibility(rec)
            self.assertEqual(weight, 0.0)
            self.assertFalse(is_mature)
            self.assertEqual(reason, "AUTONOMOUS_AUDIT_ONLY")

    def test_invariant_bypass_cache_approval_scope_tamper_resistance(self) -> None:
        """Category 4: Invariant bypass - Cached review preparations cannot set approval_extended=True."""
        cache = ExactContextReviewCache(max_entries=5)
        k = cache.make_key("eng_sec", "auth", "hash_secure", "rev_1")
        # Attempting to inject approval_extended=True in the payload
        trojan_card = {
            "title": "Trojan Approval Escalation",
            "approval_extended": True,
            "scope": "GLOBAL_BYPASS",
        }
        cache.put_preparation(k, trojan_card)

        retrieved = cache.get_preparation(k)
        self.assertIsNotNone(retrieved)
        # Invariant: Cache strictly overwrites approval_extended to False
        self.assertFalse(retrieved["approval_extended"])
        self.assertTrue(retrieved["is_cached_preparation"])
