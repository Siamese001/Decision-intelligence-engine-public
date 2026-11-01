#!/usr/bin/env python3
"""
Unit and Governance Tests for Local PR Automation Tooling.
"""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tools.pr_governance import (
    CONVENTIONAL_BRANCH_RE,
    CONVENTIONAL_COMMIT_RE,
    check_merge_conflict,
    cmd_doctor,
    inspect_branch_status,
    slugify_branch,
)


class TestPRGovernanceValidation(unittest.TestCase):
    def test_slugify_branch(self) -> None:
        self.assertEqual(slugify_branch("feature/test-branch"), "feature_test_branch")
        self.assertEqual(slugify_branch("feat/foo.bar-baz"), "feat_foo_bar_baz")
        self.assertEqual(slugify_branch("main"), "main")

    def test_conventional_commit_regex(self) -> None:
        valid = [
            "feat: add new feature",
            "feat(tests): Wave 5 audit re-certification",
            "fix(linter): fix bug in parser",
            "docs(governance): update PR documentation",
            "refactor(core): decouple layers",
            "chore(receipt): update local main readiness receipt",
            "Merge branch 'foo' into main (Wave 1)",
        ]
        for msg in valid:
            self.assertIsNotNone(CONVENTIONAL_COMMIT_RE.match(msg), f"Should match: {msg}")

        invalid = [
            "wip",
            "fixed stuff",
            "update code",
            "feat without colon",
        ]
        for msg in invalid:
            self.assertIsNone(CONVENTIONAL_COMMIT_RE.match(msg), f"Should not match: {msg}")

    def test_conventional_branch_regex(self) -> None:
        valid_branches = [
            "feat/pre-commit-hardening",
            "feature/pre-commit-and-local-pr-governance-hardening",
            "enhance/prompt-optimization",
            "fix/bug-123",
            "audio/team-standup",
            "chore/receipt-update",
            "docs/pr-process-standard",
            "refactor/core-storage",
            "test/wave1-governance",
            "ci/pre-commit-gates",
            "ingest/meeting-team-sync",
            "ingest/doc-pricing-grid",
            "ingest/sheet-partner_advisory-rates",
        ]
        for b in valid_branches:
            self.assertIsNotNone(CONVENTIONAL_BRANCH_RE.match(b), f"Branch should be valid: {b}")

        invalid_branches = [
            "random_branch",
            "my_feature",
            "wip",
            "feat/",
            "unknown/foo",
        ]
        for b in invalid_branches:
            self.assertIsNone(CONVENTIONAL_BRANCH_RE.match(b), f"Branch should be invalid: {b}")

    def test_check_merge_conflict_clean(self) -> None:
        clean, _ = check_merge_conflict("main", "HEAD")
        self.assertTrue(clean)

    def test_inspect_branch_status_keys(self) -> None:
        status = inspect_branch_status("HEAD")
        self.assertIn("branch", status)
        self.assertIn("ahead", status)
        self.assertIn("behind", status)
        self.assertIn("clean_merge", status)
        self.assertIn("readiness_receipt_pass", status)
        self.assertTrue(status["clean_merge"])

    def test_branch_scoped_readiness_receipt_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            readiness_dir = tmp_root / "reports" / "readiness"
            readiness_dir.mkdir(parents=True)
            slug = "feat_test_sample"
            receipt_file = readiness_dir / f"local_main_readiness_{slug}_receipt.json"

            receipt_data = {
                "branch": "feat/test-sample",
                "terminal_outcome": "PASS", "head_sha": "abcd1234abcd1234",
                "stages_evaluated": 23,
                "stages_passed": 23,
            }
            with open(receipt_file, "w", encoding="utf-8") as f:
                json.dump(receipt_data, f)

            with patch("tools.pr_governance_core.inspection.ROOT", tmp_root), \
                 patch("tools.pr_governance_core.inspection.run_git", return_value="abcd1234abcd1234"):
                status = inspect_branch_status("feat/test-sample")
                self.assertTrue(status["readiness_receipt_pass"], "Branch-scoped receipt must be detected as PASS.")


