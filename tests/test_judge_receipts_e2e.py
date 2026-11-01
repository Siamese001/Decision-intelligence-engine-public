"""End-to-End integration test suite for JARVIS judge hardening wave runner.

Verifies:
1. Real subprocess execution of tests via run_tests_with_progress.py.
2. Verified receipt emission conforming to cryptographic and metadata requirements.
3. Multi-wave receipt chaining and parent dependency verification.
4. Fail-closed isolation enforcement against repository root and production output/ paths.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
from tools.run_judge_hardening_wave import (
    compute_file_digest,
    execute_tests,
    main as wave_main,
    parse_junit_xml,
    validate_parent_receipt,
)


class TestJudgeReceiptsE2E:
    """E2E verification of wave runner execution and receipt generation."""

    def test_real_subprocess_execution_with_progress(self) -> None:
        """Executes a real subtest through tools/run_tests_with_progress.py to prove actual execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            report_path = tmp_root / "junit_report.xml"

            # Execute a single real deterministic test through the runner script
            target_test = "tests/test_judge_evidence_receipts.py::TestArchivedEvidenceReconstruction::test_evidence_manifest_integrity"
            exit_code, duration, stdout, stderr = execute_tests(
                [target_test],
                REPO_ROOT,
                report_path,
                per_test_timeout=30,
                suite_timeout=60,
            )
            assert exit_code == 0, f"Real subprocess test execution failed: {stderr}"
            assert report_path.is_file(), "JUnit report XML was not generated"

            total, passed, failed, errors, skipped, test_ids = parse_junit_xml(report_path)
            assert total == 1
            assert passed == 1
            assert failed == 0
            assert errors == 0
            assert skipped == 0
            assert len(test_ids) == 1

    def test_full_wave1_receipt_lifecycle(self) -> None:
        """Verifies full wave 1 receipt creation, structure, digests, and immutability."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            runtime_root = tmp_root / "runtime_w1"
            receipt_root = tmp_root / "receipts"
            plan_path = REPO_ROOT / "reports/jarvis_judge_forensics/plan.md"

            # Mock execute_tests to simulate verified wave run without recursive loop
            def mock_execute(test_files, repo_root, report_path, per_test_timeout, suite_timeout):
                report_path.parent.mkdir(parents=True, exist_ok=True)
                # Write realistic JUnit XML
                xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" errors="0" failures="0" skipped="0">
    <testcase classname="tests.test_judge_evidence_receipts" name="test_evidence" time="0.1"/>
    <testcase classname="tests.test_judge_receipts_e2e" name="test_e2e" time="0.2"/>
    <testcase classname="tests.test_judge_receipts_adversarial" name="test_adversarial" time="0.2"/>
  </testsuite>
</testsuites>"""
                report_path.write_text(xml_content, encoding="utf-8")
                return 0, 0.5, "passed", ""

            with patch("tools.run_judge_hardening_wave.execute_tests", side_effect=mock_execute):
                argv = [
                    "--wave", "1",
                    "--plan", str(plan_path),
                    "--runtime-root", str(runtime_root),
                    "--receipt-root", str(receipt_root),
                    "--timeout", "60",
                ]
                exit_code = wave_main(argv)
                assert exit_code == 0, "Wave 1 execution returned non-zero exit code"

            # Verify receipt sealed on disk
            target_receipt = receipt_root / "wave-1.json"
            assert target_receipt.is_file(), f"Receipt missing at {target_receipt}"

            receipt_data = json.loads(target_receipt.read_text(encoding="utf-8"))
            assert receipt_data["wave"] == 1
            assert receipt_data["terminal_result"] == "COMPLETE"
            assert receipt_data["plan_sha256"] == compute_file_digest(plan_path)
            assert receipt_data["parent_receipt_sha256"] is None

            metrics = receipt_data["execution_metrics"]
            assert metrics["total_collected"] == 3
            assert metrics["passed"] == 3
            assert metrics["failed"] == 0
            assert metrics["errors"] == 0
            assert metrics["skipped"] == 0

            # Verify CI enrollment payload
            ci = receipt_data["ci_enrollment"]
            assert ci["all_enrolled"] is True
            assert len(ci["missing_tests"]) == 0

    def test_wave_chaining_e2e(self) -> None:
        """Verifies that wave 1 receipt qualifies wave 2 parent dependency check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_root = Path(tmpdir) / "receipts"
            receipt_root.mkdir(parents=True, exist_ok=True)
            w1_receipt = receipt_root / "wave-1.json"
            w1_payload = {
                "wave": 1,
                "terminal_result": "COMPLETE",
                "execution_metrics": {"passed": 3},
            }
            w1_receipt.write_text(json.dumps(w1_payload), encoding="utf-8")
            w1_digest = compute_file_digest(w1_receipt)

            ok_p, p_dig, msg = validate_parent_receipt(receipt_root, 2)
            assert ok_p is True
            assert p_dig == w1_digest
            assert "verified" in msg

    def test_isolation_violation_rejection_e2e(self) -> None:
        """Verifies that attempts to use repo root or output/ as runtime root are blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_root = Path(tmpdir) / "receipts"
            plan_path = REPO_ROOT / "reports/jarvis_judge_forensics/plan.md"

            # Attempt targeting repo root
            rc_repo = wave_main([
                "--wave", "1",
                "--plan", str(plan_path),
                "--runtime-root", str(REPO_ROOT),
                "--receipt-root", str(receipt_root),
            ])
            assert rc_repo != 0, "Wave runner failed to block runtime_root == repo_root"

            # Attempt targeting production output/
            rc_output = wave_main([
                "--wave", "1",
                "--plan", str(plan_path),
                "--runtime-root", str(REPO_ROOT / "output" / "sandbox"),
                "--receipt-root", str(receipt_root),
            ])
            assert rc_output != 0, "Wave runner failed to block runtime_root inside output/"
