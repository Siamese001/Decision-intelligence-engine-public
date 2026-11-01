"""
Unit and Adversarial Test Suite for Wave 2:
Zero-Commit Stale Worktree & Hunk-Absorption Deprecation Engine.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from tools.pr_governance_core.code_review_rules import (
    ChangeClass,
    DeterministicSignals,
    ReviewVerdict,
    evaluate_review_verdict,
)
from tools.pr_governance_core.diff_semantics import (
    _check_recent_merge_overlap,
)
from tools.pr_governance_core.queue_reconcile import (
    reconcile_review,
    tier_from_disposition,
    tombstone_next_action,
)
from tools.pr_topology_core.disposition import (
    BranchDisposition,
    _check_ahead_zero_worktree_safety,
    _check_waves_disposition,
    _evaluate_ahead_zero_branch,
)
from tools.pr_topology_core.matrix import BranchDiff


class TestZeroCommitStaleWorktreeDeprecation(unittest.TestCase):
    """Verifies that zero-commit stale worktrees do not retain perpetual KEEP immunity."""

    def test_waves_disposition_bypasses_keep_for_stale_ahead_zero(self):
        diff = BranchDiff(branch="feat/judge-gold-set-calibration", ahead=0, behind=185, is_unknown=False)
        waves_info = {"feat/judge-gold-set-calibration": {"open_waves": "?"}}
        wt_path = Path("/tmp/wt_judge_gold")

        # Must return None so downstream ahead-zero evaluation can prune the branch
        disp = _check_waves_disposition("feat/judge-gold-set-calibration", wt_path, waves_info, diff=diff)
        self.assertIsNone(disp)

    def test_waves_disposition_allows_keep_for_fresh_inception(self):
        diff = BranchDiff(branch="feat/fresh-branch", ahead=0, behind=2, is_unknown=False)
        waves_info = {"feat/fresh-branch": {"open_waves": "?"}}
        wt_path = Path("/tmp/wt_fresh")

        # Behind < 20 and open_waves '?' retains inception KEEP
        disp = _check_waves_disposition("feat/fresh-branch", wt_path, waves_info, diff=diff)
        self.assertIsNotNone(disp)
        self.assertEqual(disp.action, "KEEP")
        self.assertIn("Inception planning in progress", disp.reason)

    def test_waves_disposition_stale_with_active_wave_marker_bypasses_keep(self):
        diff = BranchDiff(branch="feat/stale-active-plan", ahead=0, behind=50, is_unknown=False)
        waves_info = {"feat/stale-active-plan": {"open_waves": "1"}}
        wt_path = Path("/tmp/wt_stale")

        disp = _check_waves_disposition("feat/stale-active-plan", wt_path, waves_info, diff=diff)
        self.assertIsNone(disp)

    def test_evaluate_ahead_zero_stale_branch_yields_prune(self):
        diff = BranchDiff(branch="feat/judge-gold-set-calibration", ahead=0, behind=185, is_unknown=False)
        wt = {"path": "/tmp/wt_judge", "dirty": False, "stash_count": 0}
        waves_info = {"feat/judge-gold-set-calibration": {"open_waves": "?"}}
        root = Path("/tmp")

        with unittest.mock.patch("tools.pr_topology_core.birth.inspect_branch_birth") as mock_birth, \
             unittest.mock.patch("tools.pr_topology_core.disposition.run_git") as mock_git, \
             unittest.mock.patch("tools.pr_topology_core.disposition.is_ancestor", return_value=True), \
             unittest.mock.patch("tools.pr_topology_core.disposition.evaluate_commit_supersession", return_value=(None, "", {})):
            mock_birth.return_value = MagicMock(is_inception_active=False, age_desc="14 days old")
            mock_git.return_value = "deadbeef"

            disp = _evaluate_ahead_zero_branch(
                branch="feat/judge-gold-set-calibration",
                diff=diff,
                wt=wt,
                waves_info=waves_info,
                root=root,
                main_sha="mainsha0",
                main_tree="maintree0",
            )
            self.assertEqual(disp.action, "PRUNE")
            self.assertIn("ahead==0 (No unique commits vs main, abandoned zero-commit branch)", disp.reason)
            self.assertNotIn("Absorbed into main", disp.reason)

    def test_all_six_stale_worktrees_tier_d_from_prune(self):
        stale_branches = [
            "feat/judge-gold-set-calibration",
            "feat/rca-no-false-positive-retries",
            "feat/rca-output1-judge-hitl",
            "feat/rca-speaker-attribution-mutation-bypass",
            "feat/reconcile-sherpa-connect-transcription",
            "feat/technical-debt-elimination-automation",
        ]
        for branch in stale_branches:
            tier = tier_from_disposition("P2.1", action="PRUNE")
            self.assertEqual(tier, "D.1", f"Branch {branch} with PRUNE disposition must map to D.1 tier")


class TestHunkAbsorptionReviewRules(unittest.TestCase):
    """Verifies that code review verdicts properly consume hunk absorption signals."""

    def test_hunk_absorption_above_85_emits_superseded_tombstone(self):
        signals = DeterministicSignals(
            files_changed=2,
            hunk_absorption_ratio=0.88,
            class_counts={ChangeClass.PRODUCTION_CODE.value: 2},
        )
        verdict, reason, action = evaluate_review_verdict(signals, is_clean_merge=True, branch_name="feat/absorbed")
        self.assertEqual(verdict, ReviewVerdict.SUPERSEDED_TOMBSTONE)
        self.assertIn("88% hunk absorption", reason)
        self.assertIn("[SUPERSEDED_TOMBSTONE]", action)

    def test_hunk_absorption_boundary_85_exact_emits_superseded_tombstone(self):
        signals = DeterministicSignals(
            files_changed=1,
            hunk_absorption_ratio=0.85,
            class_counts={ChangeClass.PRODUCTION_CODE.value: 1},
        )
        verdict, reason, action = evaluate_review_verdict(signals, is_clean_merge=True, branch_name="feat/boundary")
        self.assertEqual(verdict, ReviewVerdict.SUPERSEDED_TOMBSTONE)
        self.assertIn("85% hunk absorption", reason)

    def test_hunk_absorption_partial_salvage_subset(self):
        signals = DeterministicSignals(
            files_changed=3,
            hunk_absorption_ratio=0.55,
            class_counts={ChangeClass.PRODUCTION_CODE.value: 3},
        )
        verdict, reason, action = evaluate_review_verdict(signals, is_clean_merge=True, branch_name="feat/partial")
        self.assertEqual(verdict, ReviewVerdict.REVIEW_READY)
        self.assertIn("Partial hunk absorption", reason)
        self.assertIn("[SALVAGE_DELIVERABLES]", action)
        self.assertIn("55% already absorbed into main", action)

    def test_hunk_absorption_below_30_not_salvage_flagged(self):
        signals = DeterministicSignals(
            files_changed=2,
            hunk_absorption_ratio=0.15,
            has_test_changes=True,
            class_counts={ChangeClass.PRODUCTION_CODE.value: 1, ChangeClass.TEST_CODE.value: 1},
        )
        verdict, reason, action = evaluate_review_verdict(signals, is_clean_merge=True, branch_name="feat/active")
        self.assertEqual(verdict, ReviewVerdict.MERGE_READY)
        self.assertIn("[MERGE_READY]", action)


class TestQueueReconciliationHunkPreservation(unittest.TestCase):
    """Verifies that SUPERSEDED_TOMBSTONE is preserved when hunk evidence exists without cherry commits."""

    def test_reconcile_review_preserves_tombstone_on_hunk_evidence(self):
        sig = {
            "commits_in_main": 0,
            "unique_production_commits": 3,
            "hunk_absorption_ratio": 0.92,
        }
        recon = reconcile_review("SUPERSEDED_TOMBSTONE", ahead=3, dirty=False, signals=sig)
        self.assertEqual(recon.verdict, "SUPERSEDED_TOMBSTONE")
        self.assertTrue(recon.tombstone_evidence)
        self.assertIn("hunk absorption 92%", recon.note)

    def test_reconcile_review_reverts_tombstone_when_neither_cherry_nor_hunk_proven(self):
        sig = {
            "commits_in_main": 0,
            "unique_production_commits": 2,
            "hunk_absorption_ratio": 0.40,
        }
        recon = reconcile_review("SUPERSEDED_TOMBSTONE", ahead=2, dirty=False, signals=sig)
        self.assertEqual(recon.verdict, "REVIEW_READY")
        self.assertFalse(recon.tombstone_evidence)

    def test_tombstone_next_action_renders_hunk_percentage(self):
        sig = {"hunk_absorption_ratio": 0.95}
        action = tombstone_next_action(sig)
        self.assertIn("[CONFIRM_TOMBSTONE] 95% of code hunks already absorbed into main", action)

    def test_tombstone_next_action_fallback_to_cherry_when_no_hunk(self):
        sig = {"commits_in_main": 3, "unique_commits": 1, "hunk_absorption_ratio": 0.0}
        action = tombstone_next_action(sig)
        self.assertIn("3 commit(s) already in main (git cherry)", action)


class TestDirectHunkSurvivalDetection(unittest.TestCase):
    """Verifies direct hunk survival against main:<path> in _check_recent_merge_overlap."""

    def test_direct_hunk_survival_triggers_superseded_code(self):
        def runner(cmd, root):
            if "merge-base" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="base123\n")
            if "log" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="")
            if "-U0" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="@@ -1 +1 @@\n+line_a\n+line_b\n")
            if "show" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="header\nline_a\nline_b\nfooter\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        is_sup, reason = _check_recent_merge_overlap("feat/test-absorbed", {"tools/foo.py"}, Path("/tmp"), git_runner=runner)
        self.assertTrue(is_sup)
        self.assertIn("hunk absorption: 100.0%", reason)

    def test_direct_hunk_survival_not_superseded_when_lines_missing_on_main(self):
        def runner(cmd, root):
            if "merge-base" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="base123\n")
            if "log" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="")
            if "-U0" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="@@ -1 +1 @@\n+brand_new_logic_alpha\n")
            if "show" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="completely_unrelated_main_code\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        is_sup, reason = _check_recent_merge_overlap("feat/active-work", {"tools/bar.py"}, Path("/tmp"), git_runner=runner)
        self.assertFalse(is_sup)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
