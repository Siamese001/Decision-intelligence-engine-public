"""
Adversarial test suite for rogue commit prevention, core.bare tamper protection,
and worktree registry governance.
"""
from __future__ import annotations

import argparse
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.pr_governance_core.branch_integrity import run_branch_and_worktree_governance
from tools.pr_governance_core.registry import verify_worktree_registry
from tools.pr_governance_core.worktree import cmd_start


class TestDirectToMainCommitPreventionAdversarial(unittest.TestCase):
    """Adversarial tests verifying that direct commits to main are blocked fail-closed."""

    @patch("subprocess.run")
    def test_direct_commit_on_main_blocked(self, mock_run: MagicMock) -> None:
        """TC-ADV-01: Direct commit attempt on branch 'main' without merge context must fail."""
        def side_effect(cmd, **kwargs):
            if "core.bare" in cmd:
                return MagicMock(returncode=0, stdout="false\n")
            if "symbolic-ref" in cmd:
                return MagicMock(returncode=0, stdout="main\n")
            if "MERGE_HEAD" in cmd:
                return MagicMock(returncode=1, stdout="")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect
        with patch.dict(os.environ, {}, clear=True):
            passed, output, _ = run_branch_and_worktree_governance(Path("/tmp/fake_repo"))
            self.assertFalse(passed)
            self.assertIn("[DIRECT_MAIN_COMMIT_BLOCKED]", output)
            self.assertIn("Direct commits to 'main' are strictly prohibited", output)

    @patch("subprocess.run")
    def test_governed_merge_on_main_permitted_via_merge_head(self, mock_run: MagicMock) -> None:
        """TC-ADV-02: Commit on 'main' with active MERGE_HEAD must pass."""
        def side_effect(cmd, **kwargs):
            if "core.bare" in cmd:
                return MagicMock(returncode=0, stdout="false\n")
            if "symbolic-ref" in cmd:
                return MagicMock(returncode=0, stdout="main\n")
            if "MERGE_HEAD" in cmd:
                return MagicMock(returncode=0, stdout="a1b2c3d4e5\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect
        with patch.dict(os.environ, {}, clear=True):
            passed, output, _ = run_branch_and_worktree_governance(Path("/tmp/fake_repo"))
            self.assertTrue(passed)
            self.assertIn("Branch & Worktree Governance: PASS", output)

    @patch("subprocess.run")
    def test_governed_merge_on_main_permitted_via_env(self, mock_run: MagicMock) -> None:
        """TC-ADV-03: Commit on 'main' with GOVERNED_PR_MERGE=1 must pass."""
        def side_effect(cmd, **kwargs):
            if "core.bare" in cmd:
                return MagicMock(returncode=0, stdout="false\n")
            if "symbolic-ref" in cmd:
                return MagicMock(returncode=0, stdout="main\n")
            if "MERGE_HEAD" in cmd:
                return MagicMock(returncode=1, stdout="")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect
        with patch.dict(os.environ, {"GOVERNED_PR_MERGE": "1"}):
            passed, output, _ = run_branch_and_worktree_governance(Path("/tmp/fake_repo"))
            self.assertTrue(passed)
            self.assertIn("Branch & Worktree Governance: PASS", output)

    @patch("subprocess.run")
    def test_feature_worktree_branch_permitted(self, mock_run: MagicMock) -> None:
        """TC-ADV-04: Commits on non-main branches proceed normally."""
        def side_effect(cmd, **kwargs):
            if "core.bare" in cmd:
                return MagicMock(returncode=0, stdout="false\n")
            if "symbolic-ref" in cmd:
                return MagicMock(returncode=0, stdout="feat/my-feature\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect
        passed, output, _ = run_branch_and_worktree_governance(Path("/tmp/fake_repo"))
        self.assertTrue(passed)


class TestCoreBareTamperProtectionAdversarial(unittest.TestCase):
    """Adversarial tests verifying core.bare freeze defense."""

    @patch("subprocess.run")
    def test_core_bare_true_blocked_in_gatekeeper(self, mock_run: MagicMock) -> None:
        """TC-ADV-05: core.bare=true must fail gatekeeper immediately with prescriptive banner."""
        def side_effect(cmd, **kwargs):
            if "core.bare" in cmd:
                return MagicMock(returncode=0, stdout="true\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect
        passed, output, _ = run_branch_and_worktree_governance(Path("/tmp/fake_repo"))
        self.assertFalse(passed)
        self.assertIn("[CORE_BARE_VIOLATION]", output)
        self.assertIn("git config core.bare false", output)

    @patch("subprocess.run")
    def test_core_bare_true_blocked_in_worktree_hygiene(self, mock_run: MagicMock) -> None:
        """TC-ADV-06: core.bare=true must be flagged as violation in worktree registry audit."""
        def side_effect(cmd, **kwargs):
            if "core.bare" in cmd:
                return MagicMock(returncode=0, stdout="true\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect
        violations = verify_worktree_registry(Path("/tmp/fake_repo"), [])
        self.assertTrue(any("[CORE_BARE_VIOLATION]" in v for v in violations))


class TestWorktreeRegistryGovernanceAdversarial(unittest.TestCase):
    """Adversarial tests verifying 5 Non-Negotiable Invariants on worktree registry."""

    def setUp(self) -> None:
        self.repo_root = Path("/tmp/fake_repo").resolve()

    @patch("subprocess.run")
    def test_secondary_worktree_main_branch_rejected(self, mock_run: MagicMock) -> None:
        """TC-ADV-07: INV-WT-01 - Secondary worktree cannot check out 'main'."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
        fake_wts = [
            {"path": str(self.repo_root), "head": "abc", "branch": "refs/heads/main"},
            {"path": str(self.repo_root / ".worktrees" / "main_checkout"), "head": "def", "branch": "refs/heads/main"},
        ]
        violations = verify_worktree_registry(self.repo_root, fake_wts)
        self.assertTrue(any("INV-WT-01 Violation" in v for v in violations))

    @patch("subprocess.run")
    def test_worktree_named_main_merge_rejected(self, mock_run: MagicMock) -> None:
        """TC-ADV-08: INV-WT-01 - Worktree named main_merge is strictly forbidden."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
        fake_wts = [
            {"path": str(self.repo_root), "head": "abc", "branch": "refs/heads/main"},
            {"path": str(self.repo_root / ".worktrees" / "main_merge"), "head": "def", "branch": "refs/heads/feat/patch"},
        ]
        violations = verify_worktree_registry(self.repo_root, fake_wts)
        self.assertTrue(any("INV-WT-01 Violation" in v for v in violations))

    @patch("subprocess.run")
    def test_non_canonical_location_rejected(self, mock_run: MagicMock) -> None:
        """TC-ADV-09: INV-WT-02 - Worktree outside .worktrees/ must fail."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
        fake_wts = [
            {"path": str(self.repo_root), "head": "abc", "branch": "refs/heads/main"},
            {"path": "/tmp/arbitrary_dir/feat_my_work", "head": "def", "branch": "refs/heads/feat/my-work"},
        ]
        violations = verify_worktree_registry(self.repo_root, fake_wts)
        self.assertTrue(any("INV-WT-02 Violation" in v for v in violations))

    @patch("subprocess.run")
    def test_detached_worktree_rejected(self, mock_run: MagicMock) -> None:
        """TC-ADV-10: INV-WT-05 - Detached HEAD worktree must fail."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
        fake_wts = [
            {"path": str(self.repo_root), "head": "abc", "branch": "refs/heads/main"},
            {"path": str(self.repo_root / ".worktrees" / "feat_detached"), "head": "def", "branch": ""},
        ]
        violations = verify_worktree_registry(self.repo_root, fake_wts)
        self.assertTrue(any("INV-WT-05 Violation" in v for v in violations))

    @patch("subprocess.run")
    def test_non_conventional_branch_rejected(self, mock_run: MagicMock) -> None:
        """TC-ADV-11: INV-WT-04 - Non-conventional branch names must fail."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
        fake_wts = [
            {"path": str(self.repo_root), "head": "abc", "branch": "refs/heads/main"},
            {"path": str(self.repo_root / ".worktrees" / "temp_experiment"), "head": "def", "branch": "refs/heads/temp_experiment"},
        ]
        violations = verify_worktree_registry(self.repo_root, fake_wts)
        self.assertTrue(any("INV-WT-04 Violation" in v for v in violations))

    @patch("subprocess.run")
    def test_slug_mismatch_rejected(self, mock_run: MagicMock) -> None:
        """TC-ADV-12: INV-WT-03 - Directory slug mismatch must fail."""
        mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
        fake_wts = [
            {"path": str(self.repo_root), "head": "abc", "branch": "refs/heads/main"},
            {"path": str(self.repo_root / ".worktrees" / "random_directory_name"), "head": "def", "branch": "refs/heads/feat/correct-slug"},
        ]
        violations = verify_worktree_registry(self.repo_root, fake_wts)
        self.assertTrue(any("INV-WT-03 Violation" in v for v in violations))

    def test_cmd_start_rejects_main_branches(self) -> None:
        """TC-ADV-13: pr_governance start must refuse to create worktrees for main or main_merge."""
        for illegal in ("main", "master", "main_merge", "feat/main_merge_v2"):
            args = argparse.Namespace(branch=illegal)
            ret = cmd_start(args)
            self.assertEqual(ret, 1)


class TestHookScriptIntegrityAdversarial(unittest.TestCase):
    """Adversarial tests verifying that git hook files contain mandatory gates."""

    def test_pre_commit_contains_gates(self) -> None:
        """TC-ADV-14: .githooks/pre-commit must contain Gate 0A, Gate 0B, and env scrubbing."""
        hook_path = Path(__file__).resolve().parents[2] / ".githooks" / "pre-commit"
        self.assertTrue(hook_path.exists())
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("IS_BARE=$(git config --bool core.bare", content)
        self.assertIn("Direct commits to '$CURRENT_BRANCH' are strictly prohibited", content)
        common_path = hook_path.parent / "_common.sh"
        full_content = content + "\n" + (common_path.read_text(encoding="utf-8") if common_path.exists() else "")
        self.assertIn("unset GIT_DIR GIT_WORK_TREE", full_content)

    def test_commit_msg_contains_gates(self) -> None:
        """TC-ADV-15: .githooks/commit-msg must source _common.sh and run lint_commit_message.py."""
        hook_path = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"
        self.assertTrue(hook_path.exists())
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("_common.sh", content)
        self.assertIn("lint_commit_message.py", content)


if __name__ == "__main__":
    unittest.main()
