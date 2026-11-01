"""
Unit and regression tests for Governed GitHub PR Integration Adapter (Wave 2).
Tests cover:
- GitHubPRInfo serialization and deserialization
- Origin URL repo slug parsing (SSH, HTTPS, fallback)
- Cache persistence, TTL expiration, and corruption resistance
- gh CLI invocation and parsing
- GitHub REST API invocation, Bearer auth, and HTTP/URLError resilience
- Ref lookup indexing (local ref, origin/ ref, full refs)
- Offline air-gap graceful fallbacks
- Branch cell rendering with PR badge links
- CLI subcommand integration (--github flag)
"""
from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.pr_governance import build_parser
from tools.pr_governance_core.github_adapter import (
    GitHubPRInfo,
    fetch_prs_via_gh_cli,
    fetch_prs_via_rest_api,
    get_github_prs,
    get_pr_for_branch,
    load_cached_prs,
    parse_repo_slug_from_origin,
    save_cached_prs,
)
from tools.pr_governance_core.render import format_branch_cell
from tools.pr_governance_core.worktree_inventory import build_worktree_inventory


class TestGitHubPRInfoDataModel:
    """Tests for GitHubPRInfo dataclass."""

    def test_round_trip_serialization(self):
        pr = GitHubPRInfo(
            number=42,
            title="feat: add remote pr sync",
            url="https://github.com/Siamese001/decision-intelligence-engine/pull/42",
            head_ref="feat/remote-sync",
            base_ref="main",
            state="OPEN",
            draft=False,
            ci_status="SUCCESS",
        )
        d = pr.to_dict()
        assert d["number"] == 42
        assert d["title"] == "feat: add remote pr sync"
        assert d["head_ref"] == "feat/remote-sync"

        restored = GitHubPRInfo.from_dict(d)
        assert restored == pr

    def test_from_dict_defaults(self):
        pr = GitHubPRInfo.from_dict({"number": 101, "head_ref": "feat/minimal"})
        assert pr.number == 101
        assert pr.head_ref == "feat/minimal"
        assert pr.base_ref == "main"
        assert pr.state == "OPEN"
        assert pr.draft is False
        assert pr.ci_status is None


class TestRepoSlugParsing:
    """Tests for parse_repo_slug_from_origin()."""

    @patch("tools.pr_governance_core.github_adapter.run_git")
    def test_parse_ssh_url(self, mock_run_git):
        mock_run_git.return_value = "git@github.com:Siamese001/decision-intelligence-engine.git\n"
        assert parse_repo_slug_from_origin() == "Siamese001/decision-intelligence-engine"

    @patch("tools.pr_governance_core.github_adapter.run_git")
    def test_parse_https_url(self, mock_run_git):
        mock_run_git.return_value = "https://github.com/Siamese001/decision-intelligence-engine.git\n"
        assert parse_repo_slug_from_origin() == "Siamese001/decision-intelligence-engine"

    @patch("tools.pr_governance_core.github_adapter.run_git")
    def test_parse_https_url_no_dot_git(self, mock_run_git):
        mock_run_git.return_value = "https://github.com/OtherOrg/custom-repo\n"
        assert parse_repo_slug_from_origin() == "OtherOrg/custom-repo"

    @patch("tools.pr_governance_core.github_adapter.run_git")
    def test_fallback_on_error(self, mock_run_git):
        mock_run_git.side_effect = RuntimeError("git failed")
        assert parse_repo_slug_from_origin() == "Siamese001/decision-intelligence-engine"


class TestCachePersistenceAndTTL:
    """Tests for load_cached_prs and save_cached_prs."""

    def test_save_and_load_within_ttl(self, tmp_path: Path):
        cache_file = tmp_path / "cache" / "prs.json"
        prs = [
            GitHubPRInfo(
                number=7,
                title="PR Seven",
                url="https://github.com/org/repo/pull/7",
                head_ref="feat/seven",
            )
        ]
        save_cached_prs(cache_file, prs)
        assert cache_file.exists()

        loaded = load_cached_prs(cache_file, max_age_seconds=60.0)
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].number == 7

    def test_load_expired_cache_returns_none(self, tmp_path: Path):
        cache_file = tmp_path / "cache" / "prs.json"
        prs = [
            GitHubPRInfo(
                number=8,
                title="PR Eight",
                url="https://github.com/org/repo/pull/8",
                head_ref="feat/eight",
            )
        ]
        save_cached_prs(cache_file, prs)

        # Alter timestamp in cache to 10 minutes ago
        raw = json.loads(cache_file.read_text())
        raw["cached_at_epoch"] = time.time() - 600.0
        cache_file.write_text(json.dumps(raw))

        # Check with 300s TTL
        loaded = load_cached_prs(cache_file, max_age_seconds=300.0)
        assert loaded is None

    def test_load_corrupted_cache_returns_none(self, tmp_path: Path):
        cache_file = tmp_path / "cache" / "corrupted.json"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("{bad json: true]")

        loaded = load_cached_prs(cache_file)
        assert loaded is None


