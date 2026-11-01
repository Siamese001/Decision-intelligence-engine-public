"""
tests/end_to_end/test_ingestion_coverage_recovery_e2e.py

End-to-end integration tests for active ingestion coverage reconciliation,
recovery manifest generation, and contract compliance under Wave 6.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dual_pipeline.core import digest, read_json, sha256_file, write_json
from tools.reconcile_ingestion_coverage import (
    RECOVERY_CONTRACT_ID,
    STATUS_COVERED,
    STATUS_NEEDS_PUBLICATION,
    STATUS_PENDING_APPROVAL,
    reconcile_coverage,
)


@pytest.fixture
def isolated_runtime(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "receipts").mkdir()
    (runtime_root / "approval_requests").mkdir()

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()

    active_pair = {
        "active_generation": 10,
        "graph_sha256": "a" * 64,
        "vector_dim": 1024,
    }
    write_json(runtime_root / "active_pair.json", active_pair)

    source1 = sources_dir / "meeting_alpha.txt"
    source1.write_text("Meeting Alpha notes content with strategic decisions.", encoding="utf-8")

    source2 = sources_dir / "meeting_beta.txt"
    source2.write_text("Meeting Beta notes content with architecture review.", encoding="utf-8")

    source3 = sources_dir / "meeting_gamma.txt"
    source3.write_text("Meeting Gamma notes content ready for publication.", encoding="utf-8")

    return runtime_root, source1, source2, source3


def test_reconciliation_e2e_classifications(isolated_runtime: tuple[Path, Path, Path, Path]) -> None:
    runtime_root, source1, source2, source3 = isolated_runtime
    s1_digest = sha256_file(source1)
    s2_digest = sha256_file(source2)
    s3_digest = sha256_file(source3)

    # 1. Source 1: Proven active graph coverage with positive deltas
    s1_receipt = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "source_digest": s1_digest,
        "source_path": str(source1),
        "directive_id": "dir-alpha",
        "terminal_outcome": "PASS",
        "source_coverage_verified": True,
        "completion_status": "COMPLETE_UPDATED",
        "active_generation": 10,
        "delta_table_counts": {"nodes": 5, "edges": 8},
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "1" * 64},
    }
    write_json(runtime_root / "receipts" / "alpha_receipt.json", s1_receipt)

    # 2. Source 2: Legacy artifact-only +0 dry run (not covered)
    s2_receipt = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-completion-receipt:1.0.0",
        "receipt_type": "active_ingestion_completion_receipt",
        "source_digest": s2_digest,
        "source_path": str(source2),
        "directive_id": "dir-beta",
        "terminal_outcome": "PASS",
        "source_coverage_verified": False,
        "dry_run": True,
        "mutation_outcome": "IDEMPOTENT_NOOP",
        "active_generation": 10,
        "delta_table_counts": {"nodes": 0, "edges": 0},
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "2" * 64},
    }
    write_json(runtime_root / "receipts" / "beta_receipt.json", s2_receipt)

    # 3. Source 3: Approved approval request awaiting publication
    s3_request = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
        "directive_id": "dir-gamma",
        "source_package_digest": s3_digest,
        "source_path": str(source3),
        "baseline_generation": 10,
        "status": "APPROVED",
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "3" * 64},
    }
    write_json(runtime_root / "approval_requests" / "gamma_req.json", s3_request)

    out_manifest_path = runtime_root / "recovery_manifest.json"
    manifest = reconcile_coverage(
        sources=[source1, source2, source3],
        runtime_root=runtime_root,
        output_path=out_manifest_path,
    )

    assert manifest["contract_id"] == RECOVERY_CONTRACT_ID
    assert manifest["total_sources"] == 3
    assert manifest["counts_by_status"][STATUS_COVERED] == 1
    assert manifest["counts_by_status"][STATUS_PENDING_APPROVAL] == 1
    assert manifest["counts_by_status"][STATUS_NEEDS_PUBLICATION] == 1

    item1 = next(it for it in manifest["items"] if it["source_path"] == str(source1))
    assert item1["status"] == STATUS_COVERED
    assert item1["suggested_action"] == "NO_ACTION_REQUIRED"

    item2 = next(it for it in manifest["items"] if it["source_path"] == str(source2))
    assert item2["status"] == STATUS_PENDING_APPROVAL
    assert item2["suggested_action"] == "PROMPT_OPERATOR_APPROVAL"

    item3 = next(it for it in manifest["items"] if it["source_path"] == str(source3))
    assert item3["status"] == STATUS_NEEDS_PUBLICATION
    assert item3["suggested_action"] == "RESUME_ACTIVE_PUBLICATION"

    assert out_manifest_path.is_file()
    saved = read_json(out_manifest_path)
    assert saved["canonical_digest"]["value"] == manifest["canonical_digest"]["value"]


def test_reconciliation_cli_execution(isolated_runtime: tuple[Path, Path, Path, Path], tmp_path: Path) -> None:
    runtime_root, source1, source2, _ = isolated_runtime
    out_json = tmp_path / "cli_manifest.json"

    cmd = [
        sys.executable,
        "tools/reconcile_ingestion_coverage.py",
        "--runtime-root", str(runtime_root),
        "--sources", str(source1), str(source2),
        "--output-manifest", str(out_json),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"CLI error: {res.stderr}"

    assert out_json.is_file()
    data = read_json(out_json)
    assert data["total_sources"] == 2
    assert "counts_by_status" in data
    assert "canonical_digest" in data


def test_reconciliation_drift_detection(isolated_runtime: tuple[Path, Path, Path, Path]) -> None:
    runtime_root, source1, _, _ = isolated_runtime
    s1_digest = sha256_file(source1)

    # Approved request with baseline generation 8 when runtime is at 10 (generation drift)
    stale_req = {
        "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
        "directive_id": "dir-stale",
        "source_package_digest": s1_digest,
        "source_path": str(source1),
        "baseline_generation": 8,
        "status": "APPROVED",
        "canonical_digest": {"algorithm": "sha256", "scope": "artifact_without_canonical_digest", "value": "4" * 64},
    }
    write_json(runtime_root / "approval_requests" / "stale_req.json", stale_req)

    manifest = reconcile_coverage(sources=[source1], runtime_root=runtime_root)
    assert manifest["counts_by_status"]["NEEDS_REEVALUATION"] == 1
    assert manifest["items"][0]["status"] == "NEEDS_REEVALUATION"
    assert manifest["items"][0]["suggested_action"] == "REFRESH_AND_REEVALUATE"
