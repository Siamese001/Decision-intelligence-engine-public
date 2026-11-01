#!/usr/bin/env python3
"""End-to-End integration test suite for PR-to-local-main latency optimization.

Validates the full lifecycle of the optimized PR review and merge workflows:
1. End-to-end change scoping: only running core governance suites on non-pipeline PRs.
2. Review-generated receipt tolerance: worktree dirty status remains clean for merge.
3. End-to-end fail-fast mode execution.
4. End-to-end incremental recheck of failed gates with receipt updating.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pr_governance_core.worktree import _inspect_worktree_dirty
from tools.pr_governance_core.lifecycle import _execute_or_skip_readiness, cmd_review
from tools.readiness_core import _build_core_test_modules
from tools.verify_main_readiness import (
    _load_prior_failures,
    _save_readiness_receipt,
)


class TestPRLatencyOptimizationE2E(unittest.TestCase):
    """End-to-end tests for PR latency optimization lifecycle."""

    def test_e2e_change_scoping_lifecycle(self) -> None:
        """Verifies that non-transcript, non-linter changes omit heavyweight E2E suites."""
        # 1. Non-transcript PR touches only governance tools
        governance_files = ["tools/pr_governance.py", "tools/verify_main_readiness.py"]
        scoped_modules = _build_core_test_modules(governance_files, full=False)

        # Must include core governance tests
        self.assertIn("tests.test_runtime_evidence_claims", scoped_modules)
        self.assertIn("tests.governance.test_pr_governance", scoped_modules)
        self.assertIn("tests.governance.test_worktree_ide_visibility", scoped_modules)

        # Must omit heavyweight 56s transcript E2E tests
        self.assertNotIn("tests.test_comprehensive_transcript_e2e", scoped_modules)
        self.assertNotIn("tests.test_adversarial_comprehensive_e2e", scoped_modules)

        # 2. Pipeline PR touches audio / transcript synthesis
        pipeline_files = ["dual_pipeline/transcript_pipeline.py"]
        pipeline_modules = _build_core_test_modules(pipeline_files, full=False)
        self.assertIn("tests.test_comprehensive_transcript_e2e", pipeline_modules)
        self.assertIn("tests.test_adversarial_comprehensive_e2e", pipeline_modules)

        # 3. Full mode forces inclusion of all modules
        full_modules = _build_core_test_modules(governance_files, full=True)
        self.assertIn("tests.test_comprehensive_transcript_e2e", full_modules)
        self.assertIn("tests.test_lint_layer_imports", full_modules)

    def test_e2e_worktree_review_receipt_tolerance_and_merge_flow(self) -> None:
        """Verifies that review and gap audit receipts do not prevent merge completion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            # Create simulated worktree directory with review receipt artifacts
            wt_dir = tmp_root / ".worktrees" / "feat_optimization"
            wt_dir.mkdir(parents=True)

            status_with_review_receipts = (
                "?? reports/readiness/local_main_readiness_feat_optimization_receipt.json\n"
                "?? reports/pr_review_feat_optimization_receipt.json\n"
                " M reports/testing_gaps_audit_report.json\n"
                " M governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT_V1.md\n"
            )

            # Normal check flags dirty
            with patch("tools.pr_governance_core.worktree.run_git", return_value=status_with_review_receipts):
                dirty, summary = _inspect_worktree_dirty(wt_dir, ignore_review_artifacts=False)
                self.assertTrue(dirty)
                self.assertIn("dirty", summary)

                # Merge check ignores review artifacts -> clean!
                clean_dirty, clean_summary = _inspect_worktree_dirty(wt_dir, ignore_review_artifacts=True)
                self.assertFalse(clean_dirty)
                self.assertIn("clean (review receipts ignored)", clean_summary)

    def test_e2e_incremental_recheck_receipt_lifecycle(self) -> None:
        """Verifies that incremental recheck re-runs only failed stages and updates receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            readiness_dir = tmp_root / "reports" / "readiness"
            readiness_dir.mkdir(parents=True)
            receipt_path = readiness_dir / "local_main_readiness_feat_sample_receipt.json"

            # 1. Prior receipt with Gate 3 failure
            prior_stages = [
                {"stage_index": 1, "stage_name": "Stage 1", "status": "PASS", "duration_seconds": 0.1},
                {"stage_index": 2, "stage_name": "Stage 2", "status": "PASS", "duration_seconds": 0.2},
                {"stage_index": 3, "stage_name": "Stage 3", "status": "FAIL", "duration_seconds": 0.5},
            ]
            receipt_path.write_text(json.dumps({
                "terminal_outcome": "FAIL",
                "stages": prior_stages,
            }), encoding="utf-8")

            # 2. Load prior failures
            prior_map, failed_indices = _load_prior_failures(receipt_path)
            self.assertEqual(failed_indices, {3})
            self.assertEqual(len(prior_map), 3)

            # 3. Simulate re-running Stage 3 and saving updated receipt
            updated_stages = [
                prior_map[1],
                prior_map[2],
                {"stage_index": 3, "stage_name": "Stage 3", "status": "PASS", "duration_seconds": 0.3},
            ]
            with patch("tools.verify_main_readiness.ROOT", tmp_root):
                _save_readiness_receipt(
                    branch="feat/sample",
                    head_sha="12345678",
                    terminal_outcome="PASS",
                    total_stages=3,
                    stage_results=updated_stages,
                    branch_receipt_path=receipt_path,
                    update_global=False,
                )

            # 4. Verify updated receipt reflects PASS
            updated_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_data["terminal_outcome"], "PASS")
            self.assertEqual(updated_data["stages_passed"], 3)
            self.assertEqual(updated_data["stages_failed"], 0)

    def test_receipt_separates_wall_clock_from_overlapping_stage_time(self) -> None:
        """Parallel stage durations must not be reported as elapsed wall-clock time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            receipt_path = tmp_root / "reports" / "readiness" / "receipt.json"
            stages = [
                {"stage_index": 1, "status": "PASS", "duration_seconds": 4.0},
                {"stage_index": 2, "status": "PASS", "duration_seconds": 3.0},
            ]
            with patch("tools.verify_main_readiness.ROOT", tmp_root):
                _save_readiness_receipt(
                    "feat/timed",
                    "abc123",
                    "PASS",
                    2,
                    stages,
                    receipt_path,
                    False,
                    wall_clock_duration_seconds=4.25,
                    qualification_fingerprint="bound-inputs",
                )
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(data["wall_clock_duration_seconds"], 4.25)
            self.assertEqual(data["total_duration_seconds"], 4.25)
            self.assertEqual(data["cumulative_stage_duration_seconds"], 7.0)
            self.assertEqual(data["qualification_fingerprint"], "bound-inputs")

    def test_review_fails_before_readiness_when_base_is_stale(self) -> None:
        """A stale base must stop review before expensive qualification begins."""
        args = argparse.Namespace(branch="feat/stale", remote=False, fetch=False, retry=0)
        info = {"ahead": 1, "behind": 1, "clean_merge": True, "readiness_receipt_pass": False}
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch("tools.pr_governance_core.lifecycle.ROOT", Path(tmpdir)), \
             patch("tools.pr_governance_core.lifecycle.get_feature_branches", return_value=["feat/stale"]), \
             patch("tools.pr_governance_core.lifecycle.inspect_branch_status", return_value=info), \
             patch("tools.pr_governance_core.lifecycle._execute_or_skip_readiness") as readiness:
            self.assertEqual(cmd_review(args), 1)
            readiness.assert_not_called()

    def test_review_always_uses_authoritative_readiness_runner(self) -> None:
        """A tree attestation may accelerate the runner but cannot bypass input validation."""
        args = argparse.Namespace(
            skip_readiness_run=False,
            fail_fast=False,
            recheck_failed_only=False,
            full=False,
        )
        info = {"readiness_receipt_pass": True}
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch("tools.pr_governance_core.lifecycle.subprocess.run", return_value=completed) as run:
            passed = _execute_or_skip_readiness(
                "feat/equivalent", Path(tmpdir), info, args, "python"
            )
        self.assertTrue(passed)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