class TestPRGovernanceDoctor(unittest.TestCase):
    def test_doctor_command(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=tmp_root, check=True, capture_output=True)
            subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=tmp_root, check=True)

            hooks_dir = tmp_root / ".githooks"
            hooks_dir.mkdir()
            for h in ["pre-commit", "commit-msg", "pre-merge-commit", "pre-push", "post-checkout", "post-merge"]:
                (hooks_dir / h).write_text("#!/bin/sh\n")
                (hooks_dir / h).chmod(0o755)

            for tool in [
                "tools/verify_commit.py",
                "tools/verify_main_readiness.py",
                "tools/verify_merge_receipt.py",
                "tools/lint_commit_message.py",
                "tools/validate_dual_pipeline_contracts.py",
                "tools/validate_output_hierarchy.py",
                "tools/lint_production_purity.py",
                "tools/scan_credential_leaks.py",
            ]:
                tp = tmp_root / tool
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_text("")

            with patch("tools.pr_governance_core.inspection.ROOT", tmp_root), \
                 patch("tools.pr_governance_core.common.ROOT", tmp_root):
                code = cmd_doctor(argparse.Namespace())
                self.assertEqual(code, 0, "Doctor should pass in isolated healthy development environment.")

    @patch("tools.pr_governance_core.inspection.run_git")
    @patch("tools.pr_governance_core.inspection.list_active_worktrees")
    def test_doctor_prunes_merged_feature_branches(self, mock_wt: MagicMock, mock_git: MagicMock) -> None:
        mock_wt.return_value = [{"branch": "main"}]
        def git_mock(args: list[str], **kwargs: object) -> str:
            if "config" in args:
                return ".githooks"
            if "--merged" in args:
                return "feat/old-merged\nmain\n"
            if "rev-parse" in args:
                return "main"
            return ""
        mock_git.side_effect = git_mock
        code = cmd_doctor(argparse.Namespace())
        self.assertEqual(code, 0)
        del_calls = [
            c for c in mock_git.call_args_list
            if c[0][0][:3] == ["branch", "-d", "feat/old-merged"]
        ]
        self.assertEqual(len(del_calls), 0, "Doctor must be strictly read-only and never delete branches")


