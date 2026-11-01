#!/usr/bin/env python3
"""
Unit and Governance Tests for Worktree Lifecycle & Prune Protection.

Verifies the four root-cause remediations from the RCA
(rca_uncommitted_worktree_purge_defect.md):

  RC-1: Zero-commit in-flight branches are not falsely identified as merged.
  RC-2: shutil.rmtree is never called by automated cleanup on git refusal.
  RC-3: Worktrees with uncommitted changes are never pruned automatically.
  RC-4: Only the scoped branch is considered during post-merge cleanup.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Hermetic git repo factory
# ---------------------------------------------------------------------------

def _hermetic_env() -> dict[str, str]:
    """Return environment sanitized of inherited git hook state."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") or k in ("GIT_EXEC_PATH", "GIT_TEMPLATE_DIR")
    }


def _make_repo(tmp_path: Path) -> Path:
    """Initialise a hermetic git repository with one commit on main."""
    env = _hermetic_env()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(tmp_path), check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(tmp_path), check=True, env=env)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(tmp_path), check=True, env=env)
    subprocess.run(["git", "config", "core.hooksPath", ""], cwd=str(tmp_path), check=True, env=env)
    init_file = tmp_path / "README.md"
    init_file.write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), check=True, env=env)
    subprocess.run(["git", "commit", "-m", "chore: initial commit", "--no-verify"], cwd=str(tmp_path), check=True, env=env)
    return tmp_path


def _commit_file(repo: Path, filename: str, content: str, msg: str) -> None:
    """Helper: write a file and commit it in the hermetic repo."""
    env = _hermetic_env()
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=str(repo), check=True, env=env)
    subprocess.run(["git", "commit", "-m", msg, "--no-verify"], cwd=str(repo), check=True, env=env)


# ---------------------------------------------------------------------------
# RC-1: is_branch_merged — zero-commit branch exclusion
# ---------------------------------------------------------------------------

class TestIsBranchMergedRC1(unittest.TestCase):
    """RC-1: is_branch_merged must not classify zero-commit branches as merged."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> str:
        return subprocess.run(
            list(args), cwd=str(self.repo), check=True, capture_output=True, text=True, env=_hermetic_env()
        ).stdout.strip()

    def _is_merged(self, branch: str) -> bool:
        from tools.audit_worktree_hygiene import is_branch_merged
        return is_branch_merged(branch, self.repo, base_branch="main")

    def test_zero_commit_branch_is_NOT_merged(self) -> None:
        """A freshly created branch with no commits must not be classified as merged."""
        self._run("git", "checkout", "-b", "feat/zero-commit")
        self.assertFalse(
            self._is_merged("feat/zero-commit"),
            "Zero-commit branch off main must NOT be classified as merged (RC-1).",
        )

    def test_genuinely_merged_branch_IS_merged(self) -> None:
        """A branch whose tip is an ancestor of main and has ≥1 real commit is merged."""
        self._run("git", "checkout", "-b", "feat/real-work")
        _commit_file(self.repo, "work.txt", "real work\n", "feat: real commit")
        # Merge into main
        self._run("git", "checkout", "main")
        self._run("git", "merge", "--no-ff", "feat/real-work", "-m", "Merge feat/real-work into main")
        self.assertTrue(
            self._is_merged("feat/real-work"),
            "Genuinely merged branch must be classified as merged (RC-1).",
        )

    def test_unmerged_branch_is_NOT_merged(self) -> None:
        """A branch with unmerged commits must not be classified as merged."""
        self._run("git", "checkout", "-b", "feat/unmerged")
        _commit_file(self.repo, "wip.txt", "wip\n", "feat: wip")
        self.assertFalse(
            self._is_merged("feat/unmerged"),
            "Unmerged branch must NOT be classified as merged (RC-1).",
        )

    def test_ff_merged_branch_is_NOT_auto_detectable(self) -> None:
        """After --ff-only merge, branch tip == main tip: indistinguishable from zero-commit branch.

        This is by design: pr_governance.py merge always uses --no-ff.
        FF-merged branches require explicit 'pr_governance.py finish --force' for teardown.
        This test documents the known limitation rather than asserting incorrect behavior.
        """
        self._run("git", "checkout", "-b", "feat/ff")
        _commit_file(self.repo, "ff.txt", "fast\n", "feat: ff commit")
        self._run("git", "checkout", "main")
        self._run("git", "merge", "--ff-only", "feat/ff")
        # After FF merge: feat/ff tip == main tip => Condition 2 correctly returns False
        # (cannot distinguish from zero-commit branch without additional history traversal)
        self.assertFalse(
            self._is_merged("feat/ff"),
            "FF-merged branch has tip==main tip after merge: excluded from automated cleanup by design.",
        )



# ---------------------------------------------------------------------------
# RC-2: shutil.rmtree ban in automated cleanup
# ---------------------------------------------------------------------------

class TestShutilRmtreeBanRC2(unittest.TestCase):
    """RC-2: shutil.rmtree must never be called by cleanup_merged_worktrees on git refusal."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_shutil_rmtree_not_called_on_git_failure(self) -> None:
        """If git worktree remove fails, shutil.rmtree must NOT be invoked (RC-2)."""
        from tools.audit_worktree_hygiene import cleanup_merged_worktrees

        fake_wt = {
            "path": str(self.repo / ".worktrees" / "feat_test"),
            "branch": "refs/heads/feat/test-branch",
            "head": "abc1234",
        }

        with (
            patch("tools.audit_worktree_hygiene.get_git_worktrees", return_value=[fake_wt]),
            patch("tools.audit_worktree_hygiene.is_branch_merged", return_value=True),
            patch("tools.audit_worktree_hygiene.is_worktree_clean", return_value=True),
            patch("subprocess.run") as mock_run,
            patch("shutil.rmtree") as mock_rmtree,
        ):
            # Make git worktree remove fail
            mock_run.return_value = MagicMock(returncode=128, stderr="fatal: contains modified files")

            cleanup_merged_worktrees(self.repo)

            mock_rmtree.assert_not_called()

    def test_shutil_rmtree_never_called_in_automated_path(self) -> None:
        """Verify cleanup_merged_worktrees contains no shutil.rmtree call expressions (RC-2 ban)."""
        import ast
        import inspect
        import tools.audit_worktree_hygiene as mod

        source = inspect.getsource(mod.cleanup_merged_worktrees)
        tree = ast.parse(source)

        rmtree_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(getattr(node, "func", None), ast.Attribute)
            and node.func.attr == "rmtree"
        ]
        self.assertEqual(
            len(rmtree_calls),
            0,
            f"shutil.rmtree must not be called inside cleanup_merged_worktrees (RC-2 ban). "
            f"Found {len(rmtree_calls)} call(s).",
        )



