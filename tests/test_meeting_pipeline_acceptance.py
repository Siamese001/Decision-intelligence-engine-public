"""
Unit and integration tests for tools/run_meeting_pipeline_acceptance.py.

Verifies:
1. Wave 0 isolated baseline execution and immutable receipt sealing.
2. Wave 1 fail-closed enforcement when Wave 0 prerequisite receipt is missing.
3. Wave 1 clearance when Wave 0 receipt is valid and PASS.
4. Runtime isolation validation rejecting shared production .local.
5. Rejection of failed or corrupted prerequisite receipts.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import run_meeting_pipeline_acceptance as runner


def test_runtime_isolation_rejects_shared_local():
    """Verify that targeting the shared production .local root fails closed."""
    prod_local = runner.REPO_ROOT / ".local"
    ok, msg = runner.validate_runtime_isolation(prod_local)
    assert not ok
    assert "Cannot use shared production root" in msg


def test_runtime_isolation_accepts_isolated_sandbox(tmp_path: Path):
    """Verify that an isolated sandbox directory passes the isolation check."""
    sandbox = tmp_path / "isolated_runtime"
    sandbox.mkdir(parents=True, exist_ok=True)
    ok, msg = runner.validate_runtime_isolation(sandbox)
    assert ok
    assert "Isolated runtime root verified" in msg


def test_wave_0_baseline_prerequisites_verified(tmp_path: Path):
    """Verify that Wave 0 checks and verifies baseline artifacts."""
    manifest_p = runner.REPO_ROOT / "tests" / "manifests" / "meeting_pipeline_acceptance_manifest.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    wave_0_cfg = manifest["waves"]["0"]

    ok, msg, _ = runner.verify_prerequisites(0, tmp_path, wave_0_cfg)
    assert ok
    assert "Wave 0 baseline artifacts verified" in msg


def test_wave_1_fails_closed_when_wave_0_receipt_missing(tmp_path: Path):
    """Verify that Wave 1 fails closed if Wave 0 receipt is missing from runtime."""
    manifest_p = runner.REPO_ROOT / "tests" / "manifests" / "meeting_pipeline_acceptance_manifest.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    wave_1_cfg = manifest["waves"]["1"]

    isolated_root = tmp_path / "sandbox_empty"
    isolated_root.mkdir(parents=True, exist_ok=True)

    ok, msg, _ = runner.verify_prerequisites(1, isolated_root, wave_1_cfg)
    assert not ok
    assert "BLOCKED: Prerequisite receipt 'wave_0_baseline.json' for Wave 1 not found" in msg


def test_wave_1_succeeds_when_wave_0_receipt_present(tmp_path: Path):
    """Verify that Wave 1 passes prerequisite check when valid Wave 0 receipt exists."""
    manifest_p = runner.REPO_ROOT / "tests" / "manifests" / "meeting_pipeline_acceptance_manifest.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    wave_1_cfg = manifest["waves"]["1"]

    isolated_root = tmp_path / "sandbox_valid"
    receipts_dir = isolated_root / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    # Seed valid wave_0_baseline.json
    wave_0_receipt = receipts_dir / "wave_0_baseline.json"
    wave_0_receipt.write_text(
        json.dumps({
            "receipt_id": "receipt-test-wave-0",
            "wave": 0,
            "terminal_outcome": "PASS",
            "receipt_digest": "mockdigest123",
        }),
        encoding="utf-8",
    )

    ok, msg, prereq_data = runner.verify_prerequisites(1, isolated_root, wave_1_cfg)
    assert ok
    assert "verified [PASS]" in msg
    assert prereq_data["terminal_outcome"] == "PASS"


def test_wave_1_rejects_failed_prior_receipt(tmp_path: Path):
    """Verify that Wave 1 rejects a prior receipt that ended in FAIL."""
    manifest_p = runner.REPO_ROOT / "tests" / "manifests" / "meeting_pipeline_acceptance_manifest.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    wave_1_cfg = manifest["waves"]["1"]

    isolated_root = tmp_path / "sandbox_failed"
    receipts_dir = isolated_root / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    wave_0_receipt = receipts_dir / "wave_0_baseline.json"
    wave_0_receipt.write_text(
        json.dumps({
            "receipt_id": "receipt-test-wave-0",
            "wave": 0,
            "terminal_outcome": "FAIL",
            "receipt_digest": "mockdigest123",
        }),
        encoding="utf-8",
    )

    ok, msg, _ = runner.verify_prerequisites(1, isolated_root, wave_1_cfg)
    assert not ok
    assert "outcome is 'FAIL', expected PASS" in msg


def test_main_cli_execution_wave_0(tmp_path: Path):
    """Verify end-to-end execution of runner CLI for Wave 0."""
    isolated_root = tmp_path / "cli_sandbox"
    receipt_dest = isolated_root / "receipts" / "wave_0_baseline.json"

    with patch.object(runner, "execute_wave_tests", return_value=(True, {"passed": 2, "failed": 0, "total": 2})):
        ret = runner.main([
            "--wave", "0",
            "--runtime-root", str(isolated_root),
            "--receipt", str(receipt_dest),
        ])
        assert ret == 0

    assert receipt_dest.exists()
    payload = json.loads(receipt_dest.read_text(encoding="utf-8"))
    assert payload["wave"] == 0
    assert payload["terminal_outcome"] == "PASS"
    assert payload["receipt_digest"] != ""
    assert "manifests/meeting_pipeline/baseline_recording_manifest.json" in payload["evidence_digests"]
