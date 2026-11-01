"""
tests/test_worktree_hygiene_hardening.py

Regression test suite for PR worktree and branch hygiene hardening:
1. Verifies is_branch_merged requires explicit merge commit or receipt proof.
2. Verifies cleanup_merged_worktrees deletes branch when delete_branch=True.
3. Verifies PR governance CLI parser defaults delete_branch to True and honors opt-out.
4. Verifies post-merge hook dynamic git-dir resolution and --delete-branch parameter.
5. Verifies audit_hygiene fail-closed behavior on lingering merged worktrees.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_worktree_hygiene import (  # noqa: E402
    audit_hygiene,
    cleanup_merged_worktrees,
    is_branch_merged,
)
from tools.pr_governance import main as pr_gov_main  # noqa: E402


class TestWorktreeHygieneHardening(unittest.TestCase):
    """Test suite validating automated branch teardown and hygiene invariants."""

    def test_pr_governance_merge_cli_defaults_to_delete_branch(self) -> None:
        """Verify pr_governance merge mandates delete_branch=True and rejects bypass flags."""
        from tools.pr_governance_core.lifecycle import _teardown_merged_worktree
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            captured = []
            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("tools.pr_governance_core.worktree.cmd_finish", side_effect=lambda a: captured.append(a) or 0),
            ):
                _teardown_merged_worktree("feat/test-branch", delete_branch=False)
                self.assertEqual(len(captured), 0)
                _teardown_merged_worktree("feat/test-branch", delete_branch=True)
                self.assertEqual(len(captured), 1)
                self.assertTrue(captured[0].delete_branch)

        with (
            patch("sys.argv", ["pr_governance.py", "merge", "feat/test-branch", "--title", "Test Title"]),
            patch("tools.pr_governance.cmd_merge") as mock_merge,
        ):
            mock_merge.return_value = 0
            pr_gov_main()
            self.assertTrue(mock_merge.called)
            args = mock_merge.call_args[0][0]
            self.assertTrue(args.delete_branch)

        with (
            patch("sys.argv", ["pr_governance.py", "merge", "feat/test-branch", "--title", "Test", "--keep-branch"]),
            self.assertRaises(SystemExit),
        ):
            pr_gov_main()

        with (
            patch("sys.argv", ["pr_governance.py", "merge", "feat/test-branch", "--title", "Test", "--no-delete-branch"]),
            patch("tools.pr_governance.cmd_merge") as mock_merge,
        ):
            mock_merge.return_value = 0
            pr_gov_main()
            self.assertTrue(mock_merge.called)
            args = mock_merge.call_args[0][0]
            self.assertFalse(args.delete_branch)

    def test_pr_governance_finish_cli_defaults_to_delete_branch(self) -> None:
        """Verify pr_governance finish mandates delete_branch=True and rejects bypass flags."""

        with (
            patch("sys.argv", ["pr_governance.py", "finish", "feat/test-branch"]),
            patch("tools.pr_governance.cmd_finish") as mock_finish,
        ):
            mock_finish.return_value = 0
            pr_gov_main()
            self.assertTrue(mock_finish.called)
            args = mock_finish.call_args[0][0]
            self.assertTrue(args.delete_branch)

        with (
            patch("sys.argv", ["pr_governance.py", "finish", "feat/test-branch", "--keep-branch"]),
            self.assertRaises(SystemExit),
        ):
            pr_gov_main()

    def test_post_merge_hook_resolves_git_dir_and_deletes_branch(self) -> None:
        """Verify .githooks/post-merge resolves git-dir dynamically and calls --delete-branch."""
        hook_path = ROOT / ".githooks" / "post-merge"
        self.assertTrue(hook_path.is_file(), f"Hook file not found at {hook_path}")
        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("git rev-parse --git-dir", content)
        self.assertIn("--delete-branch", content)
        self.assertNotIn('MERGE_MSG_FILE="$ROOT/.git/MERGE_MSG"', content)

    def test_is_branch_merged_unmerged_candidate_returns_false(self) -> None:
        """Verify that a branch without merge commit or receipt returns False."""
        # Test on a branch without merge commit or receipt
        result = is_branch_merged("feat/unmerged-candidate-fixture", ROOT, "main")
        self.assertFalse(result)

    def test_is_branch_merged_with_merge_commit_returns_true(self) -> None:
        """Verify that a branch that was genuinely merged into main returns True."""
        result = is_branch_merged("feat/live-sprint-mandy-0924", ROOT, "main")
        self.assertTrue(result)

    def test_cleanup_merged_worktrees_with_delete_branch_flag(self) -> None:
        """Verify cleanup_merged_worktrees runs git branch -d when delete_branch is True."""
        with patch("tools.audit_worktree_hygiene.get_git_worktrees") as mock_get_wts, \
             patch("tools.audit_worktree_hygiene.is_branch_merged") as mock_merged, \
             patch("tools.audit_worktree_hygiene.is_worktree_clean") as mock_clean, \
             patch("subprocess.run") as mock_run:

            mock_get_wts.return_value = [
                {"path": str(ROOT / ".worktrees" / "mock_feature"), "branch": "refs/heads/feat/mock-feature"}
            ]
            mock_merged.return_value = True
            mock_clean.return_value = True
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            cleaned = cleanup_merged_worktrees(
                ROOT,
                base_branch="main",
                scoped_branch="feat/mock-feature",
                delete_branch=True,
            )

            self.assertEqual(len(cleaned), 1)
            self.assertIn("feat/mock-feature", cleaned[0])
            # Check that git branch -d was called
            branch_delete_calls = [
                call for call in mock_run.call_args_list
                if call[0][0][:3] == ["git", "branch", "-d"]
            ]
            self.assertEqual(len(branch_delete_calls), 1)
            self.assertEqual(branch_delete_calls[0][0][0][3], "feat/mock-feature")

    def test_audit_hygiene_check_merged_reports_violation(self) -> None:
        """Verify audit_hygiene with check_merged=True catches lingering merged worktrees."""
        with patch("tools.audit_worktree_hygiene.get_git_worktrees") as mock_get_wts, \
             patch("tools.audit_worktree_hygiene.is_branch_merged") as mock_merged, \
             patch("tools.audit_worktree_hygiene.is_worktree_clean") as mock_clean, \
             patch("tools.audit_worktree_hygiene.find_dangling_tmp_files") as mock_tmp, \
             patch("tools.audit_worktree_hygiene.verify_output_hierarchy") as mock_hier:

            mock_get_wts.return_value = [
                {"path": str(ROOT / ".worktrees" / "mock_merged"), "branch": "refs/heads/feat/mock-merged"}
            ]
            mock_merged.return_value = True
            mock_clean.return_value = True
            mock_tmp.return_value = []
            mock_hier.return_value = []

            report = audit_hygiene(
                ROOT,
                clean_tmp=False,
                clean_merged_wt=False,
                scoped_branch="feat/mock-merged",
                check_merged=True,
            )

            self.assertFalse(report.is_hygiene_compliant)
            self.assertTrue(any("HYGIENE_VIOLATION" in err for err in report.errors))
            self.assertTrue(any("feat/mock-merged" in err for err in report.errors))

    def test_teardown_merged_worktree_fallback_and_parent_session_cleanup(self) -> None:
        """Verify _teardown_merged_worktree executes fallback branch delete on non-zero finish rc and cleans parent session worktree."""
        import tempfile
        from tools.pr_governance_core.lifecycle import (
            _fallback_delete_merged_branch_ref,
            _teardown_host_session_worktree_if_applicable,
            _teardown_merged_worktree,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp) / "repo"
            tmp_root.mkdir()
            ext_wt = Path(tmp) / "ext_session"
            ext_wt.mkdir()

            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("tools.pr_governance_core.worktree.cmd_finish", return_value=1) as mock_finish,
                patch("tools.pr_governance_core.lifecycle.run_git", return_value="abc1234") as mock_git,
            ):
                _teardown_merged_worktree("feat/test-rc", delete_branch=True)
                self.assertTrue(mock_finish.called)
                self.assertTrue(any("update-ref" in call[0][0] for call in mock_git.call_args_list))

            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("tools.pr_governance_core.lifecycle.run_git", return_value=""),
            ):
                _fallback_delete_merged_branch_ref("feat/test-empty")

            finish_calls = []
            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("pathlib.Path.cwd", return_value=ext_wt),
                patch(
                    "tools.pr_governance_core.worktree.list_active_worktrees",
                    return_value=[{"path": str(ext_wt), "branch": "session_agent_branch"}],
                ),
                patch("tools.pr_governance_core.worktree._is_worktree_clean", return_value=True),
                patch("tools.pr_governance_core.lifecycle.run_git", return_value="0 0"),
                patch(
                    "tools.pr_governance_core.worktree.cmd_finish",
                    side_effect=lambda a: finish_calls.append(a) or 0,
                ),
            ):
                _teardown_host_session_worktree_if_applicable("feat/inner-branch")
                self.assertEqual(len(finish_calls), 1)
                self.assertEqual(finish_calls[0].branch, "session_agent_branch")

            # Cover exception/skip branches in _fallback_delete_merged_branch_ref, _teardown_host_session_worktree_if_applicable, _teardown_merged_worktree
            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("tools.pr_governance_core.lifecycle.run_git", side_effect=RuntimeError("git fail")),
            ):
                _fallback_delete_merged_branch_ref("feat/fail-delete")

            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("pathlib.Path.cwd", return_value=tmp_root),
            ):
                _teardown_host_session_worktree_if_applicable("feat/inner-branch")

            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("pathlib.Path.cwd", return_value=ext_wt),
                patch(
                    "tools.pr_governance_core.worktree.list_active_worktrees",
                    return_value=[{"path": str(ext_wt), "branch": "main"}, {"path": str(ext_wt), "branch": "ext_br"}],
                ),
                patch(
                    "tools.pr_governance_core.lifecycle.run_git",
                    side_effect=["abc1234", RuntimeError("boom")],
                ),
            ):
                _teardown_host_session_worktree_if_applicable("feat/inner-branch")

            with (
                patch("tools.pr_governance_core.lifecycle.ROOT", tmp_root),
                patch("tools.pr_governance_core.worktree.cmd_finish", side_effect=RuntimeError("finish err")),
                patch("tools.pr_governance_core.lifecycle._fallback_delete_merged_branch_ref") as mock_fb,
            ):
                _teardown_merged_worktree("feat/exc-branch", delete_branch=True)
                self.assertTrue(mock_fb.called)

    def test_fast_forward_merge_and_zero_commit_cleanup(self) -> None:
        """Verify fast-forward merge detection in reflog and zero-commit worktree finish/pruning."""
        import tempfile
        from tools.audit_worktree_hygiene_core.pruner import (
            _is_abandoned_live_worktree,
            cleanup_abandoned_zero_commit_worktrees,
        )
        from tools.pr_governance_core.merge_proof import (
            BranchMergeState,
            classify_branch_merge_state,
            has_fast_forward_merge_in_reflog,
        )
        from tools.pr_governance_core.worktree import _is_zero_commit_ancestor_branch

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            with (
                patch("tools.pr_governance_core.merge_proof._is_ancestor_commit", return_value=True),
                patch(
                    "tools.pr_governance_core.merge_proof.run_git",
                    return_value="7792c6f46 refs/heads/main@{0}: merge live_demo: Fast-forward",
                ),
            ):
                self.assertTrue(has_fast_forward_merge_in_reflog("live_demo", "7792c6f46", root=tmp_root))
                state = classify_branch_merge_state("live_demo", "7792c6f46", "7792c6f46", root=tmp_root)
                self.assertEqual(state, BranchMergeState.GENUINELY_MERGED)

            with (
                patch("tools.pr_governance_core.worktree.run_git", return_value="7265015fa"),
                patch(
                    "tools.pr_governance_core.merge_proof.classify_branch_merge_state",
                    return_value=BranchMergeState.ZERO_COMMIT_ANCESTOR,
                ),
            ):
                self.assertTrue(_is_zero_commit_ancestor_branch("fix/zero-commit", root=tmp_root))

            wt_dir = tmp_root / "wt"
            wt_dir.mkdir()
            with (
                patch(
                    "tools.audit_worktree_hygiene.get_git_worktrees",
                    return_value=[{"path": str(wt_dir), "branch": "refs/heads/fix/abandoned-zero"}],
                ),
                patch("subprocess.run") as mock_sub,
            ):
                def _side_effect(cmd, **kwargs):
                    if "rev-list" in cmd:
                        return MagicMock(returncode=0, stdout="2 0\n", stderr="")
                    if "status" in cmd:
                        return MagicMock(returncode=0, stdout=b"", stderr=b"")
                    return MagicMock(returncode=0, stdout="abc1234\n", stderr="")

                mock_sub.side_effect = _side_effect
                self.assertTrue(_is_abandoned_live_worktree(wt_dir, "fix/abandoned-zero", tmp_root, "def5678", max_age_hours=0.0))
                cleaned = cleanup_abandoned_zero_commit_worktrees(tmp_root, max_age_hours=0.0)
                self.assertEqual(len(cleaned), 1)
                self.assertIn("fix/abandoned-zero", cleaned[0])

            # Cover remaining branches in pruner, merge_proof, gc, and worktree
            self.assertFalse(has_fast_forward_merge_in_reflog("", "7792c6f46", root=tmp_root))
            with patch("tools.pr_governance_core.merge_proof._is_ancestor_commit", return_value=False):
                self.assertFalse(has_fast_forward_merge_in_reflog("live_demo", "7792c6f46", root=tmp_root))
            with (
                patch("tools.pr_governance_core.merge_proof._is_ancestor_commit", return_value=True),
                patch(
                    "tools.pr_governance_core.merge_proof.run_git",
                    side_effect=["", "7792c6f46 refs/heads/live_demo@{0}: commit: feat: work"],
                ),
            ):
                self.assertTrue(has_fast_forward_merge_in_reflog("live_demo", "7792c6f46", root=tmp_root))

            with (
                patch("tools.pr_governance_core.merge_proof._is_ancestor_commit", return_value=True),
                patch("tools.pr_governance_core.merge_proof.is_second_parent_of_merge_on_main", return_value=False),
                patch("tools.pr_governance_core.merge_proof.has_sealed_merge_receipt", return_value=False),
                patch(
                    "tools.pr_governance_core.merge_proof.run_git",
                    side_effect=[
                        "7792c6f46 refs/heads/main@{0}: merge live_ff: Fast-forward",
                        "",
                        "",
                        "0 0",
                    ],
                ),
            ):
                self.assertEqual(
                    classify_branch_merge_state("live_ff", "7792c6f46", "7792c6f46", root=tmp_root),
                    BranchMergeState.GENUINELY_MERGED,
                )
                self.assertFalse(has_fast_forward_merge_in_reflog("live_none", "7792c6f46", root=tmp_root))

            with patch("subprocess.run") as mock_sub2:
                mock_sub2.side_effect = [
                    MagicMock(returncode=0, stdout="abc\n"),
                    MagicMock(returncode=1, stdout=""),
                ]
                self.assertFalse(_is_abandoned_live_worktree(wt_dir, "fix/not-anc", tmp_root, "def"))

                mock_sub2.side_effect = [
                    MagicMock(returncode=0, stdout="abc\n"),
                    MagicMock(returncode=0, stdout=""),
                    MagicMock(returncode=0, stdout="0 3\n"),
                ]
                self.assertFalse(_is_abandoned_live_worktree(wt_dir, "fix/ahead", tmp_root, "def"))

                mock_sub2.side_effect = [
                    MagicMock(returncode=0, stdout="def\n"),
                    MagicMock(returncode=0, stdout=b"1 .M N... 100644 100644 100644 abc def file.py\x00"),
                ]
                self.assertFalse(_is_abandoned_live_worktree(wt_dir, "fix/dirty", tmp_root, "def"))

            from tools.pr_governance_core.worktree import _remove_worktree_files, cmd_finish
            import argparse

            with patch("tools.pr_governance_core.worktree.run_git", side_effect=RuntimeError("err")):
                self.assertFalse(_is_zero_commit_ancestor_branch("fix/err", root=tmp_root))

            with (
                patch("tools.pr_governance_core.worktree_daemon_reaper.reap_worktree_fsmonitor"),
                patch("tools.pr_governance_core.worktree.kill_ghost_processes"),
                patch("tools.pr_governance_core.audio_repatriation.repatriate_worktree_audio_artifacts", return_value=MagicMock(success=True)),
                patch("tools.pr_governance_core.worktree.safely_unlink_worktree_dependencies"),
                patch("pathlib.Path.cwd", return_value=wt_dir),
                patch("tools.pr_governance_core.worktree.run_git"),
            ):
                self.assertTrue(_remove_worktree_files(wt_dir, force=False, root=tmp_root))

            with (
                patch("tools.pr_governance_core.worktree.ROOT", tmp_root),
                patch("tools.pr_governance_core.worktree._find_worktree_dir", return_value=wt_dir),
                patch("tools.pr_governance_core.worktree.run_git", return_value="abc1234"),
                patch("tools.pr_governance_core.worktree._is_branch_merged", return_value=False),
                patch("tools.pr_governance_core.worktree._is_worktree_clean", return_value=True),
                patch("tools.pr_governance_core.worktree._is_zero_commit_ancestor_branch", return_value=True),
                patch("tools.pr_governance_core.worktree.count_branch_stashes", return_value=0),
                patch("tools.pr_governance_core.worktree._remove_worktree_files", return_value=True),
            ):
                rc = cmd_finish(argparse.Namespace(branch="fix/zero-clean", force=False, ingest=False, delete_branch=True))
                self.assertEqual(rc, 0)

            with (
                patch("tools.pr_governance_core.worktree.ROOT", tmp_root),
                patch("tools.pr_governance_core.worktree._find_worktree_dir", return_value=wt_dir),
                patch("tools.pr_governance_core.worktree.run_git", return_value="abc1234"),
                patch("tools.pr_governance_core.worktree._is_branch_merged", return_value=True),
                patch("tools.pr_governance_core.worktree._is_worktree_clean", return_value=False),
            ):
                rc = cmd_finish(argparse.Namespace(branch="fix/merged-dirty", force=False, ingest=False, delete_branch=True))
                self.assertEqual(rc, 1)

            with (
                patch("tools.pr_governance_core.worktree.ROOT", tmp_root),
                patch("tools.pr_governance_core.worktree._find_worktree_dir", return_value=wt_dir),
                patch("tools.pr_governance_core.worktree.run_git", return_value="abc1234"),
                patch("tools.pr_governance_core.worktree._is_branch_merged", return_value=False),
                patch("tools.pr_governance_core.worktree._is_worktree_clean", return_value=False),
            ):
                rc = cmd_finish(argparse.Namespace(branch="fix/unmerged-dirty", force=False, ingest=False, delete_branch=True))
                self.assertEqual(rc, 1)

            from tools.pr_governance_core.gc import inspect_worktrees_for_gc

            with (
                patch(
                    "tools.pr_governance_core.gc.run_git",
                    side_effect=[
                        f"worktree {wt_dir}\nHEAD abc1234\nbranch refs/heads/audio/test\n",
                        "abc1234",
                    ],
                ),
                patch(
                    "tools.pr_governance_core.gc.classify_branch_merge_state",
                    return_value=BranchMergeState.ZERO_COMMIT_ANCESTOR,
                ),
                patch("tools.pr_governance_core.gc.is_mid_rebase", return_value=False),
                patch("tools.pr_governance_core.gc.find_valuable_ignored_files", return_value=[]),
                patch("tools.pr_governance_core.gc.count_branch_stashes", return_value=0),
                patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=b"")),
                patch("tools.audit_worktree_hygiene_core.pruner._is_abandoned_live_worktree", return_value=True),
                patch(
                    "tools.pr_topology_core.birth.inspect_branch_birth",
                    return_value=MagicMock(is_inception_active=False),
                ),
            ):
                cands = inspect_worktrees_for_gc(root=tmp_root)
                self.assertEqual(len(cands), 1)
                self.assertTrue(cands[0]["can_auto_clean"])


if __name__ == "__main__":
    unittest.main()
