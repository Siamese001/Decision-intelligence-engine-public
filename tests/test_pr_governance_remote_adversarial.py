"""
Adversarial and boundary edge-case tests for PR Governance Remote Discovery,
GitHub PR Adapter, and Live Meeting Bridge.

Covers:
1. Network faults: timeouts, DNS failure, HTTP 403/429/500/502, malformed JSON bodies.
2. Cache corruption: truncated JSON, null bytes, non-dict payloads, directory as cache.
3. Malformed git refs: shell injection metacharacters, path traversals, emojis, control chars.
4. Live meeting boundary cases: corrupted marker files, unreadable dirs, corrupted transcripts.
5. Credential hygiene: ensuring GITHUB_TOKEN is never leaked into outputs, receipts, or logs.
"""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.pr_governance_core.github_adapter import (
    GitHubPRInfo,
    fetch_prs_via_rest_api,
    load_cached_prs,
    parse_repo_slug_from_origin,
    save_cached_prs,
)
from tools.pr_governance_core.inspection import (
    get_feature_branches,
    is_remote_tracking_branch,
)
from tools.pr_governance_core.live_meeting_bridge import (
    LiveMeetingStatus,
    classify_live_worktree,
    is_live_meeting_container,
    scan_live_meeting_markers,
)
from tools.pr_governance_core.queue_actions import explain_next_action
from tools.pr_governance_core.render import render_branch_cell


class TestNetworkFaultAdversarial:
    """Class 1: Network dropouts, timeouts, rate-limits, and upstream server errors."""

    def test_git_fetch_timeout_degrades_gracefully(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Simulates subprocess TimeoutExpired on git fetch --prune origin."""
        def side_effect(cmd, *a, **kw):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] == "fetch":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30.0)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="feat/sample\n", stderr="")

        with patch("tools.pr_governance_core.inspection.subprocess.run", side_effect=side_effect):
            branches = get_feature_branches(include_remote=True, fetch_first=True, repo_root=tmp_path)
            assert isinstance(branches, list)
            assert "feat/sample" in branches
            err = capsys.readouterr().err
            assert "Warning: git fetch --prune origin failed or timed out" in err

    def test_git_fetch_called_process_error_degrades_gracefully(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Simulates nonzero exit on git fetch."""
        def side_effect(cmd, *a, **kw):
            if isinstance(cmd, list) and len(cmd) > 1 and cmd[1] == "fetch":
                raise subprocess.CalledProcessError(returncode=128, cmd=cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="feat/sample\n", stderr="")

        with patch("tools.pr_governance_core.inspection.subprocess.run", side_effect=side_effect):
            branches = get_feature_branches(include_remote=True, fetch_first=True, repo_root=tmp_path)
            assert isinstance(branches, list)
            assert "feat/sample" in branches

    def test_github_rest_api_http_403_rate_limited(self):
        """Simulates GitHub API 403 Forbidden with rate limit exhaustion."""
        err_fp = io.BytesIO(b'{"message": "API rate limit exceeded"}')
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/test/repo/pulls",
            code=403,
            msg="Forbidden",
            hdrs={"X-RateLimit-Remaining": "0"},
            fp=err_fp,
        )
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", side_effect=mock_err):
            result = fetch_prs_via_rest_api("test/repo")
            assert result is None

    def test_github_rest_api_http_429_too_many_requests(self):
        """Simulates HTTP 429 Too Many Requests."""
        err_fp = io.BytesIO(b'{"message": "Too many requests"}')
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/test/repo/pulls",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "60"},
            fp=err_fp,
        )
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", side_effect=mock_err):
            result = fetch_prs_via_rest_api("test/repo")
            assert result is None

    def test_github_rest_api_http_502_bad_gateway(self):
        """Simulates GitHub API 502 Bad Gateway / upstream outage."""
        err_fp = io.BytesIO(b"Bad Gateway")
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/test/repo/pulls",
            code=502,
            msg="Bad Gateway",
            hdrs={},
            fp=err_fp,
        )
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", side_effect=mock_err):
            result = fetch_prs_via_rest_api("test/repo")
            assert result is None

    def test_github_rest_api_dns_failure(self):
        """Simulates socket.gaierror / URLError DNS resolution failure."""
        mock_err = urllib.error.URLError(reason="[Errno 8] nodename nor servname provided, or not known")
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", side_effect=mock_err):
            result = fetch_prs_via_rest_api("test/repo")
            assert result is None

    def test_github_rest_api_truncated_json_response(self):
        """Simulates incomplete or truncated JSON body returned by network drop."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'[{"number": 1, "title": "incomp'
        mock_resp.__enter__.return_value = mock_resp
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_prs_via_rest_api("test/repo")
            assert result is None

    def test_github_rest_api_non_list_response(self):
        """Simulates API returning a single error object instead of an array."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"message": "Not Found", "documentation_url": "https://..."}'
        mock_resp.__enter__.return_value = mock_resp
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_prs_via_rest_api("test/repo")
            assert result is None


