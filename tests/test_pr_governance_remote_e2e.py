"""
End-to-End tests for PR Governance Remote Discovery, GitHub Adapter,
and Live Meeting Bridge lifecycle.

Tests the full governance lifecycle across subcommands (list, inventory, review)
with remote tracking refs, cached/live GitHub PRs, and live worktree sessions,
asserting the 7-column table layout, diagnostic ledger, and zero repo mutation.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from tools.pr_governance import build_parser, main
from tools.pr_governance_core import (
    cmd_inventory,
    cmd_list,
    cmd_review,
    run_git,
)
from tools.pr_governance_core.github_adapter import GitHubPRInfo, save_cached_prs


@pytest.fixture
def governed_e2e_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """
    Provisions a realistic git repository with an upstream remote,
    local feature branches, remote-tracking refs, and live meeting worktrees.
    """
    origin_dir = tmp_path / "origin_remote.git"
    origin_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=origin_dir, check=True, capture_output=True)

    repo_dir = tmp_path / "e2e_workdir"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Governance Tester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo_dir, check=True)

    # Initial commit on main
    (repo_dir / "README.md").write_text("# Decision Intelligence Engine\n", encoding="utf-8")
    (repo_dir / "CLAUDE.md").write_text("# Governance rules\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "chore: initialize repository"], cwd=repo_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin_dir)], cwd=repo_dir, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_dir, check=True, capture_output=True)

    # 1. Branch: feat/shared-feature (exists both locally and remotely)
    subprocess.run(["git", "checkout", "-b", "feat/shared-feature"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "feature.py").write_text("def shared(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat: shared feature logic"], cwd=repo_dir, check=True)
    subprocess.run(["git", "push", "-u", "origin", "feat/shared-feature"], cwd=repo_dir, check=True, capture_output=True)

    # 2. Branch: feat/remote-only-alpha (pushed to origin, deleted locally to simulate remote-tracking only)
    subprocess.run(["git", "checkout", "-b", "feat/remote-only-alpha"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "remote.py").write_text("def remote(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "remote.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat: remote standalone feature"], cwd=repo_dir, check=True)
    subprocess.run(["git", "push", "-u", "origin", "feat/remote-only-alpha"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-D", "feat/remote-only-alpha"], cwd=repo_dir, check=True, capture_output=True)

    # 3. Branch: feat/local-standalone (local branch only, never pushed)
    subprocess.run(["git", "checkout", "-b", "feat/local-standalone"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "local.py").write_text("def local(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "local.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat: local standalone feature"], cwd=repo_dir, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

    # Ensure remote tracking refs are fresh
    subprocess.run(["git", "fetch", "--prune", "origin"], cwd=repo_dir, check=True, capture_output=True)

    # 4. Provision .worktrees directory with live meeting sessions
    wt_root = repo_dir / ".worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)

    # Session 1: Recording in progress
    s1 = wt_root / "live_recording_active_session"
    s1.mkdir(parents=True)
    (s1 / ".recording_active").write_text(json.dumps({"pid": 12345, "started_at": time.time()}), encoding="utf-8")
    (s1 / "chunk_001.wav").write_bytes(b"\x00" * 100)

    # Session 2: Synthesis pending (audio chunks completed, no lock marker, deliverables missing)
    s2 = wt_root / "live_synthesis_pending_session"
    s2.mkdir(parents=True)
    (s2 / "chunk_001.wav").write_bytes(b"\x00" * 100)
    (s2 / "chunk_002.wav").write_bytes(b"\x00" * 100)

    # Session 3: Stage U0 Ready (has Artifacts 01, 02, and 03)
    s3 = wt_root / "live_stage_u0_ready_session"
    s3.mkdir(parents=True)
    (s3 / "01_Review_01-01-2026_hitl_resolved_transcript.md").write_text("# Forensic Transcript\n", encoding="utf-8")
    (s3 / "02_Review_01-01-2026_executive_recap.md").write_text("# Executive Recap\n", encoding="utf-8")
    (s3 / "03_Review_01-01-2026_canonical_signal_projection.json").write_text('{"signals": []}\n', encoding="utf-8")

    # Session 4: Orphaned audio
    s4 = wt_root / "meeting_orphaned_audio"
    s4.mkdir(parents=True)
    (s4 / "raw_audio.wav").write_bytes(b"\x00" * 50)

    # Session 5: Abandoned stub
    s5 = wt_root / "live_abandoned_stub"
    s5.mkdir(parents=True)

    # 5. Populate GitHub PR Cache
    cache_path = repo_dir / ".local" / "cache" / "github_pr_cache.json"
    prs = [
        GitHubPRInfo(
            number=42,
            title="Shared Feature Implementation",
            url="https://github.com/Siamese001/decision-intelligence-engine/pull/42",
            head_ref="feat/shared-feature",
            base_ref="main",
            state="OPEN",
        ),
        GitHubPRInfo(
            number=43,
            title="Remote Only Alpha Followup",
            url="https://github.com/Siamese001/decision-intelligence-engine/pull/43",
            head_ref="feat/remote-only-alpha",
            base_ref="main",
            state="OPEN",
        ),
    ]
    save_cached_prs(cache_path, prs)

    yield repo_dir


class TestPRGovernanceRemoteE2E:
    """E2E test suite covering remote discovery, GitHub PR sync, and live bridge."""

    def test_e2e_cmd_list_offline_default_preserves_local_only(
        self,
        governed_e2e_repo: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """In default offline mode, remote-only branches are omitted, and zero network calls occur."""
        monkeypatch.setattr("tools.pr_governance_core.common.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.inspection.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.github_adapter.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.worktree_inventory.ROOT", governed_e2e_repo)

        # Baseline git status & commit hash
        head_before = run_git(["rev-parse", "HEAD"], cwd=governed_e2e_repo).strip()
        status_before = run_git(["status", "--porcelain"], cwd=governed_e2e_repo).strip()

        parser = build_parser()
        args = parser.parse_args(["list"])
        ret = cmd_list(args)
        assert ret == 0

        captured = capsys.readouterr().out
        # Local branches are present
        assert "feat/shared-feature" in captured
        assert "feat/local-standalone" in captured
        # Remote-only branch is NOT listed without --remote
        assert "origin/feat/remote-only-alpha" not in captured
        # 7-column table layout exists
        assert "| Branch" in captured
        assert "Readiness" in captured
        assert "Next Action" in captured

        # Assert Zero Repo Mutation Invariant
        head_after = run_git(["rev-parse", "HEAD"], cwd=governed_e2e_repo).strip()
        status_after = run_git(["status", "--porcelain"], cwd=governed_e2e_repo).strip()
        assert head_before == head_after
        assert status_before == status_after

    def test_e2e_cmd_list_with_remote_and_github_sync(
        self,
        governed_e2e_repo: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """
        With --remote --github, remote-only branches surface with 🌐 icon,
        GitHub PR badges appear in the Branch column, and live worktree sessions
        are classified with canonical badges.
        """
        monkeypatch.setattr("tools.pr_governance_core.common.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.inspection.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.github_adapter.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.worktree_inventory.ROOT", governed_e2e_repo)

        parser = build_parser()
        args = parser.parse_args(["list", "--remote", "--github"])
        ret = cmd_list(args)
        assert ret == 0

        out = capsys.readouterr().out

        # 1. Remote-only branch discovery & formatting
        assert "origin/feat/remote-only-alpha" in out
        assert "🌐 [REMOTE_TRACKING_ONLY]" in out
        assert "[CHECKOUT_OR_TRACK]" in out

        # 2. Local branch with counterpart is NOT marked remote-tracking-only
        assert "feat/shared-feature" in out

        # 3. GitHub PR Badges rendered inside branch cell
        assert "[[#42]](https://github.com/Siamese001/decision-intelligence-engine/pull/42)" in out
        assert "[[#43]](https://github.com/Siamese001/decision-intelligence-engine/pull/43)" in out

        # 4. Live meeting worktrees classified into canonical statuses
        assert "live_recording_active_session" in out
        assert "🎙️ Recording" in out
        assert "[WAIT_RECORDING]" in out

        assert "live_synthesis_pending_session" in out
        assert "⏳ Synthesis Pending" in out
        assert "[RUN_SYNTHESIS]" in out

        assert "live_stage_u0_ready_session" in out
        assert "📦 Stage U0 Ready" in out
        assert "[COMMIT_DELIVERABLES]" in out

        assert "meeting_orphaned_audio" in out
        assert "🎵 Orphaned Audio" in out
        assert "[QUARANTINE_AUDIO]" in out

        assert "live_abandoned_stub" in out
        assert "📁 Abandoned Stub" in out
        assert "[QUARANTINE_STUB]" in out

        # 5. Diagnostic Ledger contains branch details
        assert "* **[" in out
        assert "origin/feat/remote-only-alpha" in out

    def test_e2e_cmd_inventory_full_coverage(
        self,
        governed_e2e_repo: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """
        cmd_inventory with --remote --github reports comprehensive metrics:
        worktrees, live meetings, remote tracking candidates, and GitHub PR correlation.
        """
        monkeypatch.setattr("tools.pr_governance_core.common.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.inspection.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.github_adapter.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.worktree_inventory.ROOT", governed_e2e_repo)

        parser = build_parser()
        args = parser.parse_args(["inventory", "--remote", "--github"])
        ret = cmd_inventory(args)
        assert ret == 0

        out = capsys.readouterr().out
        assert "WORKTREE & STASH INVENTORY SUMMARY" in out
        assert "Live meeting sessions" in out
        assert "Remote tracking branches" in out
        assert "Open GitHub PRs" in out

        # JSON Mode verification
        args_json = parser.parse_args(["inventory", "--remote", "--github", "--json"])
        ret_json = cmd_inventory(args_json)
        assert ret_json == 0

        json_out = capsys.readouterr().out
        parsed = json.loads(json_out)
        assert "worktrees" in parsed
        assert "counts" in parsed
        assert "live_meeting_sessions" in parsed["counts"]
        assert parsed["counts"]["live_meeting_sessions"] >= 4
        assert parsed["counts"]["remote_tracking_only"] >= 1
        assert parsed["counts"]["open_github_prs"] == 2

    def test_e2e_cmd_review_with_remote_and_github(
        self,
        governed_e2e_repo: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """cmd_review inspects branch status and incorporates GitHub PR information."""
        monkeypatch.setattr("tools.pr_governance_core.common.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.inspection.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.github_adapter.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.lifecycle.ROOT", governed_e2e_repo)

        # Checkout branch so it's active in the repo
        run_git(["checkout", "feat/shared-feature"], cwd=governed_e2e_repo)

        parser = build_parser()
        args = parser.parse_args([
            "review",
            "feat/shared-feature",
            "--github",
        ])

        real_run = subprocess.run

        def side_effect(cmd, *a, **kw):
            if isinstance(cmd, list) and any("verify_main_readiness.py" in str(x) for x in cmd):
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ALL 29 GATES PASS\n", stderr="")
            return real_run(cmd, *a, **kw)

        with patch("tools.pr_governance_core.lifecycle.subprocess.run", side_effect=side_effect):
            ret = cmd_review(args)

        assert ret == 0
        out = capsys.readouterr().out
        assert "GOVERNED PR REVIEW AUDIT:" in out
        assert "feat/shared-feature" in out
        assert "GitHub PR:" in out
        assert "#42" in out

    def test_e2e_cli_entrypoint_via_main(
        self,
        governed_e2e_repo: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Executes main() directly as invoked by sys.argv."""
        monkeypatch.setattr("tools.pr_governance_core.common.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.inspection.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.github_adapter.ROOT", governed_e2e_repo)
        monkeypatch.setattr("tools.pr_governance_core.worktree_inventory.ROOT", governed_e2e_repo)

        monkeypatch.setattr(sys, "argv", ["pr_governance.py", "list", "--remote", "--github", "--json"])
        ret = main()
        assert ret == 0

        raw_json = capsys.readouterr().out
        data = json.loads(raw_json)
        assert isinstance(data, list)
        branches = [item["branch"] for item in data]
        assert "origin/feat/remote-only-alpha" in branches
        assert "feat/shared-feature" in branches
