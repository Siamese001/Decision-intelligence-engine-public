"""
tests/adversarial/test_hitl_upload_evidence_chain_adversarial.py

Adversarial and edge-case verification for Wave 1 evidence preservation.
Covers 4 mandatory categories:
1. Boundary payload & malformed input (0-byte files, truncated JSON, invalid types)
2. Concurrency & race (16 concurrent threads writing execution snapshots)
3. Invariant bypass attempt (overwriting immutable receipts, synthetic approval injection)
4. Exact production regression (unresolved Jarvis approval reference cleanly identified)
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.evidence_capture import (
    snapshot_run_artifacts,
    verify_run_evidence_integrity,
)
from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
    SourceOriginType,
    validate_evidence_coverage_payload,
)
from dual_pipeline.execution_evidence import write_immutable_evidence
from dual_pipeline.hitl_governance_core.approval_lookup import (
    ApprovalResolutionStatus,
    resolve_hitl_approval_reference,
)
from dual_pipeline.hitl_governance_core.crypto import (
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)


class MockTracker:
    def __init__(self, run_id: str):
        self.input_path = f"/tmp/input_{run_id}.txt"
        self.input_hash = "a" * 64
        self.metadata: dict = {}
        class MockTiming:
            def __init__(self, rid):
                self.run_id = rid
                self.evidence_directory = None
            def attributes(self):
                return {"run_id": self.run_id}
        self.timing = MockTiming(run_id)

    def build_receipt(self, out_dir, terminal_outcome="COMPLETE"):
        class MockReceipt:
            def __init__(self, tracker, out_p, outcome):
                self.run_id = tracker.timing.run_id
                self.output_dir = str(out_p)
                self.terminal_outcome = outcome
                self.artifacts = dict(tracker.metadata.get("persisted_artifacts", {}))
                self.persisted_artifacts_dir = tracker.metadata.get("persisted_artifacts_dir", "")
            def to_dict(self):
                return {
                    "run_id": self.run_id,
                    "output_dir": self.output_dir,
                    "terminal_outcome": self.terminal_outcome,
                    "artifacts": self.artifacts,
                    "persisted_artifacts_dir": self.persisted_artifacts_dir,
                }
        return MockReceipt(self, out_dir, terminal_outcome)


# --- Category 1: Boundary payload & Malformed input ---

def test_adversarial_empty_and_zero_byte_artifacts():
    """Boundary test: 0-byte artifacts and empty staging directories are handled gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        staging = root / "staging"
        staging.mkdir()
        evidence_dir = root / "executions" / "run-zero-byte"
        evidence_dir.mkdir(parents=True)

        # Create 0-byte artifact
        empty_art = staging / "01_empty.md"
        empty_art.touch()
        assert empty_art.stat().st_size == 0

        tracker = MockTracker("run-zero-byte")
        tracker.timing.evidence_directory = evidence_dir

        artifacts = snapshot_run_artifacts(
            tracker=tracker,
            staging_dir=staging,
            evidence_dir=evidence_dir,
            meta={},
        )
        assert "01_empty.md" in artifacts
        assert artifacts["01_empty.md"]["size_bytes"] == 0
        # SHA-256 of empty file is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        assert artifacts["01_empty.md"]["sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

        receipt_data = {
            "run_id": "run-zero-byte",
            "persisted_artifacts_dir": str(evidence_dir / "artifacts"),
            "artifacts": artifacts,
        }
        receipt_p = evidence_dir / "pipeline_execution_receipt.json"
        receipt_p.write_text(json.dumps(receipt_data), encoding="utf-8")

        ok, errs = verify_run_evidence_integrity(receipt_p)
        assert ok is True
        assert len(errs) == 0


