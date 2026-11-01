"""Unit and integration tests for Gate 27 implementation plan completion auditing."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_implementation_summary import validate_summary_content
from tools.validate_implementation_summary_core.auditor import (
    audit_candidate_file,
    find_plan_candidates,
    run_check_all,
)


class ValidateImplementationPlanCompletionTests(unittest.TestCase):
    """Test suite asserting Gate 27 audits implementation plans for complete waves and valid receipts."""

    def test_complete_plan_with_receipts_passes_audit(self) -> None:
        """Verify an implementation plan with all COMPLETE waves and valid receipts passes."""
        plan_content = """# Hardening Implementation Plan

Status: ACTIVE.

| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Lock Mutex Guardian | ✅ COMPLETE | Worktree lock inspection | `reports/wave1_receipt.json` |
| Wave 2 | IPC Daemon Reaper | ✅ COMPLETE | Daemon cleanup & socket unlinking | `reports/wave2_receipt.json` |

## Wave 1: Lock Mutex Guardian (COMPLETE)
Detailed wave 1 notes.

## Wave 2: IPC Daemon Reaper (COMPLETE)
Detailed wave 2 notes.
"""
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_file = tmp_path / "implementation_plan.md"
            plan_file.write_text(plan_content, encoding="utf-8")

            passed, errors = audit_candidate_file(plan_file, tmp_path, validate_summary_content)
            self.assertTrue(passed, f"Expected audit to pass, got errors: {errors}")
            self.assertEqual(len(errors), 0)

    def test_plan_with_open_wave_fails_audit(self) -> None:
        """Verify an implementation plan with OPEN wave fails audit with clear diagnostics."""
        plan_content = """# Incomplete Implementation Plan

| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Core Foundation | ✅ COMPLETE | Core models & schema | `reports/wave1_receipt.json` |
| Wave 2 | API Endpoint | ⏳ OPEN | Endpoint router & tests | Pending Wave 2 |

## Wave 1: Core Foundation (COMPLETE)
Done.

## Wave 2: API Endpoint
Not done yet.
"""
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_file = tmp_path / "implementation_plan.md"
            plan_file.write_text(plan_content, encoding="utf-8")

            passed, errors = audit_candidate_file(plan_file, tmp_path, validate_summary_content)
            self.assertFalse(passed)
            self.assertTrue(any("Wave 2 is not marked COMPLETE" in e for e in errors))
            self.assertTrue(any("Wave 2 lacks valid runtime receipt" in e for e in errors))

    def test_plan_with_blocked_wave_fails_audit(self) -> None:
        """Verify an implementation plan with BLOCKED wave fails audit."""
        plan_content = """# Blocked Plan

| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Gatekeeper Auth | 🚫 BLOCKED | Blocked on credentials | None |
"""
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_file = tmp_path / "implementation_plan.md"
            plan_file.write_text(plan_content, encoding="utf-8")

            passed, errors = audit_candidate_file(plan_file, tmp_path, validate_summary_content)
            self.assertFalse(passed)
            self.assertTrue(any("Wave 1 is not marked COMPLETE" in e for e in errors))

    def test_complete_status_with_pending_or_missing_receipt_fails(self) -> None:
        """Verify a wave marked COMPLETE but having pending/placeholder receipt fails."""
        for placeholder in ("Pending Wave 1", "TODO: run tests", "None", "TBD", "N/A", ""):
            plan_content = f"""# Suspicious Plan

| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Unverified Wave | ✅ COMPLETE | Fake deliverable description | {placeholder} |
"""
            with TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                plan_file = tmp_path / "implementation_plan.md"
                plan_file.write_text(plan_content, encoding="utf-8")

                passed, errors = audit_candidate_file(plan_file, tmp_path, validate_summary_content)
                self.assertFalse(passed, f"Expected failure for placeholder '{placeholder}'")
                self.assertTrue(any("lacks valid runtime receipt" in e for e in errors))

    def test_run_check_all_discovers_and_blocks_on_open_plan(self) -> None:
        """Verify run_check_all in a directory with open plan returns exit code 1."""
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_dir = tmp_path / "plans" / "feat_test"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "implementation_plan.md"
            plan_file.write_text(
                """# Test Plan
| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Feature Implementation | ⏳ OPEN | Feature code and tests | Pending Wave 1 |
""",
                encoding="utf-8",
            )

            ret = run_check_all(require_summary=False, repo_root=tmp_path, validate_func=validate_summary_content)
            self.assertEqual(ret, 1)

    def test_run_check_all_passes_with_completed_plan_and_walkthrough(self) -> None:
        """Verify run_check_all in a directory with all completed waves returns exit code 0."""
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_dir = tmp_path / "plans" / "feat_test"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "implementation_plan.md"
            plan_file.write_text(
                """# Test Plan
| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Feature Implementation | ✅ COMPLETE | Verified deliverable code | `reports/receipt.json` |
""",
                encoding="utf-8",
            )
            walkthrough = tmp_path / "walkthrough.md"
            walkthrough.write_text(
                """# Walkthrough
| Wave Number | Description / Scope | Completion Status | Detailed Deliverable | Runtime Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Feature Implementation | ✅ COMPLETE | Code modules verified | `reports/receipt.json` |
""",
                encoding="utf-8",
            )

            ret = run_check_all(require_summary=False, repo_root=tmp_path, validate_func=validate_summary_content)
            self.assertEqual(ret, 0)

    def test_cli_check_all_fails_on_open_plan(self) -> None:
        """Verify CLI execution of tools/validate_implementation_summary.py --check-all fails on open plan."""
        py = sys.executable
        tool = ROOT / "tools" / "validate_implementation_summary.py"

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_dir = tmp_path / "plans" / "feat_test"
            plan_dir.mkdir(parents=True)
            plan_file = plan_dir / "implementation_plan.md"
            plan_file.write_text(
                """# Test Plan
| Wave | Scope | Status | Deliverable & Evidence | Receipt |
| :--- | :--- | :--- | :--- | :--- |
| Wave 1 | Feature Implementation | ⏳ OPEN | Incomplete code | Pending |
""",
                encoding="utf-8",
            )

            res = subprocess.run(
                [py, "-B", str(tool), "--check-all", "--repo-root", str(tmp_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("VALIDATION FAILED", res.stdout)
            self.assertIn("is not marked COMPLETE", res.stdout)

    def test_cli_file_with_require_all_complete_validates_receipts(self) -> None:
        """Verify CLI --file with --require-all-complete blocks pending receipts."""
        py = sys.executable
        tool = ROOT / "tools" / "validate_implementation_summary.py"

        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_file = tmp_path / "plan.md"
            plan_file.write_text(
                """| Wave Number | Description / Scope | Completion Status | Runtime Receipt |
| :--- | :--- | :--- | :--- |
| Wave 1 | Implemented without receipt | ✅ COMPLETE | Pending |
""",
                encoding="utf-8",
            )

            res = subprocess.run(
                [py, "-B", str(tool), "--file", str(plan_file), "--require-all-complete"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 1)
            self.assertIn("lack valid runtime receipts", res.stdout)


if __name__ == "__main__":
    unittest.main()