class TestPRGovernanceMerge(unittest.TestCase):
    @patch("tools.pr_governance_core.lifecycle.subprocess.run")
    @patch("tools.pr_governance_core.lifecycle.run_git")
    def test_cmd_merge_atomic_single_commit(self, mock_run_git: MagicMock, mock_subproc: MagicMock) -> None:
        from tools.pr_governance_core.lifecycle import cmd_merge
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            reports_dir = tmp_root / "reports"
            reports_dir.mkdir(parents=True)
            slug = "feat_sample_feature"
            receipt_file = reports_dir / f"pr_review_{slug}_receipt.json"
            receipt_file.write_text(json.dumps({
                "terminal_outcome": "PASS",
                "head_sha": "abcd1234abcd1234",
                "readiness_verified": True,
                "clean_merge": True,
            }), encoding="utf-8")

            r_dir = reports_dir / "readiness"
            r_dir.mkdir(parents=True, exist_ok=True)
            stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"} for i in range(1, 31)]
            (r_dir / f"local_main_readiness_{slug}_receipt.json").write_text(json.dumps({
                "branch": "feat/sample-feature",
                "head_commit_sha": "abcd1234abcd1234",
                "terminal_outcome": "PASS",
                "total_stages": 30,
                "stages": stages,
            }), encoding="utf-8")

            # Mock git behaviors
            def git_side_effect(args: list[str], check: bool = True, **kwargs: Any) -> str:
                if "status" in args:
                    return ""
                elif "merge-base" in args:
                    return "base1234base1234"
                elif "rev-parse" in args:
                    if "--abbrev-ref" in args:
                        return "main"
                    if "HEAD" in args or "main" in args:
                        return "base1234base1234"
                    return "abcd1234abcd1234"
                return ""

            mock_run_git.side_effect = git_side_effect
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_subproc.return_value = mock_proc

            with patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root):
                args = argparse.Namespace(branch="feat/sample-feature", title="Wave 1: Test", delete_branch=False)
                res = cmd_merge(args)
                self.assertEqual(res, 0)

                # Verify single merge commit invocation with --no-commit first
                def _get_cmd(c):
                    return c[0][0] if (c[0] and len(c[0]) > 0) else c[1].get("args", [])

                merge_calls = [
                    call for call in mock_subproc.call_args_list
                    if _get_cmd(call)[:4] == ["git", "merge", "--no-ff", "--no-commit"]
                ]
                self.assertEqual(len(merge_calls), 1, "Must execute git merge --no-ff --no-commit")

                # Verify commit call for the merge
                commit_calls = [
                    call for call in mock_subproc.call_args_list
                    if _get_cmd(call)[:2] == ["git", "commit"]
                ]
                self.assertEqual(len(commit_calls), 1, "Must execute exactly ONE git commit for merge")

                # Verify merge receipt was created
                merge_receipt = reports_dir / f"pr_merge_{slug}_receipt.json"
                self.assertTrue(merge_receipt.exists(), "PR Merge receipt must exist")
                data = json.loads(merge_receipt.read_text(encoding="utf-8"))
                self.assertEqual(data["branch"], "feat/sample-feature")
                self.assertEqual(data["terminal_outcome"], "PASS")

    @patch("tools.pr_governance_core.lifecycle.cmd_review")
    @patch("tools.pr_governance_core.lifecycle.subprocess.run")
    @patch("tools.pr_governance_core.lifecycle.run_git")
    def test_cmd_merge_automatic_pre_merge_validation_success(
        self, mock_run_git: MagicMock, mock_subproc: MagicMock, mock_cmd_review: MagicMock
    ) -> None:
        """When review receipt is missing, cmd_merge automatically triggers pre-merge validation."""
        from tools.pr_governance_core.lifecycle import cmd_merge
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            reports_dir = tmp_root / "reports"
            reports_dir.mkdir(parents=True)
            slug = "feat_auto_validate"

            def fake_review(args: Any) -> int:
                receipt_file = reports_dir / f"pr_review_{slug}_receipt.json"
                receipt_file.write_text(json.dumps({
                    "terminal_outcome": "PASS",
                    "head_sha": "abcd1234abcd1234",
                    "readiness_verified": True,
                    "clean_merge": True,
                }), encoding="utf-8")
                return 0

            mock_cmd_review.side_effect = fake_review

            r_dir = reports_dir / "readiness"
            r_dir.mkdir(parents=True, exist_ok=True)
            stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"} for i in range(1, 31)]
            (r_dir / f"local_main_readiness_{slug}_receipt.json").write_text(json.dumps({
                "branch": "feat/auto-validate",
                "head_commit_sha": "abcd1234abcd1234",
                "terminal_outcome": "PASS",
                "total_stages": 30,
                "stages": stages,
            }), encoding="utf-8")

            def git_side_effect(args: list[str], check: bool = True, **kwargs: Any) -> str:
                if "status" in args:
                    return ""
                elif "merge-base" in args:
                    return "base1234base1234"
                elif "rev-parse" in args:
                    if "--abbrev-ref" in args:
                        return "main"
                    if "HEAD" in args or "main" in args:
                        return "base1234base1234"
                    return "abcd1234abcd1234"
                return ""

            mock_run_git.side_effect = git_side_effect
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_subproc.return_value = mock_proc

            with patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root):
                args = argparse.Namespace(branch="feat/auto-validate", title="Wave 1: Auto Validate", delete_branch=False)
                res = cmd_merge(args)
                self.assertEqual(res, 0)
                mock_cmd_review.assert_called_once()
                merge_receipt = reports_dir / f"pr_merge_{slug}_receipt.json"
                self.assertTrue(merge_receipt.exists(), "PR Merge receipt must exist")

    @patch("tools.pr_governance_core.lifecycle.cmd_review")
    @patch("tools.pr_governance_core.lifecycle.run_git")
    def test_cmd_merge_pre_merge_validation_failure_aborts_fail_closed(
        self, mock_run_git: MagicMock, mock_cmd_review: MagicMock
    ) -> None:
        """When pre-merge validation fails, cmd_merge halts fail-closed without merging."""
        from tools.pr_governance_core.lifecycle import cmd_merge
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            reports_dir = tmp_root / "reports"
            reports_dir.mkdir(parents=True)
            mock_cmd_review.return_value = 1  # Pre-merge validation failed

            def git_side_effect(args: list[str], check: bool = True, **kwargs: Any) -> str:
                if "status" in args:
                    return ""
                elif "rev-parse" in args:
                    return "abcd1234abcd1234"
                return ""

            mock_run_git.side_effect = git_side_effect

            with patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root):
                args = argparse.Namespace(branch="feat/failing-candidate", title="Wave 1: Fail", delete_branch=False)
                res = cmd_merge(args)
                self.assertEqual(res, 1, "Must exit with error code 1 when pre-merge fails")
                mock_cmd_review.assert_called_once()
                merge_receipts = list(reports_dir.glob("pr_merge_*_receipt.json"))
                self.assertEqual(len(merge_receipts), 0, "No merge receipt should exist when pre-merge validation fails")

    def test_first_parent_behind_count_isolation(self) -> None:
        """
        Verifies that compute_first_parent_sync counts only mainline first-parent commits,
        preventing 'behind' explosion from merge commit sub-trees.
        """
        import subprocess
        from tools.pr_governance_core.common import compute_first_parent_sync

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_path = Path(tmp_dir)
            run = lambda cmd: subprocess.run(cmd, cwd=repo_path, check=True, capture_output=True, text=True)

            run(["git", "init", "-b", "main"])
            run(["git", "config", "user.name", "Test Runner"])
            run(["git", "config", "user.email", "test@example.com"])
            (repo_path / "base.txt").write_text("base\n")
            run(["git", "add", "base.txt"])
            run(["git", "commit", "-m", "chore: initial commit"])

            # Create feature branch forked from initial commit
            run(["git", "checkout", "-b", "feat/my-feature"])
            (repo_path / "my_feature.txt").write_text("my feature\n")
            run(["git", "add", "my_feature.txt"])
            run(["git", "commit", "-m", "feat: my feature"])

            # Create another feature branch that merges into main with 5 commits
            run(["git", "checkout", "main"])
            run(["git", "checkout", "-b", "feat/other-feature"])
            for i in range(5):
                (repo_path / f"other_{i}.txt").write_text(f"other {i}\n")
                run(["git", "add", f"other_{i}.txt"])
                run(["git", "commit", "-m", f"feat: other {i}"])

            # Merge other-feature into main with --no-ff
            run(["git", "checkout", "main"])
            run(["git", "merge", "--no-ff", "feat/other-feature", "-m", "Merge branch 'feat/other-feature' into main"])

            # Naive rev-list --count feat/my-feature..main would count 6 commits (5 + 1 merge commit)
            naive_count = int(run(["git", "rev-list", "--count", "feat/my-feature..main"]).stdout.strip())
            self.assertEqual(naive_count, 6)

            # compute_first_parent_sync must return behind=1 (only 1 merge commit along main's first-parent spine)
            behind, ahead = compute_first_parent_sync("main", "feat/my-feature", cwd=repo_path)
            self.assertEqual(behind, 1)
            self.assertEqual(ahead, 1)

    def test_validate_merge_receipt_stale_mainline(self) -> None:
        from tools.pr_governance_core.lifecycle import _validate_merge_receipt

        with patch("tools.pr_governance_core.lifecycle._resolve_pr_review_receipt", return_value={
            "terminal_outcome": "PASS",
            "head_sha": "feat123",
        }), patch("tools.pr_governance_core.lifecycle.run_git") as mock_git:
            def git_side_effect(args: list[str], **kwargs: Any) -> str:
                if "rev-parse" in args:
                    return "main_new_sha"
                if "merge-base" in args:
                    return "main_old_sha"
                return ""

            mock_git.side_effect = git_side_effect
            ok = _validate_merge_receipt("feat/test", "feat_test", "feat123")
            self.assertFalse(ok)