class TestCacheCorruptionAdversarial:
    """Class 2: Corrupted, truncated, malformed, or hostile cache files."""

    def test_load_empty_cache_file(self, tmp_path: Path):
        """0-byte empty cache file returns None without crashing."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_bytes(b"")
        assert load_cached_prs(cache_path) is None

    def test_load_binary_garbage_cache(self, tmp_path: Path):
        """Cache containing raw binary bytes returns None."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_bytes(b"\x00\x01\xfe\xff\xca\xfe\xba\xbe")
        assert load_cached_prs(cache_path) is None

    def test_load_malformed_json_syntax(self, tmp_path: Path):
        """Cache containing invalid JSON syntax returns None."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_text('{"cached_at_epoch": 12345, "prs": [', encoding="utf-8")
        assert load_cached_prs(cache_path) is None

    def test_load_non_dict_root(self, tmp_path: Path):
        """Cache containing JSON list, string, or number at root returns None."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps(["item1", "item2"]), encoding="utf-8")
        assert load_cached_prs(cache_path) is None

        cache_path.write_text(json.dumps("just a string"), encoding="utf-8")
        assert load_cached_prs(cache_path) is None

        cache_path.write_text(json.dumps(12345), encoding="utf-8")
        assert load_cached_prs(cache_path) is None

    def test_load_cache_missing_epoch(self, tmp_path: Path):
        """Cache missing cached_at_epoch field returns None."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps({"prs": []}), encoding="utf-8")
        assert load_cached_prs(cache_path) is None

    def test_load_cache_non_numeric_epoch(self, tmp_path: Path):
        """Cache with corrupted string epoch returns None."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps({"cached_at_epoch": "corrupted", "prs": []}), encoding="utf-8")
        assert load_cached_prs(cache_path) is None

    def test_load_cache_directory_instead_of_file(self, tmp_path: Path):
        """Path is a directory rather than a file."""
        cache_path = tmp_path / "cache.json"
        cache_path.mkdir(parents=True)
        assert load_cached_prs(cache_path) is None

    def test_save_cached_prs_write_error_is_swallowed(self, tmp_path: Path):
        """Simulates write failure during save_cached_prs; must not raise exception."""
        cache_path = tmp_path / "non_writable_dir" / "cache.json"
        with patch("pathlib.Path.write_text", side_effect=PermissionError("Read-only filesystem")):
            # Must complete safely without raising PermissionError
            save_cached_prs(cache_path, [GitHubPRInfo(number=1, title="T", url="U", head_ref="H")])


class TestGitRefSecurityAndMetacharacters:
    """Class 3: Malformed git branch refnames, command injection attempts, path traversals."""

    @pytest.mark.parametrize(
        "hostile_ref",
        [
            "origin/feat/$(rm -rf /)",
            "origin/feat/`touch /tmp/pwn`",
            "origin/feat/; cat /etc/passwd",
            "origin/feat/| id",
            "origin/feat/&& echo pwned",
            "origin/feat/../../../../etc/shadow",
            "origin/feat/has spaces in name",
            "origin/feat/newline\nin\nbranch",
            "origin/feat/\r\ncarriage\r\n",
            "origin/feat/🔥-rocket-🚀-unicode",
            "origin/feat/" + ("nested/" * 25) + "long_branch",
            "origin/HEAD",
            "origin/HEAD -> origin/main",
        ],
    )
    def test_is_remote_tracking_branch_with_hostile_refs(self, hostile_ref: str):
        """is_remote_tracking_branch accurately classifies refs starting with origin/."""
        assert is_remote_tracking_branch(hostile_ref) is True

    def test_render_branch_cell_sanitizes_hostile_refs(self):
        """render_branch_cell safely handles special characters and injection payloads."""
        hostile_ref = "origin/feat/$(whoami);rm -rf /"
        rendered = render_branch_cell(hostile_ref, is_remote=True)
        assert "🌐" in rendered
        assert hostile_ref in rendered

    def test_explain_next_action_handles_hostile_ref(self):
        """explain_next_action generates safe [CHECKOUT_OR_TRACK] recommendation."""
        hostile_ref = "origin/feat/$(calc.exe)"
        act = explain_next_action({"branch": hostile_ref, "behind": 0, "open_waves": "—", "dirty": False})
        assert "[CHECKOUT_OR_TRACK]" in act
        assert hostile_ref in act

    def test_parse_repo_slug_malformed_urls(self):
        """parse_repo_slug_from_origin handles invalid git remote URLs gracefully."""
        with patch("tools.pr_governance_core.github_adapter.run_git", return_value="https://gitlab.com/other/repo.git"):
            slug = parse_repo_slug_from_origin()
            assert slug == "Siamese001/decision-intelligence-engine"

        with patch("tools.pr_governance_core.github_adapter.run_git", side_effect=Exception("git error")):
            slug = parse_repo_slug_from_origin()
            assert slug == "Siamese001/decision-intelligence-engine"


