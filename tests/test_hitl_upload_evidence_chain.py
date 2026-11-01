"""
tests/test_hitl_upload_evidence_chain.py

Unit tests for Wave 1:
- Evidence coverage schema validation and disposition state transitions
- Distinguishing operational controls from eligible graph facts
- HITL approval reference resolution (unresolved reference vs explicit rejection)
- Immutable artifact snapshotting and receipt evidence integrity
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
    SourceOriginType,
    validate_evidence_coverage_payload,
)
from dual_pipeline.canonical_pipeline.evidence_capture import (
    snapshot_run_artifacts,
    verify_run_evidence_integrity,
)
from dual_pipeline.hitl_governance_core.approval_lookup import (
    ApprovalResolutionResult,
    ApprovalResolutionStatus,
    resolve_hitl_approval_reference,
)
from dual_pipeline.hitl_governance_core.crypto import (
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)


def test_evidence_coverage_manifest_schema_validation():
    """Verify evidence coverage manifest validates cleanly against draft 2020-12 schema."""
    manifest = EvidenceCoverageManifest(
        manifest_id="cov-20261008-01",
        run_id="run-test-12345",
        source_digest="a" * 64,
        meeting_slug="jarvis-kickoff",
        items=[
            EvidenceItem(
                item_id="item-001",
                item_type=ItemType.SPEAKER_ROLE.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.CAPTURED.value,
                source_origin={
                    "source_type": SourceOriginType.TRANSCRIPT_HITL.value,
                    "source_hash": "a" * 64,
                    "locator": "turn-14",
                },
                disposition_reason="Captured from operator turn correction",
            ),
            EvidenceItem(
                item_id="item-002",
                item_type=ItemType.OPERATIONAL_CONTROL.value,
                is_eligible_graph_fact=False,
                state=EvidenceState.CAPTURED.value,
                source_origin={
                    "source_type": SourceOriginType.OPERATOR_DIRECTIVE.value,
                    "source_hash": "a" * 64,
                },
                disposition_reason="Operator selected retry strategy; operational control only",
            ),
        ],
    )
    is_valid, errors = manifest.validate()
    assert is_valid, f"Manifest schema validation failed: {errors}"
    summary = manifest.compute_summary()
    assert summary["total_items"] == 2
    assert summary["eligible_facts_count"] == 1
    assert summary["operational_controls_count"] == 1
    assert summary["pending_count"] == 2


def test_evidence_coverage_state_transitions():
    """Verify item-level state transitions and audit reason logging."""
    item = EvidenceItem(
        item_id="item-role-dan",
        item_type=ItemType.SPEAKER_ROLE.value,
        is_eligible_graph_fact=True,
        state=EvidenceState.CAPTURED.value,
        source_origin={
            "source_type": SourceOriginType.TRANSCRIPT_HITL.value,
            "source_hash": "b" * 64,
        },
        disposition_reason="Initial capture",
    )
    assert item.state == "CAPTURED"
    item.transition_to(EvidenceState.PROPOSED, "Added to Output 03 candidates")
    assert item.state == "PROPOSED"
    assert item.updated_at is not None

    item.transition_to(EvidenceState.VERIFIED, "Confirmed via independent readback")
    assert item.state == "VERIFIED"


def test_resolve_hitl_approval_reference_valid():
    """Verify resolving an authentic Ed25519 signed approval entry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
        priv, pub = generate_hitl_keypair()
        subject_sha256 = "c" * 64
        entry = create_signed_hitl_approval_entry(
            subject_sha256=subject_sha256,
            case_ids=["case-01"],
            decision="AFFIRMED",
            operator_id="operator_dan",
            private_key=priv,
            public_key=pub,
        )
        entry["entry_id"] = "auth-ev-test-valid-01"
        ledger_payload = {"decisions": [entry]}
        ledger_path.write_text(json.dumps(ledger_payload), encoding="utf-8")

        res = resolve_hitl_approval_reference(
            ref={"entry_id": "auth-ev-test-valid-01", "ledger_path": str(ledger_path)},
            expected_subject_sha256=subject_sha256,
        )
        assert res.is_valid is True
        assert res.is_rejected is False
        assert res.is_unresolved is False
        assert res.status == ApprovalResolutionStatus.RESOLVED_VALID
        assert res.entry_id == "auth-ev-test-valid-01"
        assert res.operator_id == "operator_dan"


