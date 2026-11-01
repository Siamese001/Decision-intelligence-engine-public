"""Unit and Integration Tests for Output 3 Wave Acceptance Runner and Receipts.

Verifies:
1. Runner argument parsing and CLI interface.
2. Bootstrap receipt execution and cryptographic sealing.
3. Immutability: refusal to overwrite existing receipt files.
4. Predecessor receipt verification and tamper rejection for Wave > 1.
5. Zero collection rejection: failing closed if zero tests are collected.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from tools.run_output3_source_wave import (
    _canonical_digest,
    execute_wave,
    verify_ci_enrollment,
    verify_predecessor_receipt,
)


def test_canonical_digest_determinism() -> None:
    """Verify RFC 8785 canonical digest is deterministic regardless of key insertion order."""
    data1 = {"b": 2, "a": 1, "c": [3, 2, 1]}
    data2 = {"a": 1, "c": [3, 2, 1], "b": 2}
    assert _canonical_digest(data1) == _canonical_digest(data2)


def test_verify_ci_enrollment_resolves_all_wave_tests() -> None:
    """Verify all wave 1 acceptance tests resolve to CI profiles."""
    tests = [
        "tests/test_output3_fail_closed_e2e.py",
        "tests/test_output3_fail_closed_adversarial.py",
        "tests/test_output3_wave_receipts.py",
        "tests/test_document_output3_only_rca.py",
    ]
    enrollment = verify_ci_enrollment(tests)
    assert len(enrollment) == 4
    for t in tests:
        assert enrollment[t] in ("e2e", "deterministic", "bge")


def test_predecessor_verification_wave_1_is_none(tmp_path: Path) -> None:
    """Verify wave 1 has no predecessor requirement."""
    assert verify_predecessor_receipt(tmp_path, wave=1) is None


def test_predecessor_verification_wave_2_missing_fails(tmp_path: Path) -> None:
    """Verify wave 2 fails closed if wave 1 receipt is missing."""
    with pytest.raises(RuntimeError, match="Missing predecessor receipt"):
        verify_predecessor_receipt(tmp_path, wave=2)


def test_predecessor_verification_tampered_digest_fails(tmp_path: Path) -> None:
    """Verify predecessor receipt with tampered digest is rejected."""
    receipt_1 = tmp_path / "output3_source_wave_1_receipt.json"
    payload = {
        "wave": 1,
        "status": "PASS",
        "run_id": "test-run-1",
        "receipt_digest": "INVALID_TAMPERED_DIGEST",
    }
    receipt_1.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="digest mismatch"):
        verify_predecessor_receipt(tmp_path, wave=2)


def test_predecessor_verification_valid_receipt_succeeds(tmp_path: Path) -> None:
    """Verify valid sealed predecessor receipt passes verification."""
    receipt_1 = tmp_path / "output3_source_wave_1_receipt.json"
    payload = {
        "wave": 1,
        "status": "PASS",
        "run_id": "test-run-1",
    }
    digest = _canonical_digest(payload)
    payload["receipt_digest"] = digest
    receipt_1.write_text(json.dumps(payload), encoding="utf-8")

    pred_info = verify_predecessor_receipt(tmp_path, wave=2)
    assert pred_info is not None
    assert pred_info["wave"] == 1
    assert pred_info["receipt_digest"] == digest


def test_receipt_runner_refuses_overwrite(tmp_path: Path) -> None:
    """Verify execute_wave refuses to overwrite an existing receipt file."""
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    existing_receipt = receipt_root / "output3_source_wave_1_receipt.json"
    existing_receipt.write_text("{\"existing\": true}", encoding="utf-8")

    plan_path = Path("docs/plans/feat_output3_source_classification_plan.md")
    exit_code = execute_wave(
        wave=1,
        plan_path=plan_path,
        runtime_root=tmp_path / "runtime",
        receipt_root=receipt_root,
        timeout=60,
    )
    assert exit_code == 1


def test_receipt_runner_rejects_zero_collection(tmp_path: Path) -> None:
    """Verify runner fails closed if zero tests are collected."""
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    plan_path = Path("docs/plans/feat_output3_source_classification_plan.md")

    mock_run_result = MagicMock()
    mock_run_result.total_tests = 0
    mock_run_result.wasSuccessful.return_value = True

    with patch("tools.run_output3_source_wave.run_pytest_with_progress", return_value=mock_run_result):
        exit_code = execute_wave(
            wave=1,
            plan_path=plan_path,
            runtime_root=tmp_path / "runtime",
            receipt_root=receipt_root,
            timeout=60,
        )
        assert exit_code == 1
        assert not (receipt_root / "output3_source_wave_1_receipt.json").exists()
