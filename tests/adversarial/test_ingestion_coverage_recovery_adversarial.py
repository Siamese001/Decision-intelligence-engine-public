"""
tests/adversarial/test_ingestion_coverage_recovery_adversarial.py

Adversarial and edge-case testing for ingestion coverage reconciliation under Wave 6.
Covers:
1. Boundary & malformed input (missing files, unparseable receipts).
2. Concurrency & crash recovery (journal uncertain write states).
3. Invariant bypass prevention (zero-mutation receipts masquerading as covered).
4. Explicit decline preservation (operator rejected states).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.core import sha256_file, write_json
from tools.reconcile_ingestion_coverage import (
    STATUS_COVERED,
    STATUS_DECLINED,
    STATUS_NEEDS_REEVALUATION,
    STATUS_PENDING_APPROVAL,
    STATUS_RECONCILIATION_REQUIRED,
    STATUS_UNKNOWN,
    reconcile_coverage,
)


@pytest.fixture
def test_env(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "receipts").mkdir()
    (runtime_root / "approval_requests").mkdir()

    source = tmp_path / "source.txt"
    source.write_text("Adversarial scenario source payload.", encoding="utf-8")
    return runtime_root, source


def test_adversarial_missing_source_classified_as_unknown(test_env: tuple[Path, Path], tmp_path: Path) -> None:
    runtime_root, _ = test_env
    non_existent = tmp_path / "ghost_file.txt"

    manifest = reconcile_coverage(sources=[non_existent], runtime_root=runtime_root)
    assert manifest["total_sources"] == 1
    assert manifest["counts_by_status"][STATUS_UNKNOWN] == 1
    item = manifest["items"][0]
    assert item["status"] == STATUS_UNKNOWN
    assert item["suggested_action"] == "VERIFY_SOURCE_PERMISSIONS"


def test_adversarial_uncertain_journal_state_requires_reconciliation(test_env: tuple[Path, Path]) -> None:
    runtime_root, source = test_env
    s_digest = sha256_file(source)

    # Simulate an incomplete writer crash or uncertain state in journal
    journal_path = runtime_root / "publication_journal.jsonl"
    journal_entry = {
        "directive_id": "dir-crashed",
        "source_digest": s_digest,
        "source_path": str(source),
        "status": "CRASHED",
        "timestamp": "2026-10-06T12:00:00Z",
    }
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(journal_entry) + "\n")

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    assert manifest["counts_by_status"][STATUS_RECONCILIATION_REQUIRED] == 1
    item = manifest["items"][0]
    assert item["status"] == STATUS_RECONCILIATION_REQUIRED
    assert item["suggested_action"] == "RECONCILE_AND_RECOVER"


def test_adversarial_zero_delta_receipt_cannot_claim_covered(test_env: tuple[Path, Path]) -> None:
    runtime_root, source = test_env
    s_digest = sha256_file(source)

    # Craft a receipt that claims PASS and source_coverage_verified=True, but has zero delta counts
    fake_covered = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "source_digest": s_digest,
        "source_path": str(source),
        "directive_id": "dir-fake",
        "terminal_outcome": "PASS",
        "source_coverage_verified": True,
        "completion_status": "COMPLETE_UPDATED",
        "active_generation": 5,
        "delta_table_counts": {"nodes": 0, "edges": 0, "facts": 0},  # All zero!
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "f" * 64},
    }
    write_json(runtime_root / "receipts" / "fake_receipt.json", fake_covered)

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    # Must NOT be classified as COVERED!
    assert manifest["counts_by_status"][STATUS_COVERED] == 0
    assert manifest["counts_by_status"][STATUS_PENDING_APPROVAL] == 1
    assert manifest["items"][0]["status"] == STATUS_PENDING_APPROVAL


def test_adversarial_operator_declined_persists_as_declined(test_env: tuple[Path, Path]) -> None:
    runtime_root, source = test_env
    s_digest = sha256_file(source)

    declined_req = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
        "directive_id": "dir-declined",
        "source_package_digest": s_digest,
        "source_path": str(source),
        "status": "DECLINED",
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "d" * 64},
    }
    write_json(runtime_root / "approval_requests" / "declined.json", declined_req)

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    assert manifest["counts_by_status"][STATUS_DECLINED] == 1
    item = manifest["items"][0]
    assert item["status"] == STATUS_DECLINED
    assert item["suggested_action"] == "FRESH_OPERATOR_AUTHORIZATION_REQUIRED"


def test_adversarial_corrupted_receipt_files_handled_gracefully(test_env: tuple[Path, Path]) -> None:
    runtime_root, source = test_env

    # Write truncated garbage JSON to receipts dir
    corrupt_file = runtime_root / "receipts" / "corrupted.json"
    corrupt_file.write_text("{\"incomplete\": ", encoding="utf-8")

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    # Reconciler survives corrupted receipt without exception and falls back to uningested/pending
    assert manifest["total_sources"] == 1
    assert manifest["counts_by_status"][STATUS_PENDING_APPROVAL] == 1
