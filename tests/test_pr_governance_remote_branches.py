"""
Unit and regression tests for remote branch discovery and opt-in fetch protocol (Wave 1).
Tests cover:
- Default offline behavior (no fetch, no remotes)
- Remote ref parsing and filtering (origin/HEAD, origin/main, origin/backup-*, etc.)
- Local counterpart deduplication
- Opt-in fetch protocol (--fetch flag, timeout guard, graceful error recovery)
- Queue routing and action assignment ([CHECKOUT_OR_TRACK])
- Table rendering and branch formatting (🌐 emoji prefix)
- Worktree inventory integration with remote counts
- CLI argument parsing across list, inventory, and review commands
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from tools.pr_governance import build_parser
from tools.pr_governance_core.inspection import (
    cmd_list,
    get_feature_branches,
    inspect_branch_status,
    is_remote_tracking_branch,
)
from tools.pr_governance_core.queue_actions import explain_next_action
from tools.pr_governance_core.render import format_branch_cell
from tools.pr_governance_core.worktree_inventory import build_worktree_inventory


class TestRemoteTrackingBranchDetection:
    """Tests for is_remote_tracking_branch()."""

    def test_remote_branch_patterns(self):
        assert is_remote_tracking_branch("origin/feat/my-feature") is True
        assert is_remote_tracking_branch("origin/main") is True
        assert is_remote_tracking_branch("remotes/origin/feat/something") is True
        assert is_remote_tracking_branch("upstream/feat/something") is True

    def test_local_branch_patterns(self):
        assert is_remote_tracking_branch("feat/my-feature") is False
        assert is_remote_tracking_branch("main") is False
        assert is_remote_tracking_branch("fix/bug-123") is False
        assert is_remote_tracking_branch("stash@{0}") is False


class TestBranchCellFormatting:
    """Tests for format_branch_cell()."""

    def test_remote_branch_adds_globe_icon(self):
        result = format_branch_cell("origin/feat/pgd-followup-artifacts")
        assert result == "🌐 origin/feat/pgd-followup-artifacts"

    def test_explicit_is_remote_flag(self):
        result = format_branch_cell("feat/remote-candidate", is_remote=True)
        assert result == "🌐 feat/remote-candidate"

    def test_local_worktree_markdown_link(self):
        wt = {"path": "/path/to/worktree", "is_root": False}
        result = format_branch_cell("feat/active-wt", wt=wt)
        assert result == "[feat/active-wt](file:///path/to/worktree)"

    def test_root_worktree_renders_plain(self):
        wt = {"path": "/path/to/root", "is_root": True}
        result = format_branch_cell("main", wt=wt)
        assert result == "main"

    def test_plain_branch_without_worktree(self):
        result = format_branch_cell("feat/plain-branch", wt=None)
        assert result == "feat/plain-branch"


class TestRemoteNextActionRouting:
    """Tests for explain_next_action() on remote-only branches."""

    def test_remote_branch_checkout_or_track_action(self):
        item = {
            "branch": "origin/feat/pgd-followup-artifacts",
            "behind": 0,
            "open_waves": "—",
            "dirty": False,
        }
        action = explain_next_action(item)
        assert "[CHECKOUT_OR_TRACK]" in action
        assert "git checkout -b feat/pgd-followup-artifacts origin/feat/pgd-followup-artifacts" in action

    def test_nested_remote_branch_name_checkout(self):
        item = {
            "branch": "origin/topic/subtopic/feature-x",
            "behind": 2,
            "open_waves": "—",
            "dirty": False,
        }
        action = explain_next_action(item)
        assert "[CHECKOUT_OR_TRACK]" in action
        assert "git checkout -b topic/subtopic/feature-x origin/topic/subtopic/feature-x" in action


class TestGetFeatureBranches:
    """Tests for get_feature_branches() logic and mock isolation."""

    @patch("tools.pr_governance_core.inspection.subprocess.run")
    def test_default_is_strictly_offline(self, mock_run):
        # Local branches mock
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="feat/alpha\nfeat/beta\n",
        )

        branches = get_feature_branches(include_remote=False, fetch_first=False)

        assert branches == ["feat/alpha", "feat/beta"]
        # Ensure 'git fetch' was never called
        for call_args in mock_run.call_args_list:
            args = call_args[0][0]
            assert "fetch" not in args

    @patch("tools.pr_governance_core.inspection.subprocess.run")
    def test_remote_filtering_and_counterpart_deduplication(self, mock_run):
        def side_effect(cmd, *args, **kwargs):
            if "branch" in cmd:
                return MagicMock(returncode=0, stdout="feat/alpha\nfeat/beta\n")
            elif "for-each-ref" in cmd:
                # Include origin, origin/HEAD, origin/main, origin/master,
                # duplicate local (feat/alpha), backup branch, and a genuine new remote branch
                output = (
                    "origin\n"
                    "origin/HEAD\n"
                    "origin/main\n"
                    "origin/master\n"
                    "origin/backup-20260901\n"
                    "origin/feat/alpha\n"
                    "origin/feat/gamma-remote-only\n"
                )
                return MagicMock(returncode=0, stdout=output)
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect

        branches = get_feature_branches(include_remote=True, fetch_first=False)

        # 'feat/alpha' and 'feat/beta' are local
        # 'origin/feat/alpha' is deduplicated since 'feat/alpha' exists locally
        # 'origin/feat/gamma-remote-only' should be included
        # 'origin', 'origin/HEAD', 'origin/main', 'origin/master', 'origin/backup-*' must be filtered out
        assert branches == ["feat/alpha", "feat/beta", "origin/feat/gamma-remote-only"]

    @patch("tools.pr_governance_core.inspection.subprocess.run")
    def test_fetch_first_invokes_git_fetch_prune(self, mock_run):
        def side_effect(cmd, *args, **kwargs):
            if "fetch" in cmd:
                assert "--prune" in cmd
                assert "origin" in cmd
                return MagicMock(returncode=0, stdout="")
            elif "branch" in cmd:
                return MagicMock(returncode=0, stdout="feat/one\n")
            elif "for-each-ref" in cmd:
                return MagicMock(returncode=0, stdout="origin/feat/remote\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect

        branches = get_feature_branches(include_remote=True, fetch_first=True)

        assert branches == ["feat/one", "origin/feat/remote"]
        # Verify fetch was executed
        fetch_calls = [c for c in mock_run.call_args_list if "fetch" in c[0][0]]
        assert len(fetch_calls) == 1

    @patch("tools.pr_governance_core.inspection.subprocess.run")
    def test_fetch_timeout_or_error_graceful_degradation(self, mock_run):
        def side_effect(cmd, *args, **kwargs):
            if "fetch" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=15)
            elif "branch" in cmd:
                return MagicMock(returncode=0, stdout="feat/local-only\n")
            elif "for-each-ref" in cmd:
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect

        # Should log warning but not raise exception
        branches = get_feature_branches(include_remote=True, fetch_first=True)
        assert branches == ["feat/local-only"]


class TestInspectBranchStatus:
    """Tests for inspect_branch_status() with remote branch."""

    @patch("tools.pr_governance_core.common.subprocess.run")
    def test_inspect_remote_branch_status(self, mock_run):
        def side_effect(cmd, *args, **kwargs):
            if "merge-tree" in cmd:
                return MagicMock(returncode=0, stdout="tree-sha\n")
            elif "rev-list" in cmd:
                # Format: <behind>\t<ahead>
                return MagicMock(returncode=0, stdout="0\t3\n")
            elif "rev-parse" in cmd:
                return MagicMock(returncode=0, stdout="sha123\n")
            elif "diff" in cmd:
                return MagicMock(returncode=0, stdout="file.py\n")
            return MagicMock(returncode=0, stdout="")

        mock_run.side_effect = side_effect

        info = inspect_branch_status("origin/feat/some-feature")
        assert info["branch"] == "origin/feat/some-feature"
        assert info["is_remote"] is True
        assert info["clean_merge"] is True
        assert info["ahead"] == 3
        assert info["behind"] == 0


class TestWorktreeInventoryIntegration:
    """Tests for build_worktree_inventory() with remote tracking branches."""

    @patch("tools.pr_governance_core.inspection.get_feature_branches")
    def test_inventory_without_remote(self, mock_get_branches, tmp_path):
        repo_root = tmp_path
        (repo_root / ".worktrees").mkdir()

        inv = build_worktree_inventory(repo_root, include_remote=False)

        assert "remote_branches" not in inv
        assert "remote_tracking_only" not in inv["counts"]
        mock_get_branches.assert_not_called()

    @patch("tools.pr_governance_core.inspection.get_feature_branches")
    def test_inventory_with_remote(self, mock_get_branches, tmp_path):
        repo_root = tmp_path
        (repo_root / ".worktrees").mkdir()
        mock_get_branches.return_value = ["feat/local", "origin/feat/remote-1", "origin/feat/remote-2"]

        inv = build_worktree_inventory(repo_root, include_remote=True, fetch_first=False)

        assert "remote_branches" in inv
        assert inv["remote_branches"] == ["origin/feat/remote-1", "origin/feat/remote-2"]
        assert inv["counts"]["remote_tracking_only"] == 2
        mock_get_branches.assert_called_once_with(include_remote=True, fetch_first=False, repo_root=repo_root)


class TestCliArgumentParsing:
    """Tests for CLI argument parsing across subcommands."""

    def test_list_subcommand_flags(self):
        parser = build_parser()
        args = parser.parse_args(["list", "--remote", "--fetch"])
        assert args.command == "list"
        assert args.remote is True
        assert args.fetch is True

    def test_list_subcommand_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.remote is False
        assert args.fetch is False

    def test_inventory_subcommand_flags(self):
        parser = build_parser()
        args = parser.parse_args(["inventory", "--remote", "--fetch"])
        assert args.command == "inventory"
        assert args.remote is True
        assert args.fetch is True

    def test_review_subcommand_flags(self):
        parser = build_parser()
        args = parser.parse_args(["review", "origin/feat/remote", "--remote", "--fetch"])
        assert args.command == "review"
        assert args.branch == "origin/feat/remote"
        assert args.remote is True
        assert args.fetch is True


class TestCmdListE2E:
    """Integration test for cmd_list with remote branches."""

    @patch("tools.pr_governance_core.inspection.list_active_worktrees")
    @patch("tools.pr_governance_core.inspection.inspect_branch_status")
    @patch("tools.pr_governance_core.inspection.get_feature_branches")
    def test_cmd_list_surfaces_remote_only_branch(
        self,
        mock_get_branches,
        mock_inspect,
        mock_worktrees,
    ):
        mock_get_branches.return_value = ["origin/feat/pgd-followup-artifacts"]
        mock_worktrees.return_value = []
        mock_inspect.return_value = {
            "branch": "origin/feat/pgd-followup-artifacts",
            "ahead": 1,
            "behind": 0,
            "clean_merge": True,
            "readiness_receipt_pass": False,
            "is_remote": True,
        }

        buf = io.StringIO()
        args = argparse.Namespace(
            command="list",
            json=True,
            remote=True,
            fetch=False,
            all=True,
            clean=False,
            dirty=False,
            min_subrank=None,
            max_subrank=None,
            root=None,
        )

        with redirect_stdout(buf):
            ret = cmd_list(args)

        assert ret == 0
        output_data = json.loads(buf.getvalue())
        assert len(output_data) >= 1
        remote_item = next(
            (x for x in output_data if x["branch"] == "origin/feat/pgd-followup-artifacts"),
            None,
        )
        assert remote_item is not None
        assert remote_item["is_remote"] is True
        assert remote_item["readiness"] == "🌐 [REMOTE_TRACKING_ONLY]"
        assert "[CHECKOUT_OR_TRACK]" in remote_item["next_action"]


class TestLiveRepositoryRemoteBehavior:
    """Live read-only test verifying the actual repo behaves according to invariants."""

    def test_live_get_feature_branches_offline_vs_remote(self):
        # Offline mode
        offline_branches = get_feature_branches(include_remote=False)
        assert all(not b.startswith("origin/") for b in offline_branches)

        # Remote mode
        remote_branches = get_feature_branches(include_remote=True, fetch_first=False)
        # origin/feat/pgd-followup-artifacts exists on origin without local counterpart
        assert "origin/feat/pgd-followup-artifacts" in remote_branches
        assert "origin" not in remote_branches
        assert "origin/main" not in remote_branches
        assert "origin/HEAD" not in remote_branches
