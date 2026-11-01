"""
tests/adversarial/test_hitl_upload_publication_adversarial.py

Adversarial and boundary tests for Wave 5: Active publication verification,
stale base generations, changed deliverables, concurrent locking, and replay prevention.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
)
from dual_pipeline.canonical_pipeline.publication_verifier import (
    validate_publication_coverage_receipt,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    _validate_status_and_idempotency,
    validate_mutation_authorization,
)


def _init_test_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            claim_text TEXT,
            currentness_state TEXT,
            is_quarantined INTEGER DEFAULT 0,
            superseded_by_assertion_id TEXT
        );
        """
    )
    conn.commit()
    return conn


class TestHITLUploadPublicationAdversarial:
    """Adversarial stress and boundary tests for Wave 5."""

    def test_boundary_missing_database_file_fails_honestly(self, tmp_path: Path):
        missing_db = tmp_path / "nonexistent.sqlite"
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-b1",
            run_id="run-b1",
            source_digest="0" * 64,
            items=[],
        )
        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=missing_db,
            manifest=manifest,
            proposal_digest="a" * 64,
            activated_generation_id="gen-001",
        )
        assert not ok
        assert "Graph database file not found" in violations[0]
        assert receipt == {}

    def test_boundary_all_excluded_items_passes_with_zero_committed(self, tmp_path: Path):
        db_p = tmp_path / "graph.sqlite"
        conn = _init_test_graph(db_p)
        conn.close()

        item = EvidenceItem(
            item_id="item-excl",
            item_type=ItemType.SPEAKER_ROLE.value,
            is_eligible_graph_fact=True,
            state=EvidenceState.EXCLUDED.value,
            source_origin={"source_type": "TRANSCRIPT_HITL", "source_hash": "a" * 64},
            disposition_reason="Demo sample attendee outside engagement scope",
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-b2",
            run_id="run-b2",
            source_digest="a" * 64,
            items=[item],
        )
        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=db_p,
            manifest=manifest,
            proposal_digest="b" * 64,
            activated_generation_id="gen-002",
        )
        assert ok
        assert receipt["status"] == "VERIFIED"
        assert receipt["coverage_summary"]["committed_count"] == 0
        assert receipt["coverage_summary"]["excluded_count"] == 1

    def test_malformed_receipt_schema_validation(self):
        bad_receipt = {
            "contract_id": "urn:nee:dual-pipeline:publication-coverage-receipt:1.0.0",
            # missing schema_version, receipt_id, run_id, etc.
            "status": "INVALID_STATUS",
        }
        is_val, errs = validate_publication_coverage_receipt(bad_receipt)
        assert not is_val
        assert len(errs) > 0

    def test_invariant_bypass_explicit_rejection_blocks_mutation(self, tmp_path: Path):
        rec = {
            "status": "REJECTED",
            "request_id": "req-rej-1",
        }
        with pytest.raises(MutationAuthorizationError) as exc_info:
            _validate_status_and_idempotency(
                rec=rec,
                active_gen="gen-001",
                out_name="test_output",
                origin="human_operator",
                allow_idempotent_recovery=True,
            )
        assert "AUTHORIZATION_DENIED_EXPLICIT_REJECTION" in str(exc_info.value)

    def test_invariant_bypass_awaiting_approval_blocks_mutation(self, tmp_path: Path):
        rec = {
            "status": "AWAITING_APPROVAL",
            "request_id": "req-pend-1",
        }
        with pytest.raises(MutationAuthorizationError) as exc_info:
            _validate_status_and_idempotency(
                rec=rec,
                active_gen="gen-001",
                out_name="test_output",
                origin="human_operator",
                allow_idempotent_recovery=True,
            )
        assert "AUTHORIZATION_DENIED_PENDING_APPROVAL" in str(exc_info.value)

    def test_invariant_bypass_consumed_receipt_replay_different_generation_rejected(self, tmp_path: Path):
        rec = {
            "status": "ACTIVATED",
            "request_id": "req-act-1",
            "activated_generation_id": "gen-committed-100",
        }
        with pytest.raises(MutationAuthorizationError) as exc_info:
            _validate_status_and_idempotency(
                rec=rec,
                active_gen="gen-active-200",  # Generation moved!
                out_name="test_output",
                origin="human_operator",
                allow_idempotent_recovery=True,
            )
        assert "AUTHORIZATION_DENIED_REPLAY_DETECTED" in str(exc_info.value)

    def test_concurrency_race_file_lock_protects_mutation_authorization(self, tmp_path: Path):
        rt_root = tmp_path / "rt_concurrent"
        rt_root.mkdir()
        approval_store = rt_root / "approval"
        approval_store.mkdir()
        requests_file = approval_store / "host_approval_requests.json"
        req_data = {
            "req-c1": {
                "request_id": "req-c1",
                "meeting_name": "Concurrent Meeting",
                "proposal_digest": "c" * 64,
                "status": "APPROVED",
                "authorized": True,
                "operator_origin": "human_operator",
                "expected_base_generation": "current",
            }
        }
        requests_file.write_text(json.dumps(req_data), encoding="utf-8")

        results = []
        errors = []

        def worker():
            try:
                res = validate_mutation_authorization(
                    output_dir=rt_root,
                    metadata={"meeting_name": "Concurrent Meeting", "approval_request_id": "req-c1"},
                    written={},
                    runtime_root=rt_root,
                    allow_idempotent_recovery=True,
                )
                results.append(res)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly all completed without locking errors
        assert len(errors) == 0
        assert len(results) == 4
        # All workers authorized under serialized lock
        assert all(r["authorized"] is True for r in results)
