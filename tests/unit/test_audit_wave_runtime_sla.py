"""
tests/unit/test_audit_wave_runtime_sla.py

Unit tests for the Wave Runtime SLA Auditor (<= 240s SLA enforcement).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Sequence

import pytest
from tools.audit_wave_runtime_sla import (
    DEFAULT_SLA_BUDGET_SECONDS,
    audit_test_runtime,
    main,
)


class FakeClock:
    """Mock-free deterministic clock for testing SLA timeouts."""
    def __init__(self, timestamps: Sequence[float]):
        self._timestamps = list(timestamps)
        self._idx = 0

    def __call__(self) -> float:
        val = self._timestamps[min(self._idx, len(self._timestamps) - 1)]
        self._idx += 1
        return val


def _make_fake_runner(returncode: int = 0, stdout: str = "All passed", stderr: str = ""):
    def _runner(cmd: Sequence[str], env: Dict[str, str]) -> Any:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return _runner


def test_audit_test_runtime_pass(tmp_path: Path):
    """Verify that successful execution within budget passes SLA audit."""
    receipt_file = tmp_path / "sla_receipt.json"
    fake_runner = _make_fake_runner(returncode=0, stdout="All passed")
    fake_clock = FakeClock([100.0, 105.25])

    outcome = audit_test_runtime(
        test_targets=["tests/unit/dummy_test.py"],
        budget_seconds=240.0,
        receipt_path=receipt_file,
        runner=fake_runner,
        clock=fake_clock,
    )

    assert outcome["sla_verdict"] == "PASS"
    assert outcome["status"] == "COMPLIANT_UNDER_4_MINUTES"
    assert outcome["total_runtime_seconds"] == 5.25
    assert outcome["sla_margin_seconds"] == 234.75
    assert outcome["target_count"] == 1

    assert receipt_file.exists()
    saved = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert saved["sla_verdict"] == "PASS"
    assert saved["contract_id"] == "urn:nee:governance:receipt:wave-sla-benchmark:1.0.0"


def test_audit_test_runtime_sla_breach(tmp_path: Path):
    """Verify that execution exceeding budget triggers SLA_BREACH and FAIL."""
    receipt_file = tmp_path / "sla_receipt_breach.json"
    fake_runner = _make_fake_runner(returncode=0, stdout="Slow run")
    fake_clock = FakeClock([0.0, 245.0])

    outcome = audit_test_runtime(
        test_targets=["tests/unit/slow_test.py"],
        budget_seconds=240.0,
        receipt_path=receipt_file,
        runner=fake_runner,
        clock=fake_clock,
    )

    assert outcome["sla_verdict"] == "FAIL"
    assert outcome["status"] == "SLA_BREACH"
    assert outcome["total_runtime_seconds"] == 245.0
    assert outcome["sla_margin_seconds"] == -5.0


def test_audit_test_runtime_test_failure(tmp_path: Path):
    """Verify that failing pytest exits fail the SLA audit even if under budget."""
    fake_runner = _make_fake_runner(returncode=1, stdout="Failed tests")
    fake_clock = FakeClock([0.0, 10.0])

    outcome = audit_test_runtime(
        test_targets=["tests/unit/failing_test.py"],
        budget_seconds=240.0,
        runner=fake_runner,
        clock=fake_clock,
    )

    assert outcome["sla_verdict"] == "FAIL"
    assert outcome["pytest_exit_code"] == 1
    assert outcome["status"] == "COMPLIANT_UNDER_4_MINUTES"


def test_main_cli_success(tmp_path: Path):
    """Verify CLI entrypoint returns 0 when tests pass within budget."""
    receipt_file = tmp_path / "cli_receipt.json"
    fake_runner = _make_fake_runner(returncode=0, stdout="OK")
    fake_clock = FakeClock([0.0, 1.5])

    code = main(
        [
            "--budget", "240",
            "--profile", "wave",
            "--receipt", str(receipt_file),
        ],
        runner=fake_runner,
        clock=fake_clock,
    )

    assert code == 0
    assert receipt_file.exists()


def test_main_cli_failure(tmp_path: Path):
    """Verify CLI entrypoint returns 1 on SLA breach."""
    receipt_file = tmp_path / "cli_receipt_fail.json"
    fake_runner = _make_fake_runner(returncode=0, stdout="OK")
    fake_clock = FakeClock([0.0, 300.0])

    code = main(
        [
            "--budget", "240",
            "--receipt", str(receipt_file),
        ],
        runner=fake_runner,
        clock=fake_clock,
    )

    assert code == 1