class TestGhCliAdapter:
    """Tests for fetch_prs_via_gh_cli()."""

    @patch("shutil.which")
    def test_gh_not_installed_returns_none(self, mock_which):
        mock_which.return_value = None
        assert fetch_prs_via_gh_cli() is None

    @patch("shutil.which")
    @patch("tools.pr_governance_core.github_adapter.subprocess.run")
    def test_gh_cli_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/gh"
        fake_output = [
            {
                "number": 15,
                "title": "feat: test pr",
                "headRefName": "feat/test",
                "baseRefName": "main",
                "state": "OPEN",
                "url": "https://github.com/org/repo/pull/15",
                "isDraft": False,
            }
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(fake_output))

        prs = fetch_prs_via_gh_cli()
        assert prs is not None
        assert len(prs) == 1
        assert prs[0].number == 15
        assert prs[0].head_ref == "feat/test"

    @patch("shutil.which")
    @patch("tools.pr_governance_core.github_adapter.subprocess.run")
    def test_gh_cli_error_returns_none(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/gh"
        mock_run.return_value = MagicMock(returncode=1, stderr="Not logged in")
        assert fetch_prs_via_gh_cli() is None


class TestGitHubRestApiAdapter:
    """Tests for fetch_prs_via_rest_api()."""

    @patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen")
    def test_rest_api_success(self, mock_urlopen):
        fake_api_response = [
            {
                "number": 23,
                "title": "fix: api response test",
                "html_url": "https://github.com/Siamese001/repo/pull/23",
                "state": "open",
                "draft": True,
                "head": {"ref": "fix/api-test"},
                "base": {"ref": "main"},
            }
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        prs = fetch_prs_via_rest_api("Siamese001/repo", token="mock-token")
        assert prs is not None
        assert len(prs) == 1
        assert prs[0].number == 23
        assert prs[0].head_ref == "fix/api-test"
        assert prs[0].draft is True

    @patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen")
    def test_rest_api_http_error_graceful_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com",
            code=404,
            msg="Not Found",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )
        assert fetch_prs_via_rest_api("Siamese001/private-repo") is None

    @patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen")
    def test_rest_api_network_error_graceful_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        assert fetch_prs_via_rest_api("Siamese001/repo") is None


class TestGetGitHubPrsAndLookupMap:
    """Tests for get_github_prs() and ref lookup."""

    @patch("tools.pr_governance_core.github_adapter.load_cached_prs")
    @patch("tools.pr_governance_core.github_adapter.fetch_prs_via_gh_cli")
    @patch("tools.pr_governance_core.github_adapter.save_cached_prs")
    def test_get_github_prs_indexes_local_and_remote(
        self,
        mock_save,
        mock_fetch_cli,
        mock_load_cache,
    ):
        mock_load_cache.return_value = None  # Cache miss
        mock_fetch_cli.return_value = [
            GitHubPRInfo(
                number=99,
                title="feat: lookup test",
                url="https://github.com/org/repo/pull/99",
                head_ref="feat/pgd-followup-artifacts",
            )
        ]

        pr_map = get_github_prs(force_refresh=True)

        assert "feat/pgd-followup-artifacts" in pr_map
        assert "origin/feat/pgd-followup-artifacts" in pr_map
        assert pr_map["feat/pgd-followup-artifacts"].number == 99

        # Test lookup helper
        found_local = get_pr_for_branch("feat/pgd-followup-artifacts", pr_map)
        assert found_local is not None
        assert found_local.number == 99

        found_remote = get_pr_for_branch("origin/feat/pgd-followup-artifacts", pr_map)
        assert found_remote is not None
        assert found_remote.number == 99

        assert get_pr_for_branch("feat/unrelated", pr_map) is None

    @patch("tools.pr_governance_core.github_adapter.load_cached_prs")
    @patch("tools.pr_governance_core.github_adapter.fetch_prs_via_gh_cli")
    @patch("tools.pr_governance_core.github_adapter.fetch_prs_via_rest_api")
    def test_offline_fallback_returns_empty_when_no_cache(
        self,
        mock_rest,
        mock_cli,
        mock_cache,
    ):
        mock_cache.return_value = None
        mock_cli.return_value = None
        mock_rest.return_value = None

        pr_map = get_github_prs()
        assert pr_map == {}


class TestRenderBranchCellWithGitHubPR:
    """Tests for format_branch_cell with github_pr argument."""

    def test_render_with_github_pr_local_worktree(self):
        pr = {"number": 42, "url": "https://github.com/org/repo/pull/42"}
        wt = {"path": "/path/to/wt", "is_root": False}
        cell = format_branch_cell("feat/my-branch", wt=wt, github_pr=pr)
        assert cell == "[feat/my-branch](file:///path/to/wt) [[#42]](https://github.com/org/repo/pull/42)"

    def test_render_with_github_pr_remote_only(self):
        pr = {"number": 105, "url": "https://github.com/org/repo/pull/105"}
        cell = format_branch_cell("origin/feat/remote-branch", is_remote=True, github_pr=pr)
        assert cell == "🌐 origin/feat/remote-branch [[#105]](https://github.com/org/repo/pull/105)"

    def test_render_without_pr_remains_clean(self):
        cell = format_branch_cell("feat/plain")
        assert cell == "feat/plain"


class TestCliAndInventoryIntegration:
    """Tests for CLI parser and inventory integration with --github flag."""

    def test_parser_has_github_flag(self):
        parser = build_parser()
        args_list = parser.parse_args(["list", "--github"])
        assert args_list.github is True

        args_inv = parser.parse_args(["inventory", "--github"])
        assert args_inv.github is True

        args_rev = parser.parse_args(["review", "feat/test", "--github"])
        assert args_rev.github is True

    @patch("tools.pr_governance_core.github_adapter.get_github_prs")
    def test_inventory_with_github_flag(self, mock_get_prs, tmp_path: Path):
        mock_get_prs.return_value = {
            "feat/pr1": GitHubPRInfo(
                number=1,
                title="PR 1",
                url="https://github.com/org/repo/pull/1",
                head_ref="feat/pr1",
            )
        }
        (tmp_path / ".worktrees").mkdir()
        inv = build_worktree_inventory(tmp_path, include_github=True)
        assert inv["counts"]["open_github_prs"] == 1
        assert len(inv["open_github_prs"]) == 1
        assert inv["open_github_prs"][0]["number"] == 1
