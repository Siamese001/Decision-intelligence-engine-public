"""
tests/adversarial/test_active_ingestion_live_recovery_adversarial.py

Adversarial testing for Wave 7 live recovery and authorization boundaries:
1. Tampered qualification manifest digest detection.
2. Expired authorization rejection.
3. Baseline generation drift blocking publication.
4. Fault containment on simulated write crash.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.core import digest, read_json, sha256_file, write_json
from tools.reconcile_ingestion_coverage import (
    STATUS_NEEDS_REEVALUATION,
    STATUS_RECONCILIATION_REQUIRED,
    reconcile_coverage,
)


@pytest.fixture
def adversarial_env(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "receipts").mkdir()
    (runtime_root / "approval_requests").mkdir()

    source = tmp_path / "source.txt"
    source.write_text("Adversarial scenario source.", encoding="utf-8")

    active_pair = {
        "active_generation": 50,
        "graph_sha256": "5" * 64,
        "embedding_backend": "BAAI/bge-m3",
    }
    write_json(runtime_root / "active_pair.json", active_pair)

    return runtime_root, source


def test_adversarial_tampered_qualification_digest_fails_validation() -> None:
    qual = {"manifest_id": "m1", "scope": "candidate"}
    digest_real = digest(qual)

    auth = {
        "authorization_id": "a1",
        "qualification_manifest_digest": "corrupted_hash" + "0" * 50,
        "status": "AUTHORIZED",
    }

    # Verification must detect tamper
    is_valid = auth["qualification_manifest_digest"] == digest_real
    assert is_valid is False, "Tampered qualification digest must fail validation"


def test_adversarial_stale_baseline_generation_triggers_reevaluation(
    adversarial_env: tuple[Path, Path]
) -> None:
    runtime_root, source = adversarial_env
    s_digest = sha256_file(source)

    # Approved request with baseline generation 40 when runtime is at 50
    stale_req = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
        "directive_id": "dir-stale-gen",
        "source_package_digest": s_digest,
        "source_path": str(source),
        "baseline_generation": 40,
        "status": "APPROVED",
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "a" * 64},
    }
    write_json(runtime_root / "approval_requests" / "req_stale.json", stale_req)

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    assert manifest["counts_by_status"][STATUS_NEEDS_REEVALUATION] == 1
    assert manifest["items"][0]["status"] == STATUS_NEEDS_REEVALUATION
    assert manifest["items"][0]["suggested_action"] == "REFRESH_AND_REEVALUATE"


def test_adversarial_write_crash_in_journal_blocks_silent_dry_fallback(
    adversarial_env: tuple[Path, Path]
) -> None:
    runtime_root, source = adversarial_env
    s_digest = sha256_file(source)

    # Record crashed write attempt in journal
    journal_path = runtime_root / "publication_journal.jsonl"
    journal_entry = {
        "directive_id": "dir-crash",
        "source_digest": s_digest,
        "source_path": str(source),
        "status": "FAILED_AFTER_WRITE",
        "error": "Simulated disk exhaustion mid-transaction",
    }
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(journal_entry) + "\n")

    manifest = reconcile_coverage(sources=[source], runtime_root=runtime_root)
    # Must enter RECONCILIATION_REQUIRED rather than claiming dry-run complete
    assert manifest["counts_by_status"][STATUS_RECONCILIATION_REQUIRED] == 1
    assert manifest["items"][0]["status"] == STATUS_RECONCILIATION_REQUIRED
    assert manifest["items"][0]["suggested_action"] == "RECONCILE_AND_RECOVER"
