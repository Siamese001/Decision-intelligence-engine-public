"""
tests/adversarial/test_hitl_resolution_w3_adversarial.py

Adversarial stress and tamper test suite for Wave 3: Persistent, Signed Autonomous Resolutions.
Covers 3 mandatory categories under e2e-and-edge-case-coverage.md:
1. Concurrency and race conditions (parallel idempotency race).
2. Invariant bypass and tampering (digest-as-signature rejection, payload tampering, Merkle break).
3. Rollback & recovery (idempotency conflict rejection, out-of-order export prevention).
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
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
    EligibilityEvaluationResult,
    ResolutionTerminalResult,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.policy import (
    load_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.projection import (
    export_projection,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.signing import (
    generate_autonomous_keypair,
    verify_autonomous_signature,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.storage import (
    AutonomousResolutionStore,
    ConflictResolutionError,
)


class TestHitlResolutionW3Adversarial:
    """Adversarial security and corruption test suite for Wave 3 store."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        db_path = tmp_path / "ledger" / "auto_adv.sqlite"
        return AutonomousResolutionStore(db_path), db_path

    @pytest.fixture
    def test_keypair(self):
        return generate_autonomous_keypair()

    @pytest.fixture
    def policy_snapshot(self):
        return load_policy_snapshot(
            overrides={"ambiguity_threshold": 0.30, "conviction_floor": 0.65}
        )

    def _build_request(self, decision_id: str, score_top: float = 0.95) -> DecisionRequest:
        return DecisionRequest(
            decision_id=decision_id,
            revision=1,
            scope=DecisionScope(
                worktree="/repo/.worktrees/feat_hitl",
                run_id="run_adv_w3",
                wave=3,
                action_target="pipeline_stage_routine_test",
            ),
            options=[
                DecisionOption(option_id="opt_alpha", description="Alpha", confidence=score_top),
                DecisionOption(option_id="opt_beta", description="Beta", confidence=0.40),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Adversarial storage routine operation",
        )

    # =========================================================================
    # CATEGORY 1: CONCURRENCY & RACE CONDITIONS
    # =========================================================================

    def test_concurrency_race_for_identical_idempotency_key(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """
        Multiple workers race to persist the exact same decision simultaneously.
        Must result in exactly one row in SQLite, zero crashes, and all workers get identical record.
        """
        store, _ = temp_store
        priv_key, pub_key = test_keypair
        req = self._build_request("DECISION-RACE-IDEMP")
        eval_res = evaluate_decision_eligibility(req, policy_snapshot)
        worker_count = 10

        def _insert_task():
            return store.record_resolution(req, eval_res, priv_key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_insert_task) for _ in range(worker_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == worker_count
        first_id = results[0].resolution_id
        assert all(r.resolution_id == first_id for r in results)
        assert len(store.list_all()) == 1

        is_valid, defects = store.verify_integrity(pub_key)
        assert is_valid is True
        assert defects == []

    # =========================================================================
    # CATEGORY 2: INVARIANT BYPASS & TAMPERING ATTEMPTS
    # =========================================================================

    def test_invariant_digest_is_never_accepted_as_signature(self, test_keypair):
        """A SHA-256 digest string is never accepted as a valid Ed25519 signature."""
        _, pub_key = test_keypair
        fake_digest_sig = "a" * 64
        payload = {"decision_id": "DECISION-FAKE-SIG", "score": 0.95}

        assert verify_autonomous_signature(pub_key, payload, fake_digest_sig) is False
        assert verify_autonomous_signature(pub_key, payload, "") is False
        assert verify_autonomous_signature(pub_key, payload, "corrupted_sig_base64==") is False

    def test_invariant_tampered_sqlite_row_fails_integrity(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Tampering directly with a persisted row in SQLite is caught during verification."""
        store, db_path = temp_store
        priv_key, pub_key = test_keypair
        req = self._build_request("DECISION-TAMPER-001")
        eval_res = evaluate_decision_eligibility(req, policy_snapshot)
        store.record_resolution(req, eval_res, priv_key)

        # Directly tamper with SQLite DB
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE autonomous_resolutions SET margin = 0.9999 WHERE decision_id = 'DECISION-TAMPER-001'")
        conn.commit()
        conn.close()

        is_valid, defects = store.verify_integrity(pub_key)
        assert is_valid is False
        assert any("differs from recomputed" in d or "Invalid digital signature" in d for d in defects)

    def test_invariant_broken_merkle_chain_detected(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Tampering with previous_resolution_hash in the chain fails integrity check closed."""
        store, db_path = temp_store
        priv_key, pub_key = test_keypair
        for i in range(1, 4):
            req = self._build_request(f"DECISION-MERKLE-{i}")
            eval_res = evaluate_decision_eligibility(req, policy_snapshot)
            store.record_resolution(req, eval_res, priv_key)

        # Corrupt previous_resolution_hash on record 2
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE autonomous_resolutions SET previous_resolution_hash = 'f'*64 WHERE sequence_number = 2")
        conn.commit()
        conn.close()

        is_valid, defects = store.verify_integrity(pub_key)
        assert is_valid is False
        assert any("Merkle chain broken" in d for d in defects)

    # =========================================================================
    # CATEGORY 3: ROLLBACK, RECOVERY & CONFLICT HANDLING
    # =========================================================================

    def test_conflict_rejection_on_conflicting_idempotency_payload(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Submitting a different payload under an existing decision_id/revision/wave fails closed."""
        store, _ = temp_store
        priv_key, _ = test_keypair

        req1 = self._build_request("DECISION-CONFLICT-001", score_top=0.95)
        eval_res1 = evaluate_decision_eligibility(req1, policy_snapshot)
        store.record_resolution(req1, eval_res1, priv_key)

        # Build conflicting request with different option description/confidence under same key
        req2 = self._build_request("DECISION-CONFLICT-001", score_top=0.90)
        eval_res2 = evaluate_decision_eligibility(req2, policy_snapshot)

        # Must raise ConflictResolutionError
        with pytest.raises(ConflictResolutionError, match="Idempotency conflict"):
            store.record_resolution(req2, eval_res2, priv_key)

    def test_out_of_order_projection_export_rejection(
        self, temp_store, test_keypair, policy_snapshot, tmp_path
    ):
        """An exporter cannot overwrite a JSON projection with an older/regressed sequence head."""
        store1, _ = temp_store
        priv_key, _ = test_keypair
        json_path = tmp_path / "projections" / "out_of_order.json"

        # Record 2 decisions in store1 and export
        for i in range(2):
            req = self._build_request(f"DECISION-OOO-{i}")
            eval_res = evaluate_decision_eligibility(req, policy_snapshot)
            store1.record_resolution(req, eval_res, priv_key)
        export_projection(store1, json_path)

        # Store2 has only 1 decision (older head)
        db_path2 = tmp_path / "ledger" / "store2.sqlite"
        store2 = AutonomousResolutionStore(db_path2)
        req_single = self._build_request("DECISION-OOO-SINGLE")
        eval_res_single = evaluate_decision_eligibility(req_single, policy_snapshot)
        store2.record_resolution(req_single, eval_res_single, priv_key)

        # Attempting to export store2 over existing json_path must be rejected
        with pytest.raises(ValueError, match="Out-of-order export rejected"):
            export_projection(store2, json_path)

    def test_ineligible_resolution_persistence_rejection(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Attempting to persist an ineligible evaluation result raises ValueError."""
        store, _ = temp_store
        priv_key, _ = test_keypair
        req = self._build_request("DECISION-INELIGIBLE")

        # Fake ineligible result
        ineligible_eval = EligibilityEvaluationResult(
            terminal_result=ResolutionTerminalResult.HUMAN_REVIEW_REQUIRED,
            is_eligible=False,
            selected_option_id=None,
            selected_option=None,
            margin=0.10,
            entropy=0.95,
            top_confidence=0.50,
            second_confidence=0.40,
            reason_codes=["CONFIDENCE_FLOOR_BREACH"],
            reasons=["Confidence floor breached"],
            policy_snapshot=policy_snapshot,
            evaluated_at_iso="2026-10-06T00:00:00Z",
        )

        with pytest.raises(ValueError, match="Cannot persist ineligible"):
            store.record_resolution(req, ineligible_eval, priv_key)
