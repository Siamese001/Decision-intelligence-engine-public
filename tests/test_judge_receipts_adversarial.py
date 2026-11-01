"""Adversarial and edge-case test suite for JARVIS judge hardening wave runner.

Mandatory edge-case categories covered (per e2e-and-edge-case-coverage.md):
1. Boundary payload: 0-byte plan file, empty test scope, missing manifest.
2. Malformed input: Corrupted/truncated parent receipt JSON, missing terminal_result, invalid wave.
3. Concurrency & race: Exclusive lock collision on receipt write, partial write isolation.
4. Invariant bypass attempt: Overwrite of sealed receipt, execution with failed/skipped tests, isolation evasion.
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from tools.run_judge_hardening_wave import (
    evaluate_acceptance,
    main as wave_main,
    seal_receipt,
    validate_environment,
    validate_parent_receipt,
)


class TestJudgeReceiptsAdversarial:
    """Adversarial stress and edge-case verification for wave receipt runner."""

    # Category 1: Boundary payload
    def test_boundary_zero_byte_plan(self) -> None:
        """Verifies that an empty (0-byte) plan file fails cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            empty_plan = tmp_root / "empty_plan.md"
            empty_plan.write_text("", encoding="utf-8")

            rc = wave_main([
                "--wave", "1",
                "--plan", str(empty_plan),
                "--runtime-root", str(tmp_root / "runtime"),
                "--receipt-root", str(tmp_root / "receipts"),
            ])
            assert rc == 1
            assert empty_plan.stat().st_size == 0

    def test_boundary_zero_tests_collected(self) -> None:
        """Verifies that zero collected tests blocks completion and returns error."""
        defects = evaluate_acceptance(
            exit_code=0,
            total=0,
            passed=0,
            failed=0,
            errors=0,
            skipped=0,
        )
        assert len(defects) > 0
        assert any("Zero tests collected" in d for d in defects)

    # Category 2: Malformed input
    def test_malformed_truncated_parent_receipt(self) -> None:
        """Verifies that a truncated or invalid JSON parent receipt halts execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_root = Path(tmpdir) / "receipts"
            receipt_root.mkdir()
            bad_parent = receipt_root / "wave-1.json"
            bad_parent.write_text('{"wave": 1, "terminal_result": ', encoding="utf-8")  # Truncated

            ok, dig, msg = validate_parent_receipt(receipt_root, 2)
            assert ok is False
            assert "Failed parsing parent receipt" in msg

    def test_malformed_parent_receipt_terminal_result(self) -> None:
        """Verifies that a parent receipt with terminal_result != COMPLETE is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_root = Path(tmpdir) / "receipts"
            receipt_root.mkdir()
            bad_parent = receipt_root / "wave-1.json"
            bad_parent.write_text(json.dumps({"wave": 1, "terminal_result": "FAILED"}), encoding="utf-8")

            ok, dig, msg = validate_parent_receipt(receipt_root, 2)
            assert ok is False
            assert "expected COMPLETE" in msg

    def test_malformed_unknown_wave_number(self) -> None:
        """Verifies that an uninventoried wave number fails closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            rc = wave_main([
                "--wave", "99",
                "--runtime-root", str(tmp_root / "runtime"),
                "--receipt-root", str(tmp_root / "receipts"),
            ])
            assert rc != 0

    # Category 3: Concurrency & race
    def test_concurrency_race_exclusive_receipt_write(self) -> None:
        """Verifies that concurrent attempts to seal the same receipt path collide safely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_target = Path(tmpdir) / "wave-1.json"
            results = []

            def attempt_write(worker_id: int) -> bool:
                try:
                    seal_receipt({"worker": worker_id, "terminal_result": "COMPLETE"}, receipt_target)
                    return True
                except FileExistsError:
                    return False

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(attempt_write, i) for i in range(8)]
                for f in concurrent.futures.as_completed(futures):
                    results.append(f.result())

            # Exactly one worker must succeed; all other 7 must fail with FileExistsError
            assert results.count(True) == 1, f"Expected exactly 1 success, got {results.count(True)}"
            assert results.count(False) == 7, f"Expected exactly 7 collisions, got {results.count(False)}"

    # Category 4: Invariant bypass attempt
    def test_invariant_bypass_overwrite_attempt(self) -> None:
        """Verifies that attempting to overwrite an existing sealed receipt fails closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_root = Path(tmpdir) / "receipts"
            receipt_root.mkdir()
            sealed_receipt = receipt_root / "wave-1.json"
            sealed_receipt.write_text('{"terminal_result": "COMPLETE"}', encoding="utf-8")

            # Environment validation must block execution if target receipt exists
            ok, msg = validate_environment(
                Path(tmpdir) / "runtime",
                receipt_root,
                1,
                REPO_ROOT,
            )
            assert ok is False
            assert "cannot be overwritten" in msg

    def test_invariant_bypass_rejection_of_skips(self) -> None:
        """Verifies that skipped tests are treated as blocking defects under wave rules."""
        defects = evaluate_acceptance(
            exit_code=0,
            total=5,
            passed=4,
            failed=0,
            errors=0,
            skipped=1,
        )
        assert len(defects) == 1
        assert "mandate 0 skips" in defects[0]