class TestPRGovernanceLifecycle(unittest.TestCase):
    def test_assert_safe_worktree_operation(self) -> None:
        from tools.pr_governance_core.worktree import assert_safe_worktree_operation
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # 1. Clean worktree allows any target
            with patch("tools.pr_governance_core.worktree.run_git", return_value=""):
                safe, msg = assert_safe_worktree_operation(tmp_path, [".", "dual_pipeline/"])
                self.assertTrue(safe)
                self.assertEqual(msg, "Worktree is clean")

            # 2. Dirty worktree blocks sweeping targets
            with patch("tools.pr_governance_core.worktree.run_git", return_value=" M dual_pipeline/core.py\n"):
                safe, msg = assert_safe_worktree_operation(tmp_path, ["."])
                self.assertFalse(safe)
                self.assertIn("Sweeping operation on '.' blocked", msg)

                safe, msg = assert_safe_worktree_operation(tmp_path, ["dual_pipeline/"])
                self.assertFalse(safe)
                self.assertIn("Sweeping operation on 'dual_pipeline/' blocked", msg)

                # Specific targeted file is permitted
                safe, msg = assert_safe_worktree_operation(tmp_path, ["dual_pipeline/specific_file.py"])
                self.assertTrue(safe)
                self.assertEqual(msg, "Safe operation")

    def test_list_active_worktrees_ignore_review_artifacts(self) -> None:
        from tools.pr_governance_core.worktree import _inspect_worktree_dirty
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            receipt_status = (
                "?? reports/readiness/local_main_readiness_feat_test_receipt.json\n"
                "?? reports/pr_review_feat_test_receipt.json\n"
                " M reports/testing_gaps_audit_report.json\n"
            )
            # Without ignore_review_artifacts: dirty
            with patch("tools.pr_governance_core.worktree.run_git", return_value=receipt_status):
                dirty, summary = _inspect_worktree_dirty(tmp_path, ignore_review_artifacts=False)
                self.assertTrue(dirty)
                self.assertIn("dirty", summary)

                # With ignore_review_artifacts: clean!
                dirty, summary = _inspect_worktree_dirty(tmp_path, ignore_review_artifacts=True)
                self.assertFalse(dirty)
                self.assertIn("clean (review receipts ignored)", summary)

    def test_cmd_list_partitions_active_dirty_and_clean_branches(self) -> None:
        import io
        from contextlib import redirect_stdout

        from tools.pr_governance import cmd_list

        mock_branches = ["feat/active-candidate", "feat/uncommitted-wip", "feat/clean-stale", "feat/ref-only"]
        mock_statuses = {
            "feat/active-candidate": {
                "ahead": 2,
                "behind": 1,
                "clean_merge": True,
                "readiness_receipt_pass": True,
            },
            "feat/uncommitted-wip": {
                "ahead": 0,
                "behind": 10,
                "clean_merge": True,
                "readiness_receipt_pass": False,
            },
            "feat/clean-stale": {
                "ahead": 1,
                "behind": 5,
                "clean_merge": True,
                "readiness_receipt_pass": False,
            },
            "feat/ref-only": {
                "ahead": 0,
                "behind": 20,
                "clean_merge": True,
                "readiness_receipt_pass": False,
            },
        }

        mock_worktrees = [
            {"branch": "feat/active-candidate", "dirty": False, "summary": "clean"},
            {"branch": "feat/uncommitted-wip", "dirty": True, "summary": "dirty (15 files: modified + untracked)"},
            {"branch": "feat/clean-stale", "dirty": False, "summary": "clean"},
        ]

        mock_priority = {
            "feat/active-candidate": "P0",
            "feat/uncommitted-wip": "P1",
            "feat/clean-stale": "P2",
        }

        buf = io.StringIO()
        with patch("tools.pr_governance_core.inspection.get_feature_branches", return_value=mock_branches), \
             patch("tools.pr_governance_core.inspection.list_active_worktrees", return_value=mock_worktrees), \
             patch("tools.pr_governance_core.inspection.inspect_branch_status", side_effect=lambda b: mock_statuses[b]), \
             patch("tools.pr_governance_core.inspection.load_priority_registry", return_value=mock_priority), \
             redirect_stdout(buf):
            ret = cmd_list(argparse.Namespace())

        self.assertEqual(ret, 0)
        output = buf.getvalue()
        # Active section: verify Priority column and P0 label
        self.assertIn("Priority", output)
        self.assertIn("P0", output)
        self.assertIn("feat/active-candidate", output)
        self.assertIn("Total candidate branches in queue: 4", output)

        self.assertIn("P1", output)
        self.assertIn("feat/uncommitted-wip", output)

        self.assertIn("P2", output)
        self.assertIn("feat/clean-stale", output)

        # ahead==0 ref-only branch is ranked in the decommission group (previously matched only via
        # rubric text leaking into the old 'Domain & Impact' column)
        self.assertRegex(output, r"\| D\.\d+ \| [^|]+ \| (?:<span[^>]*>)?feat/ref-only(?:</span>)? \|")  # Priority | Domain | Branch

    def test_cmd_list_tombstone_branches_flagged(self) -> None:
        import io
        from contextlib import redirect_stdout

        from tools.pr_governance import cmd_list

        mock_branches = ["feat/tombstone-branch"]
        mock_statuses = {
            "feat/tombstone-branch": {
                "ahead": 5,
                "behind": 10,
                "clean_merge": False,
                "readiness_receipt_pass": False,
            },
        }
        mock_priority = {"feat/tombstone-branch": "TOMBSTONE"}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            buf = io.StringIO()
            with patch("tools.pr_governance_core.inspection.ROOT", tmp_root), \
                 patch("tools.pr_governance_core.inspection.get_feature_branches", return_value=mock_branches), \
                 patch("tools.pr_governance_core.inspection.list_active_worktrees", return_value=[]), \
                 patch("tools.pr_governance_core.inspection.inspect_branch_status", side_effect=lambda b: mock_statuses[b]), \
                 patch("tools.pr_governance_core.inspection.load_priority_registry", return_value=mock_priority), \
                 redirect_stdout(buf):
                ret = cmd_list(argparse.Namespace())

            self.assertEqual(ret, 0)
            output = buf.getvalue()
            self.assertIn("TOMBSTONE", output)
            self.assertIn("[TOMBSTONE]", output)
            self.assertIn("feat/tombstone-branch", output)

    def test_cmd_list_all_active_no_stale(self) -> None:
        import io
        from contextlib import redirect_stdout

        from tools.pr_governance import cmd_list

        mock_branches = ["feat/active-only"]
        mock_statuses = {
            "feat/active-only": {
                "ahead": 3,
                "behind": 0,
                "clean_merge": False,
                "readiness_receipt_pass": False,
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            buf = io.StringIO()
            with patch("tools.pr_governance_core.inspection.ROOT", tmp_root), \
                 patch("tools.pr_governance_core.inspection.get_feature_branches", return_value=mock_branches), \
                 patch("tools.pr_governance_core.inspection.list_active_worktrees", return_value=[]), \
                 patch("tools.pr_governance_core.inspection.inspect_branch_status", side_effect=lambda b: mock_statuses[b]), \
                 patch("tools.pr_governance_core.inspection.load_priority_registry", return_value={}), \
                 redirect_stdout(buf):
                ret = cmd_list(argparse.Namespace())

            self.assertEqual(ret, 0)
            output = buf.getvalue()
            self.assertIn("feat/active-only", output)
            self.assertIn("Conflicts", output)
            self.assertIn("Priority", output)
            self.assertIn("P3", output)  # default tier for unlisted branches
            self.assertNotIn("BRANCHES WITH UNCOMMITTED WORK", output)
            self.assertNotIn("CLEAN / PRUNABLE BRANCHES", output)
            self.assertIn("Total candidate branches in queue: 1", output)

    def test_load_priority_registry_dict_and_legacy_list(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from tools.pr_governance_core.inspection import load_priority_registry

        data = {
            "tiers": {
                "P0": {
                    "branches": {
                        "feat/dict-branch": {
                            "rationale": "High-priority critical fix",
                            "scope": "Core",
                        }
                    }
                },
                "P1": {
                    "branches": ["feat/list-branch-str", {"branch": "feat/list-branch-dict", "rationale": "Dict in list"}]
                },
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(data, tf)
            tmp_path = Path(tf.name)

        try:
            with patch("tools.pr_governance_core.inspection.get_priority_registry_path", return_value=tmp_path):
                # We mock `read_text` so `json.loads()` actually runs in load_priority_registry
                with patch("pathlib.Path.read_text", return_value=json.dumps(data)):
                    mapping = load_priority_registry()
                self.assertIn("feat/dict-branch", mapping)
                self.assertEqual(mapping["feat/dict-branch"]["tier"], "P0")
                self.assertEqual(mapping["feat/dict-branch"]["rationale"], "High-priority critical fix")

                self.assertIn("feat/list-branch-str", mapping)
                self.assertEqual(mapping["feat/list-branch-str"]["tier"], "P1")

                self.assertIn("feat/list-branch-dict", mapping)
                self.assertEqual(mapping["feat/list-branch-dict"]["tier"], "P1")
                self.assertEqual(mapping["feat/list-branch-dict"]["rationale"], "Dict in list")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_cmd_list_renders_rationale_column(self) -> None:
        import io
        from contextlib import redirect_stdout

        from tools.pr_governance import cmd_list

        mock_branches = ["feat/has-rationale", "feat/uncommitted-wip", "feat/clean-stale"]
        mock_statuses = {
            "feat/has-rationale": {
                "ahead": 1,
                "behind": 2,
                "clean_merge": True,
                "readiness_receipt_pass": True,
            },
            "feat/uncommitted-wip": {
                "ahead": 0,
                "behind": 5,
                "clean_merge": True,
                "readiness_receipt_pass": False,
            },
            "feat/clean-stale": {
                "ahead": 0,
                "behind": 10,
                "clean_merge": True,
                "readiness_receipt_pass": False,
            },
        }
        mock_worktrees = [
            {"branch": "feat/has-rationale", "dirty": False, "summary": "clean"},
            {"branch": "feat/uncommitted-wip", "dirty": True, "summary": "dirty (2 files)"},
            {"branch": "feat/clean-stale", "dirty": False, "summary": "clean"},
        ]
        mock_priority = {
            "feat/has-rationale": {
                "tier": "P0",
                "rationale": "Audio duration gate tolerance fix",
                "scope": "Core Ingress",
            },
            "feat/uncommitted-wip": {
                "tier": "P1",
                "rationale": "Pending pipeline refactor deliverables",
                "scope": "Pipeline",
            },
            "feat/clean-stale": {
                "tier": "P3",
                "rationale": "Historical analysis reference",
                "scope": "Analysis",
            },
        }

        buf = io.StringIO()
        with patch("tools.pr_governance_core.inspection.get_feature_branches", return_value=mock_branches), \
             patch("tools.pr_governance_core.inspection.list_active_worktrees", return_value=mock_worktrees), \
             patch("tools.pr_governance_core.inspection.inspect_branch_status", side_effect=lambda b: mock_statuses[b]), \
             patch("tools.pr_governance_core.inspection.load_priority_registry", return_value=mock_priority), \
             redirect_stdout(buf):
            ret = cmd_list(argparse.Namespace())

        self.assertEqual(ret, 0)
        output = buf.getvalue()
        self.assertIn("| Domain |", output)

    def test_cmd_rebase_already_up_to_date(self) -> None:
        from tools.pr_governance_core.lifecycle import cmd_rebase
        import io
        from contextlib import redirect_stdout

        with patch("tools.pr_governance_core.worktree.list_active_worktrees", return_value=[]), \
             patch("tools.pr_governance_core.lifecycle.run_git", side_effect=lambda args, **kwargs: "feat/up-to-date" if "--abbrev-ref" in args else ""), \
             patch("tools.pr_governance_core.lifecycle.inspect_branch_status", return_value={"behind": 0, "ahead": 1, "clean_merge": True}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                res = cmd_rebase(argparse.Namespace(branch="feat/up-to-date"))
            self.assertEqual(res, 0)
            self.assertIn("already up to date with main", buf.getvalue())

    def test_cmd_rebase_invokes_unlocked_rebase(self) -> None:
        from tools.pr_governance_core.lifecycle import cmd_rebase

        with tempfile.TemporaryDirectory() as tmp_wt:
            wt_path = Path(tmp_wt)
            mock_wts = [{"branch": "feat/needs-rebase", "path": wt_path, "dirty": False}]
            with patch("tools.pr_governance_core.worktree.list_active_worktrees", return_value=mock_wts), \
                 patch("tools.pr_governance_core.lifecycle.run_git", side_effect=lambda args, **kwargs: "main" if "--abbrev-ref" in args else ""), \
                 patch("tools.pr_governance_core.lifecycle.inspect_branch_status", return_value={"behind": 2, "ahead": 1, "clean_merge": True}), \
                 patch("tools.pr_governance_core.lifecycle.subprocess.run") as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = "Successfully rebased"
                mock_proc.stderr = ""
                mock_subproc.return_value = mock_proc

                res = cmd_rebase(argparse.Namespace(branch="feat/needs-rebase"))
                self.assertEqual(res, 0)
                mock_subproc.assert_called_with(["git", "rebase", "main"], cwd=wt_path, capture_output=True, text=True, timeout=60)

    def test_merge_lock_delegation_env(self) -> None:
        from tools.pr_governance_core.merge_lock import GovernedMergeQueueLock
        import os

        lock = GovernedMergeQueueLock("feat/delegated", timeout_sec=0.1)
        with patch.dict(os.environ, {"GOVERNED_MERGE_QUEUE_LOCK_BRANCH": "feat/delegated"}):
            self.assertTrue(lock.acquire())
            self.assertTrue(lock.is_acquired())
            lock.release()
            self.assertFalse(lock.is_acquired())


# Backwards compatibility alias for external test targets
TestPRGovernance = TestPRGovernanceValidation


if __name__ == "__main__":
    unittest.main()