def test_resolve_hitl_approval_reference_unresolved_vs_rejected():
    """Verify distinguishing unresolved historical references from explicit operator rejections."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
        priv, pub = generate_hitl_keypair()
        entry_declined = create_signed_hitl_approval_entry(
            subject_sha256="d" * 64,
            case_ids=["case-01"],
            decision="DECLINED",
            operator_id="operator_dan",
            private_key=priv,
            public_key=pub,
        )
        entry_declined["entry_id"] = "auth-ev-declined-01"
        ledger_payload = {"decisions": [entry_declined]}
        ledger_path.write_text(json.dumps(ledger_payload), encoding="utf-8")

        # 1. Unresolved reference (entry not in ledger)
        res_unresolved = resolve_hitl_approval_reference(
            ref="auth-ev-nonexistent-99",
            default_ledger_path=ledger_path,
        )
        assert res_unresolved.is_valid is False
        assert res_unresolved.is_rejected is False
        assert res_unresolved.is_unresolved is True
        assert res_unresolved.status == ApprovalResolutionStatus.UNRESOLVED_REFERENCE

        # 2. Explicitly declined entry
        res_declined = resolve_hitl_approval_reference(
            ref="auth-ev-declined-01",
            default_ledger_path=ledger_path,
        )
        assert res_declined.is_valid is False
        assert res_declined.is_rejected is True
        assert res_declined.is_unresolved is False
        assert res_declined.status == ApprovalResolutionStatus.REJECTED_OPERATOR_DECLINED


def test_resolve_hitl_approval_reference_digest_and_subject_mismatch():
    """Verify digest and subject tampering are detected and fail closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256="e" * 64,
            case_ids=["case-01"],
            decision="AFFIRMED",
            operator_id="operator_dan",
            private_key=priv,
            public_key=pub,
        )
        entry["entry_id"] = "auth-ev-tamper-01"
        ledger_path.write_text(json.dumps({"decisions": [entry]}), encoding="utf-8")

        # Subject mismatch
        res_sub = resolve_hitl_approval_reference(
            ref="auth-ev-tamper-01",
            expected_subject_sha256="f" * 64,
            default_ledger_path=ledger_path,
        )
        assert res_sub.status == ApprovalResolutionStatus.SUBJECT_MISMATCH
        assert res_sub.is_valid is False

        # Digest mismatch
        res_dig = resolve_hitl_approval_reference(
            ref="auth-ev-tamper-01",
            expected_digest="0" * 64,
            default_ledger_path=ledger_path,
        )
        assert res_dig.status == ApprovalResolutionStatus.DIGEST_MISMATCH
        assert res_dig.is_valid is False


def test_snapshot_run_artifacts_and_receipt_embedding():
    """Verify immutable artifact snapshotting and integrity verification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        staging = root / "staging"
        staging.mkdir()
        evidence_dir = root / "executions" / "run-test-evidence-01"
        evidence_dir.mkdir(parents=True)

        # Create sample stage deliverables
        f1 = staging / "01_test_normalizer.md"
        f1.write_text("# Normalized Transcript\nTurn 1: hello", encoding="utf-8")
        f2 = staging / "03_test_graph_projection.json"
        f2.write_text('{"nodes": ["Dan"], "edges": []}', encoding="utf-8")

        class MockTracker:
            def __init__(self):
                self.input_path = "/tmp/meeting.m4a"
                self.input_hash = "1" * 64
                self.metadata = {}

        tracker = MockTracker()
        artifacts = snapshot_run_artifacts(
            tracker=tracker,
            staging_dir=staging,
            evidence_dir=evidence_dir,
            meta={"raw_source_sha256": "2" * 64, "source_classification": "RESTRICTED"},
            modality="audio",
        )

        assert "01_test_normalizer.md" in artifacts
        assert "03_test_graph_projection.json" in artifacts
        assert (evidence_dir / "artifacts" / "01_test_normalizer.md").is_file()
        assert (evidence_dir / "artifacts" / "03_test_graph_projection.json").is_file()

        # Simulate writing receipt
        receipt_data = {
            "run_id": "run-test-evidence-01",
            "persisted_artifacts_dir": str(evidence_dir / "artifacts"),
            "artifacts": artifacts,
            "source_evidence": tracker.metadata["source_evidence"],
        }
        receipt_path = evidence_dir / "pipeline_execution_receipt.json"
        receipt_path.write_text(json.dumps(receipt_data), encoding="utf-8")

        ok, errors = verify_run_evidence_integrity(receipt_path)
        assert ok is True
        assert len(errors) == 0