class TestLiveMeetingBridgeAdversarial:
    """Class 4: Corrupted live meeting markers, unreadable files, and malformed directories."""

    def test_corrupted_empty_recording_active_marker(self, tmp_path: Path):
        """0-byte .recording_active marker still detects container and classifies RECORDING_IN_PROGRESS."""
        wt = tmp_path / "live_session_corrupt_marker"
        wt.mkdir()
        (wt / ".recording_active").write_bytes(b"")
        assert is_live_meeting_container(wt) is True
        state, info = classify_live_worktree(wt)
        assert state == LiveMeetingStatus.RECORDING_IN_PROGRESS

    def test_marker_is_directory_instead_of_file(self, tmp_path: Path):
        """.recording_active is a subdirectory."""
        wt = tmp_path / "live_session_dir_marker"
        wt.mkdir()
        (wt / ".recording_active").mkdir()
        assert is_live_meeting_container(wt) is True
        state, info = classify_live_worktree(wt)
        assert state == LiveMeetingStatus.RECORDING_IN_PROGRESS

    def test_corrupted_live_transcript_binary_garbage(self, tmp_path: Path):
        """_live_transcript.md containing null bytes and invalid UTF-8 does not crash scanner."""
        wt = tmp_path / "live_session_bad_transcript"
        wt.mkdir()
        (wt / "meeting_live_transcript.md").write_bytes(b"\x00\x80\xff\xfe\xca\xfe")
        scan = scan_live_meeting_markers(wt)
        assert len(scan["live_transcripts"]) == 1

    def test_nonexistent_or_deleted_worktree_path(self, tmp_path: Path):
        """classify_live_meeting_worktree handles nonexistent path without raising FileNotFoundError."""
        missing = tmp_path / "deleted_midway_session"
        state, info = classify_live_worktree(missing)
        assert state == LiveMeetingStatus.ABANDONED_STUB

    def test_unreadable_permission_denied_worktree(self, tmp_path: Path):
        """Worktree directory without read permissions degrades safely."""
        wt = tmp_path / "live_locked_session"
        wt.mkdir()
        try:
            wt.chmod(0o000)
            state, info = classify_live_worktree(wt)
            assert isinstance(state, LiveMeetingStatus)
        finally:
            wt.chmod(0o755)


class TestCredentialHygieneAdversarial:
    """Class 5: Verification that sensitive credentials are never leaked."""

    def test_github_token_is_not_leaked_on_network_failure(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
        """Asserts GITHUB_TOKEN never appears in exception messages or stderr."""
        fake_token = "ghp_VERY_SECRET_CREDENTIAL_STRING_DO_NOT_LEAK_12345"
        monkeypatch.setenv("GITHUB_TOKEN", fake_token)

        # Trigger HTTP error with token set
        err_fp = io.BytesIO(b'{"message": "Bad credentials"}')
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/test/repo/pulls",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=err_fp,
        )
        with patch("tools.pr_governance_core.github_adapter.urllib.request.urlopen", side_effect=mock_err):
            res = fetch_prs_via_rest_api("test/repo")
            assert res is None

        captured = capsys.readouterr()
        assert fake_token not in captured.out
        assert fake_token not in captured.err

    def test_github_token_is_not_stored_in_cache_payload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Asserts cached PR JSON never contains the token or authorization headers."""
        fake_token = "ghp_LEAK_TEST_TOKEN_SECRET_99999"
        monkeypatch.setenv("GITHUB_TOKEN", fake_token)

        cache_path = tmp_path / "test_cache.json"
        prs = [GitHubPRInfo(number=100, title="PR", url="https://...", head_ref="feat/pr")]
        save_cached_prs(cache_path, prs)

        content = cache_path.read_text(encoding="utf-8")
        assert fake_token not in content
        assert "Authorization" not in content
        assert "Bearer" not in content
