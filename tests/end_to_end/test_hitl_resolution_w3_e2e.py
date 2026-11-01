"""
tests/end_to_end/test_hitl_resolution_w3_e2e.py

End-to-end qualification suite for Wave 3: Persistent, Signed Autonomous Resolutions.
Verifies append-only SQLite storage, Ed25519 signing, Merkle chaining,
idempotent replay, projection export, and 16-worker concurrency.
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
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
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.policy import (
    load_policy_snapshot,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.projection import (
    export_projection,
    verify_projection_integrity,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.signing import (
    generate_autonomous_keypair,
)
from dual_pipeline.hitl_governance_core.autonomous_resolution.storage import (
    AutonomousResolutionStore,
)


class TestHitlResolutionW3E2E:
    """E2E verification of signed autonomous resolution storage and idempotency."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        db_path = tmp_path / "ledger" / "autonomous_resolutions.sqlite"
        return AutonomousResolutionStore(db_path), db_path

    @pytest.fixture
    def test_keypair(self):
        return generate_autonomous_keypair()

    @pytest.fixture
    def policy_snapshot(self):
        return load_policy_snapshot(
            overrides={
                "ambiguity_threshold": 0.30,
                "conviction_floor": 0.65,
                "max_entropy": 0.75,
                "divergence_threshold": 0.20,
                "policy_source": "test_e2e_w3",
            }
        )

    def _build_request(self, decision_id: str, run_id: str = "run_w3_e2e") -> DecisionRequest:
        return DecisionRequest(
            decision_id=decision_id,
            revision=1,
            scope=DecisionScope(
                worktree="/repo/.worktrees/feat_hitl",
                run_id=run_id,
                wave=3,
                action_target="pipeline_stage_routine_storage",
            ),
            options=[
                DecisionOption(option_id="opt_alpha", description="Alpha", confidence=0.95),
                DecisionOption(option_id="opt_beta", description="Beta", confidence=0.50),
            ],
            authority_type=AuthorityType.AUTONOMOUS,
            mode=DecisionMode.ROUTINE,
            category="routine",
            action_description="Commit routine decision resolution to storage",
        )

    def test_e2e_record_resolution_and_authoritative_readback(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Verify recording a signed resolution, Merkle head init, and readback."""
        store, _ = temp_store
        priv_key, pub_key = test_keypair
        req = self._build_request("DECISION-W3-001")
        eval_res = evaluate_decision_eligibility(req, policy_snapshot)

        rec = store.record_resolution(req, eval_res, priv_key, signer_id="signer_test_01")

        assert rec.sequence_number == 1
        assert rec.previous_resolution_hash == "0" * 64
        assert rec.decision_id == "DECISION-W3-001"
        assert rec.selected_option_id == "opt_alpha"

        # Readback by resolution_id
        read_id = store.get_by_resolution_id(rec.resolution_id)
        assert read_id is not None
        assert read_id.resolution_digest == rec.resolution_digest

        # Readback by idempotency key
        read_idemp = store.get_by_idempotency_key(rec.idempotency_key)
        assert read_idemp is not None
        assert read_idemp.sequence_number == 1

        # Integrity verification
        is_valid, defects = store.verify_integrity(pub_key)
        assert is_valid is True
        assert defects == []

    def test_e2e_monotonic_sequence_progression_and_merkle_chaining(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Append multiple decisions; verify strictly monotonic sequence and Merkle linking."""
        store, _ = temp_store
        priv_key, pub_key = test_keypair
        records = []

        for i in range(1, 6):
            req = self._build_request(f"DECISION-CHAIN-{i:03d}")
            eval_res = evaluate_decision_eligibility(req, policy_snapshot)
            rec = store.record_resolution(req, eval_res, priv_key)
            records.append(rec)
            assert rec.sequence_number == i

        # Verify Merkle linkage
        for i in range(1, len(records)):
            assert records[i].previous_resolution_hash == records[i - 1].resolution_digest

        is_valid, defects = store.verify_integrity(pub_key)
        assert is_valid is True
        assert defects == []

    def test_e2e_semantic_idempotent_replay(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Resubmitting identical request returns existing record without duplicate insertion."""
        store, _ = temp_store
        priv_key, pub_key = test_keypair
        req = self._build_request("DECISION-IDEMP-001")
        eval_res = evaluate_decision_eligibility(req, policy_snapshot)

        rec1 = store.record_resolution(req, eval_res, priv_key)
        rec2 = store.record_resolution(req, eval_res, priv_key)

        assert rec1.resolution_id == rec2.resolution_id
        assert rec1.sequence_number == rec2.sequence_number == 1
        assert len(store.list_all()) == 1

    def test_e2e_projection_export_and_readback_integrity(
        self, temp_store, test_keypair, policy_snapshot, tmp_path
    ):
        """Export SQLite records to JSON projection and verify cryptographic integrity."""
        store, _ = temp_store
        priv_key, pub_key = test_keypair
        json_path = tmp_path / "projections" / "autonomous_ledger.json"

        for i in range(3):
            req = self._build_request(f"DECISION-PROJ-{i:03d}")
            eval_res = evaluate_decision_eligibility(req, policy_snapshot)
            store.record_resolution(req, eval_res, priv_key)

        proj = export_projection(store, json_path)
        assert proj["head_sequence_number"] == 3
        assert proj["record_count"] == 3
        assert json_path.is_file()

        is_valid, defects = verify_projection_integrity(json_path, pub_key)
        assert is_valid is True
        assert defects == []

    def test_e2e_persistence_survives_store_reopen(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """Verify data persistence across separate store instances."""
        store, db_path = temp_store
        priv_key, pub_key = test_keypair
        req = self._build_request("DECISION-REOPEN-001")
        eval_res = evaluate_decision_eligibility(req, policy_snapshot)
        store.record_resolution(req, eval_res, priv_key)

        # Open fresh store on same db
        new_store = AutonomousResolutionStore(db_path)
        assert len(new_store.list_all()) == 1
        rec = new_store.list_all()[0]
        assert rec.decision_id == "DECISION-REOPEN-001"
        is_valid, defects = new_store.verify_integrity(pub_key)
        assert is_valid is True
        assert defects == []

    def test_e2e_16_worker_concurrency_stress(
        self, temp_store, test_keypair, policy_snapshot
    ):
        """16 concurrent workers writing unique resolutions into SQLite store under WAL mode."""
        store, _ = temp_store
        priv_key, pub_key = test_keypair
        worker_count = 16

        def _worker_task(idx: int):
            req = self._build_request(f"DECISION-CONCUR-{idx:02d}", run_id=f"run_{idx}")
            eval_res = evaluate_decision_eligibility(req, policy_snapshot)
            return store.record_resolution(req, eval_res, priv_key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_worker_task, i) for i in range(worker_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == worker_count
        all_records = store.list_all()
        assert len(all_records) == worker_count

        # Check sequence progression 1..16 without holes or collisions
        sequences = sorted([r.sequence_number for r in all_records])
        assert sequences == list(range(1, worker_count + 1))

        # Check full chain integrity
        is_valid, defects = store.verify_integrity(pub_key)
        assert is_valid is True
        assert defects == []