def test_adversarial_malformed_ledger_json_fails_safe():
    """Malformed input test: Corrupt/truncated JSON in ledger returns LEDGER_UNAVAILABLE cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        corrupt_ledger = Path(tmpdir) / "hitl_decision_ledger.json"
        corrupt_ledger.write_text('{"decisions": [{"broken": ', encoding="utf-8")  # Truncated JSON

        res = resolve_hitl_approval_reference(
            ref="auth-ev-any",
            default_ledger_path=corrupt_ledger,
        )
        assert res.is_valid is False
        assert res.is_rejected is False
        assert res.is_unresolved is True
        assert res.status == ApprovalResolutionStatus.LEDGER_UNAVAILABLE
        assert "Failed to parse ledger" in res.reason


def test_adversarial_schema_rejects_missing_disposition_reason():
    """Malformed input test: Schema validator rejects items without explicit disposition reason."""
    invalid_manifest = {
        "contract_id": "urn:nee:dual-pipeline:evidence-coverage:1.0.0",
        "schema_version": "1.0.0",
        "manifest_id": "cov-invalid-01",
        "run_id": "run-test",
        "source_digest": "a" * 64,
        "items": [
            {
                "item_id": "item-01",
                "item_type": "PERSON_IDENTITY",
                "is_eligible_graph_fact": True,
                "state": "CAPTURED",
                "source_origin": {
                    "source_type": "TRANSCRIPT_HITL",
                    "source_hash": "a" * 64,
                },
                # Missing required 'disposition_reason'
                "created_at": "2026-10-08T00:00:00Z",
            }
        ],
        "coverage_summary": {
            "total_items": 1,
            "eligible_facts_count": 1,
            "operational_controls_count": 0,
            "active_verified_count": 0,
            "excluded_count": 0,
            "quarantined_count": 0,
            "pending_count": 1,
        },
        "generated_at": "2026-10-08T00:00:00Z",
    }
    is_valid, errors = validate_evidence_coverage_payload(invalid_manifest)
    assert is_valid is False
    assert any("disposition_reason" in e for e in errors)


# --- Category 2: Concurrency & Race ---

def test_adversarial_concurrent_run_snapshotting():
    """Concurrency test: 16 concurrent threads snapshotting artifacts independently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        executions_root = root / "executions"
        executions_root.mkdir()

        def _worker(thread_idx: int) -> bool:
            run_id = f"run-concurrent-{thread_idx:03d}"
            evidence_dir = executions_root / run_id
            evidence_dir.mkdir(parents=True, exist_ok=False)

            staging = root / f"staging_{thread_idx}"
            staging.mkdir()
            (staging / f"artifact_{thread_idx}.txt").write_text(f"content-{thread_idx}", encoding="utf-8")

            tracker = MockTracker(run_id)
            tracker.timing.evidence_directory = evidence_dir
            snapshot_run_artifacts(
                tracker=tracker,
                staging_dir=staging,
                evidence_dir=evidence_dir,
                meta={},
            )
            receipt = tracker.build_receipt(evidence_dir, "COMPLETE").to_dict()
            write_immutable_evidence(evidence_dir / "pipeline_execution_receipt.json", receipt)
            ok, _ = verify_run_evidence_integrity(evidence_dir / "pipeline_execution_receipt.json")
            return ok

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(_worker, i) for i in range(16)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert all(results)
        assert len(results) == 16
        # Verify 16 distinct subdirectories exist
        assert len(list(executions_root.iterdir())) == 16


# --- Category 3: Invariant bypass attempt ---

def test_adversarial_immutable_evidence_collision_prevention():
    """Invariant bypass test: Overwriting an existing immutable receipt raises FileExistsError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        receipt_path = Path(tmpdir) / "pipeline_execution_receipt.json"
        write_immutable_evidence(receipt_path, {"run_id": "run-initial", "outcome": "FAILED"})
        assert receipt_path.is_file()

        # Attempting to overwrite with divergent bytes must fail closed
        with pytest.raises(FileExistsError):
            write_immutable_evidence(receipt_path, {"run_id": "run-initial", "outcome": "COMPLETE"})


def test_adversarial_synthetic_approval_bypass_refusal():
    """Invariant bypass test: Synthetic auto-approval from mock environment is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
        priv, pub = generate_hitl_keypair()
        entry = create_signed_hitl_approval_entry(
            subject_sha256="b" * 64,
            case_ids=["case-synthetic"],
            decision="APPROVE",
            operator_id="auto_synthetic_mock",
            metadata={"approval_origin": "mock_synthetic_auto_stop_hook"},
            private_key=priv,
            public_key=pub,
        )
        entry["entry_id"] = "auth-ev-synthetic-01"
        ledger_path.write_text(json.dumps({"decisions": [entry]}), encoding="utf-8")

        res = resolve_hitl_approval_reference(
            ref="auth-ev-synthetic-01",
            default_ledger_path=ledger_path,
        )
        assert res.is_valid is False
        assert res.is_rejected is True
        assert res.status == ApprovalResolutionStatus.REJECTED_SYNTHETIC
        assert "Synthetic" in res.reason


# --- Category 4: Exact production regression ---

def test_adversarial_production_regression_jarvis_unresolved_approval():
    """Exact production regression: Pin Jarvis cohort missing approval reference.

    Audited runs 540343af0ac849a3bf15f58a592322eb and c2c70c6d86734c9286b6ef5c3b9a8597
    referenced 'auth-ev-57977c11240acd3816a3e7bb40ae4093' which was missing from root ledger.
    Verify that lookup classifies this strictly as UNRESOLVED_REFERENCE, not rejected and not valid.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root_ledger = Path(tmpdir) / "hitl_decision_ledger.json"
        # Current root ledger contains other unrelated decisions
        root_ledger.write_text(json.dumps({"decisions": [{"entry_id": "auth-ev-other-123"}]}), encoding="utf-8")

        res = resolve_hitl_approval_reference(
            ref="auth-ev-57977c11240acd3816a3e7bb40ae4093",
            default_ledger_path=root_ledger,
        )
        assert res.status == ApprovalResolutionStatus.UNRESOLVED_REFERENCE
        assert res.is_valid is False
        assert res.is_rejected is False
        assert res.is_unresolved is True
        assert "not found in ledger" in res.reason
