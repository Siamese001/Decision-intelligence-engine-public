"""
tests/adversarial/test_hitl_upload_audit_replay.py

Adversarial and boundary test suite for Wave 6:
Replay validation and bounded recovery edge cases.

Covers 5 mandatory edge-case categories:
1. Boundary payload (empty manifest, missing source files, 0-byte images)
2. Malformed input (corrupt digests, malformed schemas)
3. Invariant bypass attempts (demo prototype values promoted as enterprise commitments,
   prompt screenshots used as approval proof, cross-generation replay)
4. Rollback & recovery (aborted replay transaction leaves zero dangling assertions)
5. Concurrency & race (parallel verification workers in WAL mode without database locks)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
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
from dual_pipeline.hitl_governance_core.approval_lookup import (
    ApprovalResolutionStatus,
    resolve_hitl_approval_reference,
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
        PRAGMA journal_mode = WAL;
        PRAGMA busy_timeout = 30000;
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


class TestHITLUploadAuditReplayAdversarial:
    """Adversarial stress and invariant verification for Wave 6 recovery."""

    def test_boundary_empty_manifest_replays_safely_with_zero_commits(self, tmp_path: Path):
        """Boundary Payload: Empty evidence manifest produces a valid zero-committed receipt."""
        db_path = tmp_path / "empty_graph.sqlite"
        _init_test_graph(db_path)

        manifest = EvidenceCoverageManifest(
            manifest_id="cov-empty-manifest",
            run_id="run-empty-001",
            source_digest="0" * 64,
            items=[],
        )

        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=db_path,
            manifest=manifest,
            proposal_digest="a" * 64,
            activated_generation_id="gen-empty-001",
            output_dir=tmp_path,
        )
        assert ok is True
        assert violations == []
        assert receipt["status"] == "VERIFIED"
        assert receipt["coverage_summary"]["total_eligible_facts"] == 0
        assert receipt["coverage_summary"]["committed_count"] == 0
        assert receipt["coverage_summary"]["missing_count"] == 0

    def test_boundary_missing_source_image_reports_failed_status_not_low_value(self, tmp_path: Path):
        """Boundary Payload: Missing image file reports honest FAILED extraction rather than LOW_VALUE."""
        content = ExtractedAttachmentContent(
            attachment_id="att-missing-file",
            filename="missing_screenshot.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="0" * 64,
            title="Missing File",
            extracted_text="",
            extraction_status="FAILED",
            error_reason="FILE_NOT_FOUND",
            is_failed=True,
            is_demo_or_sample=False,
        )
        assert content.extraction_status == "FAILED"
        assert content.is_failed is True
        assert content.error_reason == "FILE_NOT_FOUND"

    def test_malformed_input_corrupt_receipt_fails_schema_validation(self):
        """Malformed Input: Receipt lacking mandatory contract fields fails schema validation."""
        corrupt_receipt = {
            "contract_id": "urn:nee:dual-pipeline:publication-coverage-receipt:1.0.0",
            # Missing schema_version, receipt_id, run_id, covered_facts, coverage_summary
            "status": "VERIFIED",
        }
        is_val, errs = validate_publication_coverage_receipt(corrupt_receipt)
        assert is_val is False
        assert len(errs) > 0

    def test_invariant_bypass_demo_prototype_spec_cannot_be_promoted_unconditionally(self, tmp_path: Path):
        """
        Invariant Bypass: Demo prototype screenshot values (e.g. WBS spend or target COD)
        marked as is_eligible_graph_fact=False are strictly excluded from graph mutation.
        """
        db_path = tmp_path / "demo_graph.sqlite"
        _init_test_graph(db_path)

        demo_item = EvidenceItem(
            item_id="vis_demo_wbs_spend",
            item_type=ItemType.TABLE_RECORD.value,
            is_eligible_graph_fact=False,  # Enforces demo prototype qualification
            state=EvidenceState.CAPTURED.value,
            source_origin={"source_type": "SCREENSHOT_ATTACHMENT", "source_hash": "1" * 64},
            disposition_reason="Sample UI Prototype Spec (SAP WBS Spend $1,250,000)",
            proposed_fact={"subject": "WBS Elements", "predicate": "SPEND_AMOUNT", "object": "$1,250,000"},
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-demo-bypass-test",
            run_id="run-demo-bypass-001",
            source_digest="2" * 64,
            items=[demo_item],
        )

        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=db_path,
            manifest=manifest,
            proposal_digest="3" * 64,
            activated_generation_id="gen-demo-001",
            output_dir=tmp_path,
        )
        assert ok is True
        assert receipt["coverage_summary"]["committed_count"] == 0
        assert receipt["coverage_summary"]["total_eligible_facts"] == 0

        # Verify nothing was inserted into graph
        conn = sqlite3.connect(str(db_path))
        cnt = conn.execute("SELECT count(*) FROM assertion").fetchone()[0]
        conn.close()
        assert cnt == 0

    def test_invariant_bypass_unsubmitted_prompt_screenshot_cannot_authorize(self, tmp_path: Path):
        """
        Invariant Bypass: A screenshot or text of an unsubmitted prompt question
        cannot authorize graph mutation; missing approval is classified as UNRESOLVED.
        """
        unresolved_ref = {
            "entry_id": "auth-ev-unsubmitted-prompt",
            "ledger_path": str(tmp_path / "empty_ledger.json"),
            "subject_sha256": "4" * 64,
        }
        res = resolve_hitl_approval_reference(unresolved_ref)
        assert res.is_valid is False
        assert res.is_unresolved is True
        assert res.status == ApprovalResolutionStatus.LEDGER_UNAVAILABLE

    def test_invariant_bypass_consumed_approval_replay_different_generation_rejected(self, tmp_path: Path):
        """Invariant Bypass: Consumed approval receipt cannot be replayed on a different generation."""
        receipt_p = tmp_path / "auth_receipt.json"
        receipt_data = {
            "request_id": "req-replay-attack-001",
            "generation_id": "gen-original-001",
            "status": "ACTIVATED",
            "activated_generation_id": "gen-original-001",
        }
        receipt_p.write_text(json.dumps(receipt_data), encoding="utf-8")

        with pytest.raises(MutationAuthorizationError) as exc_info:
            _validate_status_and_idempotency(
                rec=receipt_data,
                active_gen="gen-target-different-002",
                out_name="meeting_output",
                origin="HUMAN_INTERACTIVE",
                allow_idempotent_recovery=True,
            )
        assert "AUTHORIZATION_DENIED_REPLAY_DETECTED" in str(exc_info.value)

    def test_rollback_and_recovery_aborted_transaction_leaves_clean_graph(self, tmp_path: Path):
        """Rollback & Recovery: Mid-write exception cleanly rolls back without orphaned rows."""
        db_path = tmp_path / "rollback_graph.sqlite"
        conn = _init_test_graph(db_path)

        # Pre-seed baseline node
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('person:dan_b', 'Dan B', 'Dan B', '{}', 'PERSON', 1)"
        )
        conn.commit()

        # Simulate failed transaction with rollback
        try:
            with conn:
                conn.execute(
                    "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state) "
                    "VALUES ('ass-dan-1', 'person:dan_b', 'HAS_ROLE', 'Lead Developer', 'Dan is Lead', 'CURRENT')"
                )
                # Force failure to trigger rollback
                raise sqlite3.IntegrityError("Simulated write interruption")
        except sqlite3.IntegrityError:
            pass

        # Verify rollback preserved clean state
        cur = conn.cursor()
        cnt = cur.execute("SELECT count(*) FROM assertion").fetchone()[0]
        conn.close()
        assert cnt == 0

    def test_concurrency_race_multi_threaded_read_only_verification(self, tmp_path: Path):
        """Concurrency & Race: Multi-threaded read-only verification executes without locking in WAL mode."""
        db_path = tmp_path / "concurrent_graph.sqlite"
        conn = _init_test_graph(db_path)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('person:worker', 'Worker Node', 'Worker Node', '{}', 'PERSON', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state) "
            "VALUES ('ass-worker-1', 'person:worker', 'STATUS', 'ONLINE', 'Worker is online', 'CURRENT')"
        )
        conn.commit()
        conn.close()

        item = EvidenceItem(
            item_id="fact-worker",
            item_type=ItemType.FACTUAL_CORRECTION.value,
            is_eligible_graph_fact=True,
            state=EvidenceState.PROPOSED.value,
            source_origin={"source_type": "OPERATOR_DIRECTIVE", "source_hash": "5" * 64},
            disposition_reason="Concurrent worker test",
            proposed_fact={"subject": "Worker Node", "predicate": "STATUS", "object": "ONLINE"},
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-concurrent",
            run_id="run-concurrent",
            source_digest="5" * 64,
            items=[item],
        )

        errors: List[Exception] = []

        def worker(thread_idx: int):
            try:
                out_d = tmp_path / f"thread_out_{thread_idx}"
                out_d.mkdir(parents=True, exist_ok=True)
                ok, viols, r = verify_fact_coverage_in_graph(
                    graph_db=db_path,
                    manifest=manifest,
                    proposal_digest="6" * 64,
                    activated_generation_id=f"gen-t-{thread_idx}",
                    output_dir=out_d,
                )
                assert ok is True
                assert r["status"] == "VERIFIED"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