# ---------------------------------------------------------------------------
# RC-3: Dirty-state pre-flight blocks automated teardown
# ---------------------------------------------------------------------------

class TestDirtyPreFlightRC3(unittest.TestCase):
    """RC-3: Worktrees with uncommitted changes must never be auto-removed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dirty_merged_worktree_is_skipped(self) -> None:
        """A merged-but-dirty worktree must be skipped, not deleted (RC-3)."""
        from tools.audit_worktree_hygiene import cleanup_merged_worktrees

        fake_wt = {
            "path": str(self.repo / ".worktrees" / "feat_dirty"),
            "branch": "refs/heads/feat/dirty-branch",
            "head": "abc1234",
        }

        with (
            patch("tools.audit_worktree_hygiene.get_git_worktrees", return_value=[fake_wt]),
            patch("tools.audit_worktree_hygiene.is_branch_merged", return_value=True),
            patch("tools.audit_worktree_hygiene.is_worktree_clean", return_value=False),  # dirty!
            patch("subprocess.run") as mock_run,
        ):
            result = cleanup_merged_worktrees(self.repo)

            # Must not attempt git worktree remove
            for c in mock_run.call_args_list:
                args = c.args[0] if c.args else []
                self.assertFalse(
                    "worktree" in args and "remove" in args,
                    "git worktree remove must NOT be called for dirty worktrees (RC-3).",
                )
            self.assertEqual(result, [], "Dirty worktree must not appear in cleaned list (RC-3).")

    def test_is_worktree_clean_returns_false_for_modified_file(self) -> None:
        """is_worktree_clean must return False when tracked file is modified."""
        from tools.audit_worktree_hygiene import is_worktree_clean
        (self.repo / "README.md").write_text("modified\n", encoding="utf-8")
        self.assertFalse(is_worktree_clean(self.repo))

    def test_is_worktree_clean_returns_false_for_untracked_file(self) -> None:
        """is_worktree_clean must return False when untracked file exists."""
        from tools.audit_worktree_hygiene import is_worktree_clean
        (self.repo / "untracked.txt").write_text("new\n", encoding="utf-8")
        self.assertFalse(is_worktree_clean(self.repo))

    def test_is_worktree_clean_returns_true_for_clean_repo(self) -> None:
        """is_worktree_clean must return True for a fully clean working tree."""
        from tools.audit_worktree_hygiene import is_worktree_clean
        self.assertTrue(is_worktree_clean(self.repo))

    def test_cmd_finish_blocks_on_dirty_worktree(self) -> None:
        """cmd_finish (non-force) must return 1 and refuse to delete a dirty worktree (RC-3)."""
        import argparse
        from tools.pr_governance_core.worktree import cmd_finish

        fake_dir = self.repo / ".worktrees" / "feat_some_branch"
        fake_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("tools.pr_governance_core.worktree.ROOT", self.repo),
            patch("tools.pr_governance_core.worktree.run_git") as mock_git,
            patch("tools.pr_governance_core.worktree._is_worktree_clean", return_value=False),
        ):
            # Simulate merged branch (tip == merge-base, tip != base_tip)
            mock_git.side_effect = ["abc123", "xyz999", "abc123"]

            ns = argparse.Namespace(branch="feat/some-branch", force=False, delete_branch=False)
            result = cmd_finish(ns)

        self.assertEqual(result, 1, "cmd_finish must return 1 for dirty worktree (RC-3).")


# ---------------------------------------------------------------------------
# RC-4: Scoped post-merge cleanup
# ---------------------------------------------------------------------------

class TestScopedCleanupRC4(unittest.TestCase):
    """RC-4: cleanup_merged_worktrees must only process the scoped branch when provided."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scoped_branch_skips_other_worktrees(self) -> None:
        """When scoped_branch is set, other merged worktrees are not touched (RC-4)."""
        from tools.audit_worktree_hygiene import cleanup_merged_worktrees

        fake_wts = [
            {"path": str(self.repo / ".worktrees" / "feat_target"), "branch": "refs/heads/feat/target", "head": "aaa"},
            {"path": str(self.repo / ".worktrees" / "feat_bystander"), "branch": "refs/heads/feat/bystander", "head": "bbb"},
        ]

        removed: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            if "remove" in cmd:
                removed.append(cmd[cmd.index("remove") + 1])
            return MagicMock(returncode=0, stderr="")

        with (
            patch("tools.audit_worktree_hygiene.get_git_worktrees", return_value=fake_wts),
            patch("tools.audit_worktree_hygiene.is_branch_merged", return_value=True),
            patch("tools.audit_worktree_hygiene.is_worktree_clean", return_value=True),
            patch("subprocess.run", side_effect=fake_run),
        ):
            cleanup_merged_worktrees(self.repo, scoped_branch="feat/target")

        self.assertTrue(
            any("feat_target" in r for r in removed),
            "Scoped target worktree should be removed (RC-4).",
        )
        self.assertFalse(
            any("feat_bystander" in r for r in removed),
            "Non-scoped bystander worktree must NOT be touched (RC-4).",
        )

    def test_no_scope_processes_all_merged_worktrees(self) -> None:
        """Without scoped_branch, all genuinely merged clean worktrees are candidates."""
        from tools.audit_worktree_hygiene import cleanup_merged_worktrees

        fake_wts = [
            {"path": str(self.repo / ".worktrees" / "feat_a"), "branch": "refs/heads/feat/a", "head": "aaa"},
            {"path": str(self.repo / ".worktrees" / "feat_b"), "branch": "refs/heads/feat/b", "head": "bbb"},
        ]

        removed: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            if "remove" in cmd:
                removed.append(cmd[cmd.index("remove") + 1])
            return MagicMock(returncode=0, stderr="")

        with (
            patch("tools.audit_worktree_hygiene.get_git_worktrees", return_value=fake_wts),
            patch("tools.audit_worktree_hygiene.is_branch_merged", return_value=True),
            patch("tools.audit_worktree_hygiene.is_worktree_clean", return_value=True),
            patch("subprocess.run", side_effect=fake_run),
        ):
            cleanup_merged_worktrees(self.repo, scoped_branch=None)

        self.assertEqual(len(removed), 2, "Without scope, both merged clean worktrees should be removed (RC-4).")


if __name__ == "__main__":
    unittest.main()
